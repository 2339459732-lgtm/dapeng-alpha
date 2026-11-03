"""决策闭环核心纯函数测试：情绪可复算、缺失不补值、六专家聚合与审计否决。"""
from __future__ import annotations

from datetime import datetime

from server.decision_experts import aggregate_expert_opinions, build_expert_opinions
from server.decision_snapshot import build_sentiment_snapshot
from server.market_coverage import _market_breadth, _quarter_candidates


def _packet(**overrides):
    packet = {
        "ok": True,
        "version": "market-packet.v1",
        "trace_id": "mk_test",
        "as_of": "2026-11-02T10:00:00+08:00",
        "quality": {
            "level": "good", "coverage_pct": 99.0, "score": 0.99,
            "stale": False, "actionable": True, "critical_missing": [],
        },
        "market": {
            "indices": [
                {"code": "sh000001", "name": "上证指数", "price": 3600, "chg": 0.8},
                {"code": "sz399001", "name": "深证成指", "price": 11000, "chg": 1.2},
                {"code": "sz399006", "name": "创业板指", "price": 2400, "chg": 1.6},
            ],
            "breadth": {"up": 3200, "down": 1800, "flat": 100, "limit_up": 70, "limit_down": 8},
            "turnover": {"amount": 1.2e12, "avg_turnover": 2.8},
            "capital_flow": {"available": True, "flow_a": 2e9, "flow_b": 1e9},
            "sectors": [
                {"name": "半导体", "avg_chg": 2.2, "up_ratio": 80, "amount": 2e10, "count": 50, "leader": {"name": "A", "chg": 9}},
                {"name": "银行", "avg_chg": 0.3, "up_ratio": 60, "amount": 1e10, "count": 20, "leader": {"name": "B", "chg": 2}},
                {"name": "煤炭", "avg_chg": -1.0, "up_ratio": 20, "amount": 5e9, "count": 10, "leader": {"name": "C", "chg": 1}},
            ],
            "stale": False,
            "risk_tags": [],
        },
        "north": {"ok": True, "data": {"hgt_up": 600, "hgt_down": 300}},
        "global": {"ok": True, "as_of_date": "2026-10-31", "scorecard": {"composite_score": 0.32}},
        "intel": {"risk_score": 25, "risk_level": "低", "ts": "2026-11-01 10:00:00"},
    }
    for key, value in overrides.items():
        if key == "quality":
            packet["quality"] = value
        elif key == "market":
            packet["market"] = value
        else:
            packet[key] = value
    return packet


def test_sentiment_is_recomputable_and_missing_dimensions_are_not_filled():
    packet = _packet()
    snapshot = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    dims = snapshot["dimensions"]
    assert snapshot["ok"] is True
    assert snapshot["version"] == "sentiment.v1"
    assert dims["volume"]["available"] is False
    assert dims["volume"]["value"] is None
    assert abs(snapshot["coverage"] - 0.85) < 1e-9
    expected = (
        dims["breadth"]["value"] * 0.30
        + dims["momentum"]["value"] * 0.20
        + dims["limit"]["value"] * 0.15
        + dims["global"]["value"] * 0.10
        + dims["event"]["value"] * 0.10
    ) / 0.85
    assert abs(snapshot["score"] - round(expected, 1)) < 1e-9


def test_sentiment_without_critical_dimensions_returns_null_score():
    packet = _packet()
    packet["market"]["indices"] = []
    packet["market"]["breadth"] = {}
    packet["global"]["scorecard"] = {}
    packet["intel"] = {}
    snapshot = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    assert snapshot["score"] is None
    assert snapshot["status"] == "数据不足"
    assert snapshot["coverage"] < 0.70


def test_six_experts_and_consensus_keep_dissent():
    packet = _packet()
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    assert [e["expert_id"] for e in experts] == ["E1", "E2", "E3", "E4", "E5", "E6"]
    assert all(e["evidence"] for e in experts[:5])
    result = aggregate_expert_opinions(packet, sentiment, experts)
    assert result["ok"] is True
    assert -1 <= result["consensus"]["score"] <= 1
    assert result["consensus"]["position_cap"] <= min(e["position_cap"] for e in experts)
    assert result["basis"] and result["invalidation"]


