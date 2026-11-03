"""T03 技术指标单测 —— 12 类指标的正确性、对拍与边界。

三层验证
--------
1. **手算断言**（``test_hand_*``）：用 6 根手工构造的 K 线，逐点核对已知值。
   期望值在注释里给出**推导过程**（含分数精确值），不是从实现里抄的；
2. **对拍**（``test_parity_*``）：supertrend / adx / mfi / aroon 与「独立参考实现」
   在 60 根样本上逐点对比，误差 < 1e-6。参考实现刻意走**不同代码路径**：
   Wilder 平滑用 ``pandas.ewm(alpha=1/n, adjust=False)``（C 实现），
   生产代码用 Python 显式递推；滚动窗口用 pandas ``rolling`` vs ``sliding_window_view``；
3. **边界**（``test_boundary_*``）：空数组 / 全相同价格 / 长度不足
   —— 不抛异常、不返回 0 占位、``values`` 为空且 ``reason`` 非空。

样本数据（``SAMPLE``，6 根，手工指定）：

    idx   o      h      l      c      v
    0     9.0   10.0    8.0    9.0    100
    1     9.0   12.0   10.0   11.0    200
    2    11.0   11.0    9.0    9.5    300
    3     9.5   13.0   11.0   12.5    400
    4    12.5   12.6   11.5   12.0    500
    5    12.0   12.4   11.8   12.2    600

    TR  = [2, 3, 2, 3.5, 1.1, 0.6]        （tr[0] = h-l，其余 max(h-l, |h-c'|, |l-c'|)）
    hl2 = [9, 11, 10, 12, 12.05, 12.1]
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

# 允许 `pytest tests/test_indicators.py`（不带 -m）也能找到 server 包
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server import indicators as I  # noqa: E402

# ------------------------------------------------------------------
# 样本与工具
# ------------------------------------------------------------------

_RAW = [
    (9.0, 10.0, 8.0, 9.0, 100.0),
    (9.0, 12.0, 10.0, 11.0, 200.0),
    (11.0, 11.0, 9.0, 9.5, 300.0),
    (9.5, 13.0, 11.0, 12.5, 400.0),
    (12.5, 12.6, 11.5, 12.0, 500.0),
    (12.0, 12.4, 11.8, 12.2, 600.0),
]

SAMPLE = [
    {
        "d": f"2026010{i + 1}",
        "o": o, "h": h, "l": l, "c": c, "v": v,
    }
    for i, (o, h, l, c, v) in enumerate(_RAW)
]

# 同一天的 6 根（用于 VWAP 日内累计口径）
SAMPLE_SAME_DAY = [dict(bar, d="20260105") for bar in SAMPLE]

# 跨两天（前 3 根 / 后 3 根），用于 VWAP 日内重置口径
SAMPLE_TWO_DAYS = [
    dict(bar, d=("20260105" if i < 3 else "20260106")) for i, bar in enumerate(SAMPLE)
]

FLAT = [
    {"d": f"2026010{i + 1}", "o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0, "v": 1000.0}
    for i in range(40)
]


def _random_walk(n: int = 60, seed: int = 20261019) -> list:
    """确定性随机游走样本（对拍用）。"""
    rng = np.random.default_rng(seed)
    bars, price = [], 100.0
    for i in range(n):
        o = price
        c = o + float(rng.normal(0, 1.4))
        h = max(o, c) + abs(float(rng.normal(0, 0.5)))
        l = min(o, c) - abs(float(rng.normal(0, 0.5)))
        v = float(rng.integers(500, 3000))
        bars.append({"d": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                     "o": o, "h": h, "l": l, "c": c, "v": v})
        price = c
    return bars


def _assert_close(actual, expected, tol=1e-9, label=""):
    """逐点对比，None 与 None 视为相等。"""
    assert len(actual) == len(expected), f"{label}: 长度 {len(actual)} != {len(expected)}"
    for i, (a, e) in enumerate(zip(actual, expected)):
        if e is None:
            assert a is None, f"{label}[{i}] 期望 None，实得 {a}"
        else:
            assert a is not None, f"{label}[{i}] 期望 {e}，实得 None"
            assert abs(a - e) <= tol, f"{label}[{i}] 期望 {e}，实得 {a}（差 {abs(a - e)}）"


def _nonnull(seq):
    return [v for v in seq if v is not None]


# ------------------------------------------------------------------
# 参考实现（独立代码路径，供对拍；不复用生产代码任何函数）
# ------------------------------------------------------------------


def _ref_ohlc(bars):
    h = np.array([b["h"] for b in bars], dtype=float)
    l = np.array([b["l"] for b in bars], dtype=float)
    c = np.array([b["c"] for b in bars], dtype=float)
    v = np.array([b["v"] for b in bars], dtype=float)
    return h, l, c, v


def _ref_tr(h, l, c):
    tr = np.empty(h.size)
    tr[0] = h[0] - l[0]
    for i in range(1, h.size):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return tr


def _ref_rma(values, period, start=0):
    """Wilder 平滑的 pandas 版本：seed=首窗口均值，之后 ewm(alpha=1/period, adjust=False)。"""
    n = values.size
    out = np.full(n, np.nan)
    seed_at = -1
    for s in range(start, n - period + 1):
        if np.all(np.isfinite(values[s:s + period])):
            seed_at = s
            break
    if seed_at < 0:
        return out
    seed = float(values[seed_at:seed_at + period].mean())
    tail = values[seed_at + period:]
    series = pd.Series(np.concatenate(([seed], tail)))
    out[seed_at + period - 1:] = series.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    return out


def _ref_supertrend(bars, period=10, mult=3.0):
    h, l, c, _ = _ref_ohlc(bars)
    tr = _ref_tr(h, l, c)
    atr_s = _ref_rma(tr, period)
    first = period - 1
    n = c.size
    line = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    for i in range(first, n):
        hl2 = (h[i] + l[i]) / 2.0
        bl, bu = hl2 - mult * atr_s[i], hl2 + mult * atr_s[i]
        if i == first:
            lower[i], upper[i], direction[i] = bl, bu, 1.0
        else:
            lower[i] = max(bl, lower[i - 1]) if c[i - 1] > lower[i - 1] else bl
            upper[i] = min(bu, upper[i - 1]) if c[i - 1] < upper[i - 1] else bu
            if c[i] > upper[i - 1]:
                direction[i] = 1.0
            elif c[i] < lower[i - 1]:
                direction[i] = -1.0
            else:
                direction[i] = direction[i - 1]
        line[i] = lower[i] if direction[i] > 0 else upper[i]
    return line, direction


def _ref_adx(bars, period=14):
    h, l, c, _ = _ref_ohlc(bars)
    tr = _ref_tr(h, l, c)
    up = np.zeros(c.size)
    down = np.zeros(c.size)
    up[1:] = np.diff(h)
    down[1:] = -np.diff(l)
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    atr_s = _ref_rma(tr, period)
    p_s = _ref_rma(pdm, period)
    n_s = _ref_rma(ndm, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * p_s / atr_s
        ndi = 100.0 * n_s / atr_s
        dx = 100.0 * np.abs(pdi - ndi) / (pdi + ndi)
    dx = np.where(np.isfinite(dx), dx, np.nan)
    adx_v = _ref_rma(dx, period, start=period - 1)
    return adx_v, pdi, ndi


def _ref_mfi(bars, period=14):
    h, l, c, v = _ref_ohlc(bars)
    tp = (h + l + c) / 3.0
    mf = tp * np.where(v > 0, v, 0.0)
    pos = pd.Series(np.where(np.r_[np.nan, np.diff(tp)] > 0, mf, 0.0))
    neg = pd.Series(np.where(np.r_[np.nan, np.diff(tp)] < 0, mf, 0.0))
    pos.iloc[0] = np.nan       # 首根无前值，不参与窗口
    neg.iloc[0] = np.nan
    pos_sum = pos.rolling(period).sum().to_numpy()
    neg_sum = neg.rolling(period).sum().to_numpy()
    out = np.full(c.size, np.nan)
    for i in range(c.size):
        ps, ns = pos_sum[i], neg_sum[i]
        if not (np.isfinite(ps) and np.isfinite(ns)):
            continue
        if ns > 0:
            out[i] = 100.0 - 100.0 / (1.0 + ps / ns)
        elif ps > 0:
            out[i] = 100.0
    return out


def _ref_aroon(bars, period=25):
    h, l, _, _ = _ref_ohlc(bars)
    n = h.size
    up = np.full(n, np.nan)
    down = np.full(n, np.nan)
    for i in range(period, n):
        window_h = h[i - period:i + 1]
        window_l = l[i - period:i + 1]
        since_high = period - int(np.argmax(window_h))
        since_low = period - int(np.argmin(window_l))
        up[i] = 100.0 * (period - since_high) / period
        down[i] = 100.0 * (period - since_low) / period
    return up, down


# ==================================================================
# 一、手算断言（已知值）
# ==================================================================


def test_hand_atr():
    """TR=[2,3,2,3.5,1.1,0.6]，period=3：
    atr[2]=mean(2,3,2)=7/3
    atr[3]=7/3+(3.5-7/3)/3=49/18
    atr[4]=49/18+(1.1-49/18)/3=58.9/27
    atr[5]=58.9/27+(0.6-58.9/27)/3=134/81
    """
    out = I.atr(SAMPLE, period=3)["atr"]
    _assert_close(out[:2], [None, None], label="atr.head")
    _assert_close(
        out[2:],
        [7 / 3, 49 / 18, 58.9 / 27, 134 / 81],
        tol=1e-12,
        label="atr.tail",
    )


def test_hand_obv():
    """收盘 9→11↑ →9.5↓ →12.5↑ →12↓ →12.2↑，量 100..600：
    [0, 200, -100, 300, -200, 400]
    """
    out = I.obv(SAMPLE)["obv"]
    _assert_close(out, [0.0, 200.0, -100.0, 300.0, -200.0, 400.0], label="obv")


def test_hand_donchian():
    """高=[10,12,11,13,12.6,12.4]，低=[8,10,9,11,11.5,11.8]，period=3。"""
    out = I.donchian(SAMPLE, period=3)
    _assert_close(out["upper"], [None, None, 12, 13, 13, 13], label="dc.upper")
    _assert_close(out["lower"], [None, None, 8, 9, 9, 11], label="dc.lower")
    _assert_close(out["middle"], [None, None, 10, 11, 11, 12], label="dc.middle")


def test_hand_williams_r():
    """wr=-100*(hh-c)/(hh-ll)，period=3：
    idx2: -(12-9.5)/4*100=-62.5；idx3: -(13-12.5)/4*100=-12.5；idx4: -(13-12)/4*100=-25
    idx5: hh=13, ll=min(11,11.5,11.8)=11 → -(13-12.2)/2*100=-40
    """
    out = I.williams_r(SAMPLE, period=3)["wr"]
    _assert_close(
        out[2:], [-62.5, -12.5, -25.0, -40.0], tol=1e-12, label="wr"
    )


def test_hand_stoch():
    """k=3,d=2：RSV=[_, _, 37.5, 87.5, 75, (12.2-11)/(12.6-11)*100=60]
    D[idx3]=(37.5+87.5)/2=62.5；D[idx4]=(87.5+75)/2=81.25；D[idx5]=(75+60)/2=67.5
    J=3K-2D：idx4=225-162.5=62.5；idx5=180-135=45
    """
    out = I.stoch(SAMPLE, k_period=3, d_period=2)
    _assert_close(out["k"][2:], [37.5, 87.5, 75.0, 60.0], tol=1e-12, label="k")
    _assert_close(out["d"][3:], [62.5, 81.25, 67.5], tol=1e-12, label="d")
    _assert_close(out["j"][4:], [62.5, 45.0], tol=1e-12, label="j")


def test_hand_cci():
    """tp=[9, 11, 59/6, 73/6, 361/30, ...]，period=3。

    手算（分数精确）：
      ma[4] = (59/6 + 73/6 + 361/30)/3 = 1021/90
      dev = [1/9, 7/6, 31/45]（idx2..4）
      md[4] = (1/9+7/6+31/45)/3 = 59/90
      cci[4] = (361/30 - 1021/90) / (0.015 * 59/90) = (62/90)/(0.885/90) = 12400/177
    """
    out = I.cci(SAMPLE, period=3)["cci"]
    _assert_close(out[:4], [None, None, None, None], label="cci.head")
    _assert_close(out[4:5], [12400 / 177], tol=1e-9, label="cci[4]")


def test_hand_mfi():
    """tp=[9, 11, 59/6, 73/6, 361/30, 12.033...]，mf=tp*v，period=3。

    idx4 窗口（2..4）：pos=73/6*400=14600/3，neg=59/6*300 + 361/30*500 = 26900/3
      mfi = 100*pos/(pos+neg) = 100*14600/(14600+26900) = 2920/83
    idx3 窗口（1..3）：pos=2200+14600/3=21200/3，neg=2950 → 21200/(21200+8850)*100
    """
    out = I.mfi(SAMPLE, period=3)["mfi"]
    _assert_close(out[:3], [None, None, None], label="mfi.head")
    _assert_close(out[3:5], [2120000 / 30050, 2920 / 83], tol=1e-9, label="mfi[3:5]")


def test_hand_aroon():
    """period=2（窗口 3 根）：
    idx2: 高窗口[10,12,11] argmax=1 → up=50；低窗口[8,10,9] argmin=0 → down=0
    idx3: 高[12,11,13] argmax=2 → up=100；低[10,9,11] argmin=1 → down=50
    idx4: 高[11,13,12.6] argmax=1 → up=50；低[9,11,11.5] argmin=0 → down=0
    idx5: 高[13,12.6,12.4] argmax=0 → up=0；低[11,11.5,11.8] argmin=0 → down=0
    """
    out = I.aroon(SAMPLE, period=2)
    _assert_close(out["aroon_up"][2:], [50.0, 100.0, 50.0, 0.0], label="aroon_up")
    _assert_close(out["aroon_down"][2:], [0.0, 50.0, 0.0, 0.0], label="aroon_down")
    _assert_close(out["aroon_osc"][2:], [50.0, 50.0, 50.0, 0.0], label="aroon_osc")


def test_hand_supertrend():
    """period=2, mult=1.0（ATR: [_,2.5,2.25,2.875,1.9875,1.29375]）。

    i=1: lower=11-2.5=8.5, upper=13.5, dir=+1, line=8.5
    i=2: c'=11>8.5 → lower=max(7.75,8.5)=8.5；c'=11<13.5 → upper=min(12.25,13.5)=12.25；
         c=9.5 未破 13.5/8.5 → dir 保持 +1 → line=8.5
    i=3: lower=max(9.125,8.5)=9.125；upper=min(14.875,12.25)=12.25；c=12.5>12.25 → dir=+1，line=9.125
    i=4: lower=max(10.0625,9.125)=10.0625；c=12.5 未破 → upper=14.0375；dir=+1，line=10.0625
    i=5: lower=max(10.80625,10.0625)=10.80625；dir=+1，line=10.80625
    """
    out = I.supertrend(SAMPLE, period=2, mult=1.0)
    _assert_close(
        out["st_line"],
        [None, 8.5, 8.5, 9.125, 10.0625, 10.80625],
        tol=1e-12,
        label="st_line",
    )
    _assert_close(out["st_dir"], [None, 1.0, 1.0, 1.0, 1.0, 1.0], label="st_dir")


def test_hand_ichimoku():
    """tenkan=2, kijun=3, senkou_b=4, shift=1。

    tenkan = (max2+min2)/2 → idx1:(12+8)/2=10, idx2:(12+9)/2=10.5, idx3:(13+9)/2=11,
                                idx4:(13+11)/2=12, idx5:(12.6+11.5)/2=12.05
    kijun  = (max3+min3)/2 → idx2:10, idx3:11, idx4:11, idx5:12
    senkou_a[i] = ((tenkan+kijun)/2)[i-1] → idx3:10.25, idx4:11, idx5:11.5
    senkou_b[i] = ((max4+min4)/2)[i-1]     → idx4:10.5,  idx5:11
    chikou[i]   = close[i+1]               → idx0:11 … idx4:12.2, idx5:None
    """
    out = I.ichimoku(SAMPLE, tenkan=2, kijun=3, senkou_b=4, shift=1)
    _assert_close(out["tenkan"][1:], [10.0, 10.5, 11.0, 12.0, 12.05], label="tenkan")
    _assert_close(out["kijun"][2:], [10.0, 11.0, 11.0, 12.0], label="kijun")
    _assert_close(out["senkou_a"][3:], [10.25, 11.0, 11.5], label="senkou_a")
    _assert_close(out["senkou_b"][4:], [10.5, 11.0], label="senkou_b")
    _assert_close(
        out["chikou"][:5], [11.0, 9.5, 12.5, 12.0, 12.2], label="chikou"
    )
    assert out["chikou"][5] is None


def test_hand_keltner():
    """EMA(3) 以首值为种子 alpha=0.5：9, 10, 9.75, 11.125, 11.5625, 11.88125
    ATR(3): 7/3, 49/18, 58.9/27, 134/81
    upper[5] = 11.88125 + 2*134/81，lower[5] = 11.88125 - 2*134/81
    """
    out = I.keltner(SAMPLE, period=3, mult=2.0, atr_period=3)
    _assert_close(
        out["middle"],
        [9.0, 10.0, 9.75, 11.125, 11.5625, 11.88125],
        tol=1e-12,
        label="kc.middle",
    )
    atr5 = 134 / 81
    _assert_close([out["upper"][5]], [11.88125 + 2 * atr5], tol=1e-12, label="kc.upper")
    _assert_close([out["lower"][5]], [11.88125 - 2 * atr5], tol=1e-12, label="kc.lower")
    assert out["upper"][1] is None        # ATR 未成形


def test_hand_vwap():
    """tp=[9, 11, 59/6, 73/6, 361/30, 361/30]，量 100..600，累计量额：
    9.0；3100/300=31/3；6050/600=121/12；10916.67/1000=131/12；50800/3/1500=508/45
    """
    out = I.vwap(SAMPLE_SAME_DAY)["vwap"]
    _assert_close(
        out[:5],
        [9.0, 31 / 3, 121 / 12, 131 / 12, 508 / 45],
        tol=1e-9,
        label="vwap.daily",
    )


def test_hand_vwap_resets_per_session():
    """跨日样本：第 4 根（新交易日首根）重新累计 -> vwap == 该根典型价。"""
    out = I.vwap(SAMPLE_TWO_DAYS)["vwap"]
    tp3 = (13.0 + 11.0 + 12.5) / 3.0
    _assert_close([out[3]], [tp3], tol=1e-12, label="vwap.reset")
    # 前 3 根仍在同一日内累计
    _assert_close(out[:3], [9.0, 31 / 3, 121 / 12], tol=1e-9, label="vwap.day1")


def test_hand_choppiness():
    """period=3：chop = 100*log10(ΣTR/(hh-ll))/log10(3)
    idx2: ΣTR=7, hh-ll=12-8=4 → 100*log10(1.75)/log10(3)
    """
    out = I.choppiness(SAMPLE, period=3)["chop"]
    expected = 100.0 * math.log10(1.75) / math.log10(3.0)
    _assert_close([out[2]], [expected], tol=1e-12, label="chop")


def test_hand_adx_and_di():
    """period=2。

    h=[10,12,11,13,12.6,12.4] l=[8,10,9,11,11.5,11.8]
    up = diff(h)      = [2, -1, 2, -0.4, -0.2]    （对应 i=1..5）
    down = -diff(l)   = [-2, 1, -2, -0.5, -0.3]
    +DM = [0, 2, 0, 2, 0, 0]；-DM = [0, 0, 1, 0, 0, 0]
    ATR(RMA2)      = [_, 2.5, 2.25, 2.875, 1.9875, 1.29375]
    +DM 平滑(RMA2) = [_, 1, 0.5, 1.25, 0.625, 0.3125]
    -DM 平滑(RMA2) = [_, 0, 0.5, 0.25, 0.125, 0.0625]
    +DI: idx1=100*1/2.5=40；idx2=100*0.5/2.25=200/9；idx5=100*0.3125/1.29375
    -DI: idx2=200/9；idx3=100*0.25/2.875；idx5=100*0.0625/1.29375
    DX:  idx1=100（-DI=0）；idx2=0（两线相等）；idx3..5=200/3（差值/和=2/3）
    ADX(RMA2, seed=mean(DX1,DX2)=50)：idx2=50, idx3=175/3, idx4=62.5, idx5=775/12
    """
    out = I.adx(SAMPLE, period=2)
    _assert_close(
        out["plus_di"][1:],
        [40.0, 200 / 9, 100 * 1.25 / 2.875, 100 * 0.625 / 1.9875, 100 * 0.3125 / 1.29375],
        tol=1e-9,
        label="plus_di",
    )
    _assert_close(
        out["minus_di"][1:],
        [0.0, 200 / 9, 100 * 0.25 / 2.875, 100 * 0.125 / 1.9875, 100 * 0.0625 / 1.29375],
        tol=1e-9,
        label="minus_di",
    )
    _assert_close(
        out["adx"][2:], [50.0, 175 / 3, 62.5, 775 / 12], tol=1e-9, label="adx"
    )
    _assert_close(I.di(SAMPLE, period=2)["plus_di"], out["plus_di"], label="di==adx.pdi")


# ==================================================================
# 二、对拍（独立参考实现，≥30 根，误差 < 1e-6）
# ==================================================================

BARS60 = _random_walk(60)
TOL = 1e-6


def _cmp(prod, ref, label):
    """dev 序列 vs 参考序列：非 None 数量一致，逐点误差 < TOL。"""
    pairs = [(p, r) for p, r in zip(prod, ref) if p is not None and np.isfinite(r)]
    assert len(pairs) >= 30, f"{label}: 可比样本仅 {len(pairs)} 个（要求 ≥30）"
    diff = max(abs(p - float(r)) for p, r in pairs)
    assert diff < TOL, f"{label}: 最大误差 {diff:.3e} >= {TOL}"
    return diff


def test_parity_supertrend():
    ref_line, ref_dir = _ref_supertrend(BARS60, period=10, mult=3.0)
    got = I.supertrend(BARS60, period=10, mult=3.0)
    assert _cmp(got["st_line"], ref_line, "supertrend.line") < TOL
    assert _cmp(got["st_dir"], ref_dir, "supertrend.dir") < TOL
    # 方向只允许 +1/-1
    assert set(_nonnull(got["st_dir"])) <= {1.0, -1.0}


def test_parity_adx():
    ref_adx, ref_pdi, ref_ndi = _ref_adx(BARS60, period=14)
    got = I.adx(BARS60, period=14)
    _cmp(got["adx"], ref_adx, "adx")
    _cmp(got["plus_di"], ref_pdi, "plus_di")
    _cmp(got["minus_di"], ref_ndi, "minus_di")


def test_parity_mfi():
    ref = _ref_mfi(BARS60, period=14)
    got = I.mfi(BARS60, period=14)["mfi"]
    _cmp(got, ref, "mfi")


def test_parity_aroon():
    ref_up, ref_down = _ref_aroon(BARS60, period=25)
    got = I.aroon(BARS60, period=25)
    _cmp(got["aroon_up"], ref_up, "aroon_up")
    _cmp(got["aroon_down"], ref_down, "aroon_down")


def test_parity_extra_atr_and_cci():
    """额外对拍：ATR 与 CCI 用 pandas rolling 参考实现交叉验证（100 根保证 ≥30 可比点）。"""
    bars = _random_walk(100, seed=77)
    h, l, c, _ = _ref_ohlc(bars)
    tr = _ref_tr(h, l, c)
    ref_atr = _ref_rma(tr, 14)
    _cmp(I.atr(bars, period=14)["atr"], ref_atr, "atr")

    tp = pd.Series((h + l + c) / 3.0)
    ma = tp.rolling(20).mean()
    md = (tp - ma).abs().rolling(20).mean()
    ref_cci = ((tp - ma) / (0.015 * md)).to_numpy()
    _cmp(I.cci(bars, period=20)["cci"], ref_cci, "cci")


# ==================================================================
# 三、边界：空 / 全相同价格 / 长度不足 —— 不抛异常、不返回 0
# ==================================================================


ALL_NAMES = list(I.INDICATOR_NAMES) + ["choppiness", "williams_r", "di"]


@pytest.mark.parametrize("name", ALL_NAMES)
@pytest.mark.parametrize(
    "payload",
    [None, [], {}, np.zeros((0, 5))],
    ids=["none", "empty_list", "empty_dict", "empty_ndarray"],
)
def test_boundary_no_crash_and_no_zero_fill(name, payload):
    """空输入：不抛异常，且 values 为空（绝不补 0 当成功）。"""
    res = I.compute(name, payload)
    assert res["values"] == [], f"{name}: 空输入应返回空序列，实得 {res['values'][:5]}"
    assert res["series"] in ({}, {p: [] for p in res["panes"]})
    assert res["reason"], f"{name}: 空输入必须给 reason"


@pytest.mark.parametrize("name", ALL_NAMES)
def test_boundary_single_bar_never_crashes(name):
    """单根 K 线：不抛异常；除 min_bars==1 的 vwap 外全为空序列。"""
    res = I.compute(name, SAMPLE[:1])
    assert isinstance(res["values"], list)
    if res["min_bars"] > 1:
        assert res["values"] == []
        assert res["reason"]
    else:
        # vwap(1 根) 的累计均价 == 该根典型价，这是真实值，不是占位
        assert len(res["values"]) == 1


@pytest.mark.parametrize(
    "name",
    ["stoch", "cci", "williams_r", "choppiness", "mfi", "adx"],
)
def test_boundary_flat_price_denominator_zero_is_none(name):
    """全相同价格 → 分母为 0 的振荡指标必须给空序列 + reason（不写 0/50 占位）。"""
    res = I.compute(name, FLAT)
    assert res["values"] == [], f"{name}: 横盘应无有效值，实得 {res['values'][:5]}"
    assert res["reason"]


@pytest.mark.parametrize("name", ALL_NAMES)
def test_boundary_flat_price_no_exception(name):
    """全相同价格：不抛异常；有值时的长度必须等于根数（允许 0 为定义值，如 ATR=0）。"""
    res = I.compute(name, FLAT)
    assert isinstance(res["values"], list)
    if res["values"]:
        assert len(res["values"]) == len(FLAT), f"{name}: 横盘序列长度异常"
        assert all(v is None or math.isfinite(v) for v in res["values"])
    for pane, seq in res["series"].items():
        assert all(v is None or math.isfinite(v) for v in seq), f"{name}.{pane} 含非有限值"


@pytest.mark.parametrize("name", ALL_NAMES)
def test_boundary_insufficient_length(name):
    """长度不足 min_bars：values 为空 + reason（不是等长 NaN/0 数组）。"""
    res = I.compute(name, BARS60)
    need = res["min_bars"]
    assert need >= 1
    short = I.compute(name, BARS60[: max(0, need - 1)])
    assert short["values"] == []
    assert short["reason"], f"{name}: 数据不足必须给 reason"


@pytest.mark.parametrize("name", ALL_NAMES)
def test_series_length_equals_bars(name):
    """正常样本：每个 pane 序列长度严格等于 K 线根数。"""
    res = I.compute(name, BARS60)
    assert res["reason"] is None, f"{name}: 60 根样本不应失败（{res['reason']}）"
    assert len(res["values"]) == len(BARS60)
    assert set(res["series"].keys()) == set(res["panes"])
    for pane, seq in res["series"].items():
        assert len(seq) == len(BARS60), f"{name}.{pane} 长度 {len(seq)} != {len(BARS60)}"
        assert all(v is None or math.isfinite(v) for v in seq)


# ==================================================================
# 四、注册表 / 契约
# ==================================================================


def test_registry_has_12_primary_and_overlay_flags():
    assert len(I.INDICATOR_NAMES) == 12
    for name in I.INDICATOR_NAMES:
        assert name in I.INDICATOR_REGISTRY, f"注册表缺 {name}"
    overlay_expected = {"supertrend", "ichimoku", "donchian", "keltner", "vwap"}
    for name, spec in I.INDICATOR_REGISTRY.items():
        assert spec.overlay is (name in overlay_expected), f"{name}.overlay 与契约不符"
        assert spec.primary in spec.panes, f"{name}.primary 不在 panes 里"


def test_registry_params_schema_complete():
    """每个参数的 schema 必须含 type/min/max/default/unit/desc 五要素且在合法区间。"""
    for name, spec in I.INDICATOR_REGISTRY.items():
        for p in spec.params:
            d = p.as_dict()
            for key in ("type", "min", "max", "default", "unit", "desc"):
                assert key in d, f"{name}.{p.name} 缺 {key}"
            assert d["type"] in ("int", "float")
            assert d["min"] <= d["default"] <= d["max"], f"{name}.{p.name} 默认值越界"
            assert d["desc"].strip(), f"{name}.{p.name} 描述为空"
    # 5 个 overlay 指标已由上一个用例断言


def test_alias_and_catalog():
    assert I.resolve_name("kc") == "keltner"
    assert I.resolve_name("chop") == "choppiness"
    assert I.resolve_name("wr") == "williams_r"
    assert I.resolve_name("kdj") == "stoch"
    assert I.resolve_name("不存在") is None
    catalog = I.list_registry()
    assert len(catalog) >= 15
    assert sum(1 for i in catalog if i["overlay"]) >= 5


def test_params_normalization_clamps_and_defaults():
    spec = I.INDICATOR_REGISTRY["adx"]
    assert spec.normalize_params()["period"] == 14
    assert spec.normalize_params({"period": "999"})["period"] == 200      # 夹到上限
    assert spec.normalize_params({"period": "-5"})["period"] == 2         # 夹到下限
    assert spec.normalize_params({"period": "abc"})["period"] == 14       # 非法 -> 默认
    assert spec.normalize_params(positional=[7])["period"] == 7
    st = I.INDICATOR_REGISTRY["supertrend"]
    got = st.normalize_params(positional=[12, 2.5])
    assert got == {"period": 12, "mult": 2.5}
    assert st.normalize_params(positional=[12])["mult"] == 3.0            # 缺省保留默认


def test_unknown_indicator_flagged():
    res = I.compute("no_such_indicator", BARS60)
    assert res["unresolved"] is True
    assert res["values"] == []
    assert "未知指标名" in res["reason"]


def test_input_forms_equivalent():
    """list[dict] / dict[list] / ndarray 三种入参结果一致。"""
    as_dict = {
        "o": [b["o"] for b in BARS60],
        "h": [b["h"] for b in BARS60],
        "l": [b["l"] for b in BARS60],
        "c": [b["c"] for b in BARS60],
        "v": [b["v"] for b in BARS60],
    }
    as_arr = np.array([[b["o"], b["h"], b["l"], b["c"], b["v"]] for b in BARS60])
    base = I.compute("adx", BARS60)["series"]["adx"]
    for alt in (as_dict, as_arr):
        alt_vals = I.compute("adx", alt)["series"]["adx"]
        _assert_close(alt_vals, base, tol=1e-12, label="input_form")


# ==================================================================
# 五、端点层契约（纯函数 _indicator_payload，不打网络）
# ==================================================================


def _payload(names, params=None, klines=None, limit=250):
    from server.routers.kline import _indicator_payload

    return _indicator_payload(
        "sh600519", "day", limit, names, params, BARS60 if klines is None else klines
    )


def test_payload_contract_shape():
    payload = _payload(["adx", "mfi"])
    assert payload["ok"] is True
    assert payload["message"] == "ok"
    assert payload["source"] and payload["as_of"]
    data = payload["data"]
    assert data["bars"] == len(BARS60)
    for name in ("adx", "mfi"):
        item = data["series"][name]
        assert len(item["values"]) == len(BARS60)
        assert item["overlay"] is False
        assert item["params"], f"{name}: params 不能为空"
        assert item["params_schema"], f"{name}: params_schema 不能为空"
        assert item["panes"]
    # overlay 契约
    overlay_payload = _payload(["supertrend", "ichimoku", "donchian", "keltner", "vwap"])
    for name in ("supertrend", "ichimoku", "donchian", "keltner", "vwap"):
        assert overlay_payload["data"]["series"][name]["overlay"] is True


def test_payload_insufficient_is_not_success():
    """数据不足（limit=5 对应 5 根）→ ok:false + reason，不是空数组当成功。"""
    payload = _payload(["adx"], klines=BARS60[:5], limit=5)
    assert payload["ok"] is False
    assert payload["degraded"] is True
    item = payload["data"]["series"]["adx"]
    assert item["values"] == []
    assert item["reason"] and "需要" in item["reason"]
    assert payload["message"]


def test_payload_unknown_name():
    payload = _payload(["adx", "bogus"])
    assert payload["ok"] is False
    assert "bogus" in payload["data"]["failed"]


def test_payload_params_positional():
    payload = _payload(["adx"], params="7")
    assert payload["data"]["series"]["adx"]["params"]["period"] == 7
    # 参数个数不吻合 -> 用默认值并记录被忽略
    payload2 = _payload(["supertrend"], params="7")
    assert payload2["data"]["series"]["supertrend"]["params"]["period"] == 10
    assert "supertrend" in payload2["data"]["params_ignored"]


def test_payload_all_expands_to_12():
    from server.routers.kline import _expand_names

    expanded = _expand_names(["all"])
    assert len(expanded) == 12
    payload = _payload(expanded)
    assert len(payload["data"]["series"]) == 12
    assert payload["ok"] is True, payload["message"]
