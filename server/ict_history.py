# -*- coding: utf-8 -*-
"""ICT 滚动回测统计 —— ``GET /api/ict/history`` 的纯计算层。

本模块只做数学：**无 IO、无缓存、无 FastAPI、无第三方依赖**（对齐
``server/ict_model.py`` 的「纯 stdlib」风格），便于单独 import 做回归与抽查。

第一红线：**严禁未来函数（look-ahead bias）**
--------------------------------------------
第 ``i`` 次滚动扫描把 ``candles[0:i+1]``（**含**当日、**不含**其后任何一根）交给
:func:`server.ict_model.scan`。因为 ``scan`` 是纯函数且只接收该前缀，``i`` 日记录的
状态在数学上不可能包含 ``i`` 日之后的信息。全流程没有任何一处索引到
``i + 1`` 之后（唯一例外是**事后**计算收益的切片，那属于「评估」而非「判定」，
且已被显式隔离在 :func:`_forward_return` 里）。

口径（写死在这里，前端与文档引用同一份，避免各处解释不一致）
------------------------------------------------------------
1. **滚动起点**：第 ``MIN_CANDLES``（=80）根，即索引 ``79``。少于 80 根结构识别无统计意义。
2. **事件去重**：仅在 ``status`` 由「非 confirmed」→「confirmed」的那一天记一次；连续多日
   confirmed 只算一个事件（否则同一波行情会被重复计数，胜率被系统性放大）。
   ``invalidated`` 同理按「进入失效态」的去重口径计数，仅用于观察失效频率。
3. **入场基准**：事件当日**收盘价**（不做次日开盘假设，避免引入额外假设）。
4. **收益**：``close[i+h] / close[i] - 1``（百分比，**毛口径**：未扣佣金、印花税、滑点）。
5. **样本不足即 null**：若事件日之后不足 ``h`` 根 K 线，该样本在该 horizon 记 ``null``，
   **绝不用最后一个交易日顶替**（那会把「没数据」伪装成「0 收益」，是典型的回测造假）。
6. **基准对照**：同一扫描窗口内**任意一个可交易日**入场、持有同样 ``h`` 天的平均收益
   （全样本滚动），用于判断事件收益是否真有超额，而不是只报一个孤立的胜率。

判定纪律（不许美化）
--------------------
- ``confirmed == 0`` → 所有 ``stats`` 为 ``null``、``verdict.available=false``，
  并如实写明「不是故障，是该模型在此标的/周期上从严不给信号」；
- 有效样本 ``< 10`` → ``verdict.note`` 必须明写「统计不具参考价值」；
- **不放宽六道门去凑样本**：参数只能由调用方显式传入，本模块原样透传到 ``params`` 回显。
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from server import ict_model

#: 允许的 horizon 上限（根 bar）。超过该值样本必然极少，且失去短期统计意义。
MAX_HORIZON = 60
#: 有效样本下限：低于该值不出「有参考价值」的结论。
MIN_USABLE_SAMPLES = 10
#: 默认 horizon 组合（交易日）
DEFAULT_HORIZONS: Tuple[int, ...] = (1, 3, 5, 10)
#: 日线时间标签时区（与 ict_model 的 session_timezone 默认值一致）
_BAR_TZ = "Asia/Shanghai"

#: ``stats`` / ``baseline`` 每个 horizon 的字段集合（顺序即展示顺序，改则破坏前端契约）
_STAT_FIELDS: Tuple[str, ...] = (
    "samples",
    "win_rate_pct",
    "avg_return_pct",
    "median_return_pct",
    "max_return_pct",
    "min_return_pct",
)


# ----------------------------------------------------------------------
#  工具
# ----------------------------------------------------------------------
def bar_date(candle: ict_model.Candle) -> str:
    """日线 bar 的本地日期标签 ``YYYY-MM-DD``（与 /api/ict/scan 的 last_candle 同口径）。"""
    try:
        return candle.timestamp.astimezone(ZoneInfo(_BAR_TZ)).strftime("%Y-%m-%d")
    except Exception:  # pragma: no cover - 非法时间戳
        return candle.timestamp.isoformat()[:10]


def _forward_return(candles: Sequence[ict_model.Candle], start: int,
                    horizon: int) -> Optional[float]:
    """``start`` 日收盘 → ``start+horizon`` 日收盘的百分比收益。

    **评估专用**：这是唯一会读取 ``start`` 之后数据的地方，且发生在状态判定完成之后，
    不参与任何 ``status`` 计算，因此不构成未来函数。

    Returns:
        ``None``：未来交易日不足 ``horizon`` 根（**不用最后一天顶替**）。
    """
    end = start + horizon
    if start < 0 or end >= len(candles):
        return None
    base = candles[start].close
    if not base:
        return None
    return (candles[end].close / base - 1.0) * 100.0


def _stats_block(values: Sequence[Optional[float]]) -> Dict[str, Any]:
    """把一个 horizon 的收益序列压成统计块；空序列返回全 ``null``（samples=0）。

    「胜」的定义：收益**严格大于 0**（0 收益不计胜，避免平盘被算成胜率）。
    """
    clean = [float(v) for v in values if v is not None]
    if not clean:
        payload: Dict[str, Any] = {field: None for field in _STAT_FIELDS}
        payload["samples"] = 0
        return payload
    wins = sum(1 for v in clean if v > 0)
    return {
        "samples": len(clean),
        "win_rate_pct": round(wins / len(clean) * 100.0, 2),
        "avg_return_pct": round(statistics.fmean(clean), 4),
        "median_return_pct": round(statistics.median(clean), 4),
        "max_return_pct": round(max(clean), 4),
        "min_return_pct": round(min(clean), 4),
    }


def grade_samples(samples: int) -> str:
    """样本量分级（供 verdict 与前端措辞共用，避免各处自造阈值）。

    阈值由 :data:`MIN_USABLE_SAMPLES` 单点定义，``ict_structure`` 复用同一套。
    """
    if samples <= 0:
        return "none"
    if samples < MIN_USABLE_SAMPLES:
        return "insufficient"
    if samples < 30:
        return "thin"
    return "usable"


# ----------------------------------------------------------------------
#  滚动扫描
# ----------------------------------------------------------------------
def rolling_scan(candles: List[ict_model.Candle],
                 params: Optional[Dict[str, Any]] = None,
                 *, direction: str = "auto",
                 is_daily: bool = True) -> List[Dict[str, Any]]:
    """逐日滚动扫描：第 ``i`` 次只看 ``candles[0:i+1]``。

    Args:
        candles: 已排序的 Candle 列表（长度须 ≥ ``MIN_CANDLES``）。
        params: 已解析好的参数（``DEFAULT_PARAMS`` ← KV ← 查询参数），原样透传。
        direction: ``auto`` / ``long`` / ``short``，透传给 ``scan``。
        is_daily: 日线为 True（本端点只做日线）。

    Returns:
        逐日记录列表 ``[{index, date, status, direction, score, passed_gates}]``；
        ``index`` 为**内部字段**，供收益计算定位，不直接出现在响应里。

    Raises:
        ValueError: K 线不足 ``MIN_CANDLES`` 根。
    """
    total = len(candles)
    if total < ict_model.MIN_CANDLES:
        raise ValueError(f"K 线不足：需要至少 {ict_model.MIN_CANDLES} 根，实际 {total} 根")

    rows: List[Dict[str, Any]] = []
    for index in range(ict_model.MIN_CANDLES - 1, total):
        # ⚠️ 未来函数红线：这里必须且只能切到 index + 1（含当日）。
        window = candles[:index + 1]
        result = ict_model.scan(window, params=params, direction=direction, is_daily=is_daily)
        gates = result.get("gates") or []
        rows.append({
            "index": index,
            "date": bar_date(candles[index]),
            "status": str(result.get("status") or "no_trade"),
            "direction": result.get("direction"),
            "score": result.get("score"),
            "passed_gates": sum(1 for gate in gates if gate.get("state") == "pass"),
        })
    return rows


# ----------------------------------------------------------------------
#  事件抽取 + 收益统计
# ----------------------------------------------------------------------
def extract_events(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """按「进入该状态」的去重口径抽取 confirmed / invalidated 事件。

    Returns:
        ``{"confirmed": [row...], "invalidated": [row...],
           "confirmed_transitions": int, "invalidated_transitions": int}``
    """
    confirmed: List[Dict[str, Any]] = []
    invalidated: List[Dict[str, Any]] = []
    confirmed_transitions = 0
    invalidated_transitions = 0
    previous = ""
    for row in rows:
        status = row["status"]
        if status == "confirmed" and previous != "confirmed":
            confirmed.append(row)
            confirmed_transitions += 1
        elif status == "invalidated" and previous != "invalidated":
            invalidated.append(row)
            invalidated_transitions += 1
        previous = status
    return {
        "confirmed": confirmed,
        "invalidated": invalidated,
        "confirmed_transitions": confirmed_transitions,
        "invalidated_transitions": invalidated_transitions,
    }


def _horizon_stats(candles: Sequence[ict_model.Candle],
                   event_rows: Sequence[Dict[str, Any]],
                   horizons: Sequence[int]) -> Dict[str, Dict[str, Any]]:
    """事件样本的收益统计：``{"1": {...}, "3": {...}, ...}``。"""
    stats: Dict[str, Dict[str, Any]] = {}
    for horizon in horizons:
        returns = [_forward_return(candles, row["index"], horizon) for row in event_rows]
        stats[str(horizon)] = _stats_block(returns)
    return stats


def _baseline_stats(candles: Sequence[ict_model.Candle], start_index: int,
                    horizons: Sequence[int]) -> Dict[str, Dict[str, Any]]:
    """基准对照：同一窗口内**任意可交易日**入场、持有同样天数的分布。

    「随机持有」用全枚举代替抽样：结果确定、可复现、无需随机种子。
    样本 = ``[start_index, n-1-h]`` 内每个入场日，未来不足 ``h`` 根的入场日同样排除，
    因此基准与事件样本面对完全相同的「末端无未来数据」问题，两者可比。
    """
    total = len(candles)
    baseline: Dict[str, Dict[str, Any]] = {}
    for horizon in horizons:
        values: List[Optional[float]] = []
        for index in range(max(0, start_index), total):
            if index + horizon >= total:
                break
            values.append(_forward_return(candles, index, horizon))
        baseline[str(horizon)] = _stats_block(values)
    return baseline


def _build_verdict(confirmed_count: int, stats: Dict[str, Dict[str, Any]],
                   baseline: Dict[str, Dict[str, Any]], *, scanned_from: str,
                   scanned_to: str, scanned_days: int) -> Dict[str, Any]:
    """结论判定（严格、不美化）。

    三段式：
    1. 无 confirmed 事件 → ``available=false`` + 明确说明（**不是** 0% 胜率）；
    2. 有效样本 < 10 → ``available=true``（数字已算出）但 note 明写「不具参考价值」；
    3. 样本够 → 给出事件收益 vs 同窗口随机持有的超额，并附样本量警告。
    """
    horizon_keys = list(stats.keys())
    samples_by_horizon = {k: int((stats.get(k) or {}).get("samples") or 0) for k in horizon_keys}
    max_samples = max(samples_by_horizon.values()) if samples_by_horizon else 0

    if confirmed_count <= 0:
        return {
            "available": False,
            "confirmed_events": 0,
            "max_samples": 0,
            "grade": "none",
            "note": (f"{scanned_from} ~ {scanned_to}（{scanned_days} 个交易日）内未出现 "
                     "confirmed（六道门无一全过），无法统计；这不是故障，说明该模型在此"
                     "标的/周期上从严不给信号"),
            "edge_vs_baseline": None,
        }

    grade = grade_samples(max_samples)
    # 超额：仅在「同一 horizon 的事件样本数与基准样本数都 > 0」时可比
    edges: Dict[str, Optional[float]] = {}
    for key in horizon_keys:
        event_avg = (stats.get(key) or {}).get("avg_return_pct")
        base_avg = (baseline.get(key) or {}).get("avg_return_pct")
        if event_avg is None or base_avg is None:
            edges[key] = None
        else:
            edges[key] = round(float(event_avg) - float(base_avg), 4)

    comparable = [v for v in edges.values() if v is not None]
    edge_summary: Optional[str] = None
    if comparable:
        best_key = max(
            (k for k in horizon_keys if edges.get(k) is not None),
            key=lambda k: float(edges[k]),  # type: ignore[arg-type]
        )
        positive = [k for k in horizon_keys if edges.get(k) is not None and float(edges[k]) > 0]  # type: ignore[arg-type]
        if not positive:
            edge_summary = "所有 horizon 的事件平均收益均不高于同窗口随机持有（无超额证据）"
        else:
            edge_summary = ("相对同窗口随机持有的最大超额出现在 +"
                            f"{best_key} 日：{edges[best_key]:+.2f}pp")

    if grade == "insufficient":
        note = (f"confirmed 事件 {confirmed_count} 个，各 horizon 有效样本 "
                f"{max_samples} 个，不足 {MIN_USABLE_SAMPLES} 个，统计不具参考价值")
        if edge_summary:
            note = f"{note}；{edge_summary}（仅供参考）"
    elif grade == "thin":
        note = (f"confirmed 事件 {confirmed_count} 个，最大有效样本 {max_samples} 个"
                f"（<30，偏薄）；{edge_summary or '无可比基准'}")
    else:
        note = (f"confirmed 事件 {confirmed_count} 个，最大有效样本 {max_samples} 个；"
                f"{edge_summary or '无可比基准'}")

    return {
        "available": True,
        "confirmed_events": int(confirmed_count),
        "max_samples": int(max_samples),
        "samples_by_horizon": samples_by_horizon,
        "grade": grade,
        "note": note,
        "edge_vs_baseline": edges,
    }


# ----------------------------------------------------------------------
#  主入口
# ----------------------------------------------------------------------
def build_history(candles: List[ict_model.Candle],
                  params: Optional[Dict[str, Any]] = None,
                  horizons: Sequence[int] = DEFAULT_HORIZONS,
                  *, direction: str = "auto",
                  is_daily: bool = True) -> Dict[str, Any]:
    """滚动回测主流程：滚动扫描 → 事件去重 → 收益统计 → 基准 → 结论。

    Args:
        candles: 已排序的 Candle 列表。
        params: 已解析好的参数（原样透传并回显）。
        horizons: 持有天数列表（交易日 / bar 数），会被规整为升序去重。
        direction: ``auto`` / ``long`` / ``short``。
        is_daily: 日线 True。

    Returns:
        响应 ``data`` 中「非 IO 部分」的全部字段；``params_signature`` / ``code`` /
        ``period`` 由路由层补齐（它们属于请求层面信息）。

    Raises:
        ValueError: K 线不足 ``MIN_CANDLES`` 根 / horizons 全非法。
    """
    total = len(candles)
    if total < ict_model.MIN_CANDLES:
        raise ValueError(f"K 线不足：需要至少 {ict_model.MIN_CANDLES} 根，实际 {total} 根")

    clean_horizons = sorted({int(h) for h in horizons if int(h) > 0 and int(h) <= MAX_HORIZON})
    if not clean_horizons:
        raise ValueError(f"horizons 非法：需为 1~{MAX_HORIZON} 之间的正整数")

    # 实际生效参数 = DEFAULT ← 传入覆盖（与 ict_model.scan 内部合并逻辑保持一致）
    effective: Dict[str, Any] = dict(ict_model.DEFAULT_PARAMS)
    if params:
        effective.update({k: v for k, v in params.items() if v is not None})

    started = time.perf_counter()
    rows = rolling_scan(candles, effective, direction=direction, is_daily=is_daily)
    events = extract_events(rows)
    stats = _horizon_stats(candles, events["confirmed"], clean_horizons)
    start_index = ict_model.MIN_CANDLES - 1
    baseline = _baseline_stats(candles, start_index, clean_horizons)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)

    scanned_from = rows[0]["date"] if rows else bar_date(candles[start_index])
    scanned_to = rows[-1]["date"] if rows else bar_date(candles[-1])

    # 状态天数分布：让读者看到模型「多数时间在等」，而不是只看到事件
    status_days: Dict[str, int] = {"confirmed": 0, "developing": 0,
                                   "invalidated": 0, "no_trade": 0}
    for row in rows:
        key = row["status"] if row["status"] in status_days else "no_trade"
        status_days[key] += 1

    confirmed_events = [
        {
            "date": row["date"],
            "close": round(float(candles[row["index"]].close), 4),
            "direction": row["direction"],
            "score": row["score"],
            "passed_gates": row["passed_gates"],
        }
        for row in events["confirmed"]
    ]

    verdict = _build_verdict(
        events["confirmed_transitions"], stats, baseline,
        scanned_from=scanned_from, scanned_to=scanned_to, scanned_days=len(rows),
    )

    warnings: List[str] = [
        "滚动口径：第 i 次扫描只传入 candles[0:i+1]（含当日），判定不含未来数据。",
        "收益为收盘价毛口径（close→close），未扣佣金/印花税/滑点；A 股双边成本约 0.11%，"
        "会同时压低事件收益与基准，但不改变两者之差的方向性参考价值。",
        "事件去重：仅在 status 由非 confirmed 转入 confirmed 的当日记一次，连续多日不重复计数。",
        "基准 = 同窗口内任意可交易日入场、持有同样天数的全枚举平均（非随机抽样）。",
        "机械 confirmed 不等于可下单：本统计只评估「信号出现后价格怎么走」，未包含止损/目标位管理。",
        "ICT 标签是价格行为假说，不是机构行为证据；本端点不接入任何交易链路。",
    ]
    if events["confirmed_transitions"] > 0 and verdict.get("grade") == "insufficient":
        warnings.append(
            f"confirmed 事件仅 {events['confirmed_transitions']} 个（<{MIN_USABLE_SAMPLES}），"
            "任何胜率/均值都不具备统计意义，请勿据此调参或下结论。"
        )
    if events["confirmed_transitions"] == 0:
        warnings.append(
            "样本期内 confirmed = 0：这不是数据故障，而是六道门从严不放行；"
            "如需产生信号只能显式传入更宽松的参数（会记录在 params 中）。"
        )

    return {
        "candles": total,
        "scanned_from": scanned_from,
        "scanned_to": scanned_to,
        "scanned_days": len(rows),
        "params": effective,
        "events": {
            "confirmed": int(events["confirmed_transitions"]),
            "invalidated": int(events["invalidated_transitions"]),
        },
        "status_days": status_days,
        "confirmed_events": confirmed_events,
        "stats": stats,
        "baseline": baseline,
        "verdict": verdict,
        "horizons": list(clean_horizons),
        "scan_ms": elapsed_ms,
        "no_lookahead": {
            "method": "expand_window",
            "detail": ("第 i 次扫描仅传入 candles[0:i+1]；该次判定的最大可见索引 = i。"
                       "收益计算（读取 i+h）在判定之后独立进行，不回流影响 status。"),
            "scans": len(rows),
            "min_candles": ict_model.MIN_CANDLES,
        },
        "warnings": warnings,
    }