def test_audit_veto_when_coverage_is_low():
    packet = _packet(quality={
        "level": "poor", "coverage_pct": 42.0, "score": 0.25,
        "stale": False, "actionable": False, "critical_missing": ["涨跌家数缺失"],
    })
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    audit = experts[-1]
    assert audit["veto"] is True
    assert audit["position_cap"] == 0
    result = aggregate_expert_opinions(packet, sentiment, experts)
    assert result["consensus"]["actionable"] is False
    assert result["consensus"]["position_cap"] == 0


def test_quarter_candidates_use_last_completed_quarter():
    values = _quarter_candidates(datetime(2026, 11, 2))
    assert values[0] == "2026-09-30"
    assert values[1] == "2026-06-30"


def test_market_breadth_handles_full_a_share_rows():
    rows = [
        {"code": "sh600000", "name": "浦发银行", "price": 10, "chg": 10.0, "amount": 1000},
        {"code": "sz300001", "name": "特锐德", "price": 20, "chg": 20.0, "amount": 2000},
        {"code": "bj920001", "name": "北交样本", "price": 5, "chg": -30.0, "amount": 300},
        {"code": "sh600001", "name": "ST样本", "price": 2, "chg": -5.0, "amount": 100},
    ]
    result = _market_breadth(rows)
    assert result["valid"] == 4
    assert result["up"] == 2
    assert result["down"] == 2
    assert result["limit_up"] == 2
    assert result["limit_down"] == 2
    assert result["amount"] == 3400


def test_limit_split_flag_calibrates_by_balance():
    """涨停/跌停同时活跃时，只有两侧接近均衡才算高强度撕裂。"""
    def flags_for(lu, ld):
        packet = _packet()
        packet["market"]["breadth"] = {"up": 2921, "down": 2017, "flat": 160, "limit_up": lu, "limit_down": ld}
        snapshot = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
        return [f for f in snapshot["risk_flags"] if f["code"] == "limit_extreme_split"]

    # 60 / 27：一侧主导，应为 medium 而非 high，且文案不称"撕裂"
    dominant = flags_for(60, 27)
    assert len(dominant) == 1
    assert dominant[0]["level"] == "medium"
    assert "撕裂" not in dominant[0]["text"]
    assert "60" in dominant[0]["text"] and "27" in dominant[0]["text"]

    # 45 / 45：真正均衡，保持 high
    balanced = flags_for(45, 45)
    assert len(balanced) == 1
    assert balanced[0]["level"] == "high"

    # 60 / 10：跌停不活跃，不触发
    assert flags_for(60, 10) == []


def test_sentiment_declares_data_scope():
    """情绪输出必须自描述数据口径：全市场 / 核心样本可区分。"""
    full = _packet()
    full["market"]["full_market"] = {"total_universe": 6100}
    full["market"]["breadth"] = {"up": 2921, "down": 2017, "flat": 160, "limit_up": 60, "limit_down": 27,
                                 "valid": 5098, "total": 6100}
    snap = build_sentiment_snapshot(full, history={"series": [], "daily": {}})
    assert snap["data_scope"] == "full_a_share"
    assert "5098" in snap["scope_label"]
    assert not [f for f in snap["risk_flags"] if f["code"] == "scope_degraded"]

    core = _packet()  # 无 full_market，breadth 只有 430 只
    core["market"]["breadth"] = {"up": 281, "down": 149, "flat": 10, "limit_up": 4, "limit_down": 0}
    snap2 = build_sentiment_snapshot(core, history={"series": [], "daily": {}})
    assert snap2["data_scope"] == "core_sample"
    assert [f for f in snap2["risk_flags"] if f["code"] == "scope_degraded"]


def test_momentum_falls_back_to_breadth_median():
    """指数涨跌幅缺失时，动量维用全市场个股涨跌中位数兜底，不让整体情绪分消失。"""
    packet = _packet()
    packet["market"]["breadth"] = {"up": 4782, "down": 714, "flat": 85,
                                   "limit_up": 105, "limit_down": 15, "median_chg": 1.0}
    packet["market"]["indices"] = [
        {"code": "sh000001", "name": "上证指数", "price": 3640.0, "chg": None},
        {"code": "sz399001", "name": "深证成指", "price": 11092.0, "chg": None},
    ]
    snap = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    mom = snap["dimensions"]["momentum"]
    assert mom["available"] is True
    assert mom["value"] == 75.0  # 50 + 25×1.0，未被 _clamp 截断
    assert "中位数" in mom["note"]
    assert snap["inputs"]["momentum_source"] == "breadth_median"
    # 覆盖率回到 0.85，情绪分可用
    assert snap["coverage"] == 0.85
    assert snap["score"] is not None


