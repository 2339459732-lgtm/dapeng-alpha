"""决策分流：规则可配置、优先级稳定、缺失可降级、只收紧不放大。

分流是「环境闸门」，不是又一个方向判断器。这些用例守四件事：
1. 配置错了要能查出来（未知指标 / 优先级重复 / 仓位越界 / 阈值倒挂）；
2. 规则按优先级先保护后进攻，命中即停；
3. 指标不够时走观察兜底，不允许在缺数据的情况下放开仓位；
4. 备忘录只被收紧：分流说防御，进攻结论必须降级。
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import decision_routing as routing  # noqa: E402
from server.brain_capabilities import compose_memo  # noqa: E402

CONFIG = routing.load(force=True)


def _metric(metric_id, score, confidence=0.8, available=True, stale=False, value=None):
    return {"id": metric_id, "available": available, "score": score if available else None,
            "confidence": confidence, "stale": stale, "value": value,
            "reason": None if available else "缺失"}


def _payload(scores, confidences=None, stale=(), missing=()):
    confidences = confidences or {}
    metrics = []
    for metric_id in routing.load(force=True)["weights"]:
        if metric_id in missing:
            metrics.append(_metric(metric_id, None, available=False))
        else:
            metrics.append(_metric(metric_id, scores.get(metric_id, 0.0),
                                   confidences.get(metric_id, 0.8),
                                   stale=metric_id in stale))
    return {"ok": True, "as_of": "2026-11-03T10:00:00", "trace_id": "mk_t", "metrics": metrics}


# ---------------------------------------------------------------------------
# 配置校验
# ---------------------------------------------------------------------------
def test_default_config_is_valid():
    report = routing.validate(CONFIG)
    assert report["ok"] is True, report["issues"]
    assert report["routes"] >= 5


def test_validate_catches_unknown_metric_and_bad_cap():
    bad = copy.deepcopy(CONFIG)
    bad["routes"][0]["conditions"][0]["metric"] = "trend.nonexistent"
    bad["routes"][0]["action"]["cap_max"] = 120
    report = routing.validate(bad)
    assert report["ok"] is False
    reasons = " ".join(item["reason"] for item in report["issues"])
    assert "未知指标" in reasons and "0~80" in reasons


def test_validate_catches_duplicate_priority_and_threshold_order():
    bad = copy.deepcopy(CONFIG)
    bad["routes"][1]["priority"] = bad["routes"][0]["priority"]
    bad["thresholds"] = {"risk_on": -0.4, "risk_off": 0.4}
    report = routing.validate(bad)
    assert any("重复" in item["reason"] for item in report["issues"])
    assert any("risk_on" in item["reason"] for item in report["issues"])


# ---------------------------------------------------------------------------
# 路由命中
# ---------------------------------------------------------------------------
def test_risk_off_wins_over_opportunity():
    result = routing.evaluate(_payload({
        "trend.breadth": -0.9, "flow.main": -0.8, "trend.index": 0.5,
    }), CONFIG)
    assert result["route_id"] == "risk_off"
    assert result["action"]["cap_max"] == 10
    assert result["action"]["stance"] == "defend"
    # 优先级 100 > 70，即便趋势也满足，也必须先保护
    assert result["priority"] == 100


def test_risk_on_requires_all_three():
    strong = {"trend.breadth": 0.8, "buyratio.market": 0.6, "flow.main": 0.5, "trend.index": 0.6}
    assert routing.evaluate(_payload(strong), CONFIG)["route_id"] == "risk_on"

    weak_flow = dict(strong, **{"flow.main": -0.2})
    result = routing.evaluate(_payload(weak_flow), CONFIG)
    assert result["route_id"] != "risk_on"


def test_buy_squeeze_triggers_on_buy_ratio():
    result = routing.evaluate(_payload({
        "buyratio.market": -0.9, "flow.main": -0.3, "trend.breadth": 0.1,
    }), CONFIG)
    assert result["route_id"] == "buy_squeeze"
    assert result["action"]["cap_max"] == 20


def test_composite_is_weighted_and_renormalised():
    """缺一项时按剩余权重归一，而不是把总分拉向 0。"""
    full = _payload({k: 0.5 for k in CONFIG["weights"]})
    partial = _payload({k: 0.5 for k in CONFIG["weights"]}, missing=("flow.north",))
    assert routing.evaluate(full, CONFIG)["composite"] == pytest.approx(0.5, abs=1e-6)
    assert routing.evaluate(partial, CONFIG)["composite"] == pytest.approx(0.5, abs=1e-6)


def test_stale_metric_confidence_is_penalised():
    fresh = routing.evaluate(_payload({"trend.index": 0.8}, missing=("flow.north",)), CONFIG)
    stale = routing.evaluate(
        _payload({"trend.index": 0.8}, missing=("flow.north",), stale=("trend.index",)), CONFIG)
    fresh_c = next(c for c in fresh["contributions"] if c["metric"] == "trend.index")
    stale_c = next(c for c in stale["contributions"] if c["metric"] == "trend.index")
    assert stale_c["contribution"] < fresh_c["contribution"]


# ---------------------------------------------------------------------------
# 降级
# ---------------------------------------------------------------------------
def test_insufficient_metrics_falls_back_to_observe():
    result = routing.evaluate(_payload({}, missing=("flow.main", "flow.north",
                                                   "buyratio.market", "trend.limit")), CONFIG)
    assert result["degraded"] is True
    assert result["route_id"] == "observe"
    assert result["action"]["cap_max"] == 0
    assert any("可用指标" in reason for reason in result["degraded_reasons"])


def test_missing_required_condition_blocks_route_but_keeps_scanning():
    """risk_on 缺 buyratio 时不能命中，应继续往下判到中性区间。"""
    result = routing.evaluate(_payload(
        {"trend.breadth": 0.5, "flow.main": 0.4},
        missing=("buyratio.market",)), CONFIG)
    assert result["route_id"] != "risk_on"
    assert result["route_id"] in {"neutral_range", "observe"}


def test_evaluate_is_deterministic():
    payload = _payload({"trend.breadth": 0.3, "flow.main": 0.2})
    a = routing.evaluate(payload, CONFIG)
    b = routing.evaluate(payload, CONFIG)
    assert a["route_id"] == b["route_id"] and a["composite"] == b["composite"]


# ---------------------------------------------------------------------------
# 与备忘录的耦合：只收紧，不放大
# ---------------------------------------------------------------------------
def _memo_inputs(actionable=True):
    survey = {"ok": True, "regime": {"status": "强势上行", "key": "warm"},
              "divergence": {"detected": False}}
    flow = {"ok": True, "level": 66, "regime": {"key": "warm", "label": "偏热"}, "confidence": 0.8}
    experts = {"ok": True, "consensus": {
        "stance": "attack", "score": 0.5, "disagreement": 0.2,
        "confidence": 0.7, "position_cap": 60, "actionable": actionable,
    }}
    return survey, flow, experts


def test_memo_cap_is_capped_by_routing():
    survey, flow, experts = _memo_inputs()
    plain = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00")
    routed = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00", routing={
        "route_id": "risk_off", "label": "系统性风险收缩",
        "action": {"stance": "defend", "cap_max": 10, "note": "防御"}, "bias": "risk_off",
        "composite": -0.6, "coverage": 0.9, "degraded": False, "degraded_reasons": [],
    })
    assert plain["position_cap"] >= routed["position_cap"]
    assert routed["position_cap"] <= 10
    assert routed["routing"]["route_id"] == "risk_off"


def test_routing_never_raises_the_cap():
    survey, flow, experts = _memo_inputs()
    routed = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00", routing={
        "route_id": "risk_on", "label": "风险偏好上行",
        "action": {"stance": "attack", "cap_max": 60}, "bias": "risk_on",
        "composite": 0.7, "coverage": 0.9, "degraded": False, "degraded_reasons": [],
    })
    plain = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00")
    assert routed["position_cap"] <= plain["position_cap"]


def test_defend_route_downgrades_attack_stance():
    survey, flow, experts = _memo_inputs()
    routed = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00", routing={
        "route_id": "risk_off", "label": "系统性风险收缩",
        "action": {"stance": "defend", "cap_max": 10}, "bias": "risk_off",
        "composite": -0.6, "coverage": 0.9, "degraded": False, "degraded_reasons": [],
    })
    assert routed["stance"] != "attack"


def test_degraded_routing_shows_up_in_risks():
    survey, flow, experts = _memo_inputs()
    routed = compose_memo(survey, flow, experts, now="2026-11-03 10:00:00", routing={
        "route_id": "observe", "label": "数据不足·观察",
        "action": {"stance": "neutral", "cap_max": 0}, "bias": "unknown",
        "composite": None, "coverage": 0.3, "degraded": True,
        "degraded_reasons": ["可用指标 2 < 下限 4"],
    })
    assert any(item["code"] == "routing_degraded" for item in routed["risks"])
    assert routed["position_cap"] == 0
