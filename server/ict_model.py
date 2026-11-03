# -*- coding: utf-8 -*-
"""ICT Trading Model —— 九阶段证据链 + 六道机械门（A 股适配移植版）。

来源：`~/.workbuddy\\skills\\ict-trading-model`（MIT），
本模块**移植算法**而非调用其 CLI，保持「纯 stdlib」：只用 json/statistics/datetime/
zoneinfo，不引入任何第三方依赖。

移植时修掉的两个原版缺陷：
1. 原版把 ``timezone_for_sessions`` 硬编码为 ``America/New_York`` 并使用 NY kill zone
   （london_focus / new_york_open / silver_bullet / lunch_no_trade …）。A 股没有这些
   时段，照搬会让所有 K 线判成 ``outside_preferred_window``，时段信息失去意义。
   本版改为 **可配置 ``session_timezone``（默认 Asia/Shanghai）**，时段按 A 股
   09:30–11:30 / 13:00–15:00 划分（含尾盘 half hour）；日线明确标 ``daily_bar``。
2. 原版 ``swing_width=2`` 对 A 股日线过于灵敏（250 根能出 ~19 个候选）。
   本版改为可配置，并把 A 股日线实测建议值写进 :data:`DEFAULT_PARAMS`。

纪律（与原 skill 一致，不允许简化）：
- 单一 FVG / OB **不能**单独构成 ``confirmed`` —— 必须六道机械门全部 ``pass``；
- 取不到/算不出就是 ``None`` + ``reason``，不编造；
- ``status`` 四态：``confirmed / developing / invalidated / no_trade``。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from server.config import ICT_DEFAULTS

# ----------------------------------------------------------------------
#  A 股默认参数（实测依据见每个字段注释）
# ----------------------------------------------------------------------
DEFAULT_PARAMS: Dict[str, Any] = dict(ICT_DEFAULTS)

# 说明（与 server/config.py::ICT_DEFAULTS 保持同值，此处仅作可读性复述）：
_ICT_DEFAULTS_DOC = {
    # 摆动点两侧各比较几根。原版 2 在 250 根日线上出 ~19 个候选（噪音过多）；
    # A 股日线实测 5 摆动点更贴近「周级别结构」，候选降到一个更可读的量级。
    "swing_width": 5,
    # 流动性扫单回看的摆动点数量（日线 80 根 ≈ 4 个月，保留原版量级）
    "sweep_lookback": 80,
    # 扫单后最多等多少根出现 MSS/位移（日线 12 根 ≈ 2.5 周）
    "setup_lookahead": 12,
    "atr_period": 14,
    # 位移判定：实体 ≥ 前 20 根实体中位数 × 1.5；振幅 ≥ ATR × 1.0
    "displacement_body_multiple": 1.5,
    "displacement_atr_multiple": 1.0,
    # 最小盈亏比
    "minimum_rr": 2.0,
    # ✅ 修正 1：会话时区（原版硬编码 America/New_York）
    "session_timezone": "Asia/Shanghai",
}

#: 六道机械门（顺序即展示顺序；改动会破坏前端契约，谨慎）
GATE_DEFS: Tuple[Tuple[str, str], ...] = (
    ("meaningful_sweep", "有效流动性扫单"),
    ("displacement_and_strict_fvg", "位移 + 严格 FVG"),
    ("closed_candle_mss", "收盘价 MSS"),
    ("correct_premium_discount_side", "正确的溢价/折价侧"),
    ("retrace_observed", "已观察到回撤进入 FVG"),
    ("minimum_rr", "最小盈亏比"),
)

#: 数据充足性下限：低于该根数直接 ok:false（结构识别没有统计意义）
MIN_CANDLES = 80


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


def _iso(candle: Candle) -> str:
    return candle.timestamp.isoformat().replace("+00:00", "Z")


def _bar_label(candle: Candle, is_daily: bool, tz_name: str) -> str:
    """输出用的时间标签：日线给本地日期（2026-10-20），分钟线给带时区的 ISO。"""
    if is_daily:
        try:
            return candle.timestamp.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
        except Exception:  # pragma: no cover
            return _iso(candle)[:10]
    return _iso(candle)


def _parse_ts(value: Any) -> datetime:
    """解析我们的 K 线时间：``2026-10-19`` / ``2026-10-19 10:30`` / ISO / 时间戳。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ValueError("空时间戳")
    sh = ZoneInfo("Asia/Shanghai")
    # 腾讯分钟线返回紧凑格式：YYYYMMDDHHMM / YYYYMMDD（按 A 股本地时间理解，
    # 不能当成 epoch 秒——那是原版只处理 ISO/Unix 时踩不到、接入我们 K 线必踩的坑）
    if text.isdigit() and len(text) == 12:
        return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=sh).astimezone(timezone.utc)
    if text.isdigit() and len(text) == 8:
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=sh).astimezone(timezone.utc)
    if text.replace(".", "", 1).isdigit():
        return _parse_ts(float(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) == 10:                       # 2026-10-19（日线）
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=sh).astimezone(timezone.utc)
    parsed = datetime.fromisoformat(text.replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_kline_rows(rows: Sequence[Dict[str, Any]]) -> List[Candle]:
    """把 ``/api/kline`` 的 ``klines``（``[{d,o,h,l,c,v}]``）转成 Candle 列表。

    Raises:
        ValueError: 行数不足 / 字段非法 / OHLC 不自洽 / 时间重复。
    """
    if not isinstance(rows, (list, tuple)) or len(rows) < MIN_CANDLES:
        raise ValueError(f"K 线不足：需要至少 {MIN_CANDLES} 根，实际 {len(rows) if rows else 0} 根")
    candles: List[Candle] = []
    for i, row in enumerate(rows):
        try:
            ts = _parse_ts(row.get("d") or row.get("date") or row.get("t"))
            o = float(row.get("o", row.get("open")))
            h = float(row.get("h", row.get("high")))
            low = float(row.get("l", row.get("low")))
            c = float(row.get("c", row.get("close")))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {i} 根 K 线字段非法：{exc}") from exc
        vol = row.get("v", row.get("volume"))
        candle = Candle(ts, o, h, low, c, float(vol) if vol is not None else None)
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            raise ValueError(f"第 {i} 根 K 线 OHLC 不自洽")
        candles.append(candle)
    candles.sort(key=lambda x: x.timestamp)
    if len({c.timestamp for c in candles}) != len(candles):
        raise ValueError("K 线时间戳重复")
    return candles


# ----------------------------------------------------------------------
#  A 股交易时段（替换原版 NY kill zone）
# ----------------------------------------------------------------------
_SESSION_LABELS = {
    "pre_open": "盘前",
    "opening_auction": "集合竞价",
    "morning_session": "上午盘",
    "lunch_break": "午间休市",
    "afternoon_session": "下午盘",
    "closing_half_hour": "尾盘半小时",
    "post_close": "盘后",
    "daily_bar": "日线（无盘中时段）",
}


def session_name(moment: datetime, tz_name: str = "Asia/Shanghai",
                 is_daily: bool = False) -> str:
    """A 股时段判定（09:30–11:30 / 13:00–15:00，尾盘单列）。日线返回 ``daily_bar``。"""
    if is_daily:
        return "daily_bar"
    try:
        local = moment.astimezone(ZoneInfo(tz_name))
    except Exception:  # pragma: no cover - 非法时区
        local = moment.astimezone(ZoneInfo("Asia/Shanghai"))
    minute = local.hour * 60 + local.minute
    if minute < 555:                 # < 09:15
        return "pre_open"
    if 555 <= minute < 570:          # 09:15–09:30 集合竞价
        return "opening_auction"
    if 570 <= minute <= 690:         # 09:30–11:30
        return "morning_session"
    if 690 < minute < 780:           # 11:30–13:00
        return "lunch_break"
    if 780 <= minute < 870:          # 13:00–14:30
        return "afternoon_session"
    if 870 <= minute <= 900:         # 14:30–15:00
        return "closing_half_hour"
    return "post_close"


# ----------------------------------------------------------------------
#  结构识别（与原版一致的判定逻辑）
# ----------------------------------------------------------------------
def detect_swings(candles: List[Candle], width: int) -> List[Dict[str, Any]]:
    if width < 1:
        raise ValueError("swing_width 至少为 1")
    swings: List[Dict[str, Any]] = []
    for index in range(width, len(candles) - width):
        candle = candles[index]
        left = candles[index - width:index]
        right = candles[index + 1:index + width + 1]
        if all(candle.high > other.high for other in left + right):
            swings.append({"kind": "high", "index": index, "price": candle.high, "time": _iso(candle)})
        if all(candle.low < other.low for other in left + right):
            swings.append({"kind": "low", "index": index, "price": candle.low, "time": _iso(candle)})
    return swings


def detect_fvgs(candles: List[Candle]) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for end in range(2, len(candles)):
        first, third = candles[end - 2], candles[end]
        if first.high < third.low:
            lower, upper, direction = first.high, third.low, "bullish"
        elif first.low > third.high:
            lower, upper, direction = third.high, first.low, "bearish"
        else:
            continue
        gaps.append({
            "direction": direction,
            "start_index": end - 2,
            "middle_index": end - 1,
            "end_index": end,
            "time": _iso(candles[end - 1]),
            "zone": [round(lower, 6), round(upper, 6)],
            "ce": round((lower + upper) / 2.0, 6),
            "size": round(upper - lower, 6),
        })
    return gaps


def atr_at(candles: List[Candle], index: int, period: int) -> Optional[float]:
    start = max(1, index - period + 1)
    values: List[float] = []
    for position in range(start, index + 1):
        candle, previous = candles[position], candles[position - 1]
        values.append(max(candle.high - candle.low,
                          abs(candle.high - previous.close),
                          abs(candle.low - previous.close)))
    return sum(values) / len(values) if values else None


def _median_body_before(candles: List[Candle], index: int, window: int = 20) -> float:
    bodies = [c.body for c in candles[max(0, index - window):index]]
    nonzero = [b for b in bodies if b > 0]
    return statistics.median(nonzero) if nonzero else 0.0


def _confirm_lag(swing_width: int) -> int:
    """摆动点的最小确认滞后（根）：摆动点需右侧 ``swing_width`` 根才成立。

    ``detect_swings`` 判定 index 处是否为摆动点时要用到 ``index+1 .. index+width``
    这些**未来** K 线，因此站在第 ``i`` 根上，只有 ``index <= i - width`` 的摆动点
    才是「已经确认可见」的。历史上 ``detect_sweeps`` 允许引用 ``index < i`` 的任意
    摆动点，等于提前用了 1~width 根之后才知道的信息 —— 属**前视偏差**
    （会让历史统计偏乐观）。此函数把该滞后单点定义，供 sweep / MSS / 目标位共用。

    Args:
        swing_width: 摆动点宽度（``ICT_DEFAULTS["swing_width"]``，默认 5）。

    Returns:
        最小滞后根数，至少为 1（保证不会引用「当日」的摆动点，保持旧语义下界）。
    """
    return max(1, int(swing_width or 0))


def detect_sweeps(candles: List[Candle], swings: List[Dict[str, Any]], lookback: int,
                  tz_name: str, is_daily: bool, *,
                  swing_width: int = 0) -> List[Dict[str, Any]]:
    """流动性扫单识别。

    Args:
        swing_width: 摆动点宽度；传入后**只引用已确认的摆动点**
            （``index - swing_index >= swing_width``），消除前视偏差。
            传 0 表示沿用旧行为（任何更早的摆动点，含尚未确认的），仅用于兼容旧调用。

    Returns:
        扫单列表（``kind`` = ``sellside_sweep``/``buyside_sweep``）。
    """
    lag = _confirm_lag(swing_width)
    sweeps: List[Dict[str, Any]] = []
    for index, candle in enumerate(candles):
        prior = [s for s in swings if lag <= index - s["index"] <= lookback]
        prior_lows = [s for s in prior if s["kind"] == "low"]
        prior_highs = [s for s in prior if s["kind"] == "high"]
        if prior_lows:
            reference = max(prior_lows, key=lambda item: item["index"])
            if candle.low < reference["price"] and candle.close > reference["price"]:
                sweeps.append({
                    "direction": "long", "kind": "sellside_sweep",
                    "index": index, "time": _iso(candle),
                    "session": session_name(candle.timestamp, tz_name, is_daily),
                    "session_label": _SESSION_LABELS.get(
                        session_name(candle.timestamp, tz_name, is_daily), "-"),
                    "extreme": candle.low, "reference": reference,
                })
        if prior_highs:
            reference = max(prior_highs, key=lambda item: item["index"])
            if candle.high > reference["price"] and candle.close < reference["price"]:
                sweeps.append({
                    "direction": "short", "kind": "buyside_sweep",
                    "index": index, "time": _iso(candle),
                    "session": session_name(candle.timestamp, tz_name, is_daily),
                    "session_label": _SESSION_LABELS.get(
                        session_name(candle.timestamp, tz_name, is_daily), "-"),
                    "extreme": candle.high, "reference": reference,
                })
    return sweeps


def _latest_swing(swings: Iterable[Dict[str, Any]], kind: str,
                  before_index: int, lag: int = 0) -> Optional[Dict[str, Any]]:
    """``before_index`` 之前**已确认**的最后一个同类摆动点。

    Args:
        lag: 确认滞后；只取 ``index <= before_index - max(1, lag)`` 的摆动点。
            ``lag=0`` 等价于旧行为（``index < before_index``）。
    """
    limit = before_index - _confirm_lag(lag)
    choices = [s for s in swings if s["kind"] == kind and s["index"] <= limit]
    return max(choices, key=lambda item: item["index"]) if choices else None


def _candidate_target(swings: List[Dict[str, Any]], direction: str,
                      before_index: int, threshold: float,
                      lag: int = 0) -> Optional[Dict[str, Any]]:
    """候选目标位：``before_index`` 之前**已确认**的、位于 threshold 之外的首个摆动点。

    ``lag`` 语义同 :func:`_latest_swing`（消除目标位选择上的前视偏差）。
    """
    limit = before_index - _confirm_lag(lag)
    prior = [s for s in swings if s["index"] <= limit]
    if direction == "long":
        choices = [s for s in prior if s["kind"] == "high" and s["price"] > threshold]
        return min(choices, key=lambda item: item["price"]) if choices else None
    choices = [s for s in prior if s["kind"] == "low" and s["price"] < threshold]
    return max(choices, key=lambda item: item["price"]) if choices else None


def _order_block_from_fvg(candles: List[Candle], fvg: Dict[str, Any]) -> Dict[str, Any]:
    """由 FVG 反推「带动位移的那根反向 K 线」作为 order block 候选。

    诚实标注：这是**由 FVG 派生的近似 OB**（ICT 原始定义需要 tick/成交量分布验证），
    字段 ``derived_from="pre_fvg_candle"`` 明示来源，前端据此措辞。
    """
    index = max(0, fvg["start_index"])
    candle = candles[index]
    return {
        "direction": fvg["direction"],
        "zone": [round(candle.low, 6), round(candle.high, 6)],
        "time": _iso(candle),
        "index": index,
        "derived_from": "pre_fvg_candle",
        "note": "由 FVG 起点的反向 K 线近似得出，未经成交量分布验证",
    }


def build_candidate(candles: List[Candle], swings: List[Dict[str, Any]],
                    fvgs: List[Dict[str, Any]], sweep: Dict[str, Any], *,
                    lookahead: int, atr_period: int,
                    displacement_body_multiple: float,
                    displacement_atr_multiple: float,
                    minimum_rr: float,
                    swing_width: int = 0) -> Dict[str, Any]:
    """构造单个候选（位移 FVG / MSS / 溢价折价 / 回撤 / 盈亏比 / 四态）。

    ``swing_width`` 用于消除摆动点前视偏差：MSS 参照点与目标位只允许引用
    「已被右侧 ``swing_width`` 根确认」的摆动点（见 :func:`_confirm_lag`）。
    """
    direction = sweep["direction"]
    sweep_index = sweep["index"]
    opposite_kind = "high" if direction == "long" else "low"
    mss_reference = _latest_swing(swings, opposite_kind, sweep_index, lag=swing_width)
    end = min(len(candles) - 1, sweep_index + lookahead)

    mss_index: Optional[int] = None
    if mss_reference:
        for index in range(sweep_index + 1, end + 1):
            close = candles[index].close
            crossed = close > mss_reference["price"] if direction == "long" else close < mss_reference["price"]
            if crossed:
                mss_index = index
                break

    matching: List[Dict[str, Any]] = []
    desired = "bullish" if direction == "long" else "bearish"
    for gap in fvgs:
        if gap["direction"] != desired:
            continue
        if gap["middle_index"] < sweep_index or gap["end_index"] > end:
            continue
        middle = candles[gap["middle_index"]]
        median_body = _median_body_before(candles, gap["middle_index"])
        atr = atr_at(candles, gap["middle_index"], atr_period)
        body_ok = median_body > 0 and middle.body >= median_body * displacement_body_multiple
        atr_ok = atr is not None and middle.span >= atr * displacement_atr_multiple
        direction_ok = middle.bullish if direction == "long" else middle.bearish
        if body_ok and atr_ok and direction_ok:
            matching.append({
                **gap,
                "body_multiple": round(middle.body / median_body, 4) if median_body > 0 else None,
                "range_atr": round(middle.span / atr, 4) if atr else None,
            })

    fvg = min(matching, key=lambda item: item["end_index"]) if matching else None
    range_end = mss_index if mss_index is not None else (fvg["end_index"] if fvg else sweep_index)
    segment = candles[sweep_index:range_end + 1]
    range_low = min(c.low for c in segment)
    range_high = max(c.high for c in segment)
    equilibrium = (range_low + range_high) / 2.0

    if direction == "long":
        ote = [range_high - 0.79 * (range_high - range_low),
               range_high - 0.62 * (range_high - range_low)]
    else:
        ote = [range_low + 0.62 * (range_high - range_low),
               range_low + 0.79 * (range_high - range_low)]

    location_ok: Optional[bool] = None
    entry: Optional[float] = None
    if fvg:
        entry = fvg["ce"]
        location_ok = entry <= equilibrium if direction == "long" else entry >= equilibrium
    ote_overlap: Optional[bool] = None
    if fvg:
        ote_overlap = max(fvg["zone"][0], ote[0]) <= min(fvg["zone"][1], ote[1])

    target_threshold = range_high if direction == "long" else range_low
    target = (_candidate_target(swings, direction, sweep_index, target_threshold,
                               lag=swing_width)
              if entry is not None else None)
    stop = sweep["extreme"]
    rr: Optional[float] = None
    if entry is not None and target:
        risk = abs(entry - stop)
        reward = target["price"] - entry if direction == "long" else entry - target["price"]
        if risk > 0:
            rr = reward / risk

    retest_index: Optional[int] = None
    invalidation_index: Optional[int] = None
    target_before_retest = False
    start_check = max(
        sweep_index + 1,
        (mss_index + 1) if mss_index is not None else sweep_index + 1,
        (fvg["end_index"] + 1) if fvg else sweep_index + 1,
    )
    if fvg:
        for index in range(start_check, len(candles)):
            candle = candles[index]
            invalidated = candle.low < stop if direction == "long" else candle.high > stop
            target_hit = False
            if target:
                target_hit = (candle.high >= target["price"] if direction == "long"
                              else candle.low <= target["price"])
            touched = candle.low <= fvg["zone"][1] and candle.high >= fvg["zone"][0]
            if invalidated:
                invalidation_index = index
                break
            if target_hit and not touched:
                target_before_retest = True
                break
            if touched:
                retest_index = index
                break

    checks = {
        "meaningful_sweep": True,
        "displacement_and_strict_fvg": fvg is not None,
        "closed_candle_mss": mss_index is not None,
        "correct_premium_discount_side": location_ok,
        "retrace_observed": retest_index is not None,
        "minimum_rr": rr is not None and rr >= minimum_rr,
    }

    if invalidation_index is not None:
        status = "invalidated"
        reason = "结构失效：扫单极值在有效回撤前被击穿"
    elif target_before_retest:
        status = "no_trade"
        reason = "目标价在回撤前已到达，入场过晚"
    elif location_ok is False:
        status = "no_trade"
        reason = "入场 FVG 位于均衡价错误一侧"
    elif rr is not None and rr < minimum_rr:
        status = "no_trade"
        reason = f"候选盈亏比 {rr:.2f} 低于下限 {minimum_rr:.2f}"
    elif all(checks.values()):
        status = "confirmed"
        reason = "六道机械门全部通过（仍需人工确认更高周期背景）"
    else:
        status = "developing"
        missing = [name for name, value in checks.items() if value is not True]
        reason = "等待：" + "、".join(missing)

    return {
        "status": status,
        "final_status_requires_manual_context": True,
        "reason": reason,
        "direction": direction,
        "sweep": sweep,
        "displacement_fvg": fvg,
        "mss": {
            "reference": mss_reference,
            "break_index": mss_index,
            "break_time": _iso(candles[mss_index]) if mss_index is not None else None,
            "break_close": candles[mss_index].close if mss_index is not None else None,
        },
        "dealing_range": {
            "low": round(range_low, 6), "high": round(range_high, 6),
            "equilibrium": round(equilibrium, 6),
            "ote_zone": [round(ote[0], 6), round(ote[1], 6)],
            "fvg_ote_overlap": ote_overlap,
        },
        "entry": {
            "zone": fvg["zone"] if fvg else None,
            "ce": entry,
            "retest_index": retest_index,
            "retest_time": _iso(candles[retest_index]) if retest_index is not None else None,
        },
        "invalidation": {
            "structural_level": stop,
            "instruction": "任何操作缓冲需结合该标的跳价/波动数据另设，此处只给结构位",
            "crossed_index": invalidation_index,
            "crossed_time": _iso(candles[invalidation_index]) if invalidation_index is not None else None,
        },
        "target": target,
        "gross_rr_to_candidate_target": round(rr, 4) if rr is not None else None,
        "minimum_rr": minimum_rr,
        "checks": checks,
    }


def _gates_from_checks(checks: Dict[str, Any], rr: Optional[float],
                       minimum_rr: float) -> List[Dict[str, Any]]:
    """六道门的逐条结果（pass/fail/unverified + 原因）。这是本模型相对普通指标的核心价值。"""
    gates: List[Dict[str, Any]] = []
    for name, label in GATE_DEFS:
        value = checks.get(name)
        if value is True:
            state, reason = "pass", None
        elif value is False:
            state = "fail"
            if name == "minimum_rr":
                reason = (f"盈亏比 {rr:.2f} < 下限 {minimum_rr:.2f}" if rr is not None
                          else "尚未形成入场区，无法计算盈亏比")
            else:
                reason = f"{label}：未满足"
        else:
            state, reason = "unverified", f"{label}：前置条件缺失，无法判定"
        gates.append({"name": name, "label": label, "state": state,
                      "value": value, "reason": reason})
    return gates


def score_from(gates: List[Dict[str, Any]], rr: Optional[float]) -> Tuple[Optional[int], str]:
    """0–100 综合分：六道门通过率 70 分 + 盈亏比 30 分（基准 2.0）。"""
    passed = sum(1 for g in gates if g["state"] == "pass")
    unverified = sum(1 for g in gates if g["state"] == "unverified")
    if passed == 0 and unverified == len(gates):
        return None, "所有门均未能验证，不给分"
    gate_score = passed / len(gates) * 70.0
    rr_score = 0.0
    if rr is not None and rr > 0:
        rr_score = min(1.0, rr / 2.0) * 30.0
    total = int(round(gate_score + rr_score))
    basis = f"六道门通过 {passed}/{len(gates)} → {gate_score:.0f}/70；盈亏比 {('%.2f' % rr) if rr is not None else 'n/a'} → {rr_score:.0f}/30"
    return total, basis


def scan(candles: List[Candle], *, params: Optional[Dict[str, Any]] = None,
         direction: str = "auto", is_daily: bool = True) -> Dict[str, Any]:
    """执行完整扫描，返回与原版同构的结果 + A 股适配字段。"""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    if direction not in {"auto", "long", "short"}:
        raise ValueError("direction 必须是 auto / long / short")
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"K 线不足：需要至少 {MIN_CANDLES} 根，实际 {len(candles)} 根")

    tz_name = str(p.get("session_timezone") or "Asia/Shanghai")
    width = int(p["swing_width"])
    swings = detect_swings(candles, width)
    fvgs = detect_fvgs(candles)
    sweeps = detect_sweeps(candles, swings, int(p["sweep_lookback"]), tz_name, is_daily,
                           swing_width=width)
    if direction != "auto":
        sweeps = [s for s in sweeps if s["direction"] == direction]

    candidates = [
        build_candidate(candles, swings, fvgs, sweep,
                        lookahead=int(p["setup_lookahead"]),
                        atr_period=int(p["atr_period"]),
                        displacement_body_multiple=float(p["displacement_body_multiple"]),
                        displacement_atr_multiple=float(p["displacement_atr_multiple"]),
                        minimum_rr=float(p["minimum_rr"]),
                        swing_width=width)
        for sweep in sweeps
    ]
    candidates.sort(key=lambda item: item["sweep"]["index"])
    latest = candidates[-1] if candidates else None

    last_session = session_name(candles[-1].timestamp, tz_name, is_daily)
    result: Dict[str, Any] = {
        "candle_count": len(candles),
        "first_candle": _bar_label(candles[0], is_daily, tz_name),
        "last_candle": _bar_label(candles[-1], is_daily, tz_name),
        "session_timezone": tz_name,
        "session_name": last_session,
        "session_label": _SESSION_LABELS.get(last_session, last_session),
        "is_daily": bool(is_daily),
        "parameters": {
            "swing_width": int(p["swing_width"]),
            "sweep_lookback": int(p["sweep_lookback"]),
            "setup_lookahead": int(p["setup_lookahead"]),
            "atr_period": int(p["atr_period"]),
            "displacement_body_multiple": float(p["displacement_body_multiple"]),
            "displacement_atr_multiple": float(p["displacement_atr_multiple"]),
            "minimum_rr": float(p["minimum_rr"]),
            "session_timezone": tz_name,
            "direction": direction,
        },
        "counts": {
            "swings": len(swings),
            "strict_fvgs": len(fvgs),
            "liquidity_sweeps": len(sweeps),
            "candidates": len(candidates),
        },
        "latest_candidate": latest,
        "candidates": candidates[-20:],
        "recent_swings": swings[-30:],
        "recent_fvgs": fvgs[-30:],
        "warnings": [
            "机械识别结果需配合更高周期结构与关键位人工确认。",
            "机械 confirmed 不等于可下单：仍需人工确认背景。",
            "R:R 为毛口径，未含费用、点差、滑点与合约规格。",
            "ICT 标签是价格行为假说，不是机构行为的证据。",
        ],
    }

    # ---- 四态 + 六门 + 注解（供前端标注与决策闸接入） ----
    checks = (latest or {}).get("checks") or {}
    rr = (latest or {}).get("gross_rr_to_candidate_target")
    min_rr = float(p["minimum_rr"])
    gates = _gates_from_checks(checks, rr, min_rr) if latest else [
        {"name": name, "label": label, "state": "unverified",
         "value": None, "reason": "当前样本内未出现流动性扫单，无候选可判定"}
        for name, label in GATE_DEFS
    ]
    score, score_basis = score_from(gates, rr) if latest else (None, "无候选，不给分")

    fvg_annotations = [
        {"time": g["time"], "zone": g["zone"], "direction": g["direction"],
         "ce": g["ce"], "size": g["size"]}
        for g in fvgs[-20:]
    ]
    sweep_annotations = [
        {"time": s["time"], "direction": s["direction"], "kind": s["kind"],
         "extreme": s["extreme"], "session": s["session"], "session_label": s["session_label"]}
        for s in sweeps[-20:]
    ]
    # order block 注解：优先取最新候选的位移 FVG，其次取最近 10 个 FVG 派生的 OB
    ob_source = []
    if latest and latest.get("displacement_fvg"):
        ob_source = [latest["displacement_fvg"]]
    else:
        ob_source = [g for g in fvgs[-10:]]
    ob_annotations = [_order_block_from_fvg(candles, g) for g in ob_source]

    result.update({
        "status": (latest or {}).get("status", "no_trade"),
        "direction": (latest or {}).get("direction"),
        "reason": (latest or {}).get("reason", "样本内未出现流动性扫单（无候选）"),
        "gates": gates,
        "score": score,
        "score_basis": score_basis,
        "annotations": {
            "fvgs": fvg_annotations,
            "order_blocks": ob_annotations,
            "sweeps": sweep_annotations,
        },
    })
    return result
