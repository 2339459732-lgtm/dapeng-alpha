"""趋势类指标：supertrend / ichimoku / donchian / keltner"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

from server.indicators_base import (
    NaN,
    PaneMap,
    _ema,
    _rolling_max,
    _rolling_min,
    _to_series,
    _true_range,
    _wilder,
    extract_ohlcv,
)

def supertrend(
    klines: Any, period: int = 10, mult: float = 3.0
) -> PaneMap:
    """SuperTrend（ATR 通道趋势线）。

    公式（Pine ``ta.supertrend`` / TradingView 通行定义）::

        atr   = RMA(TR, period)
        hl2   = (high + low) / 2
        basic_lower = hl2 - mult*atr          # 上升趋势的支撑轨
        basic_upper = hl2 + mult*atr          # 下降趋势的压力轨
        lower[i] = max(basic_lower[i], lower[i-1]) if close[i-1] > lower[i-1] else basic_lower[i]
        upper[i] = min(basic_upper[i], upper[i-1]) if close[i-1] < upper[i-1] else basic_upper[i]
        dir[i]   = +1 if close[i] > upper[i-1] else -1 if close[i] < lower[i-1] else dir[i-1]
        st_line  = lower if dir==+1 else upper

    Args:
        klines: K 线序列。
        period: ATR 周期，默认 10，范围 2–100。
        mult: ATR 倍数，默认 3.0，范围 0.5–10。

    Returns:
        ``{"st_line": [...], "st_dir": [...]}``；st_dir 为 +1（多头）/ -1（空头），
        与输入等长，ATR 未成形处为 None。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    st_line = np.full(n, NaN)
    st_dir = np.full(n, NaN)
    if n == 0 or period < 1:
        return {"st_line": _to_series(st_line), "st_dir": _to_series(st_dir)}

    tr = _true_range(h, l, c)
    atr_s = _wilder(tr, int(period))
    first = int(period) - 1
    if n <= first or not math.isfinite(float(atr_s[first])):
        return {"st_line": _to_series(st_line), "st_dir": _to_series(st_dir)}

    hl2 = (h + l) / 2.0
    lower = np.full(n, NaN)
    upper = np.full(n, NaN)
    direction = st_dir          # 复用同一数组，避免第二份状态

    lower[first] = hl2[first] - mult * atr_s[first]
    upper[first] = hl2[first] + mult * atr_s[first]
    direction[first] = 1.0
    st_line[first] = lower[first]

    for i in range(first + 1, n):
        if not math.isfinite(float(atr_s[i])):
            continue
        basic_lower = hl2[i] - mult * atr_s[i]
        basic_upper = hl2[i] + mult * atr_s[i]
        prev_lower, prev_upper, prev_close = lower[i - 1], upper[i - 1], c[i - 1]

        lower[i] = (
            max(basic_lower, prev_lower)
            if (math.isfinite(float(prev_lower)) and prev_close > prev_lower)
            else basic_lower
        )
        upper[i] = (
            min(basic_upper, prev_upper)
            if (math.isfinite(float(prev_upper)) and prev_close < prev_upper)
            else basic_upper
        )
        if math.isfinite(float(prev_upper)) and c[i] > prev_upper:
            direction[i] = 1.0
        elif math.isfinite(float(prev_lower)) and c[i] < prev_lower:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
        st_line[i] = lower[i] if direction[i] > 0 else upper[i]

    # 方向统一为 +1 / -1（前端据此判色），未成形处仍为 None
    st_dir_final = np.where(np.isfinite(direction), np.sign(direction), NaN)
    return {"st_line": _to_series(st_line), "st_dir": _to_series(st_dir_final)}


