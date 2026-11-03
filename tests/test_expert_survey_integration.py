"""三层贯通：判断层结论必须真正进入研判团，而不是只在管道里流过。

守两件事：
1. 判断层的「结构↔情绪背离」会改变专家意见与仓位（不是只写在报告里）；
2. 判断层缺失时，研判团不能装作三层齐全（E6 直接阻断可执行）。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.decision_experts import aggregate_expert_opinions, build_expert_opinions  # noqa: E402
from server.decision_snapshot import build_sentiment_snapshot  # noqa: E402


def _packet():
    return {
        "ok": True,
        "version": "market-packet.v1",
        "trace_id": "mk_test",
        "as_of": "2026-11-03T10:00:00+08:00",
        "quality": {"level": "good", "coverage_pct": 99.0, "score": 0.99,
                    "stale": False, "actionable": True, "critical_missing": []},
        "market": {
            "indices": [
                {"code": "sh000001", "name": "上证指数", "price": 3600, "chg": 0.8},
                {"code": "sz399001", "name": "深证成指", "price": 11000, "chg": 1.2},
                {"code": "sz399006", "name": "创业板指", "price": 2400, "chg": 1.6},
            ],
            "breadth": {"up": 3200, "down": 1800, "flat": 100, "limit_up": 70, "limit_down": 8,
                        "valid": 5100, "up_ratio": 62.7, "total": 5200},
            "turnover": {"amount": 1.2e12, "avg_turnover": 2.8},
            "capital_flow": {"available": True, "flow_a": 2e9, "flow_b": 1e9},
            "sectors": [
                {"name": "半导体", "avg_chg": 2.2, "up_ratio": 80, "amount": 2e10, "count": 50,
                 "leader": {"name": "A", "chg": 9}},
                {"name": "银行", "avg_chg": 0.3, "up_ratio": 60, "amount": 1e10, "count": 20,
                 "leader": {"name": "B", "chg": 2}},
            ],
            "stale": False, "risk_tags": [],
        },
        "north": {"ok": True, "data": {"hgt_up": 600, "hgt_down": 300}},
        "global": {"ok": True, "as_of_date": "2026-10-31",
                   "scorecard": {"composite_score": 0.32}},
        "intel": {"risk_score": 25, "risk_level": "低", "ts": "2026-11-02 10:00:00"},
    }


def _sentiment(packet):
    return build_sentiment_snapshot(packet, history={"series": [], "daily": {}})


def _survey(status="强势上行", divergence=False, ok=True):
    return {
        "ok": ok,
        "version": "survey-pipeline.v1",
        "trace_id": "mk_test",
        "as_of": "2026-11-03T10:00:10+08:00",
        "data_scope": "full_a_share",
        "regime": {"status": status, "score": 78, "confidence": "medium",
                   "main_driver": "科技", "liquidity_env": "宽松", "risk_assessment": "中"},
        "divergence": {"detected": divergence, "available": True,
                       "survey_tone": "偏强", "sentiment_tone": "偏热",
                       "note": "结构弱于情绪"},
        "alerts": [{"type": "放量"}],
        "facts": {"up_ratio": 62.7, "limit_up": 70, "amount": 1.2e12,
                  "scope": "full_a_share", "as_of": "2026-11-03T10:00:00+08:00"},
    }


def test_experts_receive_survey_and_divergence_suppresses_attack():
    packet = _packet()
    sentiment = _sentiment(packet)

    calm = {e["expert_id"]: e for e in build_expert_opinions(packet, sentiment, _survey())}
    warned = {e["expert_id"]: e for e in build_expert_opinions(
        packet, sentiment, _survey(divergence=True))}

    # 背离时情绪专家不再进攻，且仓位被压
    assert warned["E5"]["position_cap"] <= 35
    assert warned["E5"]["stance"] != "attack"
    if calm["E5"]["stance"] == "attack":
        assert calm["E5"]["position_cap"] >= warned["E5"]["position_cap"]
    assert any("背离" in str(item.get("thesis")) for item in [warned["E5"]])


def test_missing_survey_blocks_actionable_decision():
    """判断层缺失 = 三层未贯通，E6 必须阻断，而不是当没看见。"""
    packet = _packet()
    sentiment = _sentiment(packet)
    experts = build_expert_opinions(packet, sentiment, None)
    audit = experts[-1]
    assert audit["veto"] is True
    assert "判断层" in str(audit.get("veto_reason"))


def test_consensus_reports_survey_alignment():
    packet = _packet()
    sentiment = _sentiment(packet)

    bullish = build_expert_opinions(packet, sentiment, _survey("强势上行"))
    res_up = aggregate_expert_opinions(packet, sentiment, bullish, survey=_survey("强势上行"))
    assert res_up["inputs"]["survey"]["available"] is True
    assert res_up["inputs"]["survey"]["as_of"] == "2026-11-03T10:00:10+08:00"
    assert res_up["consensus"]["survey_alignment"] in {"aligned", "mixed", "divergent"}

    bearish = _survey("走弱", divergence=True)
    res_down = aggregate_expert_opinions(packet, sentiment, bullish, survey=bearish)
    # 专家仍看多、判断层看空 + 背离 → 明确标记为背离（备忘录据此继续压仓）
    if res_down["consensus"]["stance"] == "attack":
        assert res_down["consensus"]["survey_alignment"] == "divergent"


def test_survey_block_is_not_fabricated_when_absent():
    packet = _packet()
    sentiment = _sentiment(packet)
    experts = build_expert_opinions(packet, sentiment, None)
    res = aggregate_expert_opinions(packet, sentiment, experts, survey=None)
    block = res["inputs"]["survey"]
    assert block["available"] is False
    assert block["regime"] is None and block["as_of"] is None
    assert res["consensus"]["survey_alignment"] == "unknown"


def test_regime_tone_mapping_covers_sideways_wording():
    """「震荡上行」在实盘里就是偏强，不能因为不是「强势上行」被判成中性。"""
    from server.decision_experts_aggregate import _survey_block

    assert _survey_block(_survey("震荡上行"))["tone"] == "bullish"
    assert _survey_block(_survey("震荡下行"))["tone"] == "bearish"
    assert _survey_block(_survey("横盘整理"))["tone"] == "neutral"
