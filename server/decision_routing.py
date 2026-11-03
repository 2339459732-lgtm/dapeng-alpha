#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决策分流引擎（decision-routing.v1）。

职责单一：把 `decision_metrics.collect()` 的指标按 `data/decision_routing.json`
的规则算出**一条路由**，供决策层收紧仓位。它不产生方向，也不改专家的意见——
只做「这个环境下最多能承担多大风险」的闸门。

规则语义
--------
* 条件：`{metric, field(score|value), op(<,<=,>,>=,==), value, optional}`
* 匹配：`match=all|any`；`optional=true` 的缺失条件不阻断匹配，只降低证据强度
* 优先级：按 `priority` 从高到低，**第一个命中的路由生效**（先保护、后进攻）
* 综合分：`Σ(weight × confidence × score) / Σ(weight × confidence)`，缺失项自动重新归一
* 降级：可用指标 < `min_available` 或覆盖率 < `min_coverage` → 直接走 `fallback`（仓位上限 0）
* 陈旧：`stale` 指标仍可参与，但置信乘 `stale_confidence_penalty`

为什么不用 if-else 写死
------------------------
阈值会变（今天 -0.45 合适，下个月可能 -0.35），规则必须能改而不改代码；
同时「先判风险、再判机会」的优先级顺序要能一眼看懂，而不是散在嵌套分支里。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "decision_routing.json")
ROUTING_VERSION = "decision-routing.v1"

_STATE: Dict[str, Any] = {"mtime": None, "data": None}

DEFAULTS: Dict[str, Any] = {
    "version": ROUTING_VERSION,
    "weights": {},
    "thresholds": {"risk_on": 0.30, "risk_off": -0.30, "min_coverage": 0.5, "min_available": 4},
    "routes": [],
    "fallback": {"id": "observe", "label": "数据不足·观察", "stance": "neutral",
                 "cap_max": 0, "note": "不可用指标过多"},
    "degrade": {"min_available": 4, "min_coverage": 0.5, "allow_stale": True,
                "stale_confidence_penalty": 0.6},
}

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


# ---------------------------------------------------------------------------
# 配置加载与校验（mtime 热重载，与 brain_config 同一套习惯）
# ---------------------------------------------------------------------------
def load(force: bool = False) -> Dict[str, Any]:
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    if not force and _STATE["data"] is not None and _STATE["mtime"] == mtime:
        return _STATE["data"]
    data = dict(DEFAULTS)
    if mtime is not None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = {**DEFAULTS, **raw}
        except Exception as exc:
            logger.warning("routing config 读取失败，沿用上一份：%s", exc)
            if _STATE["data"] is not None:
                return _STATE["data"]
    _STATE.update({"mtime": mtime, "data": data})
    return data