def test_momentum_stays_null_without_any_fallback():
    """指数与中位数都缺失时，动量维必须为 null，不得填 50。"""
    packet = _packet()
    packet["market"]["breadth"] = {"up": 4782, "down": 714, "flat": 85, "limit_up": 105, "limit_down": 15}
    packet["market"]["indices"] = [{"code": "sh000001", "name": "上证指数", "price": None, "chg": None}]
    snap = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    assert snap["dimensions"]["momentum"]["available"] is False
    assert snap["dimensions"]["momentum"]["value"] is None
    assert snap["inputs"]["momentum_source"] is None


def _vol_series(n=21, base=100.0, last=None):
    """构造 n 个交易日的成交量序列，最后一行为"今天"（量能维按当日分位计算）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [{"date": f"2026-09-{i + 1:02d}", "vol": base + i} for i in range(n - 1)]
    rows.append({"date": today, "vol": last if last is not None else base + n})
    return rows


def test_session_volume_progress_curve():
    """U 型时段折算：开盘 0、上午收盘约 0.52、收盘 1.0。"""
    from server.decision_snapshot import _session_volume_progress

    assert _session_volume_progress(datetime(2026, 11, 2, 9, 0)) == 0.0
    assert _session_volume_progress(datetime(2026, 11, 2, 11, 30)) == 0.52
    assert _session_volume_progress(datetime(2026, 11, 2, 12, 30)) == 0.52  # 午休不变
    assert _session_volume_progress(datetime(2026, 11, 2, 15, 30)) == 1.0
    assert _session_volume_progress(datetime(2026, 11, 2, 8, 0)) == 0.0
    # 周末视作已完成
    assert _session_volume_progress(datetime(2026, 11, 1, 10, 0)) == 1.0


def test_volume_dimension_uses_volume_backfill(monkeypatch):
    """成交额样本不足时用成交量回填，并标记为预估。

    固定"交易时段进度=1.0"：该用例验证的是回填口径，不应随运行时刻变化——
    凌晨运行时 session_progress=0，产品会正确判定量能不可用，测试却会误报。
    """
    monkeypatch.setattr("server.decision_snapshot._session_volume_progress", lambda *a, **k: 1.0)
    packet = _packet()
    packet["market"]["turnover"] = {
        "amount": 1.1e12,
        "volume_backfill": {"ok": True, "source": "tencent_kline:test", "series": _vol_series(21, last=500.0)},
    }
    snap = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    vol = snap["dimensions"]["volume"]
    assert vol["available"] is True
    assert vol["inputs"]["basis"] == "volume"
    assert vol["inputs"]["unit"] == "lot"
    assert "分位" in vol["note"]
    # 覆盖率补满到 1.0（六维齐全）
    assert snap["coverage"] == 1.0


def test_volume_dimension_stays_null_without_any_history():
    """成交额与成交量都不足 20 日时，量能维必须为 null。"""
    packet = _packet()
    packet["market"]["turnover"] = {"amount": 1.1e12, "volume_backfill": {"ok": True, "series": _vol_series(10)}}
    snap = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    vol = snap["dimensions"]["volume"]
    assert vol["available"] is False
    assert vol["value"] is None
    assert "暂不计算" in vol["note"]


def test_deliberation_anonymizes_and_reviews():
    """第二轮互评：匿名别名、每人一条审阅结论、矩阵与流程可追溯。"""
    from server.decision_experts import deliberate_expert_opinions

    packet = _packet()
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    d = deliberate_expert_opinions(experts, sentiment, packet["quality"])

    assert d["rounds"] == 2 and d["anonymized"] is True
    # 5 位业务专家参与互评（E6 审计不参与打分）
    assert len(d["reviews"]) == 5
    aliases = [r["alias"] for r in d["reviews"]]
    assert len(set(aliases)) == 5 and all(a in "甲乙丙丁戊己" for a in aliases)
    # 审阅结论必须落在三种状态之一
    assert {r["verdict"] for r in d["reviews"]} <= {"endorse", "revise", "uphold"}
    assert 0.0 <= d["cross_validation"]["agreement_rate"] <= 1.0
    assert len(d["cross_validation"]["matrix"]) == 5
    assert "匿名互评" in d["process"]
    # 修正的专家必须记录第一轮状态，便于回溯"谁被什么说服"
    for item in d["revised_experts"]:
        if item.get("review_verdict") == "revise":
            assert item["first_round_stance"] != item["stance"] or item["confidence"] < item["first_round_confidence"]


def test_deliberation_is_deterministic():
    """同一份第一轮意见必须得到同一份审阅结论（可复算）。"""
    from server.decision_experts import deliberate_expert_opinions

    packet = _packet()
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    a = deliberate_expert_opinions(experts, sentiment, packet["quality"])
    b = deliberate_expert_opinions(experts, sentiment, packet["quality"])
    assert [r["verdict"] for r in a["reviews"]] == [r["verdict"] for r in b["reviews"]]
    assert [r["confidence_delta"] for r in a["reviews"]] == [r["confidence_delta"] for r in b["reviews"]]


def test_aggregate_consumes_deliberation():
    """主脑汇总必须消费互评后的意见，并把流程写入 summary。"""
    from server.decision_experts import deliberate_expert_opinions

    packet = _packet()
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    d = deliberate_expert_opinions(experts, sentiment, packet["quality"])
    result = aggregate_expert_opinions(packet, sentiment, d["revised_experts"], deliberation=d)

    assert result["method"] == "rule_v2_deliberated"
    assert result["deliberation"]["rounds"] == 2
    assert "匿名互评" in result["summary"]
    # 每个专家都带上了审阅结论
    verdicts = [e.get("review_verdict") for e in result["experts"] if e["expert_id"] != "E6"]
    assert len([v for v in verdicts if v]) == 5


def test_deliberation_same_direction_is_endorse_not_conflict():
    """同为进攻、只是强度不同的专家应判为"认同"，不得算冲突（曾因用连续差值判定而全判冲突）。"""
    from server.decision_experts import deliberate_expert_opinions

    def op(eid, name, stance, conf, cap=40):
        return {"expert_id": eid, "name": name, "stance": stance, "horizon": "intraday",
                "confidence": conf, "thesis": "t", "evidence": [], "counter_evidence": [],
                "invalidation": "", "position_cap": cap, "veto": False, "veto_reason": "",
                "abstain": False, "questions": [], "method": "rule_v1"}

    experts = [
        op("E1", "趋势与结构", "attack", 0.90),
        op("E2", "板块轮动", "attack", 0.70),
        op("E3", "资金流动性", "attack", 0.60),
        op("E4", "全球事件", "defend", 0.30),
        op("E5", "情绪行为", "attack", 0.80),
        op("E6", "反方审计", "neutral", 0.80, cap=60),
    ]
    d = deliberate_expert_opinions(experts, {}, {})
    verdicts = {r["expert_id"]: r["verdict"] for r in d["reviews"]}
    # 四票进攻强度各异，全部认同；唯一的防守票被多数说服而修正
    assert verdicts == {"E1": "endorse", "E2": "endorse", "E3": "endorse",
                        "E4": "revise", "E5": "endorse"}
    assert d["cross_validation"]["agreement_rate"] == 0.8
    assert d["disputes"] == []


# ---------------------------------------------------------------------------
# market-scanner.v1：横截面 / 轮动 / 资金注意力代理
# ---------------------------------------------------------------------------

def _scan_rows(n=3000, seed=11):
    import random
    random.seed(seed)
    inds = ["银行", "证券", "半导体", "白酒", "医药", "地产", "军工", "汽车", "传媒", "电力", "光伏", "养殖"]
    rows = []
    for i in range(n):
        rows.append({
            "code": f"sz{i:05d}", "name": f"股{i}",
            "chg": round(random.gauss(0.3, 2.0), 2),
            "amount": round(random.uniform(0.5, 40), 2),
            "turnover": round(random.uniform(0.1, 15), 2),
            "market_cap_yi": round(random.expovariate(1 / 150), 1),
            "pe": round(random.uniform(5, 110), 1),
            "industry": random.choice(inds),
        })
    return rows


def test_cross_section_structure():
    from server.market_scanner import build_cross_section

    cross = build_cross_section(_scan_rows())
    assert cross["available"] is True and cross["sample"] == 3000
    total = sum(b["count"] for b in cross["distribution"].values())
    assert total == 3000  # 分布桶全覆盖
    assert 0 <= cross["strength_score"] <= 100
    assert cross["turnover"]["sample"] > 0
    assert all(v["avg_chg"] is not None for v in cross["cap_tiers"].values())


def test_rotation_ranks_and_signal(tmp_path):
    import server.market_scanner as ms
    from server.market_scanner import build_rotation, record_sector_day

    saved = ms.SECTOR_HISTORY_PATH
    ms.SECTOR_HISTORY_PATH = str(tmp_path / "sector_history.json")  # 隔离，避免污染真实历史
    hist = {"version": 1, "days": {}}
    day1 = _scan_rows(seed=3)
    for r in day1:
        if r["industry"] in ("白酒", "半导体"):
            r["chg"] = 2.5
    record_sector_day(day1, hist, "2026-10-30")

    day2 = _scan_rows(seed=3)
    for r in day2:
        if r["industry"] in ("传媒", "证券"):
            r["chg"] = 3.0
        if r["industry"] == "白酒":
            r["chg"] = -2.0
    try:
        rot = build_rotation(day2, hist, "2026-10-31")
    finally:
        ms.SECTOR_HISTORY_PATH = saved
    assert rot["available"] and rot["history_days"] == 2
    assert rot["signal"] in ("continuity", "rotation", "churn")
    assert len(rot["top3_today"]) == 3
    # 排名变化存在且语义正确（上升为正）
    top = rot["industries"][0]
    assert top["rank"] == 1
    # 动量窗口不足
    assert all(x["mom5"] is None for x in rot["industries"])


def test_money_flow_proxy_marks_itself(tmp_path):
    import server.market_scanner as ms
    from server.market_scanner import build_money_flow_proxy, record_sector_day

    saved = ms.SECTOR_HISTORY_PATH
    ms.SECTOR_HISTORY_PATH = str(tmp_path / "sector_history.json")
    hist = {"version": 1, "days": {}}
    rows = _scan_rows(1500)
    try:
        record_sector_day(rows, hist, "2026-10-30")
        mf = build_money_flow_proxy(rows, hist, "2026-10-31")
    finally:
        ms.SECTOR_HISTORY_PATH = saved
    assert mf["proxy"] is True and mf["mode"] == "proxy"
    assert "代理" in mf["note"]
    assert isinstance(mf["attention_shifts"], list)


def test_em_backoff_blocks_after_failures():
    """东财通道连续失败后退避：封锁期内不再发请求。"""
    import server.market_scanner as ms

    saved = dict(ms._EM_STATE)
    try:
        ms._EM_STATE["fails"] = 2
        ms._EM_STATE["blocked_until"] = __import__("time").time() + 3600
        import asyncio
        result = asyncio.run(ms._em_fetch("https://{host}/x"))
        assert result is None  # 被封锁，直接返回 None 不打上游
    finally:
        ms._EM_STATE.clear()
        ms._EM_STATE.update(saved)


# ---------------------------------------------------------------------------
# 专家协作 v3：联合研判 + 命中账本
# ---------------------------------------------------------------------------

def test_collaboration_joint_thesis_and_domains():
    from server.decision_experts import build_collaboration

    def op(eid, name, stance, conf, src):
        return {"expert_id": eid, "name": name, "stance": stance, "confidence": conf,
                "evidence": [{"metric": "m", "value": 1, "unit": "%", "source": src}],
                "abstain": False}

    experts = [
        op("E1", "趋势", "attack", 0.9, "market-scanner.cross_section(全市场)"),
        op("E2", "轮动", "attack", 0.85, "market-scanner.rotation"),
        op("E3", "资金", "attack", 0.7, "market-scanner.money_flow(eastmoney)"),
        op("E4", "全球", "defend", 0.4, "global-linkage"),
        op("E5", "情绪", "attack", 0.8, "sentiment.v1"),
        op("E6", "审计", "neutral", 0.8, "audit"),
    ]
    collab = build_collaboration(experts, "attack")
    # 联合论点取置信度前 3
    assert len(collab["joint_thesis"]) == 3
    assert collab["joint_thesis"][0]["expert_id"] == "E1"
    # 跨域确认：4 个进攻专家来自 4 个不同数据域
    assert collab["independent_domains"] >= 3
    assert all(c["experts"] for c in collab["cross_confirmations"])


def test_expert_ledger_hit_rate(tmp_path):
    """账本：T+1 回看计分，中性不计分，命中率可复算。"""
    import json
    import server.decision_experts as de
    # 拆分后门面只做再导出：可变路径必须打到实现模块，否则会写进真实账本
    import server.decision_experts_ledger as ledger_impl

    ledger_path = tmp_path / "expert_ledger.json"
    senti_path = tmp_path / "sentiment_history.json"
    saved_path = ledger_impl.EXPERT_LEDGER_PATH
    ledger_impl.EXPERT_LEDGER_PATH = str(ledger_path)
    try:
        # D-1：E1 进攻、E4 防守、E2 中性；当日市场上涨（breadth 70）
        senti_path.write_text(json.dumps({"series": [
            {"ts": "2026-10-31T15:00:00+08:00", "dimensions": {"breadth": 70.0}},
            {"ts": "2026-10-31T15:05:00+08:00", "dimensions": {"breadth": 68.0}},
        ]}, ensure_ascii=False), encoding="utf-8")
        de.record_expert_day([
            {"expert_id": "E1", "stance": "attack", "confidence": 0.9},
            {"expert_id": "E2", "stance": "neutral", "confidence": 0.5},
            {"expert_id": "E4", "stance": "defend", "confidence": 0.4},
        ], "2026-10-31")
        # D0：今日不参与回看
        de.record_expert_day([
            {"expert_id": "E1", "stance": "attack", "confidence": 0.9},
        ], "2026-11-01")

        calib = de.resolve_expert_ledger("2026-11-01")
        assert calib["sample_days"] == 2 and calib["resolved_days"] == 1
        # E1 进攻 + 市场上涨 = 命中；E4 防守 + 上涨 = 未命中；E2 中性不计分
        assert calib["per_expert"]["E1"] == {"hits": 1, "total": 1, "hit_rate": 1.0}
        assert calib["per_expert"]["E4"] == {"hits": 0, "total": 1, "hit_rate": 0.0}
        assert "E2" not in calib["per_expert"]
    finally:
        ledger_impl.EXPERT_LEDGER_PATH = saved_path


def test_experts_consume_scan_data():
    """E2/E3 必须消费扫描层数据（全市场行业与资金流），并标注来源。"""
    packet = _packet()
    packet["scan"] = {
        "ok": True,
        "cross_section": {"available": True, "sample": 5581, "strength_score": 72.5,
                          "median_chg": 0.39, "distribution": {}},
        "rotation": {
            "available": True, "signal": "continuity", "signal_label": "主线延续",
            "history_days": 5,
            "industries": [
                {"name": "传媒", "avg_chg": 2.8, "amount_share": 6.2, "rank": 1, "rank_change": 3, "count": 120},
                {"name": "证券", "avg_chg": 2.1, "amount_share": 5.1, "rank": 2, "rank_change": 1, "count": 60},
                {"name": "半导体", "avg_chg": 1.8, "amount_share": 8.9, "rank": 3, "rank_change": -1, "count": 200},
            ] + [
                {"name": f"行业{i}", "avg_chg": 0.2, "amount_share": 1.0, "rank": i + 4, "rank_change": 0, "count": 50}
                for i in range(20)
            ],
        },
        "money_flow": {
            "mode": "eastmoney", "proxy": False,
            "net_total_yi": 128.5, "industries": [{"name": "传媒", "main_net_yi": 29.6}] * 30,
            "top_inflow": [{"name": "传媒", "main_net_yi": 29.6}],
        },
    }
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    by_id = {e["expert_id"]: e for e in experts}
    # E2 消费全市场轮动
    assert "全市场" in by_id["E2"]["thesis"]
    assert any("market-scanner.rotation" in str(ev.get("source")) for ev in by_id["E2"]["evidence"])
    # E3 消费资金流实测
    assert "128.5" in by_id["E3"]["thesis"]
    assert any("money_flow" in str(ev.get("source")) for ev in by_id["E3"]["evidence"])
    # E1 消费横截面
    assert any("cross_section" in str(ev.get("source")) for ev in by_id["E1"]["evidence"])


def test_aggregate_includes_collaboration():
    packet = _packet()
    sentiment = build_sentiment_snapshot(packet, history={"series": [], "daily": {}})
    experts = build_expert_opinions(packet, sentiment)
    from server.decision_experts import deliberate_expert_opinions
    d = deliberate_expert_opinions(experts, sentiment, packet["quality"])
    result = aggregate_expert_opinions(packet, sentiment, d["revised_experts"], deliberation=d)
    collab = result["collaboration"]
    assert 1 <= len(collab["joint_thesis"]) <= 3
    assert "independent_domains" in collab


def test_disagreement_veto_lifted_after_deliberation():
    """互评后终局分歧回落到阈值内时，第一轮的分歧否决必须撤销。"""
    from server.decision_experts import aggregate_expert_opinions

    def op(eid, name, stance, conf, veto=False, veto_reason=""):
        return {"expert_id": eid, "name": name, "stance": stance, "horizon": "intraday",
                "confidence": conf, "thesis": "t", "evidence": [{"metric": "m", "value": 1, "unit": "", "source": "s"}],
                "counter_evidence": [], "invalidation": "", "position_cap": 40,
                "veto": veto, "veto_reason": veto_reason, "abstain": False, "questions": [],
                "method": "rule_v1"}

    # 4 进攻 + 1 中性 + E6 分歧否决（第一轮分歧 0.6），互评后修订为 4 进攻 1 进攻 → 终局分歧 < 0.55
    experts = [
        op("E1", "趋势", "attack", 0.9),
        op("E2", "轮动", "attack", 0.8),
        op("E3", "资金", "attack", 0.7),
        op("E4", "全球", "attack", 0.6),
        op("E5", "情绪", "attack", 0.8),
        op("E6", "审计", "neutral", 0.8, veto=True, veto_reason="专家分歧指数 0.60，高于可执行阈值 0.55"),
    ]
    packet = _packet()
    packet["quality"]["actionable"] = True
    result = aggregate_expert_opinions(packet, {"coverage": 0.9, "score": 60}, experts)
    # 终局分歧 < 0.55 → 否决撤销，结论不再是防守
    assert result["consensus"]["veto"] is False
    assert result["consensus"]["stance"] == "attack"
    assert any("撤销" in x["reason"] for x in result["consensus"]["lifted_vetoes"])
    # 协作交叉确认以进攻为基准（多数方向）
    assert result["collaboration"]["confirmation_basis"] == "attack"
    assert result["collaboration"]["independent_domains"] >= 1


def test_quality_veto_persists():
    """数据质量类否决不受终局分歧复核影响。"""
    from server.decision_experts import aggregate_expert_opinions

    def op(eid, name, stance, conf, veto=False, veto_reason=""):
        return {"expert_id": eid, "name": name, "stance": stance, "horizon": "intraday",
                "confidence": conf, "thesis": "t", "evidence": [{"metric": "m", "value": 1, "unit": "", "source": "s"}],
                "counter_evidence": [], "invalidation": "", "position_cap": 40,
                "veto": veto, "veto_reason": veto_reason, "abstain": False, "questions": [],
                "method": "rule_v1"}

    experts = [
        op("E1", "趋势", "attack", 0.9), op("E2", "轮动", "attack", 0.8),
        op("E3", "资金", "attack", 0.7), op("E4", "全球", "attack", 0.6),
        op("E5", "情绪", "attack", 0.8),
        op("E6", "审计", "neutral", 0.8, veto=True, veto_reason="情绪维度覆盖不足 70%"),
    ]
    packet = _packet()
    packet["quality"]["actionable"] = True
    result = aggregate_expert_opinions(packet, {"coverage": 0.9, "score": 60}, experts)
    assert result["consensus"]["veto"] is True
    assert result["consensus"]["stance"] == "defend"
    assert result["consensus"]["lifted_vetoes"] == []
