"""跨层对证（layer-crosscheck.v1）纯函数契约。

原则：数值项按容差判 pass/warn/conflict；异量纲项（结构分 vs 情绪分）
只校验方向；缺数据记 unavailable 且**不参与打分**（降级不能伪装成不一致）。
"""
from __future__ import annotations

from server.layer_crosscheck import build_crosscheck


def _fact(**kw):
    base = {
        "sentiment_score": 80.0, "band": "过热", "data_scope": "full_a_share",
        "up_ratio": 83.9, "amount": 1.79e12, "limit_up": 145,
        "top_sectors": ["互联网", "传媒娱乐", "网络安全"],
        "as_of": "2026-11-02T21:00:00+08:00", "coverage": 1.0,
    }
    base.update(kw)
    return base


def _survey(**kw):
    base = {
        "structure_score": 100, "structure_status": "强势上行",
        "sentiment_level": 80.0, "up_ratio": 83.9, "amount": 1.79e12, "limit_up": 145,
        "top_sectors": ["互联网", "传媒娱乐", "网络安全"],
        "data_scope": "full_a_share", "as_of": "2026-11-02 21:00:30",
    }
    base.update(kw)
    return base


def _decision(**kw):
    base = {"sentiment_score": 80.0, "as_of": "2026-11-02T21:00:05+08:00",
            "consensus": 0.8, "stance": "attack"}
    base.update(kw)
    return base


def _by_id(result):
    return {c["id"]: c for c in result["checks"]}


def test_all_consistent_passes():
    r = build_crosscheck(_fact(), _survey(), _decision())
    checks = _by_id(r)
    assert r["ok"] is True
    assert checks["sentiment_fact_survey"]["status"] == "pass"
    assert checks["sentiment_fact_decision"]["status"] == "pass"
    assert checks["breadth_up_ratio"]["status"] == "pass"
    assert checks["turnover"]["status"] == "pass"
    assert checks["limit_up"]["status"] == "pass"
    assert checks["sector_lead"]["status"] == "pass"
    assert checks["scope"]["status"] == "pass"
    assert checks["freshness"]["status"] == "pass"
    # 结构分 100 与情绪分 80 不同量纲：不判数值相等，只判方向
    assert checks["score_axes"]["status"] == "incomparable"


def test_sentiment_mismatch_beyond_tolerance_is_conflict():
    r = build_crosscheck(_fact(), _survey(sentiment_level=70.0), _decision())
    checks = _by_id(r)
    assert checks["sentiment_fact_survey"]["status"] == "conflict"
    assert r["ok"] is False
    assert any(c["id"] == "sentiment_fact_survey" for c in r["conflicts"])


def test_sentiment_small_drift_is_pass_or_warn():
    pass_case = _by_id(build_crosscheck(_fact(), _survey(sentiment_level=80.3), None))
    assert pass_case["sentiment_fact_survey"]["status"] == "pass"
    warn_case = _by_id(build_crosscheck(_fact(), _survey(sentiment_level=80.9), None))
    assert warn_case["sentiment_fact_survey"]["status"] == "warn"


def test_breadth_ratio_tolerance():
    assert _by_id(build_crosscheck(_fact(), _survey(up_ratio=84.2), None))["breadth_up_ratio"]["status"] == "pass"
    # 容差 0.5、warn_factor 2 → |Δ|≤1.0 为 warn，超过才是 conflict
    assert _by_id(build_crosscheck(_fact(), _survey(up_ratio=84.7), None))["breadth_up_ratio"]["status"] == "warn"
    assert _by_id(build_crosscheck(_fact(), _survey(up_ratio=90.0), None))["breadth_up_ratio"]["status"] == "conflict"


def test_turnover_relative_tolerance():
    # 0.3% 偏差 → pass；1.6% → conflict（容差 0.5%，warn 上限 1%）
    assert _by_id(build_crosscheck(_fact(), _survey(amount=1.795e12), None))["turnover"]["status"] == "pass"
    assert _by_id(build_crosscheck(_fact(), _survey(amount=1.82e12), None))["turnover"]["status"] == "conflict"


