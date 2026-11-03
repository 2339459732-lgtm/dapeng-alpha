"""E4–E6 与两轮互评：全球、情绪、反方审计、研判会"""

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
    _evidence,
    _fin,
    _funding_expert,
    _opinion,
    _rotation_expert,
    _trend_expert,
)

def _global_expert(packet: Dict[str, Any]) -> Dict[str, Any]:
    global_data = packet.get("global") or {}
    intel = packet.get("intel") or {}
    scorecard = global_data.get("scorecard") or {}
    composite = _fin(scorecard.get("composite_score"))
    risk = _fin(intel.get("risk_score"))
    if composite is None and risk is None:
        return _opinion("E4", "全球与事件专家", "neutral", "1-2w", 0.15,
                        "全球联动与情报风险均缺失，不能判断外部冲击。",
                        [], ["缺少全球与情报输入"], "补齐全球数据后重新评估", 0,
                        abstain=True)
    veto = risk is not None and risk >= 70
    if veto or (risk is not None and risk >= 60) or (composite is not None and composite <= -0.10):
        stance, cap = "defend", 0 if veto else 25
    elif composite is not None and composite >= 0.10 and (risk is None or risk < 40):
        stance, cap = "attack", 60
    else:
        stance, cap = "neutral", 40
    conf = 0.40 + 0.20 * (1 if composite is not None else 0) + 0.18 * (1 if risk is not None else 0) + 0.12 * (_fin((packet.get("quality") or {}).get("score")) or 0.0)
    if veto:
        conf = min(0.9, conf + 0.1)
    pieces = []
    if composite is not None:
        pieces.append(f"全球联动 composite {composite:+.3f}")
    if risk is not None:
        pieces.append(f"情报风险 {risk:.0f}/100（{intel.get('risk_level') or '未知'}）")
    thesis = "；".join(pieces) + "。"
    thesis += "外部环境偏正面。" if stance == "attack" else "外部风险需要收缩。" if stance == "defend" else "外部影响中性。"
    ev = []
    if composite is not None:
        ev.append(_evidence("global_composite", composite, "score", "global/linkage", global_data.get("as_of_date")))
    if risk is not None:
        ev.append(_evidence("intel_risk_score", risk, "score", "brain/intel", intel.get("ts") or packet.get("as_of")))
    return _opinion("E4", "全球与事件专家", stance, "1-2w", conf, thesis, ev,
                    ["传导相关不等于因果；外盘上涨也可能只影响开盘"],
                    "情报风险升至 70 以上或全球联动 composite 跌破 -0.10 时禁止新增仓位",
                    cap, veto=veto,
                    veto_reason="重大事件风险，暂停新增仓位" if veto else "")


