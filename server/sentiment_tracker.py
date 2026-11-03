"""市场情绪追踪（sentiment-flow.v1）。

定位
----
``decision_snapshot`` 产出的是**一次快照**（sentiment.v1：此刻多少分、各维多少）。
但「追踪」要回答的是另一些问题：

* 现在是**升温**还是**降温**？速度多快？
* 这个分数是**高位**还是**低位**（相对近 20 个采样）？
* 有没有**拐点**（方向刚翻转）？
* 这波变化**谁在推**（哪个维度贡献最大）？
* 和「市场调查」给出的市场状态**是否一致**（分歧往往比一致更有信息量）？

本模块只做上述派生，不改 sentiment.v1 的算法，也不新增任何外部抓取：
输入是 ``sentiment.v1`` 快照 + 历史序列，输出是 ``sentiment-flow.v1``。

设计约束
--------
1. **纯函数可复算**：:func:`build_flow` 不读文件、不取时间，``now`` 由调用方给，
   便于单测与「用历史数据回放」。
2. **缺失即 None**：样本不足时宁可返回 ``None`` 并标注 ``insufficient``，
   不用 0 或中性值填充（与全系统缺失语义一致）。
3. **可扩展**：新增派生指标只往 :func:`build_flow` 的返回里加键，
   不改既有键名与语义。
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from server.core.numbers import safe_float, to_float
from server.core.timeutil import now_iso

logger = logging.getLogger(__name__)

FLOW_VERSION = "sentiment-flow.v1"
FLOW_CACHE_KEY = "sentiment_flow:v1"
FLOW_TTL = 60.0
FLOW_KEEP = 240
FLOW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "sentiment_flow.json",
)

#: 判定拐点/持续性所需的最小变化（分数点，0-100 制）
_MOVE_EPS = 1.5
#: 分位/标准化所需的最小样本数
_MIN_SAMPLE = 5

#: 情绪状态分档：(下界, key, 标签)
#:
#: 阈值与措辞必须与 ``decision_snapshot._status_of`` **完全一致**，
#: 否则同一个分数会在两个面板里显示成「偏热 / 偏暖」两种说法。
_BANDS: List[Tuple[float, str, str]] = [
    (0.0, "freezing", "极冷"),
    (20.0, "cool", "偏冷"),
    (40.0, "neutral", "中性"),
    (60.0, "warm", "偏热"),
    (80.0, "hot", "过热"),
]


def _band_of(level: Optional[float]) -> Dict[str, Any]:
    if level is None:
        return {"key": "unknown", "label": "未知", "lower": None, "upper": None}
    idx = 0
    for i, (low, _key, _label) in enumerate(_BANDS):
        if level >= low:
            idx = i
    low, key, label = _BANDS[idx]
    upper = _BANDS[idx + 1][0] if idx + 1 < len(_BANDS) else 100.0
    return {"key": key, "label": label, "lower": low, "upper": upper}


def _points(series: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """把历史序列整理成 [(epoch, score)]，按时间升序，丢弃无效点。"""
    out: List[Tuple[float, float]] = []
    for item in series or []:
        if not isinstance(item, dict):
            continue
        score = to_float(item.get("score"))
        if score is None:
            continue
        ts = item.get("ts")
        epoch: Optional[float] = None
        if isinstance(ts, str) and ts:
            try:
                epoch = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                epoch = None
        if epoch is None:
            epoch = to_float(item.get("_ts"))
        if epoch is None:
            continue
        out.append((epoch, score))
    out.sort(key=lambda pair: pair[0])
    return out


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _stdev(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _percentile(value: float, samples: List[float]) -> Optional[float]:
    """≤ value 的样本占比（0-100）；样本不足返回 None。"""
    if len(samples) < _MIN_SAMPLE:
        return None
    hits = sum(1 for s in samples if s <= value)
    return round(100.0 * hits / len(samples), 1)


def _slope(points: List[Tuple[float, float]]) -> Optional[float]:
    """最小二乘斜率，单位：分/小时。"""
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0) / 3600.0 for t, _v in points]
    ys = [v for _t, v in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 1e-9:
        return None
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(num / denom, 3)


def _drivers(dimensions: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], float]:
    """把六维拆成「谁在推高」「谁在拖低」，按对偏离 50 的贡献排序。

    贡献 = (维度值 - 50) × 归一化权重；权重按**可用维度**重归一，
    与 sentiment.v1 的缺失处理保持一致。
    """
    rows: List[Dict[str, Any]] = []
    avail_weight = 0.0
    for key, item in (dimensions or {}).items():
        if not isinstance(item, dict):
            continue
        weight = safe_float(item.get("weight"))
        if item.get("available") and to_float(item.get("value")) is not None:
            avail_weight += weight
    if avail_weight <= 0:
        return [], [], 0.0
    for key, item in (dimensions or {}).items():
        if not isinstance(item, dict):
            continue
        value = to_float(item.get("value"))
        weight = safe_float(item.get("weight"))
        if value is None or not item.get("available"):
            continue
        norm = weight / avail_weight
        contribution = (value - 50.0) * norm
        rows.append({
            "key": key,
            "label": item.get("label") or key,
            "value": round(value, 1),
            "weight": round(norm, 4),
            "contribution": round(contribution, 2),
        })
    rows.sort(key=lambda r: r["contribution"], reverse=True)
    total_abs = sum(abs(r["contribution"]) for r in rows) or 1.0
    for r in rows:
        r["share"] = round(abs(r["contribution"]) / total_abs, 3)
    drivers = [r for r in rows if r["contribution"] > 0][:3]
    drags = [r for r in rows if r["contribution"] < 0][:3]
    return drivers, drags, round(avail_weight, 3)


def _regime(level: Optional[float], slope: Optional[float], delta: Optional[float]) -> Dict[str, Any]:
    """把「水平 + 速度」映射成可读的市场情绪状态。

    与 market_survey_engine 的 regime 不同：那个描述**市场结构**（趋势/震荡/退潮），
    这里描述**情绪的温度与走向**，两者交叉校验才有意义。
    """
    band = _band_of(level)
    if level is None:
        return {"key": "unknown", "label": "未知", "band": band["key"], "tone": "neutral",
                "note": "情绪总分不可用"}
    speed = slope if slope is not None else (delta or 0.0)
    if band["key"] == "hot":
        if speed < -0.5:
            return {"key": "cooling_from_hot", "label": "高位回落", "band": band["key"],
                    "tone": "warn", "note": "情绪仍高但已转弱，注意退潮"}
        return {"key": "overheated", "label": "过热", "band": band["key"], "tone": "warn",
                "note": "情绪拥挤，追高性价比下降"}
    if band["key"] == "warm":
        if speed > 0.2:
            return {"key": "heating", "label": "升温", "band": band["key"], "tone": "good",
                    "note": "情绪回暖且仍在走强"}
        if speed < -0.2:
            return {"key": "cooling", "label": "降温", "band": band["key"], "tone": "warn",
                    "note": "情绪偏暖但在退"}
        return {"key": "steady", "label": "平稳", "band": band["key"], "tone": "neutral",
                "note": "情绪偏暖，方向不明"}
    if band["key"] == "neutral":
        if speed > 0.2:
            return {"key": "repairing", "label": "修复", "band": band["key"], "tone": "good",
                    "note": "中性区间向上修复"}
        if speed < -0.2:
            return {"key": "weakening", "label": "走弱", "band": band["key"], "tone": "warn",
                    "note": "中性区间向下走弱"}
        return {"key": "flat", "label": "横盘", "band": band["key"], "tone": "neutral",
                "note": "情绪无明确方向"}
    # 冰点 / 偏冷
    if speed > 0.2:
        return {"key": "recovering", "label": "冰点修复", "band": band["key"], "tone": "good",
                "note": "低位回升，关注持续性"}
    return {"key": "freezing", "label": "冰点", "band": band["key"], "tone": "risk",
            "note": "情绪低迷，防守为先"}


def build_flow(
    snapshot: Dict[str, Any],
    history: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """纯函数：由 sentiment.v1 快照 + 历史序列算出 sentiment-flow.v1。"""
    series = (history or {}).get("series") or []
    points = _points(series)
    level = to_float(snapshot.get("score"))
    now_ts = now if now is not None else time.time()

    # 当前点若还没落进历史（持久化被 4 分钟节流挡住），把它补进来，
    # 否则 delta_short 会退化成「上一段历史里的最后一个增量」。
    if level is not None:
        last = points[-1] if points else None
        if last is None or abs(last[1] - level) > 1e-9:
            points = points + [(now_ts, level)]

    deltas: List[float] = []
    for i in range(1, len(points)):
        deltas.append(points[i][1] - points[i - 1][1])

    delta_short = deltas[-1] if deltas else None
    delta_prev = deltas[-2] if len(deltas) >= 2 else None

    # 当日变化：以自然日为界，取今天第一个采样点做基准
    today = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d")
    today_points = [
        (t, v) for t, v in points
        if datetime.fromtimestamp(t).strftime("%Y-%m-%d") == today
    ]
    delta_day = None
    if level is not None and today_points:
        delta_day = round(level - today_points[0][1], 2)

    window = [v for _t, v in points[-20:]]
    mean20 = _mean(window)
    sd20 = _stdev(window)
    z20 = None
    if mean20 is not None and sd20 and sd20 > 1e-6:
        z20 = round((level - mean20) / sd20, 2) if level is not None else None
    pct20 = _percentile(level, window) if level is not None else None

    slope = _slope(points[-6:])
    if slope is None:
        slope = _slope(points)

    # 持续性：连续同向（同符号且超阈值）的次数
    ticks = 0
    direction = "flat"
    for d in reversed(deltas):
        if abs(d) < 0.4:
            break
        sign = "up" if d > 0 else "down"
        if ticks == 0:
            direction = sign
        elif sign != direction:
            break
        ticks += 1

    turning = {"detected": False, "kind": None, "magnitude": None, "note": "无拐点"}
    if delta_short is not None and delta_prev is not None:
        if delta_short * delta_prev < 0 and abs(delta_short) >= _MOVE_EPS:
            kind = "up" if delta_short > 0 else "down"
            turning = {
                "detected": True,
                "kind": kind,
                "magnitude": round(abs(delta_short), 2),
                "note": ("由降转升" if kind == "up" else "由升转降")
                        + f"（{delta_prev:+.1f} → {delta_short:+.1f}）",
            }

    drivers, drags, avail_weight = _drivers(snapshot.get("dimensions") or {})
    regime = _regime(level, slope, delta_short)
    band = _band_of(level)

    confidence = to_float(snapshot.get("confidence"))
    coverage = to_float(snapshot.get("coverage"))
    # 样本不足要显式降置信：3 个点的斜率不该和 20 个点一样可信
    sample_n = len(window)
    if confidence is not None and sample_n < _MIN_SAMPLE:
        confidence = round(confidence * (0.6 + 0.08 * sample_n), 3)

    evidence: List[str] = []
    if delta_short is not None:
        evidence.append(f"最近一次变化 {delta_short:+.2f}（{delta_prev:+.2f} → {delta_short:+.2f}）"
                        if delta_prev is not None else f"最近一次变化 {delta_short:+.2f}")
    if slope is not None:
        evidence.append(f"近端斜率 {slope:+.2f} 分/小时")
    if z20 is not None:
        evidence.append(f"相对近 20 采样 z={z20:+.2f}，分位 {pct20}")
    if drivers:
        evidence.append("主要推力：" + "、".join(f"{d['label']}{d['contribution']:+.1f}" for d in drivers))
    if drags:
        evidence.append("主要拖累：" + "、".join(f"{d['label']}{d['contribution']:+.1f}" for d in drags))

    flags: List[Dict[str, Any]] = []
    if sample_n < _MIN_SAMPLE:
        flags.append({"code": "insufficient_sample", "level": "low",
                      "text": f"仅 {sample_n} 个采样点，斜率与分位仅供参考"})
    if regime["key"] == "overheated":
        flags.append({"code": "overheated", "level": "medium",
                      "text": "情绪过热，追高需减仓或等回踩"})
    if regime["key"] == "freezing":
        flags.append({"code": "freezing", "level": "medium",
                      "text": "情绪冰点，避免逆势加仓"})
    if turning["detected"]:
        flags.append({"code": "turning_point", "level": "medium",
                      "text": f"情绪拐点：{turning['note']}"})

    return {
        "ok": level is not None,
        "version": FLOW_VERSION,
        "trace_id": snapshot.get("trace_id"),
        "as_of": snapshot.get("as_of"),
        "generated_at": now_iso(),
        "level": None if level is None else round(level, 1),
        "status": snapshot.get("status"),
        "band": band,
        "regime": regime,
        "delta_short": None if delta_short is None else round(delta_short, 2),
        "delta_prev": None if delta_prev is None else round(delta_prev, 2),
        "delta_day": delta_day,
        "slope_per_hour": slope,
        "z20": z20,
        "percentile20": pct20,
        "mean20": None if mean20 is None else round(mean20, 2),
        "sample_n": sample_n,
        "persistence": {"direction": direction, "ticks": ticks},
        "turning_point": turning,
        "drivers": drivers,
        "drags": drags,
        "available_weight": avail_weight,
        "confidence": confidence,
        "coverage": coverage,
        "data_scope": snapshot.get("data_scope"),
        "evidence": evidence,
        "flags": flags,
        "formula": (
            "level=sentiment.v1.score；delta=相邻采样差；slope=近 6 点最小二乘；"
            "z/分位=近 20 采样；贡献=(维度值-50)×可用权重重归一键权重"
        ),
        "message": None if level is not None else (snapshot.get("message") or "情绪分不可用"),
    }


# ----------------------------------------------------------------------
#  历史与缓存
# ----------------------------------------------------------------------
def _read_store() -> Dict[str, Any]:
    empty = {"version": FLOW_VERSION, "points": []}
    try:
        if not os.path.exists(FLOW_PATH):
            return empty
        with open(FLOW_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if not isinstance(raw, dict):
            return empty
        raw.setdefault("version", FLOW_VERSION)
        if not isinstance(raw.get("points"), list):
            raw["points"] = []
        return raw
    except Exception as exc:
        logger.warning("sentiment flow read failed: %s", exc)
        return empty


def _write_store(store: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(FLOW_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".flow-", suffix=".tmp", dir=os.path.dirname(FLOW_PATH))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(store, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, FLOW_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as exc:
        logger.warning("sentiment flow write failed: %s", exc)


def _append_point(flow: Dict[str, Any]) -> None:
    """把本次追踪结果落盘（按 trace 去重，保留最近 FLOW_KEEP 条）。"""
    store = _read_store()
    points = store.get("points") or []
    trace = flow.get("trace_id")
    if points and points[-1].get("trace_id") == trace and trace:
        return
    points.append({
        "ts": flow.get("as_of") or now_iso(),
        "trace_id": trace,
        "level": flow.get("level"),
        "regime": (flow.get("regime") or {}).get("key"),
        "delta": flow.get("delta_short"),
        "slope": flow.get("slope_per_hour"),
        "turning": bool((flow.get("turning_point") or {}).get("detected")),
    })
    store["points"] = points[-FLOW_KEEP:]
    _write_store(store)


async def get_sentiment_flow(force: bool = False) -> Dict[str, Any]:
    """取情绪追踪结果；缓存按 trace_id 复用，避免同一快照重复派生。"""
    from server.cache import cache
    from server.decision_snapshot import get_sentiment_snapshot, _load_history

    snapshot = await get_sentiment_snapshot(force=force)
    cached = cache.get(FLOW_CACHE_KEY)
    if (
        not force
        and isinstance(cached, dict)
        and cached.get("trace_id") == snapshot.get("trace_id")
        and cached.get("level") == (None if snapshot.get("score") is None else round(snapshot["score"], 1))
    ):
        return cached

    flow = build_flow(snapshot, _load_history())
    if flow.get("ok"):
        try:
            _append_point(flow)
        except Exception as exc:
            logger.warning("sentiment flow persist failed: %s", exc)
    cache.set(FLOW_CACHE_KEY, flow, FLOW_TTL)
    try:
        from server.data_bus import bus

        await bus.publish("sentiment_flow", flow, trigger="request")
    except Exception as exc:
        logger.debug("data bus publish(sentiment_flow) skipped: %s", exc)
    return flow


def get_flow_history(limit: int = 120) -> Dict[str, Any]:
    store = _read_store()
    points = store.get("points") or []
    count = max(1, min(int(limit or 120), FLOW_KEEP))
    return {
        "ok": True,
        "version": FLOW_VERSION,
        "count": len(points),
        "data": points[-count:],
    }


__all__ = [
    "FLOW_VERSION",
    "FLOW_CACHE_KEY",
    "FLOW_TTL",
    "build_flow",
    "get_sentiment_flow",
    "get_flow_history",
]
