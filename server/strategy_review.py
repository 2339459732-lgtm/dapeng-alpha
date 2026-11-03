#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""8 分制候选池的盘后复盘（strategy-review.v1）。

做三件事：
1. **留痕**：每次候选池生成时把当日快照写 `data/strategy_profile/YYYY-MM-DD.json`；
2. **验证**：用候选日之后的第一个交易日日线，判断是否触及目标/止损、收盘收益；
3. **解释偏差**：把「为什么 0 只」「为什么没到目标」写成人能读的句子，不藏数字。

边界：只评估已产生的候选，不回溯修改规则；未到下一交易日时返回 `pending`。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from server.tencent_kline import fetch_tencent_kline

logger = logging.getLogger(__name__)

VERSION = "strategy-review.v1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(ROOT, "data", "strategy_profile")


def save_snapshot(report: Dict[str, Any]) -> Optional[str]:
    """把当日候选池快照落盘（同一天覆盖）。失败不影响主流程。"""
    date = report.get("date")
    if not date:
        return None
    try:
        os.makedirs(SNAP_DIR, exist_ok=True)
        path = os.path.join(SNAP_DIR, f"{date}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except OSError as exc:
        logger.warning("strategy snapshot save failed: %s", exc)
        return None


def list_snapshots(limit: int = 10) -> List[str]:
    try:
        files = sorted((f for f in os.listdir(SNAP_DIR) if f.endswith(".json")), reverse=True)
    except OSError:
        return []
    return files[:limit]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


async def review_date(date: str) -> Dict[str, Any]:
    """评估某日候选在下一交易日的表现。"""
    path = os.path.join(SNAP_DIR, f"{date}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"status": "missing", "date": date, "reason": f"快照不可读：{exc}"}

    candidates = snap.get("candidates") or []
    if not candidates:
        return {
            "status": "no_candidates", "date": date,
            "screening": {"market": snap.get("market"), "funnel": snap.get("funnel")},
            "reason": "当日候选为空（规则判定空仓），无需评估个股表现",
            "deviations": [
                f"市场分 {((snap.get('market') or {}).get('score'))}/2；"
                f"涨停 {((snap.get('market') or {}).get('limit_up'))} / "
                f"跌停 {((snap.get('market') or {}).get('limit_down'))}"
            ],
        }

    rows = []
    for cand in candidates:
        code = cand.get("code")
        try:
            bars = await fetch_tencent_kline(code, period="day", limit=15, timeout=4.0)
        except Exception:
            bars = None
        if not bars:
            rows.append({"code": code, "name": cand.get("name"), "status": "no_data"})
            continue
        later = [b for b in bars if str(b.get("d")) > date]
        if not later:
            rows.append({"code": code, "name": cand.get("name"), "status": "pending"})
            continue
        nxt = later[0]
        high, low, close, open_px = (_num(nxt.get("h")), _num(nxt.get("l")),
                                     _num(nxt.get("c")), _num(nxt.get("o")))
        target, stop = _num(cand.get("target")), _num(cand.get("stop"))
        entry_ref = _num(cand.get("price"))
        hit_target = bool(target and high and high >= target)
        hit_stop = bool(stop and low and low <= stop)
        ret_close = ((close / entry_ref - 1) * 100) if (close and entry_ref) else None
        rows.append({
            "code": code, "name": cand.get("name"), "status": "evaluated",
            "date": nxt.get("d"), "open": open_px, "high": high, "low": low, "close": close,
            "entry_ref": entry_ref, "target": target, "stop": stop,
            "hit_target": hit_target, "hit_stop": hit_stop,
            "ret_close_pct": round(ret_close, 2) if ret_close is not None else None,
            "note": "先触目标" if hit_target and not hit_stop else
                    "先触止损" if hit_stop and not hit_target else
                    "未触目标/止损" if not hit_target and not hit_stop else "同日均触及（需分时确认先后）",
        })

    evaluated = [r for r in rows if r.get("status") == "evaluated"]
    wins = [r for r in evaluated if (r.get("ret_close_pct") or 0) > 0]
    avg_ret = (sum(r.get("ret_close_pct") or 0 for r in evaluated) / len(evaluated)) if evaluated else None
    deviations = []
    if evaluated:
        deviations.append(f"候选 {len(candidates)} 只，可评估 {len(evaluated)} 只，"
                          f"收盘为正 {len(wins)} 只，平均收益 "
                          f"{avg_ret:.2f}%" if avg_ret is not None else "")
        if len(evaluated) < len(candidates):
            deviations.append("部分候选缺少下一交易日数据，未纳入统计")
    else:
        deviations.append("尚无下一交易日数据，评估待更新")

    return {
        "status": "evaluated" if evaluated else "pending",
        "date": date,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": {
            "candidates": len(candidates), "evaluated": len(evaluated),
            "win": len(wins), "win_rate": round(len(wins) / len(evaluated) * 100, 1) if evaluated else None,
            "avg_ret_pct": round(avg_ret, 2) if avg_ret is not None else None,
            "hit_target": sum(1 for r in evaluated if r.get("hit_target")),
            "hit_stop": sum(1 for r in evaluated if r.get("hit_stop")),
        },
        "rows": rows,
        "deviations": [d for d in deviations if d],
        "screening": {"market": snap.get("market"), "funnel": snap.get("funnel")},
    }


async def latest_review() -> Dict[str, Any]:
    """取最近一份**有下一交易日数据**的快照做评估；没有就返回 pending。"""
    dates = [f[:-5] for f in list_snapshots(limit=10)]
    if not dates:
        return {"status": "pending", "reason": "尚无候选池历史快照（首日运行后生成）"}
    pending = None
    for date in dates:
        result = await review_date(date)
        if result.get("status") in ("evaluated", "no_candidates"):
            return result
        pending = result
    return pending or {"status": "pending", "reason": "历史快照均未到下一交易日"}


__all__ = ["VERSION", "save_snapshot", "list_snapshots", "review_date", "latest_review"]
