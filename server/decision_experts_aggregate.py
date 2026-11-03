"""共识聚合与面板出口：加权共识、否决复核、run_expert_panel"""

from __future__ import annotations
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from server.core.timeutil import now_iso as _now_iso
from server.core.numbers import clamp
from server.infra.kv import kv_get, kv_set

from server.decision_experts_base import (
    EXPERT_VERSION,
    _CACHE_SECONDS,
    _HISTORY_KEEP,
    _KV_HISTORY,
    _KV_LATEST,
    _fin,
    logger,
)
from server.decision_experts_global import build_expert_opinions, deliberate_expert_opinions
from server.decision_experts_ledger import build_collaboration, record_expert_day, resolve_expert_ledger

_BULLISH_TONES = {"强势上行", "震荡上行", "震荡走强", "偏强", "上行", "bullish", "strong"}
_BEARISH_TONES = {"震荡下行", "震荡走弱", "走弱", "偏弱", "下行", "bearish", "weak"}


def _survey_block(survey: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把判断层结论压成决策层需要的几个字段（可用性 / 结构状态 / 背离 / 口径）。"""
    data = survey or {}
    available = bool(data.get("ok"))
    regime = data.get("regime") or {}
    status = str(regime.get("status") or "")
    if status in _BULLISH_TONES:
        tone = "bullish"
    elif status in _BEARISH_TONES:
        tone = "bearish"
    elif status:
        tone = "neutral"
    else:
        tone = None
    divergence = data.get("divergence") or {}
    return {
        "available": available,
        "version": data.get("version"),
        "as_of": data.get("as_of"),
        "trace_id": data.get("trace_id"),
        "regime": status or None,
        "regime_score": regime.get("score"),
        "tone": tone,
        "divergence": bool(divergence.get("detected")),
        "scope": data.get("data_scope"),
        "alerts": len(data.get("alerts") or []),
    }


def aggregate_expert_opinions(
    packet: Dict[str, Any],
    sentiment: Dict[str, Any],
    experts: List[Dict[str, Any]],
    deliberation: Optional[Dict[str, Any]] = None,
    survey: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usable = [item for item in experts if not item.get("abstain")]
    total_weight = sum(item["confidence"] for item in usable)
    direction = {"attack": 1, "neutral": 0, "defend": -1}
    raw = sum(direction.get(item["stance"], 0) * item["confidence"] for item in usable)
    consensus = round(raw / total_weight, 3) if total_weight > 0 else 0.0
    disagreement = round(1 - abs(consensus), 3)
    quality = packet.get("quality") or {}
    q = _fin(quality.get("score")) or 0.0
    avg_conf = (sum(item["confidence"] for item in usable) / len(usable)) if usable else 0.0
    final_confidence = round(avg_conf * (1 - 0.4 * disagreement) * q, 2)
    # 否决复核：E6 的「分歧超阈值」否决基于第一轮分歧；若匿名互评后
    # 终局分歧已回落到阈值内，该否决项撤销（数据质量类否决不受影响）。
    vetoes: List[Dict[str, Any]] = []
    lifted_vetoes: List[Dict[str, Any]] = []
    for item in experts:
        if not item.get("veto"):
            continue
        reasons = [r for r in str(item.get("veto_reason") or "").split("；") if r]
        persisted = [
            r for r in reasons
            if not (r.startswith("专家分歧指数") and disagreement <= 0.55)
        ]
        if persisted or not reasons:
            vetoes.append(item if persisted == reasons else {**item, "veto_reason": "；".join(persisted)})
        else:
            lifted_vetoes.append({
                "expert_id": item["expert_id"],
                "reason": f"第一轮分歧否决经终局复核撤销（互评后分歧 {disagreement:.2f} ≤ 0.55）",
            })
    caps = [int(item.get("position_cap") or 0) for item in usable if not item.get("abstain")]
    cap = min(caps) if caps else 0
    actionable = bool(quality.get("actionable")) and (sentiment.get("coverage") or 0) >= 0.70
    if not actionable:
        stance, label = "neutral", "无结论"
        cap = 0
    elif vetoes:
        stance, label = "defend", "防守"
    elif consensus >= 0.25:
        stance, label = "attack", "进攻"
    elif consensus <= -0.25:
        stance, label = "defend", "防守"
    elif disagreement > 0.55:
        stance, label = "neutral", "重大分歧"
        cap = min(cap, 20)
    else:
        stance, label = "neutral", "均衡"
    if disagreement > 0.55 and actionable:
        cap = min(cap, 20)
    final = direction.get(stance, 0)
    minority = [
        {"expert_id": item["expert_id"], "name": item["name"], "stance": item["stance"],
         "confidence": item["confidence"], "thesis": item["thesis"]}
        for item in experts if item["stance"] != stance
    ]
    basis = [
        {"expert_id": item["expert_id"], "name": item["name"], "stance": item["stance"],
         "confidence": item["confidence"], "thesis": item["thesis"]}
        for item in experts
    ]
    risk_veto = [
        {"expert_id": item["expert_id"], "reason": item.get("veto_reason") or "风控否决"}
        for item in vetoes
    ]
    invalidations = [item.get("invalidation") for item in experts if item.get("invalidation")]
    collaboration = build_collaboration(experts, stance)
    # 判断层结论与研判团立场是否同向：让「三层是否真的贯通」在结论里可见，
    # 而不是只存在于调用栈里。divergent 时上层（备忘录）会继续压仓。
    survey_block = _survey_block(survey)
    alignment = "unknown"
    if survey_block["available"]:
        tone = survey_block.get("tone")
        if stance == "attack" and tone == "bearish":
            alignment = "divergent"
        elif stance == "defend" and tone == "bullish":
            alignment = "divergent"
        elif (stance == "attack" and tone == "bullish") or (stance == "defend" and tone == "bearish"):
            alignment = "aligned"
        elif survey_block.get("divergence"):
            alignment = "divergent"
        else:
            alignment = "mixed"
    return {
        "ok": True,
        "version": EXPERT_VERSION,
        "trace_id": packet.get("trace_id"),
        "as_of": packet.get("as_of"),
        "generated_at": _now_iso(),
        "method": "rule_v2_deliberated" if deliberation else "rule_v1",
        "experts": experts,
        "deliberation": deliberation,
        "consensus": {
            "score": consensus,
            "label": label,
            "stance": stance,
            "disagreement": disagreement,
            "confidence": final_confidence,
            "position_cap": int(cap),
            "actionable": actionable,
            "data_quality": q,
            "veto": bool(vetoes),
            "veto_reasons": risk_veto,
            "lifted_vetoes": lifted_vetoes,
            "survey_alignment": alignment,
        },
        "inputs": {
            "packet_trace": packet.get("trace_id"),
            "sentiment_score": sentiment.get("score"),
            "survey": survey_block,
            "experts": len([item for item in experts if not item.get("abstain")]),
        },
        "basis": basis,
        "minority": minority,
        "invalidation": invalidations[:4],
        "collaboration": collaboration,
        "summary": (
            f"六专家共识 {consensus:+.2f}，分歧 {disagreement:.2f}，"
            f"结论「{label}」，仓位上限 {int(cap)}%。"
            + (f" 判断层：{survey_block.get('regime') or '—'}"
               f"（{'同向' if alignment == 'aligned' else '背离' if alignment == 'divergent' else alignment}）。"
               if survey_block["available"] else " 判断层：未接入。")
            + (f" 流程：{deliberation.get('process')}。" if deliberation else "")
            + (" 风控否决：" + "；".join(item.get("reason") or "" for item in risk_veto) if risk_veto else "")
        ),
        "sentiment": {
            "score": sentiment.get("score"),
            "status": sentiment.get("status"),
            "coverage": sentiment.get("coverage"),
            "confidence": sentiment.get("confidence"),
        },
    }


async def run_expert_panel(force: bool = False,
                           survey: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """研判团入口。

    `survey`（判断层结论）由调用方传入（闭环里已经算过，不要重复跑）；
    单测或其他直接调用场景下传 None 时，这里按缓存取一次，取不到就如实标注未接入。
    """
    from server.decision_snapshot import get_market_packet, get_sentiment_snapshot

    packet = await get_market_packet(force=force)
    sentiment = await get_sentiment_snapshot(force=force)
    if survey is None:
        try:
            from server.market_survey_engine import market_survey_engine

            survey = await market_survey_engine.get_market_survey(force=False)
        except Exception as exc:
            logger.info("expert panel: survey unavailable: %s", exc)
            survey = None
    if not packet.get("ok") or not sentiment.get("ok"):
        return {
            "ok": False,
            "version": EXPERT_VERSION,
            "trace_id": packet.get("trace_id"),
            "as_of": packet.get("as_of"),
            "generated_at": _now_iso(),
            "message": packet.get("message") or sentiment.get("message") or "决策输入不足，无法生成专家研判",
        }

    latest = await kv_get(_KV_LATEST, None)
    survey_as_of = (survey or {}).get("as_of")
    if (not force and isinstance(latest, dict)
            and latest.get("trace_id") == packet.get("trace_id")
            and ((latest.get("inputs") or {}).get("survey") or {}).get("as_of") == survey_as_of):
        try:
            age = time.time() - datetime.fromisoformat(latest.get("generated_at")).timestamp()
            if age < _CACHE_SECONDS:
                return latest
        except Exception:
            pass

    experts = build_expert_opinions(packet, sentiment, survey)
    deliberation = deliberate_expert_opinions(experts, sentiment, packet.get("quality") or {})
    result = aggregate_expert_opinions(
        packet, sentiment, deliberation["revised_experts"],
        deliberation=deliberation, survey=survey,
    )
    # 命中跟踪：记录当日立场（幂等），并回看历史未决日形成命中率
    today = str(packet.get("as_of") or datetime.now().strftime("%Y-%m-%d"))[:10]
    try:
        record_expert_day(result["experts"], today)
        result["calibration"] = resolve_expert_ledger(today)
    except Exception as exc:
        logger.warning("expert ledger update failed: %s", exc)
        result["calibration"] = {"error": str(exc)}
    # 链路时延：数据龄 + 构建耗时
    chain = packet.get("chain") or {}
    result["chain"] = {
        "packet_age_s": None,
        "coverage_age_s": chain.get("coverage_age_s"),
        "packet_build_ms": chain.get("build_ms"),
    }
    try:
        packet_time = datetime.fromisoformat(str(packet.get("generated_at")))
        result["chain"]["packet_age_s"] = round(time.time() - packet_time.timestamp(), 1)
    except Exception:
        pass
    try:
        await kv_set(_KV_LATEST, result)
        history = await kv_get(_KV_HISTORY, [])
        history = list(history) if isinstance(history, list) else []
        history.append({
            "trace_id": result["trace_id"],
            "generated_at": result["generated_at"],
            "consensus": result["consensus"],
            "experts": [
                {"expert_id": item["expert_id"], "stance": item["stance"],
                 "confidence": item["confidence"], "veto": item["veto"],
                 "review_verdict": item.get("review_verdict")}
                for item in result["experts"]
            ],
            "deliberation": (result.get("deliberation") or {}).get("cross_validation"),
        })
        await kv_set(_KV_HISTORY, history[-_HISTORY_KEEP:])
    except Exception as exc:
        logger.warning("expert panel persist failed: %s", exc)
    # 事件触发：专家团更新后发布到总线（当前无下游，供 SSE 与观测使用）
    try:
        from server.data_bus import bus

        await bus.publish("experts", result, trigger="request")
    except Exception as exc:
        logger.debug("data bus publish(experts) skipped: %s", exc)
    return result


async def get_latest_expert_panel() -> Dict[str, Any]:
    latest = await kv_get(_KV_LATEST, None)
    if isinstance(latest, dict) and latest.get("ok"):
        return latest
    return await run_expert_panel()