def _sentiment_expert(packet: Dict[str, Any], sentiment: Dict[str, Any],
                      survey: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    score = _fin(sentiment.get("score"))
    if score is None:
        return _opinion("E5", "情绪与行为专家", "neutral", "intraday", 0.20,
                        "情绪 v1 无有效总分，不能判断市场体温。",
                        [], ["情绪维度覆盖不足"], "补齐情绪数据后重新评估", 0,
                        abstain=True)
    flags = sentiment.get("risk_flags") or []
    high_flags = [flag for flag in flags if flag.get("level") == "high"]
    if score >= 60:
        stance, cap = "attack", 65
    elif score <= 40:
        stance, cap = "defend", 25
    else:
        stance, cap = "neutral", 45
    if score >= 80 or score <= 20:
        cap = min(cap, 40)
    if high_flags:
        stance = "defend" if score < 60 else "neutral"
        cap = min(cap, 35)
    # 判断层给出的「结构 ↔ 情绪背离」：情绪正面但结构转弱（或反之）时，
    # 单看情绪分会高估，因此压仓并禁止直接进攻。这是判断层进入决策的实际通道之一。
    divergence = (survey or {}).get("divergence") or {}
    diverged = bool(divergence.get("detected"))
    if diverged:
        cap = min(cap, 35)
        if stance == "attack":
            stance = "neutral"
    conf = 0.35 + 0.35 * (_fin(sentiment.get("confidence")) or 0) + 0.15 * (1 if sentiment.get("coverage", 0) >= 0.85 else 0)
    if high_flags:
        conf = min(conf, 0.72)
    thesis = f"情绪 {score}/100（{sentiment.get('status')}），覆盖 {(_fin(sentiment.get('coverage')) or 0) * 100:.0f}%，置信 {sentiment.get('confidence_level') or '--'}"
    if flags:
        thesis += "；风险旗标：" + "、".join(str(flag.get("text")) for flag in flags[:2])
    if diverged:
        thesis += f"；判断层判背离（{divergence.get('note') or '结构弱于情绪'}），压仓至 {cap}%"
    ev = [
        _evidence("sentiment_score", score, "score", "sentiment.v1", sentiment.get("as_of"), sentiment.get("coverage")),
        _evidence("sentiment_confidence", sentiment.get("confidence"), "ratio", "sentiment.v1", sentiment.get("as_of")),
    ]
    if diverged:
        ev.append(_evidence("structure_sentiment_divergence", 1, "bool",
                            "survey-pipeline.v1", (survey or {}).get("as_of")))
    return _opinion("E5", "情绪与行为专家", stance, "intraday", conf, thesis, ev,
                    ["情绪分是样本代理，不等同于全市场投资者调查"],
                    "情绪跨档回落或出现高优先级背离时撤销当前方向",
                    cap)


def _audit_expert(packet: Dict[str, Any], sentiment: Dict[str, Any], experts: List[Dict[str, Any]],
                  survey: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    quality = packet.get("quality") or {}
    coverage = _fin(quality.get("coverage_pct"))
    actionable = bool(quality.get("actionable"))
    preliminary = [item for item in experts if not item.get("abstain")]
    c_num = sum(({"attack": 1, "neutral": 0, "defend": -1}.get(item["stance"], 0) * item["confidence"]) for item in preliminary)
    c_den = sum(item["confidence"] for item in preliminary)
    c = (c_num / c_den) if c_den else 0.0
    disagreement = 1 - abs(c)
    issues = list(quality.get("critical_missing") or [])
    if not actionable:
        issues.append(f"样本覆盖 {coverage if coverage is not None else '--'}% 或快照不可执行")
    if (sentiment.get("coverage") or 0) < 0.70:
        issues.append("情绪维度覆盖不足 70%")
    if disagreement > 0.55 and len(preliminary) >= 3:
        issues.append(f"专家分歧指数 {disagreement:.2f}，高于可执行阈值 0.55")
    # 判断层缺失属于「链断了」，不是「数据差一点」：三层贯通的结论少一层就该阻断可执行
    survey_available = bool((survey or {}).get("ok"))
    if not survey_available:
        issues.append("判断层结论缺失（survey 不可用），三层未贯通")
    veto = bool(issues)
    stance = "neutral"
    cap = 0 if not actionable else (30 if disagreement > 0.55 else 60)
    thesis = "数据与证据审计通过，未发现阻断性缺陷。" if not issues else "发现阻断项：" + "；".join(issues[:3])
    ev = [
        _evidence("sample_coverage", coverage, "%", "market-packet.quality", packet.get("as_of")),
        _evidence("sentiment_coverage", round((_fin(sentiment.get("coverage")) or 0) * 100, 1), "%", "sentiment.v1", sentiment.get("as_of")),
        _evidence("preliminary_disagreement", round(disagreement, 3), "ratio", "expert-panel", packet.get("as_of"), len(preliminary)),
        _evidence("survey_available", 1 if survey_available else 0, "bool",
                  "survey-pipeline.v1", (survey or {}).get("as_of")),
    ]
    return _opinion("E6", "反方与证据审计", stance, "intraday", 0.80, thesis, ev,
                    ["若覆盖达标但仍处于高波动，审计通过不代表方向正确"],
                    "任一关键字段缺失或数据时间戳不一致时强制撤销可执行结论",
                    cap, veto=veto, veto_reason="；".join(issues[:3]) if veto else "",
                    questions=["哪一条证据最可能反转？"] if not veto else [])


def build_expert_opinions(packet: Dict[str, Any], sentiment: Dict[str, Any],
                          survey: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """生成六份独立意见；E6 在其余五份之后审计，防止先入为主。

    `survey` 是判断层（结构状态/轮动/背离）的结论。研判团必须能看到它，
    否则「三层贯通」只是数据在管道里流过，并没有进入决策。
    """
    experts = [
        _trend_expert(packet),
        _rotation_expert(packet),
        _funding_expert(packet),
        _global_expert(packet),
        _sentiment_expert(packet, sentiment, survey),
    ]
    experts.append(_audit_expert(packet, sentiment, experts, survey))
    return experts


# ---------------------------------------------------------------------------
# 第二轮：匿名交叉审阅与交叉验证
# ---------------------------------------------------------------------------
_ALIASES = ("甲", "乙", "丙", "丁", "戊", "己")
_STANCE_DIR = {"attack": 1, "neutral": 0, "defend": -1}
_REVIEW_THRESHOLD = 0.20   # 自评与他评方向差超过该值才进入争点
_REVISE_CONF_GAP = 1.15    # 反对票信心需超过支持票的该倍数才迫使修正


def _stance_of(direction: float) -> str:
    if direction >= 0.25:
        return "attack"
    if direction <= -0.25:
        return "defend"
    return "neutral"


def _stance_label(stance: str) -> str:
    return {"attack": "进攻", "neutral": "中性", "defend": "防守"}.get(stance, "中性")


def deliberate_expert_opinions(
    experts: List[Dict[str, Any]],
    sentiment: Dict[str, Any],
    quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """第二轮匿名互评：每位专家看到其余人的匿名意见后，决定认同 / 修正 / 坚持。

    纯函数、确定性：同样的第一轮意见必然得到同样的审阅结论，便于复算与测试。
    规则：
      - 与自己方向一致的同行信心之和 >= 相反方：坚持（保留异议，写入 disputes）
      - 相反方信心显著更强（>= 1.15 倍）：修正，方向向同行均值靠拢，信心按差距衰减
      - 方向一致：认同，信心微增（上限 0.95）作为交叉验证的加成
      - E6 作为反方审计，对未自我修正的专家发质询，不参与互评打分
    """
    quality = quality or {}
    usable = [item for item in experts if not item.get("abstain")]
    alias_of = {item["expert_id"]: _ALIASES[idx] for idx, item in enumerate(experts)}
    dirs = {item["expert_id"]: _STANCE_DIR.get(item["stance"], 0) for item in usable}
    confs = {item["expert_id"]: float(item["confidence"]) for item in usable}

    reviews: List[Dict[str, Any]] = []
    disputes: List[Dict[str, Any]] = []
    revised: List[Dict[str, Any]] = []
    matrix: List[Dict[str, Any]] = []

    for item in experts:
        eid = item["expert_id"]
        if item.get("abstain") or eid == "E6":
            revised.append(dict(item))
            continue
        peers = [p for p in usable if p["expert_id"] != eid and p["expert_id"] != "E6"]
        support = [p for p in peers if dirs.get(p["expert_id"], 0) == dirs[eid]]
        oppose = [p for p in peers if dirs.get(p["expert_id"], 0) != dirs[eid]]
        sup_conf = sum(confs[p["expert_id"]] for p in support)
        opp_conf = sum(confs[p["expert_id"]] for p in oppose)
        peer_dir = (
            sum(dirs[p["expert_id"]] * confs[p["expert_id"]] for p in peers) / sum(confs[p["expert_id"]] for p in peers)
            if peers and sum(confs[p["expert_id"]] for p in peers) > 0 else 0.0
        )
        gap = peer_dir - dirs[eid]
        # 冲突只在"方向不同"时成立；同为进攻而强度不同属认同，不是争点。
        if dirs[eid] == 0:
            conflict = abs(peer_dir) >= _REVIEW_THRESHOLD
        elif abs(peer_dir) >= _REVIEW_THRESHOLD:
            conflict = (dirs[eid] > 0) != (peer_dir > 0)
        else:
            conflict = False
        verdict = "endorse"
        delta = 0.0
        reason = ""
        new_dir = dirs[eid]
        new_conf = confs[eid]

        if conflict and opp_conf > sup_conf * _REVISE_CONF_GAP:
            verdict = "revise"
            new_dir = _STANCE_DIR[_stance_of(peer_dir)] if abs(peer_dir) >= 0.25 else 0
            # 反向证据越强、自身信心越低，修正幅度越大；信心同步下调。
            shrink = min(0.35, 0.15 + 0.25 * (opp_conf - sup_conf) / max(opp_conf + sup_conf, 1e-6))
            new_conf = round(max(0.15, confs[eid] * (1 - shrink)), 2)
            delta = round(new_conf - confs[eid], 2)
            reason = (
                f"匿名互评：{len(oppose)} 位同行（合计信心 {opp_conf:.2f}）方向相反，"
                + (f"且无同行支持（本方支持信心 {sup_conf:.2f}）；" if not support else
                   f"强于 {len(support)} 位支持方（{sup_conf:.2f}）；")
                + f"自评信心下调至 {new_conf:.2f}"
            )
        elif conflict:
            verdict = "uphold"
            reason = (
                f"匿名互评：与同行均值方向差 {gap:+.2f}，但本方获 {len(support)} 位支持"
                f"（{sup_conf:.2f}）不低于反对方（{opp_conf:.2f}），保留判断并列入争议"
            )
            disputes.append({
                "expert_id": eid, "alias": alias_of.get(eid),
                "peer_alias": [alias_of.get(p["expert_id"]) for p in oppose],
                "self_stance": item["stance"], "peer_stance": _stance_of(peer_dir),
                "gap": round(gap, 3),
                "support_confidence": round(sup_conf, 2),
                "oppose_confidence": round(opp_conf, 2),
                "resolution": "保留异议，主脑在汇总时按信心加权计入分歧指数",
            })
        else:
            new_conf = round(min(0.95, confs[eid] + 0.03), 2)
            delta = round(new_conf - confs[eid], 2)
            reason = f"匿名互评：与同行均值方向一致（差 {gap:+.2f}），交叉验证成立，信心微增"

        reviews.append({
            "expert_id": eid, "alias": alias_of.get(eid),
            "verdict": verdict,
            "self_stance": item["stance"],
            "peer_stance": _stance_of(peer_dir),
            "peer_direction": round(peer_dir, 3),
            "support_confidence": round(sup_conf, 2),
            "oppose_confidence": round(opp_conf, 2),
            "confidence_delta": delta,
            "reason": reason,
        })
        matrix.append({
            "expert_id": eid,
            "alias": alias_of.get(eid),
            "agree": [alias_of.get(p["expert_id"]) for p in support],
            "disagree": [alias_of.get(p["expert_id"]) for p in oppose],
            "peer_direction": round(peer_dir, 3),
        })

        updated = dict(item)
        revised_stance = _stance_of(float(new_dir))
        if verdict == "revise":
            updated["stance"] = revised_stance
            updated["position_cap"] = int(round(item["position_cap"] * max(0.5, 1 - abs(delta))))
            updated["thesis"] = (item["thesis"] + f"｜互评修正：{reason}")[:300]
        updated["confidence"] = new_conf
        updated["first_round_stance"] = item["stance"]
        updated["first_round_confidence"] = item["confidence"]
        updated["review_verdict"] = verdict
        revised.append(updated)

    # E6 反方审计：对"有反证却未修正"的专家逐条质询
    queries: List[Dict[str, Any]] = []
    for item in revised:
        if item.get("expert_id") == "E6" or item.get("abstain"):
            continue
        if item.get("review_verdict") in {"uphold", "endorse"} and (item.get("counter_evidence") or []):
            queries.append({
                "from": "E6",
                "to_alias": alias_of.get(item["expert_id"]),
                "to_expert": item["name"],
                "question": (item.get("counter_evidence") or [""])[0][:120],
                "required": "若该反证成立，请在下一轮下调仓位上限或给出失效位",
            })

    endorse = [r for r in reviews if r["verdict"] == "endorse"]
    revise = [r for r in reviews if r["verdict"] == "revise"]
    uphold = [r for r in reviews if r["verdict"] == "uphold"]
    return {
        "rounds": 2,
        "anonymized": True,
        "cross_validation": {
            "endorse": len(endorse),
            "revise": len(revise),
            "uphold": len(uphold),
            "agreement_rate": round(len(endorse) / len(reviews), 3) if reviews else None,
            "matrix": matrix,
        },
        "reviews": reviews,
        "disputes": disputes,
        "audit_queries": queries,
        "revised_experts": revised,
        "process": (
            f"第一轮独立研判 → 第二轮匿名互评（{len(endorse)} 认同 / {len(revise)} 修正 / "
            f"{len(uphold)} 坚持）→ 反方审计质询 {len(queries)} 条 → 主脑汇总"
        ),
    }
