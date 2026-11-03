"""统计工具：排名、Pearson/Spearman、IC 汇总、分位表、面板装配"""

from __future__ import annotations
import logging
import math
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np
from server.cache import cache
from server.chan_engine import chan_engine
from server.tencent_kline import fetch_tencent_kline
from server.core.numbers import safe_float as _finite

from server.qlib_factor_lib import (
    _AVAILABLE_IDS,
    _FACTOR_COMPUTE,
    _FACTOR_DIR,
    _FORWARD_DAYS,
    _KLINE_LIMIT,
    _MIN_BACKTEST_PERIODS,
    _MIN_CROSS_SECTION,
    _MIN_IC_PERIODS,
    _MIN_QUANT_SAMPLES,
    _UNIVERSE_LIQUID,
    _UNIVERSE_MOVERS,
    logger,
)

_DEFAULT_SELECTED = ["Alpha_Mom_20", "Alpha_Vol_Std", "Alpha_Liq_Turnover"]
_DEFAULT_TOP_K = 8
_DEFAULT_START_DATE = "2024-01-01"


def computable_factor_ids() -> List[str]:
    """返回引擎实际可算出非 null 值的因子 id 列表（供一致性交叉验证）。"""
    return list(_AVAILABLE_IDS)


# ============================================================
# 统计工具（无 scipy 依赖）
# ============================================================

def _rankdata(a: np.ndarray) -> np.ndarray:
    """平均秩（处理并列）。"""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    sa = a[order]
    i = 0
    m = len(sa)
    while i < m:
        j = i
        while j + 1 < m and sa[j + 1] == sa[i]:
            j += 1
        if j > i:
            avg = (i + j + 2) / 2.0  # 秩 i+1 .. j+1 的平均
            ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks


def _pearson(x: np.ndarray, y: np.ndarray) -> Tuple[Optional[float], int]:
    """截面 Pearson 相关系数（自动掩掉 NaN），样本不足或方差为 0 返回 (None, n)。"""
    m = np.isfinite(x) & np.isfinite(y)
    cnt = int(m.sum())
    if cnt < 3:
        return None, cnt
    xv = x[m]
    yv = y[m]
    if float(np.std(xv)) < 1e-12 or float(np.std(yv)) < 1e-12:
        return None, cnt
    r = float(np.corrcoef(xv, yv)[0, 1])
    return (r if np.isfinite(r) else None), cnt


def _spearman(x: np.ndarray, y: np.ndarray) -> Tuple[Optional[float], int]:
    """截面 Spearman（rank）相关系数。"""
    m = np.isfinite(x) & np.isfinite(y)
    cnt = int(m.sum())
    if cnt < 3:
        return None, cnt
    xr = _rankdata(x[m])
    yr = _rankdata(y[m])
    if float(np.std(xr)) < 1e-12 or float(np.std(yr)) < 1e-12:
        return None, cnt
    r = float(np.corrcoef(xr, yr)[0, 1])
    return (r if np.isfinite(r) else None), cnt