def ichimoku(
    klines: Any,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    shift: int = 26,
) -> PaneMap:
    """一目均衡表（Ichimoku Kinko Hyo）。

    公式::

        tenkan  = (max(high, tenkan) + min(low, tenkan)) / 2          # 转换线
        kijun   = (max(high, kijun)  + min(low, kijun))  / 2          # 基准线
        senkou_a_raw = (tenkan + kijun) / 2
        senkou_b_raw = (max(high, senkou_b) + min(low, senkou_b)) / 2
        senkou_a[i]  = senkou_a_raw[i - shift]                       # 前移到未来
        senkou_b[i]  = senkou_b_raw[i - shift]
        chikou[i]    = close[i + shift]                              # 后移（迟行线）

    Args:
        klines: K 线序列。
        tenkan: 转换线周期，默认 9。
        kijun: 基准线周期，默认 26。
        senkou_b: 先行带 B 周期，默认 52。
        shift: 前/后移根数，默认 26（等于 kijun）。

    Returns:
        ``{"tenkan","kijun","senkou_a","senkou_b","chikou"}``，
        长度与输入等长；``senkou_*`` 头部 shift 根为 None，
        ``chikou`` 尾部 shift 根为 None（未来 K 线尚不存在，不臆造）。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    panes = {
        "tenkan": np.full(n, NaN),
        "kijun": np.full(n, NaN),
        "senkou_a": np.full(n, NaN),
        "senkou_b": np.full(n, NaN),
        "chikou": np.full(n, NaN),
    }
    if n == 0:
        return {k: _to_series(v) for k, v in panes.items()}
    if tenkan < 1 or kijun < 1 or senkou_b < 1 or shift < 0:
        return {k: _to_series(v) for k, v in panes.items()}

    def _mid(period: int) -> np.ndarray:
        hi = _rolling_max(h, int(period))
        lo = _rolling_min(l, int(period))
        return (hi + lo) / 2.0

    tenkan_v = _mid(int(tenkan))
    kijun_v = _mid(int(kijun))
    span_b_v = _mid(int(senkou_b))
    span_a_v = (tenkan_v + kijun_v) / 2.0

    panes["tenkan"] = tenkan_v
    panes["kijun"] = kijun_v
    if shift > 0:
        panes["senkou_a"][shift:] = span_a_v[:-shift]
        panes["senkou_b"][shift:] = span_b_v[:-shift]
        panes["chikou"][:-shift] = c[shift:]
    else:
        panes["senkou_a"] = span_a_v
        panes["senkou_b"] = span_b_v
        panes["chikou"] = c.astype(float)
    return {k: _to_series(v) for k, v in panes.items()}


def donchian(klines: Any, period: int = 20) -> PaneMap:
    """唐奇安通道（Donchian Channel）。

    ``upper = max(high, period)``，``lower = min(low, period)``，
    ``middle = (upper + lower) / 2``（窗口含当前 bar，与 Turtle 原义一致）。
    """
    data = extract_ohlcv(klines)
    h, l = data["h"], data["l"]
    n = h.size
    if n == 0 or period < 1:
        return {"upper": [], "middle": [], "lower": []}
    upper = _rolling_max(h, int(period))
    lower = _rolling_min(l, int(period))
    middle = (upper + lower) / 2.0
    return {
        "upper": _to_series(upper),
        "middle": _to_series(middle),
        "lower": _to_series(lower),
    }


def keltner(
    klines: Any, period: int = 20, mult: float = 2.0, atr_period: int = 20
) -> PaneMap:
    """肯特纳通道（Keltner Channel）。

    ``middle = EMA(close, period)``，``upper/lower = middle ± mult * ATR(atr_period)``。
    与布林带（收盘价标准差）的区别在于带宽用 ATR（真实波幅）度量，
    对跳空更稳健。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 1 or atr_period < 1:
        return {"upper": [], "middle": [], "lower": []}
    middle = _ema(c, int(period))
    tr = _true_range(h, l, c)
    atr_s = _wilder(tr, int(atr_period))
    upper = middle + mult * atr_s
    lower = middle - mult * atr_s
    return {
        "upper": _to_series(upper),
        "middle": _to_series(middle),
        "lower": _to_series(lower),
    }
