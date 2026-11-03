"""协作与账本：联合研判、命中记录、账本结算"""

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

from server.decision_experts_base import EXPERT_LEDGER_PATH, logger

def build_collaboration(experts: List[Dict[str, Any]], consensus_stance: str) -> Dict[str, Any]:
    """联合研判：
    - joint_thesis：取置信度最高的 3 位非弃权专家，各贡献一条最强证据，拼成共识论点；
    - cross_confirmations：与共识同向的专家，按其证据来源（source 前缀）去重，
      形成跨数据域的交叉确认清单——同一结论被越多独立数据域支持，可靠性越高。
    """
    usable = [e for e in experts if not e.get("abstain")]
    by_conf = sorted(usable, key=lambda e: e.get("confidence") or 0, reverse=True)
    joint_thesis = []
    for item in by_conf[:3]:
        ev = (item.get("evidence") or [{}])[0]
        if not ev:
            continue
        joint_thesis.append({
            "expert_id": item["expert_id"], "expert": item["name"],
            "stance": item["stance"], "confidence": item["confidence"],
            "point": f"{ev.get('metric')} = {ev.get('value')}{ev.get('unit') or ''}",
            "source": ev.get("source"),
        })
    # 交叉确认基准：共识方向优先；被否决翻转为防守或中性时，
    # 改用专家团的主导方向（加权），否则交叉确认失去基准。
    direction = {"attack": 1, "neutral": 0, "defend": -1}
    weighted = sum(direction.get(e["stance"], 0) * (e.get("confidence") or 0) for e in usable)
    dominant = "attack" if weighted >= 0.25 else "defend" if weighted <= -0.25 else "neutral"
    target = consensus_stance if consensus_stance in ("attack", "defend") else (
        dominant if dominant in ("attack", "defend") else None
    )
    supporters = [e for e in usable if target and e["stance"] == target]
    domains: Dict[str, List[str]] = {}
    for item in supporters:
        ev = (item.get("evidence") or [{}])[0]
        src = str(ev.get("source") or "unknown").split("(")[0].split(".")[-1]
        domains.setdefault(src, []).append(item["expert_id"])
    cross_confirmations = [
        {"domain": domain, "experts": ids}
        for domain, ids in sorted(domains.items(), key=lambda kv: len(kv[1]), reverse=True)
    ]
    return {
        "joint_thesis": joint_thesis,
        "cross_confirmations": cross_confirmations,
        "independent_domains": len(cross_confirmations),
        "confirmation_basis": target or "none",
        "dominant_direction": dominant,
        "note": "共识论点由各专家最强证据拼装；跨域确认数 = 支持主导方向的独立数据域数量",
    }


# ---------------------------------------------------------------------------
# 命中跟踪账本：每日记录专家立场，T+1 用当日市场方向回看，形成命中率
# ---------------------------------------------------------------------------

def _load_ledger() -> Dict[str, Any]:
    try:
        with open(EXPERT_LEDGER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "records": {}}


def _write_ledger_atomic(ledger: Dict[str, Any]) -> None:
    records = ledger.get("records") or {}
    for key in sorted(records)[:-120]:  # 保留最近 120 个交易日
        records.pop(key, None)
    try:
        os.makedirs(os.path.dirname(EXPERT_LEDGER_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=os.path.dirname(EXPERT_LEDGER_PATH))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, EXPERT_LEDGER_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as exc:
        logger.warning("expert ledger persist failed: %s", exc)


def _market_direction_of(date: str) -> Optional[str]:
    """当日市场方向：取该日最后一次情绪快照的宽度维（=100×上涨占比）。"""
    try:
        path = os.path.join(os.path.dirname(EXPERT_LEDGER_PATH), "sentiment_history.json")
        with open(path, encoding="utf-8") as fh:
            history = json.load(fh)
        entries = [x for x in (history.get("series") or []) if str(x.get("ts") or "").startswith(date)]
        if not entries:
            return None
        last = entries[-1]
        dims = last.get("dimensions") or {}
        breadth = dims.get("breadth")
        try:
            breadth = float(breadth)
        except (TypeError, ValueError):
            return None
        if breadth >= 55:
            return "up"
        if breadth <= 45:
            return "down"
        return "flat"
    except (OSError, ValueError):
        return None


def record_expert_day(experts: List[Dict[str, Any]], today: str) -> None:
    """记录当日各专家立场（幂等：同日覆盖）。"""
    ledger = _load_ledger()
    ledger.setdefault("records", {})[today] = {
        "experts": {
            item["expert_id"]: {"stance": item["stance"], "confidence": item.get("confidence")}
            for item in experts if item.get("expert_id")
        },
        "resolved": False,
        "market_dir": None,
        "recorded_at": _now_iso(),
    }
    _write_ledger_atomic(ledger)


def resolve_expert_ledger(today: str) -> Dict[str, Any]:
    """T+1 回看：为历史未决日补市场方向并计分，返回各专家命中率。"""
    ledger = _load_ledger()
    records = ledger.setdefault("records", {})
    changed = False
    for date in sorted(records):
        if date >= today or records[date].get("resolved"):
            continue
        market_dir = _market_direction_of(date)
        if market_dir is None:
            continue
        records[date]["market_dir"] = market_dir
        records[date]["resolved"] = True
        changed = True
    if changed:
        _write_ledger_atomic(ledger)

    stats: Dict[str, Dict[str, float]] = {}
    for date in sorted(records):
        rec = records[date]
        if not rec.get("resolved") or not rec.get("market_dir"):
            continue
        direction = rec["market_dir"]
        for eid, info in (rec.get("experts") or {}).items():
            stance = info.get("stance")
            if stance not in ("attack", "defend"):
                continue  # 中性不计分
            bucket = stats.setdefault(eid, {"hits": 0, "total": 0})
            bucket["total"] += 1
            if (stance == "attack" and direction == "up") or (stance == "defend" and direction == "down"):
                bucket["hits"] += 1
    resolved_days = sum(1 for r in records.values() if r.get("resolved"))
    return {
        "sample_days": len(records),
        "resolved_days": resolved_days,
        "per_expert": {
            eid: {
                "hits": int(v["hits"]), "total": int(v["total"]),
                "hit_rate": round(v["hits"] / v["total"], 3) if v["total"] else None,
            }
            for eid, v in sorted(stats.items())
        },
        "note": "命中率 = 进攻判上涨 / 防守判下跌 的正确比例（中性不计分）；满 20 个有效样本后用于专家权重校准",
    }
