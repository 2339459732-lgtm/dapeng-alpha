"""量价与震荡类指标：vwap / adx / aroon / obv / mfi / atr / stoch / cci / chop / wr"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import numpy as np

from server.indicators_base import (
    NaN,
    PaneMap,
    _EPS,
    _dmi,
    _rolling_max,
    _rolling_mean,
    _rolling_min,
    _rolling_sum,
    _safe_div,
    _session_keys,
    _to_series,
    _true_range,
    _wilder,
    _window_view,
    extract_ohlcv,
)

def vwap(klines: Any) -> PaneMap:
    """成交量加权均价（VWAP）。

    ``vwap[i] = Σ(typical * vol) / Σ vol``，``typical = (high+low+close)/3``。

    累计口径（关键设计）：

    * **盘中数据**（同一交易日出现多根 bar，如 ``period=5/15/30/60/1``）：
      每根 bar **按自然日重置**累计量额 —— 即当日累计均价，与交易终端一致；
    * **日/周/月线**（一根 bar 即一个交易日，无日内重置意义）：
      从序列首根起**锚定累计**（anchored VWAP），反映区间内的资金平均成本。

    本模块拿不到逐笔成交额（腾讯日线只给量），故用典型价近似成交均价，
    这是行业通行近似；不使用 ``close`` 单点做均价（那等于没有加权）。

    Returns:
        ``{"vwap": [...]}``；成交量为 0 的累计区间输出 None（不臆造价格）。
    """
    data = extract_ohlcv(klines)
    h, l, c, v = data["h"], data["l"], data["c"], data["v"]
    n = c.size
    out = np.full(n, NaN)
    if n == 0:
        return {"vwap": []}

    keys = _session_keys(klines, n)
    sessions = 0
    if keys is not None:
        sessions = len(set(keys))
    reset_daily = keys is not None and sessions < n   # 日内多根 -> 按日重置

    cum_pv = 0.0
    cum_v = 0.0
    prev_key: Optional[str] = None
    for i in range(n):
        price = (h[i] + l[i] + c[i]) / 3.0
        vol = v[i]
        if not math.isfinite(float(price)) or not math.isfinite(float(vol)) or vol < 0:
            out[i] = NaN
            continue
        key = keys[i] if keys is not None else None
        if reset_daily and prev_key is not None and key != prev_key:
            cum_pv = 0.0
            cum_v = 0.0
        prev_key = key
        cum_pv += float(price) * float(vol)
        cum_v += float(vol)
        out[i] = cum_pv / cum_v if cum_v > _EPS else NaN
    return {"vwap": _to_series(out)}


# ============================================================
# 3. 独立副图型指标（overlay=False，振荡量纲）
# ============================================================


def adx(klines: Any, period: int = 14) -> PaneMap:
    """平均趋向指数 ADX + DI+ / DI-（趋势强度与方向）。

    公式（Wilder 1978）::

        +DI = 100 * RMA(+DM, period) / RMA(TR, period)
        -DI = 100 * RMA(-DM, period) / RMA(TR, period)
        DX  = 100 * |+DI - -DI| / (+DI + -DI)
        ADX = RMA(DX, period)

    ADX 与 DI 方向**必须分开看**：ADX 只讲强度（<20 无趋势 / >25 趋势成型），
    方向由 +DI 与 -DI 的相对位置给出。少任一半都会误读。

    Args:
        period: 默认 14。最小样本 ``2 * period``（DX 需先由 DI 成形，ADX 再平滑一次）。

    Returns:
        ``{"adx": [...], "plus_di": [...], "minus_di": [...]}``，值域 0–100。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    empty = {"adx": [], "plus_di": [], "minus_di": []}
    if n == 0 or period < 1:
        return empty
    plus_di, minus_di, dx, first = _dmi(h, l, c, int(period))
    adx_s = _wilder(dx, int(period), start=first)
    return {
        "adx": _to_series(adx_s),
        "plus_di": _to_series(plus_di),
        "minus_di": _to_series(minus_di),
    }