def test_sector_overlap_within_top_n():
    ok = _by_id(build_crosscheck(_fact(), _survey(top_sectors=["传媒娱乐", "互联网", "通信"]), None))
    assert ok["sector_lead"]["status"] == "pass"
    bad = _by_id(build_crosscheck(_fact(), _survey(top_sectors=["银行", "保险", "煤炭"]), None))
    assert bad["sector_lead"]["status"] == "conflict"


def test_scope_mismatch_is_warn_not_conflict():
    checks = _by_id(build_crosscheck(_fact(data_scope="full_a_share"), _survey(data_scope="core_sample"), None))
    assert checks["scope"]["status"] == "warn"


def test_freshness_skew():
    fresh = _by_id(build_crosscheck(_fact(), _survey(as_of="2026-11-02 21:04:00"), None))
    assert fresh["freshness"]["status"] == "pass"
    stale = _by_id(build_crosscheck(_fact(), _survey(as_of="2026-11-02 21:30:00"), None))
    assert stale["freshness"]["status"] == "conflict"


def test_axes_direction_conflict():
    """结构说强势、情绪已转冷 → 这是真冲突，不能因为量纲不同就放过。"""
    r = build_crosscheck(_fact(band="偏冷"), _survey(structure_status="强势上行"), None)
    axes = _by_id(r)["score_axes"]
    assert axes["status"] == "conflict"
    assert r["ok"] is False


def test_axes_neutral_is_not_conflict():
    axes = _by_id(build_crosscheck(_fact(band="中性"), _survey(structure_status="强势上行"), None))["score_axes"]
    assert axes["status"] == "incomparable"


def test_missing_side_is_unavailable_and_not_scored():
    r = build_crosscheck(_fact(sentiment_score=None), _survey(up_ratio=None), None)
    checks = _by_id(r)
    assert checks["sentiment_fact_survey"]["status"] == "unavailable"
    assert checks["breadth_up_ratio"]["status"] == "unavailable"
    # 缺数据不参与打分：其余项一致时仍是高分
    assert r["score"] >= 0.9
    assert r["comparable"] < r["total"]


def test_light_mode_skips_decision_check():
    r = build_crosscheck(_fact(), _survey(), None)
    assert "sentiment_fact_decision" not in _by_id(r)
    full = build_crosscheck(_fact(), _survey(), _decision())
    assert "sentiment_fact_decision" in _by_id(full)


def test_low_comparable_ratio_marks_not_ok():
    # 只给情绪分，其余全缺 → 可比率太低
    r = build_crosscheck({"sentiment_score": 80, "band": "过热"}, {}, None)
    assert r["comparable_ratio"] < 0.5
    assert r["ok"] is False


def test_config_snapshot_exposed():
    r = build_crosscheck(_fact(), _survey(), _decision())
    cfg = r["config"]
    assert cfg["number_tolerance"] == 0.5
    assert cfg["sector_top_n"] == 3
    assert cfg["min_comparable_ratio"] == 0.5


def test_sector_taxonomy_mismatch_is_incomparable_not_conflict():
    """申万行业 vs 东财板块：名称必然对不上，判不可比而不是冲突。"""
    r = build_crosscheck(_fact(top_sectors=["数字媒体"], sector_taxonomy="sw"),
                         _survey(top_sectors=["互联网"], sector_taxonomy="em"), None)
    check = _by_id(r)["sector_lead"]
    assert check["status"] == "incomparable"
    assert "分类体系不同" in check["note"]
    assert "sector_lead" not in [c["id"] for c in r["conflicts"]]


def test_scope_mismatch_makes_breadth_incomparable_and_blocks():
    """口径不同时，145 家涨停 vs 12 家不是冲突，而是不可比 —— 但结论要阻塞。"""
    r = build_crosscheck(_fact(data_scope="full_a_share", limit_up=145, up_ratio=83.9),
                         _survey(data_scope="core_sample", limit_up=12, up_ratio=80.5), None)
    checks = _by_id(r)
    assert checks["limit_up"]["status"] == "incomparable"
    assert checks["breadth_up_ratio"]["status"] == "incomparable"
    assert checks["turnover"]["status"] == "incomparable"
    assert "权限不同" not in checks["limit_up"]["note"]
    assert "口径不同" in checks["limit_up"]["note"]
    assert r["ok"] is False
    assert "scope_mismatch" in r["blockers"]
    assert r["conflicts"] == []
