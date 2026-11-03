"""情绪追踪纯函数契约（sentiment-flow.v1）。

只测 ``build_flow``：它是纯函数，输入快照 + 历史，输出追踪结论。
外部 IO（缓存/落盘/总线）不在这里测——那部分由集成回归覆盖。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.sentiment_tracker import build_flow

TZ = timezone(timedelta(hours=8))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=TZ).isoformat(timespec="seconds")


def _snapshot(score, dims=None, trace="mk_test", confidence=0.8, coverage=0.9):
    dimensions = {}
    for key, (value, weight) in (dims or {}).items():
        dimensions[key] = {
            "key": key, "label": key.upper(), "value": value, "weight": weight,
            "available": value is not None, "method": "test", "note": "",
        }
    return {
        "ok": score is not None,
        "version": "sentiment.v1",
        "trace_id": trace,
        "as_of": _iso(1762041600),  # 2026-11-02 09:20 +08
        "score": score,
        "status": "偏暖" if score and score >= 55 else "中性",
        "confidence": confidence,
        "coverage": coverage,
        "dimensions": dimensions,
    }


BASE_TS = 1762041600.0  # 2026-11-02 09:20:00 +08:00


def _series(values, step=300):
    return {
        "series": [
            {"ts": _iso(BASE_TS - (len(values) - 1 - i) * step), "score": v}
            for i, v in enumerate(values)
        ]
    }


# ----------------------------------------------------------------------
#  基础可用性
# ----------------------------------------------------------------------
def test_flow_without_score_is_unavailable_not_zero():
    flow = build_flow({"ok": False, "score": None, "message": "覆盖不足"}, _series([]), now=BASE_TS)
    assert flow["ok"] is False
    assert flow["level"] is None
    assert flow["regime"]["key"] == "unknown"
    assert flow["band"]["key"] == "unknown"
    assert "覆盖不足" in (flow["message"] or "")


def test_flow_level_and_band():
    flow = build_flow(_snapshot(63.0), _series([58, 60, 61]), now=BASE_TS)
    assert flow["ok"] is True
    assert flow["level"] == 63.0
    assert flow["band"]["key"] == "warm"
    assert flow["regime"]["tone"] in ("good", "warn", "neutral")


def test_band_boundaries_match_snapshot_vocabulary():
    """分档阈值与措辞必须与 decision_snapshot._status_of 一致（20/40/60/80）。"""
    from server.decision_snapshot import _status_of

    cases = [(19.9, "freezing", "极冷"), (20.0, "cool", "偏冷"),
             (40.0, "neutral", "中性"), (60.0, "warm", "偏热"),
             (80.0, "hot", "过热")]
    for score, key, label in cases:
        band = build_flow(_snapshot(score), _series([]), now=BASE_TS)["band"]
        assert band["key"] == key
        assert band["label"] == label
        assert label == _status_of(score), f"{score} 两处措辞不一致"


# ----------------------------------------------------------------------
#  变化与方向
# ----------------------------------------------------------------------
def test_delta_uses_last_history_point():
    """当前分未落进历史时，应与历史最后一点比较。"""
    flow = build_flow(_snapshot(68.0), _series([60.0, 64.0]), now=BASE_TS)
    assert flow["delta_short"] == pytest.approx(4.0)


def test_delta_is_zero_when_history_already_has_current():
    flow = build_flow(_snapshot(64.0), _series([60.0, 64.0]), now=BASE_TS)
    # 最后一点就是 64，与当前相同：增量应取「上一个点 → 当前点」的 4.0
    assert flow["delta_short"] == pytest.approx(4.0)


def test_regime_heating_when_warm_and_rising():
    flow = build_flow(_snapshot(66.0), _series([60, 61, 62, 63, 64, 65]), now=BASE_TS)
    assert flow["regime"]["key"] == "heating"
    assert flow["regime"]["tone"] == "good"


def test_regime_cooling_from_hot():
    # 82 ≥ 80 = 过热档，且近端在降 → 高位回落
    flow = build_flow(_snapshot(82.0), _series([88, 86, 85, 84, 83]), now=BASE_TS)
    assert flow["regime"]["key"] == "cooling_from_hot"
    assert flow["regime"]["tone"] == "warn"


def test_turning_point_detected_on_sign_flip():
    # 80 → 79 → 78（下降），最后一跳回到 82（上升），且幅度 ≥1.5
    flow = build_flow(_snapshot(82.0), _series([84, 80, 79, 78]), now=BASE_TS)
    turning = flow["turning_point"]
    assert turning["detected"] is True
    assert turning["kind"] == "up"
    assert turning["magnitude"] >= 1.5
    assert any(f["code"] == "turning_point" for f in flow["flags"])


def test_no_turning_point_when_move_is_small():
    flow = build_flow(_snapshot(60.6), _series([62, 61, 60.8]), now=BASE_TS)
    assert flow["turning_point"]["detected"] is False


def test_persistence_counts_consecutive_moves():
    flow = build_flow(_snapshot(66.0), _series([60, 62, 64, 65]), now=BASE_TS)
    assert flow["persistence"]["direction"] == "up"
    assert flow["persistence"]["ticks"] >= 3


# ----------------------------------------------------------------------
#  位置感（z / 分位）与样本不足
# ----------------------------------------------------------------------
def test_z_and_percentile_need_min_samples():
    few = build_flow(_snapshot(60.0), _series([58, 59]), now=BASE_TS)
    assert few["percentile20"] is None
    assert any(f["code"] == "insufficient_sample" for f in few["flags"])

    many = build_flow(_snapshot(70.0), _series(list(range(50, 70))), now=BASE_TS)
    assert many["z20"] is not None
    assert many["percentile20"] is not None
    assert many["percentile20"] >= 90


def test_small_sample_lowers_confidence():
    small = build_flow(_snapshot(60.0), _series([59]), now=BASE_TS)
    big = build_flow(_snapshot(60.0), _series(list(range(50, 70))), now=BASE_TS)
    assert small["confidence"] < big["confidence"]


# ----------------------------------------------------------------------
#  驱动归因
# ----------------------------------------------------------------------
def test_drivers_and_drags_are_weighted_by_available_dims():
    dims = {
        "breadth": (80.0, 0.30),
        "momentum": (50.0, 0.20),
        "limit": (20.0, 0.15),
        "volume": (None, 0.15),   # 缺失：应被剔除且权重重归一
    }
    flow = build_flow(_snapshot(60.0, dims), _series([58, 59]), now=BASE_TS)
    assert flow["available_weight"] == pytest.approx(0.65)
    assert flow["drivers"][0]["key"] == "breadth"
    assert flow["drivers"][0]["contribution"] > 0
    assert flow["drags"][0]["key"] == "limit"
    assert flow["drags"][0]["contribution"] < 0
    shares = sum(abs(d["contribution"]) for d in flow["drivers"] + flow["drags"])
    assert shares > 0
    assert all(0 <= d["share"] <= 1 for d in flow["drivers"] + flow["drags"])


def test_drivers_empty_when_no_dimensions():
    flow = build_flow(_snapshot(None), _series([]), now=BASE_TS)
    assert flow["drivers"] == []
    assert flow["drags"] == []
    assert flow["available_weight"] == 0.0


# ----------------------------------------------------------------------
#  证据链与口径
# ----------------------------------------------------------------------
def test_evidence_and_formula_exposed():
    dims = {"breadth": (70.0, 0.30)}
    flow = build_flow(_snapshot(64.0, dims), _series([60, 62]), now=BASE_TS)
    assert flow["version"] == "sentiment-flow.v1"
    assert flow["formula"]
    assert flow["evidence"]
    assert any("主要推力" in line for line in flow["evidence"])