def di(klines: Any, period: int = 14) -> PaneMap:
    """单 pane 双线版 DI+ / DI-（设计文档 §3.4 ``indicator=di``）。

    与 :func:`adx` 共用内核，只暴露方向两条线，不含 ADX 强度线。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 1:
        return {"plus_di": [], "minus_di": []}
    plus_di, minus_di, _dx, _first = _dmi(h, l, c, int(period))
    return {"plus_di": _to_series(plus_di), "minus_di": _to_series(minus_di)}


def aroon(klines: Any, period: int = 25) -> PaneMap:
    """Aroon Up / Down + Oscillator（新高/新低距离）。

    公式（ta-lib AROON，窗口 = ``period+1`` 根）::

        up[i]   = 100 * (period - (i - idx_of_max(high[i-period..i]))) / period
        down[i] = 100 * (period - (i - idx_of_min(low[i-period..i])))  / period
        osc     = up - down            # 值域 -100–100

    Args:
        period: 默认 25。最小样本 ``period + 1``。

    Returns:
        ``{"aroon_up","aroon_down","aroon_osc"}``，前两者值域 0–100。
    """
    data = extract_ohlcv(klines)
    h, l = data["h"], data["l"]
    n = h.size
    up = np.full(n, NaN)
    down = np.full(n, NaN)
    if n == 0 or period < 1:
        return {"aroon_up": [], "aroon_down": [], "aroon_osc": []}
    p = int(period)
    win = _window_view(h, p + 1)
    if win is not None:
        idx_max = win.argmax(axis=1)
        up[p:] = 100.0 * idx_max / p
    win_l = _window_view(l, p + 1)
    if win_l is not None:
        idx_min = win_l.argmin(axis=1)
        down[p:] = 100.0 * idx_min / p
    osc = up - down
    return {
        "aroon_up": _to_series(up),
        "aroon_down": _to_series(down),
        "aroon_osc": _to_series(osc),
    }


def obv(klines: Any) -> PaneMap:
    """能量潮 OBV（On-Balance Volume）。

    ``OBV[0] = 0``（定义即为「从基准 0 起累计」，非缺数填充）；
    其后收盘涨则加当日量、跌则减当日量、平则不变。

    Returns:
        ``{"obv": [...]}``，量纲是累计成交量（数值大，必须独立副图）。
    """
    data = extract_ohlcv(klines)
    c, v = data["c"], data["v"]
    n = c.size
    if n == 0:
        return {"obv": []}
    out = np.full(n, 0.0)
    for i in range(1, n):
        vol = v[i] if math.isfinite(float(v[i])) else 0.0
        if c[i] > c[i - 1]:
            out[i] = out[i - 1] + vol
        elif c[i] < c[i - 1]:
            out[i] = out[i - 1] - vol
        else:
            out[i] = out[i - 1]
    return {"obv": _to_series(out)}


def mfi(klines: Any, period: int = 14) -> PaneMap:
    """资金流量指标 MFI（带量的 RSI）。

    公式::

        tp   = (high+low+close)/3
        mf   = tp * volume
        pos  = Σ mf where tp > tp_prev   (period 窗口)
        neg  = Σ mf where tp < tp_prev
        mfi  = 100 - 100/(1 + pos/neg)

    边界：``neg == 0 且 pos > 0`` -> 100（全流入）；``pos == 0 且 neg > 0`` -> 0
    （全流出，语义上 0 是**真实值**，不是占位）；两者皆 0（无成交）-> None。

    Args:
        period: 默认 14。最小样本 ``period + 1``。

    Returns:
        ``{"mfi": [...]}``，值域 0–100（20/80 超卖超买）。
    """
    data = extract_ohlcv(klines)
    h, l, c, v = data["h"], data["l"], data["c"], data["v"]
    n = c.size
    if n == 0 or period < 1:
        return {"mfi": []}
    tp = (h + l + c) / 3.0
    vol = np.where(np.isfinite(v) & (v > 0), v, 0.0)
    mf = tp * vol
    pos = np.full(n, NaN)
    neg = np.full(n, NaN)
    if n > 1:
        delta = np.full(n, NaN)
        delta[1:] = np.diff(tp)          # diff 无前收，故首根保持 NaN
        pos[1:] = np.where(delta[1:] > 0, mf[1:], 0.0)
        neg[1:] = np.where(delta[1:] < 0, mf[1:], 0.0)
    pos_sum = _rolling_sum(pos, int(period))
    neg_sum = _rolling_sum(neg, int(period))
    out = np.full(n, NaN)
    for i in range(n):
        ps, ns = pos_sum[i], neg_sum[i]
        if not (math.isfinite(float(ps)) and math.isfinite(float(ns))):
            continue
        if ns > _EPS:
            out[i] = 100.0 - 100.0 / (1.0 + ps / ns)
        elif ps > _EPS:
            out[i] = 100.0
        else:
            out[i] = NaN     # 窗口内无有效成交：缺数，不臆造
    return {"mfi": _to_series(out)}


def atr(klines: Any, period: int = 14) -> PaneMap:
    """真实波幅均值 ATR（Wilder 平滑的 TR 序列）。

    与 ``server/strategies.py::compute_atr`` 的区别：那里只返回**末值**用于止损位，
    这里返回**整条序列**（副图需要）。

    Args:
        period: 默认 14。最小样本 ``period``。

    Returns:
        ``{"atr": [...]}``，价格量纲（止损/仓位计算用）。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 1:
        return {"atr": []}
    tr = _true_range(h, l, c)
    return {"atr": _to_series(_wilder(tr, int(period)))}