def _summarize_ic(series: List[float]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """由 IC 时间序列计算 (mean, IR, reason)。样本不足返回 (None, None, reason)。"""
    vals = [v for v in series if v is not None and np.isfinite(v)]
    if len(vals) < _MIN_IC_PERIODS:
        return None, None, "insufficient_samples"
    mean = float(np.mean(vals))
    if len(vals) < 2:
        return round(mean, 6), None, "insufficient_samples"
    std = float(np.std(vals, ddof=1))
    if std < 1e-12:
        return round(mean, 6), None, "zero_variance"
    return round(mean, 6), round(mean / std, 4), None


def _quantile_table(fmat: np.ndarray, fwd: np.ndarray,
                    n_groups: int = 5) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """按因子值排序分 n_groups 组，返回各组样本数 / 因子值区间 / 组内未来收益均值。

    Q1 = 因子值最低组，Q5 = 因子值最高组（真实分组，非写死比例）。
    """
    xs: List[float] = []
    ys: List[float] = []
    T, N = fmat.shape
    for t in range(T):
        for i in range(N):
            a = fmat[t, i]
            b = fwd[t, i]
            if np.isfinite(a) and np.isfinite(b):
                xs.append(float(a))
                ys.append(float(b))
    total = len(xs)
    if total < _MIN_QUANT_SAMPLES:
        return None, "insufficient_samples"
    order = np.argsort(np.asarray(xs), kind="mergesort")
    xs_s = [xs[k] for k in order]
    ys_s = [ys[k] for k in order]
    groups: List[Dict[str, Any]] = []
    for g in range(n_groups):
        lo = int(round(g * total / n_groups))
        hi = int(round((g + 1) * total / n_groups))
        segx = xs_s[lo:hi]
        segy = ys_s[lo:hi]
        if not segy:
            groups.append({"group": f"Q{g + 1}", "sample_count": 0,
                           "factor_low": None, "factor_high": None,
                           "mean_return": None, "annual_return": None})
            continue
        mean_ret = float(np.mean(segy))
        ann = (1.0 + mean_ret) ** (252.0 / _FORWARD_DAYS) - 1.0
        groups.append({
            "group": f"Q{g + 1}",
            "sample_count": len(segy),
            "factor_low": round(segx[0], 6),
            "factor_high": round(segx[-1], 6),
            "mean_return": round(mean_ret * 100, 4),
            "annual_return": round(ann * 100, 2),
        })
    return groups, None


# ============================================================
# 数据获取
# ============================================================

async def _dashboard_quotes() -> List[Dict[str, Any]]:
    """复用大盘纵览缓存的真实行情（避免重复抓取全样本）。"""
    try:
        from server.services.market_dashboard import build_dashboard as _build_dashboard, CACHE_KEY, CACHE_TTL

        async def fetch():
            d = await _build_dashboard()
            return d or {}

        dash = await cache.get_or_fetch(CACHE_KEY, fetch, CACHE_TTL)
        return (dash or {}).get("quotes") or []
    except Exception as exc:
        logger.warning("dynamic factor: dashboard quotes unavailable: %s", exc)
        return []


def _universe(quotes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从真实行情中选取动态选股域：成交额 Top + 涨跌异动 Top（去重）。"""
    valid = [q for q in quotes if _finite(q.get("price")) > 0]
    if not valid:
        return []
    liquid = sorted(valid, key=lambda q: _finite(q.get("amount")), reverse=True)[:_UNIVERSE_LIQUID]
    movers = sorted(valid, key=lambda q: abs(_finite(q.get("chg"))), reverse=True)[:_UNIVERSE_MOVERS]
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for q in liquid + movers:
        code = q.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(q)
    return out


async def _kline_cached(code: str) -> List[Dict[str, Any]]:
    ck = f"dynamic_factor:kline:{code}"
    cached = cache.get(ck)
    if cached:
        return cached
    try:
        ks = await fetch_tencent_kline(code, period="day", limit=_KLINE_LIMIT)
    except Exception:
        ks = []
    rows = [k for k in (ks or []) if isinstance(k, dict) and _finite(k.get("c")) > 0]
    if rows:
        cache.set(ck, rows, 3600)
    return rows


async def _load_universe() -> List[Dict[str, Any]]:
    """加载动态选股域（行情 + 真实日K）。"""
    quotes = await _dashboard_quotes()
    uni = _universe(quotes)
    out: List[Dict[str, Any]] = []
    for q in uni:
        code = q.get("code")
        ks = await _kline_cached(code)
        if not ks:
            continue
        out.append({
            "code": code,
            "name": q.get("name") or code,
            "price": _finite(q.get("price")),
            "chg": _finite(q.get("chg")),
            "amount": _finite(q.get("amount")),
            "klines": ks,
        })
    return out


def _market_returns(bench_klines: List[Dict[str, Any]]) -> Dict[str, float]:
    """由基准日K构造 date -> 日收益 的映射。"""
    out: Dict[str, float] = {}
    prev = None
    for k in bench_klines or []:
        d = str(k.get("d"))
        c = _finite(k.get("c"))
        if prev is not None and prev > 0 and c > 0:
            out[d] = c / prev - 1.0
        prev = c
    return out


def _benchmark_closes(bench_klines: List[Dict[str, Any]]) -> Dict[str, float]:
    """由基准日K构造 date -> 收盘 的映射。"""
    out: Dict[str, float] = {}
    for k in bench_klines or []:
        d = str(k.get("d"))
        c = _finite(k.get("c"))
        if c > 0:
            out[d] = c
    return out


# ============================================================
# 截面面板
# ============================================================

class _Panel:
    """对齐后的因子截面面板。"""

    def __init__(self) -> None:
        self.dates: List[str] = []
        self.codes: List[str] = []
        self.factors: Dict[str, np.ndarray] = {}   # fid -> (T, N)
        self.forward: np.ndarray = np.zeros((0, 0))  # (T, N) 下一期收益
        self.reason: Optional[str] = None


def _build_panel(stocks: List[Dict[str, Any]],
                 mret: Dict[str, float]) -> _Panel:
    """由逐只真实日K构造对齐的截面面板（公用交易日 = 各股日期交集）。"""
    panel = _Panel()
    if len(stocks) < _MIN_CROSS_SECTION:
        panel.reason = "universe_too_small"
        return panel

    idx_by_code: List[Dict[str, int]] = []
    date_sets: List[set] = []
    for st in stocks:
        dmap: Dict[str, int] = {}
        for i, k in enumerate(st["klines"]):
            ds = str(k.get("d"))
            if ds and ds not in dmap:
                dmap[ds] = i
        idx_by_code.append(dmap)
        date_sets.append(set(dmap.keys()))

    if not date_sets:
        panel.reason = "no_data"
        return panel
    common = sorted(set.intersection(*date_sets))
    if len(common) < _MIN_BACKTEST_PERIODS + 1:
        panel.reason = "insufficient_samples"
        panel.dates = common
        panel.codes = [st["code"] for st in stocks]
        return panel

    T = len(common)
    N = len(stocks)
    factor_mats: Dict[str, np.ndarray] = {fid: np.full((T, N), np.nan) for fid in _AVAILABLE_IDS}
    fwd = np.full((T, N), np.nan)

    for i, st in enumerate(stocks):
        ks = st["klines"]
        closes = np.array([_finite(k.get("c")) for k in ks], dtype=float)
        vols = np.array([_finite(k.get("v")) for k in ks], dtype=float)
        dates = [str(k.get("d")) for k in ks]
        series: Dict[str, np.ndarray] = {}
        for fid, fn in _FACTOR_COMPUTE.items():
            try:
                series[fid] = fn(closes, vols, dates, mret)
            except Exception as exc:  # pragma: no cover - 防御性
                logger.debug("factor %s compute failed for %s: %s", fid, st["code"], exc)
                series[fid] = np.full(len(closes), np.nan)
        dmap = idx_by_code[i]
        for t, d in enumerate(common):
            idx = dmap.get(d)
            if idx is None:
                continue
            for fid in _AVAILABLE_IDS:
                arr = series.get(fid)
                if arr is not None and idx < len(arr):
                    factor_mats[fid][t, i] = arr[idx]
            # 前瞻收益：由该股自身序列的下一根K线计算
            if idx + 1 < len(closes) and closes[idx] > 0:
                nxt = closes[idx + 1]
                if nxt > 0:
                    fwd[t, i] = nxt / closes[idx] - 1.0

    panel.dates = common
    panel.codes = [st["code"] for st in stocks]
    panel.factors = factor_mats
    panel.forward = fwd
    return panel


def _zscores(row: np.ndarray) -> np.ndarray:
    """截面 z-score（NaN 原样保留）。"""
    m = np.isfinite(row)
    out = np.full(len(row), np.nan)
    if int(m.sum()) < 2:
        return out
    vals = row[m]
    mu = float(np.mean(vals))
    sd = float(np.std(vals))
    if sd < 1e-12:
        out[m] = 0.0
    else:
        out[m] = (vals - mu) / sd
    return out


def _composite_matrix(panel: _Panel, used: List[str],
                      norm_w: Dict[str, float]) -> np.ndarray:
    """构建综合因子截面矩阵：(T, N)，每期为各因子 z-score 的加权和（方向已并入）。"""
    T = len(panel.dates)
    N = len(panel.codes)
    comp = np.full((T, N), np.nan)
    for t in range(T):
        acc = np.zeros(N)
        cnt = np.zeros(N)
        for fid in used:
            row = panel.factors[fid][t, :]
            z = _zscores(row)
            d = _FACTOR_DIR.get(fid, 1.0)
            mask = np.isfinite(z)
            acc[mask] += norm_w[fid] * d * z[mask]
            cnt[mask] += norm_w[fid]
        valid = cnt > 0
        comp[t, valid] = acc[valid] / cnt[valid]
    return comp