def validate(config: Optional[Dict[str, Any]] = None,
             metric_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """校验规则配置：未知指标、非法算子、优先级重复、仓位越界、阈值倒挂。"""
    cfg = config if config is not None else load(force=True)
    issues: List[Dict[str, Any]] = []
    # 未显式给指标清单时，用 weights 里声明过的指标作为已知集合：
    # weights 是「参与分流的指标白名单」，规则里引用名单外的指标一律视为笔误
    known = set(metric_ids or cfg.get("weights") or {})
    routes = cfg.get("routes") or []
    if not routes:
        issues.append({"path": "routes", "reason": "至少需要一条路由"})
    seen_priority: Dict[int, str] = {}
    for idx, route in enumerate(routes):
        rid = str(route.get("id") or f"#{idx}")
        priority = route.get("priority")
        if not isinstance(priority, int):
            issues.append({"path": f"routes[{rid}].priority", "reason": "必须是整数"})
        elif priority in seen_priority:
            issues.append({"path": f"routes[{rid}].priority",
                           "reason": f"与 {seen_priority[priority]} 重复（顺序会不稳定）"})
        else:
            seen_priority[priority] = rid
        if route.get("match") not in (None, "all", "any"):
            issues.append({"path": f"routes[{rid}].match", "reason": "只能是 all / any"})
        for cond in route.get("conditions") or []:
            metric = str(cond.get("metric") or "")
            if known and metric not in known:
                issues.append({"path": f"routes[{rid}].cond", "reason": f"未知指标 {metric}"})
            if cond.get("field", "score") not in ("score", "value"):
                issues.append({"path": f"routes[{rid}].cond", "reason": "field 只能是 score/value"})
            if cond.get("op") not in _OPS:
                issues.append({"path": f"routes[{rid}].cond", "reason": f"非法算子 {cond.get('op')}"})
            if not isinstance(cond.get("value"), (int, float)) or isinstance(cond.get("value"), bool):
                issues.append({"path": f"routes[{rid}].cond", "reason": "阈值必须是数字"})
        cap = (route.get("action") or {}).get("cap_max")
        if not isinstance(cap, int) or not 0 <= cap <= 80:
            issues.append({"path": f"routes[{rid}].cap_max", "reason": "仓位上限需 0~80 的整数"})
    fallback = cfg.get("fallback") or {}
    if not isinstance(fallback.get("cap_max"), int) or not 0 <= fallback.get("cap_max", -1) <= 80:
        issues.append({"path": "fallback.cap_max", "reason": "仓位上限需 0~80 的整数"})
    th = cfg.get("thresholds") or {}
    if isinstance(th.get("risk_on"), (int, float)) and isinstance(th.get("risk_off"), (int, float)):
        if th["risk_on"] <= th["risk_off"]:
            issues.append({"path": "thresholds", "reason": "risk_on 必须大于 risk_off"})
    weights = cfg.get("weights") or {}
    if known:
        unknown = [k for k in weights if k not in known]
        if unknown:
            issues.append({"path": "weights", "reason": f"未知指标的权重 {unknown}"})
    return {"ok": not issues, "version": cfg.get("version"), "issues": issues,
            "routes": len(routes), "path": CONFIG_PATH}


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------
def _metric_map(metrics: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(m.get("id")): m for m in metrics or [] if m.get("id")}


def _check(cond: Dict[str, Any], metric: Optional[Dict[str, Any]],
           penalty: float) -> Tuple[Optional[bool], Dict[str, Any]]:
    """返回 (是否满足 / None 表示不可判定, 证据)。"""
    cid = cond.get("metric")
    field = cond.get("field", "score")
    op = cond.get("op")
    threshold = cond.get("value")
    evidence = {"metric": cid, "field": field, "op": op, "threshold": threshold,
                "optional": bool(cond.get("optional"))}
    if not metric or not metric.get("available"):
        evidence.update({"result": None, "reason": (metric or {}).get("reason") or "指标不可用"})
        return None, evidence
    value = metric.get(field)
    if value is None:
        evidence.update({"result": None, "reason": f"{field} 为空"})
        return None, evidence
    if field == "score" and metric.get("stale"):
        value = value * penalty        # 陈旧数据按置信折减，不直接作废
        evidence["stale_penalized"] = True
    ok = bool(_OPS[op](value, threshold))
    evidence.update({"value": round(float(value), 3), "result": ok,
                     "confidence": metric.get("confidence")})
    return ok, evidence


def evaluate(payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对一份 metrics payload 做分流。为纯函数，便于单测与回放。"""
    cfg = config or load()
    metrics = (payload or {}).get("metrics") or []
    by_id = _metric_map(metrics)
    degrade = cfg.get("degrade") or {}
    penalty = float(degrade.get("stale_confidence_penalty") or 0.6)
    weights = cfg.get("weights") or {}
    thresholds = cfg.get("thresholds") or {}

    available = [m for m in metrics if m.get("available")]
    coverage = round(len(available) / len(metrics), 3) if metrics else 0.0
    min_available = int(degrade.get("min_available") or 0)
    min_coverage = float(degrade.get("min_coverage") or 0.0)

    # 综合分：按 weight × confidence 加权，缺失项自动归一
    num = den = 0.0
    contributions: List[Dict[str, Any]] = []
    for metric in available:
        mid = metric["id"]
        weight = float(weights.get(mid) or 0.0)
        if weight <= 0:
            continue
        conf = float(metric.get("confidence") or 0.0) * (penalty if metric.get("stale") else 1.0)
        score = metric.get("score")
        if score is None or conf <= 0:
            continue
        num += weight * conf * float(score)
        den += weight * conf
        contributions.append({"metric": mid, "weight": weight, "confidence": round(conf, 2),
                              "score": score, "contribution": round(weight * conf * float(score), 4)})
    composite = round(num / den, 3) if den > 0 else None
    if composite is None:
        bias = "unknown"
    elif composite >= float(thresholds.get("risk_on") or 0.3):
        bias = "risk_on"
    elif composite <= float(thresholds.get("risk_off") or -0.3):
        bias = "risk_off"
    else:
        bias = "neutral"

    base = {
        "version": ROUTING_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "as_of": (payload or {}).get("as_of"),
        "trace_id": (payload or {}).get("trace_id"),
        "composite": composite,
        "bias": bias,
        "coverage": coverage,
        "available": [m["id"] for m in available],
        "unavailable": [m["id"] for m in metrics if not m.get("available")],
        "contributions": sorted(contributions, key=lambda c: abs(c["contribution"]), reverse=True),
    }

    # 降级优先于任何规则：指标不足时不允许走「进攻」类路由
    if len(available) < min_available or coverage < min_coverage:
        fb = cfg.get("fallback") or DEFAULTS["fallback"]
        return {
            **base,
            "route_id": fb.get("id"), "label": fb.get("label"),
            "priority": None, "degraded": True,
            "degraded_reasons": [
                *([f"可用指标 {len(available)} < 下限 {min_available}"] if len(available) < min_available else []),
                *([f"覆盖率 {coverage} < 下限 {min_coverage}"] if coverage < min_coverage else []),
            ],
            "action": {"stance": fb.get("stance", "neutral"),
                       "cap_max": int(fb.get("cap_max") or 0),
                       "note": fb.get("note")},
            "evidence": [], "matched": [],
        }

    traces: List[Dict[str, Any]] = []
    for route in sorted(cfg.get("routes") or [], key=lambda r: -(r.get("priority") or 0)):
        results: List[Optional[bool]] = []
        evidences: List[Dict[str, Any]] = []
        blocked = False
        for cond in route.get("conditions") or []:
            ok, ev = _check(cond, by_id.get(str(cond.get("metric"))), penalty)
            evidences.append(ev)
            results.append(ok)
            if ok is None and not cond.get("optional"):
                blocked = True
        if blocked:
            traces.append({"route": route.get("id"), "matched": False,
                           "reason": "必需指标不可用", "evidence": evidences})
            continue
        decided = [r for r in results if r is not None]
        if not decided:
            traces.append({"route": route.get("id"), "matched": False,
                           "reason": "无可用条件", "evidence": evidences})
            continue
        matched = all(decided) if route.get("match") == "all" else any(decided)
        traces.append({"route": route.get("id"), "matched": matched,
                       "evidence": evidences})
        if matched:
            action = route.get("action") or {}
            return {
                **base,
                "route_id": route.get("id"), "label": route.get("label"),
                "priority": route.get("priority"), "degraded": False,
                "degraded_reasons": [],
                "action": {"stance": action.get("stance", "neutral"),
                           "cap_max": int(action.get("cap_max") or 0),
                           "note": action.get("note")},
                "evidence": evidences,
                "matched": [ev.get("metric") for ev in evidences if ev.get("result")],
                "evaluated": traces,
            }

    fb = cfg.get("fallback") or DEFAULTS["fallback"]
    return {
        **base,
        "route_id": fb.get("id"), "label": fb.get("label"),
        "priority": None, "degraded": False,
        "degraded_reasons": ["所有路由条件均未命中"],
        "action": {"stance": fb.get("stance", "neutral"),
                   "cap_max": int(fb.get("cap_max") or 0), "note": fb.get("note")},
        "evidence": [], "matched": [], "evaluated": traces,
    }


_CACHE: Dict[str, Any] = {"ts": 0.0, "result": None, "payload": None}
CACHE_TTL = 20.0


async def current(force: bool = False, with_metrics: bool = False,
                  ttl: float = CACHE_TTL) -> Dict[str, Any]:
    """取当前分流结论（可选附带指标明细）。

    短缓存（默认 20s）：分流会被页面轮询，而买手率要打腾讯批量接口；
    没有缓存时每次请求都会重建指标，既慢又容易被上游限流。
    """
    import time as _time

    from server.decision_metrics import METRICS_VERSION, collect

    cached = _CACHE["result"]
    if not force and cached is not None and _time.time() - _CACHE["ts"] < ttl:
        result = dict(cached)
        result["cached"] = True
        if with_metrics:
            result["metrics"] = (_CACHE["payload"] or {}).get("metrics")
            result["summary"] = (_CACHE["payload"] or {}).get("summary")
        return result

    payload = await collect(force=force)
    result = evaluate(payload)
    result["metrics_version"] = METRICS_VERSION
    result["config"] = validate(metric_ids=[m["id"] for m in payload.get("metrics") or []])
    result["cached"] = False
    _CACHE.update({"ts": _time.time(), "result": dict(result), "payload": payload})
    if with_metrics:
        result["metrics"] = payload.get("metrics")
        result["summary"] = payload.get("summary")
    return result


__all__ = ["ROUTING_VERSION", "CONFIG_PATH", "DEFAULTS", "load", "validate", "evaluate", "current"]