def stoch(klines: Any, k_period: int = 9, d_period: int = 3) -> PaneMap:
    """随机指标 KDJ -> 输出 K / D / J（与现有 ``compute_kdj`` 同源公式的序列版）。

    公式::

        RSV[i] = 100 * (close - min(low, k_period)) / (max(high, k_period) - min(low, k_period))
        K      = RSV（快速线，不做额外平滑）
        D      = SMA(K, d_period)
        J      = 3K - 2D

    通道宽度为 0（全相同价格）-> RSV 为 None（缺数），**不写 0/50 占位**。

    Args:
        k_period: 默认 9。
        d_period: 默认 3。最小样本 ``k_period + d_period - 1``。

    Returns:
        ``{"k","d","j"}``，K/D 值域 0–100，J 可越界（超买超卖极值提示）。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or k_period < 1 or d_period < 1:
        return {"k": [], "d": [], "j": []}
    hi = _rolling_max(h, int(k_period))
    lo = _rolling_min(l, int(k_period))
    width = hi - lo
    rsv = _safe_div(c - lo, width, scale=100.0)
    k = rsv
    d = _rolling_mean(k, int(d_period))
    j = 3.0 * k - 2.0 * d
    return {"k": _to_series(k), "d": _to_series(d), "j": _to_series(j)}


def cci(klines: Any, period: int = 20) -> PaneMap:
    """顺势指标 CCI（Commodity Channel Index）。

    公式::

        tp  = (high+low+close)/3
        ma  = SMA(tp, period)
        md  = SMA(|tp - ma|, period)         # 平均绝对偏差
        cci = (tp - ma) / (0.015 * md)

    平均绝对偏差为 0（完全横盘）-> None（除数不可用，禁止 0 兜底）。

    Args:
        period: 默认 20。最小样本 ``period``。

    Returns:
        ``{"cci": [...]}``，值域理论无界，实务常用 ±100 判强弱势。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 1:
        return {"cci": []}
    tp = (h + l + c) / 3.0
    ma = _rolling_mean(tp, int(period))
    dev = np.abs(tp - ma)
    md = _rolling_mean(dev, int(period))
    out = _safe_div(tp - ma, 0.015 * md)
    return {"cci": _to_series(out)}


def choppiness(klines: Any, period: int = 14) -> PaneMap:
    """震荡指数 CHOP（设计文档 §3.4 ``indicator=choppiness``）。

    公式::

        chop = 100 * log10(Σ TR(period) / (max(high, period) - min(low, period))) / log10(period)

    ``> 61.8`` 视为震荡市（无趋势），``< 38.2`` 视为强趋势。

    Args:
        period: 默认 14。最小样本 ``period``。

    Returns:
        ``{"chop": [...]}``，值域 0–100。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 2:
        return {"chop": []}
    tr = _true_range(h, l, c)
    tr_sum = _rolling_sum(tr, int(period))
    hi = _rolling_max(h, int(period))
    lo = _rolling_min(l, int(period))
    ratio = _safe_div(tr_sum, hi - lo)
    out = np.full(n, NaN)
    good = np.isfinite(ratio) & (ratio > 0)
    if np.any(good):
        out[good] = 100.0 * np.log10(ratio[good]) / math.log10(float(period))
    return {"chop": _to_series(out)}


def williams_r(klines: Any, period: int = 14) -> PaneMap:
    """威廉指标 %R（设计文档 §3.4 ``indicator=williams_r``）。

    ``wr = -100 * (max(high, period) - close) / (max(high, period) - min(low, period))``

    Args:
        period: 默认 14。最小样本 ``period``。

    Returns:
        ``{"wr": [...]}``，值域 -100–0（-20 以上超买，-80 以下超卖）。
    """
    data = extract_ohlcv(klines)
    h, l, c = data["h"], data["l"], data["c"]
    n = c.size
    if n == 0 or period < 1:
        return {"wr": []}
    hi = _rolling_max(h, int(period))
    lo = _rolling_min(l, int(period))
    out = _safe_div(-(hi - c), hi - lo, scale=100.0)
    return {"wr": _to_series(out)}
