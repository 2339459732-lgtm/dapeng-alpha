"""主脑能力契约（brain-capability.v1）与决策备忘录合成。

守两件事：
1. 能力表本身自洽（id 唯一、输入是已注册的总线资源、图的边都指向真实节点）；
2. ``compose_memo`` 的合成规则可复现（立场/仓位/置信/失效条件都可解释）。
"""
from __future__ import annotations

import pytest

from server.brain_capabilities import (
    CAPABILITIES,
    capability_graph,
    capability_map,
    compose_memo,
    report,
)

#: data_bus.register_default_resources() 注册的资源键
BUS_RESOURCES = {
    "coverage", "packet", "sentiment", "sentiment_flow", "scan", "survey", "experts",
}


# ----------------------------------------------------------------------
#  能力表自洽
# ----------------------------------------------------------------------
def test_capability_ids_unique():
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids))


def test_capability_kinds_are_known():
    assert {c.kind for c in CAPABILITIES} <= {"compute", "decide"}


def test_capability_inputs_are_registered_resources():
    unknown = {res for c in CAPABILITIES for res in c.inputs} - BUS_RESOURCES
    assert not unknown, f"能力声明了未注册的资源：{unknown}"


def test_every_capability_has_metric_target_and_fallback():
    for cap in CAPABILITIES:
        assert cap.metrics, f"{cap.id} 缺少提升目标"
        assert cap.fallback, f"{cap.id} 缺少退化策略"
        for metric in cap.metrics:
            assert metric.get("name") and metric.get("target")


def test_graph_edges_reference_existing_nodes():
    graph = capability_graph()
    node_ids = {f"cap:{c.id}" for c in CAPABILITIES} | {
        f"data:{res}" for res in {r for c in CAPABILITIES for r in c.inputs}
    }
    for edge in graph["edges"]:
        assert edge["from"] in node_ids, edge
        assert edge["to"] in node_ids, edge
    assert graph["count"] == len(CAPABILITIES)


def test_graph_has_compute_to_decide_edges():
    graph = capability_graph()
    kinds = {e["kind"] for e in graph["edges"]}
    assert "data" in kinds
    # 至少有一条「计算能力 → 决策能力」的派生边
    assert "derive" in kinds


def test_capability_map_roundtrip():
    mapping = capability_map()
    assert set(mapping) == {c.id for c in CAPABILITIES}
    assert mapping["sentiment_flow"].kind == "compute"
    assert mapping["memo_compose"].kind == "decide"


# ----------------------------------------------------------------------
#  可用性评估
# ----------------------------------------------------------------------
def test_report_all_fresh_is_fully_available():
    states = {res: {"fresh": True} for res in BUS_RESOURCES}
    matrix = report(states)
    assert matrix["counts"]["blocked"] == 0
    assert matrix["counts"]["degraded"] == 0
    assert matrix["available_ratio"] == 1.0
    assert len(matrix["capabilities"]) == len(CAPABILITIES)


def test_report_missing_resource_blocks_capability():
    """缺 sentiment 应挡住「下游」能力；sentiment_score 只依赖 packet，不受影响。"""
    states = {res: {"fresh": True} for res in BUS_RESOURCES}
    states["sentiment"] = {"missing": True}
    matrix = report(states)
    blocked = {row["id"] for row in matrix["capabilities"] if row["status"] == "blocked"}
    assert "sentiment_flow" in blocked
    assert "expert_panel" in blocked
    assert "sentiment_score" not in blocked
    row = next(r for r in matrix["capabilities"] if r["id"] == "sentiment_flow")
    assert any("缺失" in b for b in row["blockers"])


def test_report_missing_packet_blocks_sentiment_score():
    states = {res: {"fresh": True} for res in BUS_RESOURCES}
    states["packet"] = {"missing": True}
    matrix = report(states)
    blocked = {row["id"] for row in matrix["capabilities"] if row["status"] == "blocked"}
    assert {"sentiment_score", "regime_diagnosis", "expert_panel"} <= blocked


def test_report_stale_degrades_not_blocks():
    states = {res: {"fresh": True} for res in BUS_RESOURCES}
    states["coverage"] = {"fresh": False}
    matrix = report(states)
    degraded = {row["id"] for row in matrix["capabilities"] if row["status"] == "degraded"}
    assert "breadth_stats" in degraded
    assert matrix["counts"]["blocked"] == 0
    assert 0 < matrix["available_ratio"] < 1.0


def test_report_unknown_resources_do_not_fail():
    matrix = report({})
    assert matrix["counts"]["blocked"] == 0
    assert matrix["available_ratio"] == 1.0


# ----------------------------------------------------------------------
#  决策备忘录
# ----------------------------------------------------------------------
def _experts(stance="attack", score=0.6, cap=60, conf=0.7, actionable=True, veto=False):
    return {
        "consensus": {
            "score": score, "label": "进攻" if stance == "attack" else "防守",
            "stance": stance, "disagreement": 0.2, "confidence": conf,
            "position_cap": cap, "actionable": actionable, "veto": veto,
            "veto_reasons": [{"expert_id": "E6", "reason": "分歧过大"}] if veto else [],
        },
        "trace_id": "mk_1",
        "as_of": "2026-11-02T10:00:00+08:00",
        "summary": "六专家共识 +0.60",
        "invalidation": ["宽度跌破 45%"],
    }


def _flow(level=66.0, regime_key="heating", conf=0.8, flags=None):
    return {
        "ok": True, "level": level, "confidence": conf,
        "band": {"key": "warm", "label": "偏暖"},
        "regime": {"key": regime_key, "label": "升温", "tone": "good"},
        "delta_short": 2.0, "slope_per_hour": 1.2,
        "turning_point": {"detected": False},
        "flags": flags or [],
    }


def _survey(status="强势上行", divergence=False):
    return {
        "ok": True,
        "regime": {"status": status, "main_driver": "领涨：半导体", "confidence": 70},
        "divergence": {"detected": divergence, "note": "结构强但情绪退"},
        "alerts": [{"level": "high", "code": "SURGE", "desc": "某股急涨"}],
    }


def test_memo_attack_keeps_consensus_cap():
    memo = compose_memo(_survey(), _flow(), _experts(), now="2026-11-02T10:01:00+08:00")
    assert memo["ok"] is True
    assert memo["stance"] == "attack"
    assert memo["position_cap"] == 60
    assert memo["invalidation"]
    assert len(memo["thesis"]) == 3
    assert memo["inputs"]["experts"]["available"] is True


def test_memo_without_inputs_degrades_to_observe():
    memo = compose_memo(None, None, None, now="2026-11-02T10:01:00+08:00")
    assert memo["ok"] is False
    assert memo["stance"] == "observe"
    assert memo["position_cap"] == 0
    assert len(memo["gaps"]) == 3
    assert "只观察" in memo["actions"][0]["action"]


def test_memo_cooling_cuts_position_cap():
    memo = compose_memo(_survey(), _flow(regime_key="cooling"), _experts(cap=60))
    assert memo["position_cap"] == int(60 * 0.7)
    assert any("情绪" in a["reason"] for a in memo["adjustments"])


def test_memo_divergence_cuts_cap_and_confidence():
    base = compose_memo(_survey(), _flow(), _experts())
    diverge = compose_memo(_survey(divergence=True), _flow(), _experts())
    assert diverge["position_cap"] < base["position_cap"]
    assert diverge["confidence"] < base["confidence"]
    assert diverge["inputs"]["survey"]["divergence"] is True


def test_memo_veto_forces_zero_cap():
    memo = compose_memo(_survey(), _flow(), _experts(veto=True))
    assert memo["position_cap"] == 0
    assert any(r["code"] == "veto" for r in memo["risks"])


def test_memo_cap_hard_ceiling():
    memo = compose_memo(_survey(), _flow(), _experts(cap=95))
    assert memo["position_cap"] <= 80


def test_memo_not_actionable_is_observe():
    memo = compose_memo(_survey(), _flow(), _experts(actionable=False))
    assert memo["stance"] == "observe"
    assert memo["position_cap"] == 0


def test_memo_invalidation_contains_sentiment_threshold():
    memo = compose_memo(_survey(), _flow(level=66.0), _experts())
    assert any("情绪总分跌破" in item for item in memo["invalidation"])
    assert any("宽度跌破" in item for item in memo["invalidation"])


def test_memo_risks_include_flow_flags_and_veto():
    flow = _flow(flags=[{"code": "overheated", "text": "情绪过热"}])
    memo = compose_memo(_survey(), flow, _experts(veto=True))
    codes = {r["code"] for r in memo["risks"]}
    assert "overheated" in codes
    assert "veto" in codes


def test_memo_version_and_summary():
    memo = compose_memo(_survey(), _flow(), _experts())
    assert memo["version"] == "brain-memo.v1"
    assert "仓位上限" in memo["summary"]


# ----------------------------------------------------------------------
#  跨层对证对备忘录的影响
# ----------------------------------------------------------------------
def test_memo_conflict_lowers_confidence_and_adds_risk():
    base = compose_memo(_survey(), _flow(), _experts())
    conflict = {"ok": False, "score": 0.4, "comparable_ratio": 0.9,
                "conflicts": [{"id": "breadth", "name": "上涨占比", "note": "Δ 6.0%"}]}
    degraded = compose_memo(_survey(), _flow(), _experts(), None, conflict)
    assert degraded["confidence"] < base["confidence"]
    assert any(r["code"] == "layer_conflict" for r in degraded["risks"])
    assert degraded["inputs"]["crosscheck"]["conflicts"] == 1


def test_memo_thin_crosscheck_adds_risk():
    thin = {"ok": False, "score": 0.9, "comparable_ratio": 0.2, "conflicts": []}
    memo = compose_memo(_survey(), _flow(), _experts(), None, thin)
    assert any(r["code"] == "crosscheck_thin" for r in memo["risks"])


def test_memo_without_crosscheck_is_unchanged():
    memo = compose_memo(_survey(), _flow(), _experts())
    assert memo["inputs"]["crosscheck"]["available"] is False
    assert not any(r["code"] == "layer_conflict" for r in memo["risks"])
