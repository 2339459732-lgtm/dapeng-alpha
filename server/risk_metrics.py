# -*- coding: utf-8 -*-
"""组合风险指标（T04，纯 numpy，无新增依赖）。

设计纪律（对齐 `docs/10-architecture/openbb-dsa-integration-design.md` §3.7 / §6.2 T04）：
- **样本不足就是 null**：不给 0、不用短样本硬算、不给"看起来合理"的默认值；
- 每个函数都返回 ``{"value"... , "samples": n, "reason": str|None}``，失败不抛异常；
- 年化口径统一 **252 交易日**，并在返回值里显式标注 ``annualized``；
- 夏普明确 ``rf_annual``（默认 0.0）并附 ``note``，前端 tooltip 可直接展示口径。

单位约定：
- 收益率序列为**小数日收益**（0.0123 = +1.23%）；
- 百分比类输出（``idio_vol_pct`` / ``volatility_pct`` / ``cvar_95_pct`` 等）已 ×100。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: 年化交易日数（A 股约 242–244，行业惯例统一取 252）
TRADING_DAYS = 252
#: 统计量最小样本（60 个交易日 ≈ 3 个月）
MIN_SAMPLES = 60


# ----------------------------------------------------------------------
#  工具
# ----------------------------------------------------------------------
def _to_array(values: Optional[Iterable[Any]]) -> np.ndarray:
    """转成干净的 float 数组（丢弃 None/NaN/±inf）。"""
    if values is None:
        return np.array([], dtype=float)
    out: List[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return np.array(out, dtype=float)


def _insufficient(samples: int, required: int, reason: Optional[str] = None,
                  **extra: Any) -> Dict[str, Any]:
    """统计量不可用的标准返回：value=null + 明确 reason（样本不足或分布退化）。"""
    payload: Dict[str, Any] = {
        "value": None,
        "samples": int(samples),
        "sample_required": int(required),
        "reason": reason or f"样本 {int(samples)}/{int(required)}，不足",
    }
    payload.update(extra)
    return payload


def _std(a: np.ndarray) -> float:
    """样本标准差（ddof=1）；长度 <2 返回 0。"""
    if a.size < 2:
        return 0.0
    return float(np.std(a, ddof=1))


# ----------------------------------------------------------------------
#  β / R² / 特质风险（§3.7 核心）
# ----------------------------------------------------------------------
def beta(returns: Sequence[float], bench_returns: Sequence[float],
         min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """对基准做 OLS 回归，返回 β / R² / 年化特质波动 / 年化 α。

    Args:
        returns: 组合（或个股）日收益序列（小数）。
        bench_returns: 基准日收益序列（小数），与 ``returns`` 等长对齐。
        min_samples: 最小样本数，默认 60。

    Returns:
        ``{beta, alpha_annual, r2, idio_vol_pct, total_risk_pct, systematic_pct,
        samples, sample_required, regression{intercept, slope, stderr}, reason}``。
        样本不足或基准无波动时，统计值全为 ``None`` + ``reason``。
    """
    r = _to_array(returns)
    b = _to_array(bench_returns)
    n = int(min(r.size, b.size))
    base = {
        "beta": None, "alpha_annual": None, "r2": None,
        "idio_vol_pct": None, "total_risk_pct": None, "systematic_pct": None,
        "samples": n, "sample_required": int(min_samples),
        "regression": {"intercept": None, "slope": None, "stderr": None},
        "reason": None,
    }
    if n < min_samples:
        base["reason"] = f"样本 {n}/{int(min_samples)}"
        return base

    r = r[:n]
    b = b[:n]
    var_b = float(np.var(b, ddof=1))
    var_r = float(np.var(r, ddof=1))
    if var_b <= 0.0:
        base["reason"] = "基准收益方差为 0（样本无波动），无法回归"
        return base

    cov = float(np.cov(r, b, ddof=1)[0, 1])
    slope = cov / var_b
    intercept = float(np.mean(r) - slope * np.mean(b))
    resid = r - (intercept + slope * b)
    var_resid = float(np.var(resid, ddof=1))

    total_risk = math.sqrt(var_r) * math.sqrt(TRADING_DAYS) * 100.0
    idio_risk = math.sqrt(max(0.0, var_resid)) * math.sqrt(TRADING_DAYS) * 100.0
    r2 = None if var_r <= 0 else max(0.0, min(1.0, 1.0 - var_resid / var_r))
    systematic_pct = None if total_risk <= 0 or r2 is None else round(r2 * 100.0, 2)

    # 斜率标准误：SE = sqrt(σ²_resid / ((n-2)·var_b))
    stderr = None
    if n > 2 and var_b > 0:
        stderr = math.sqrt(var_resid / ((n - 2) * var_b))

    return {
        "beta": round(slope, 4),
        "alpha_annual": round(intercept * TRADING_DAYS * 100.0, 4),
        "r2": None if r2 is None else round(r2, 4),
        "idio_vol_pct": round(idio_risk, 4),
        "total_risk_pct": round(total_risk, 4),
        "systematic_pct": systematic_pct,
        "samples": n,
        "sample_required": int(min_samples),
        "regression": {
            "intercept": round(intercept, 8),
            "slope": round(slope, 6),
            "stderr": None if stderr is None else round(stderr, 6),
        },
        "reason": None,
    }


# ----------------------------------------------------------------------
#  收益/风险调整指标
# ----------------------------------------------------------------------
def sharpe(returns: Sequence[float], rf: float = 0.0,
           min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """夏普比率（年化）。``rf`` 为**年化**无风险利率（小数），默认 0.0。

    Returns:
        ``{value, samples, sample_required, rf_annual, annualized, note, reason}``
    """
    r = _to_array(returns)
    n = int(r.size)
    meta = {"rf_annual": float(rf), "annualized": True, "trading_days": TRADING_DAYS}
    if n < min_samples:
        return _insufficient(n, min_samples, **meta,
                             note=f"无风险利率取 {rf * 100:g}%（rf_annual={rf}）；样本不足不计算")
    sd = _std(r)
    if sd <= 1e-12:          # 浮点噪声也算无波动（例如整段收益恒为同一值）
        return _insufficient(n, min_samples, reason="收益序列无波动（标准差为 0），夏普无定义", **meta,
                             note=f"无风险利率取 {rf * 100:g}%（rf_annual={rf}）；收益序列无波动")
    excess = r - rf / TRADING_DAYS
    value = float(np.mean(excess) / sd * math.sqrt(TRADING_DAYS))
    return {
        "value": round(value, 4),
        "samples": n,
        "sample_required": int(min_samples),
        "annualized": True,
        "trading_days": TRADING_DAYS,
        "rf_annual": float(rf),
        "note": f"无风险利率取 {rf * 100:g}%（rf_annual={rf}）；按 {TRADING_DAYS} 交易日年化",
        "reason": None,
    }


def sortino(returns: Sequence[float], target: float = 0.0,
            min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """索提诺比率（年化）：超额收益 / 下行波动（仅统计低于 target 的部分）。"""
    r = _to_array(returns)
    n = int(r.size)
    meta = {"annualized": True, "trading_days": TRADING_DAYS, "target_daily": float(target)}
    if n < min_samples:
        return _insufficient(n, min_samples, **meta, note="样本不足不计算索提诺")
    diff = r - target / TRADING_DAYS
    downside = np.minimum(diff, 0.0)
    dd = float(np.sqrt(np.mean(np.square(downside))))
    if dd <= 1e-12:
        return _insufficient(n, min_samples, reason="样本内无下行波动（无低于目标的收益），索提诺无定义",
                             **meta, note="样本内无下行波动（无低于目标的收益）")
    value = float(np.mean(diff) / dd * math.sqrt(TRADING_DAYS))
    return {
        "value": round(value, 4),
        "samples": n,
        "sample_required": int(min_samples),
        "annualized": True,
        "trading_days": TRADING_DAYS,
        "target_daily": float(target),
        "note": f"下行偏差按低于 {target:g} 的收益计算；按 {TRADING_DAYS} 交易日年化",
        "reason": None,
    }


def omega(returns: Sequence[float], threshold: float = 0.0,
          min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """Omega 比率：Σ(超过阈值的收益) / Σ(低于阈值的损失)。"""
    r = _to_array(returns)
    n = int(r.size)
    meta = {"threshold_daily": float(threshold)}
    if n < min_samples:
        return _insufficient(n, min_samples, **meta, note="样本不足不计算 Omega")
    diff = r - threshold
    gains = float(np.sum(diff[diff > 0]))
    losses = float(-np.sum(diff[diff < 0]))
    if losses <= 0:
        return _insufficient(n, min_samples, reason="样本内无亏损，Omega 无定义（不返回无穷大）",
                             **meta, note="样本内无亏损，Omega 无定义（不返回无穷大）")
    return {
        "value": round(gains / losses, 4),
        "samples": n,
        "sample_required": int(min_samples),
        "gains": round(gains, 8),
        "losses": round(losses, 8),
        "note": f"阈值为 {threshold:g}/日；= 超额收益总和 ÷ 亏损总和",
        "reason": None,
    }


def cvar95(returns: Sequence[float], min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """95% 条件风险价值（日频，百分比，负值表示亏损）。"""
    r = _to_array(returns)
    n = int(r.size)
    if n < min_samples:
        return _insufficient(n, min_samples, note="样本不足不计算 CVaR95")
    q = np.percentile(r, 5)
    tail = r[r <= q]
    value = float(np.mean(tail)) * 100.0
    return {
        "value": round(value, 4),
        "samples": n,
        "sample_required": int(min_samples),
        "quantile": round(float(q), 6),
        "tail_count": int(tail.size),
        "note": "日频 95% CVaR：最差 5% 交易日的平均收益（%）",
        "reason": None,
    }


def max_drawdown(points: Sequence[float], min_samples: int = 2) -> Dict[str, Any]:
    """权益曲线最大回撤（百分比，负值），按峰谷法计算。"""
    vals = _to_array(points)
    n = int(vals.size)
    if n < min_samples:
        return _insufficient(n, min_samples, reason=f"权益曲线样本 {n}/{int(min_samples)}，不足",
                             note="权益曲线样本不足，无法计算回撤")
    peak = float(vals[0])
    worst = 0.0
    for v in vals:
        peak = max(peak, float(v))
        if peak > 0:
            worst = min(worst, (float(v) - peak) / peak)
    return {
        "value": round(worst * 100.0, 4),
        "samples": n,
        "sample_required": int(min_samples),
        "note": "峰谷法：相对历史最高权益的最大跌幅（%）",
        "reason": None,
    }


# ----------------------------------------------------------------------
#  组合收益合成（按持仓权重加权个股日收益）
# ----------------------------------------------------------------------
def portfolio_return_series(
    prices_by_code: Dict[str, Dict[str, float]],
    weights: Dict[str, float],
    dates: Sequence[str],
) -> Dict[str, Any]:
    """按权重合成组合日收益序列（仅在**所有标的都有收盘价**的日期上计算）。

    Args:
        prices_by_code: ``{code: {date: close}}``
        weights: ``{code: 权重}``（无需归一化，函数内部按可用标的重新归一）
        dates: 升序日期列表（通常取基准指数交易日）

    Returns:
        ``{dates, returns, weights_used, used_weight, coverage, missing, reason}``
        ``coverage < 1`` 表示有标的缺数据（权重被重新归一，需在响应里如实说明）。
    """
    codes = [c for c in weights if c in prices_by_code]
    missing = [c for c in weights if c not in prices_by_code]
    if not codes or len(dates) < 2:
        return {"dates": [], "used_dates": [], "returns": [], "weights_used": {},
                "used_weight": 0.0, "coverage": 0.0, "missing": missing,
                "reason": "无可用的持仓价格序列" if not codes else "交易日不足"}

    # 仅保留所有 code 都有收盘价的日期
    usable_dates: List[str] = []
    for d in dates:
        if all(prices_by_code[c].get(d) is not None for c in codes):
            usable_dates.append(d)
    if len(usable_dates) < 2:
        return {"dates": [], "used_dates": [], "returns": [], "weights_used": {},
                "used_weight": 0.0, "coverage": 0.0, "missing": missing,
                "reason": "持仓与基准的共同交易日不足 2 天"}

    total_w = sum(abs(float(w)) for w in weights.values()) or 1.0
    used_w = {c: float(weights[c]) for c in codes}
    sum_used = sum(abs(w) for w in used_w.values()) or 1.0
    norm = {c: w / sum_used for c, w in used_w.items()}

    rets: List[float] = []
    for i in range(1, len(usable_dates)):
        d0, d1 = usable_dates[i - 1], usable_dates[i]
        r = 0.0
        for c in codes:
            p0 = float(prices_by_code[c][d0])
            p1 = float(prices_by_code[c][d1])
            if p0 <= 0:
                continue
            r += norm[c] * ((p1 / p0) - 1.0)
        rets.append(r)

    return {
        "dates": usable_dates[1:],
        "used_dates": usable_dates,
        "returns": rets,
        "weights_used": {c: round(w, 6) for c, w in norm.items()},
        "used_weight": round(sum_used / total_w, 6),
        "coverage": round(sum_used / total_w, 6),
        "missing": missing,
        "reason": None if not missing else f"缺价格序列：{','.join(missing)}",
    }


def returns_from_closes(dates: Sequence[str], closes: Dict[str, float]) -> List[float]:
    """由收盘价 dict + 日期序列算日收益（跳过缺失/非正价格）。"""
    out: List[float] = []
    prev: Optional[float] = None
    for d in dates:
        px = closes.get(d)
        try:
            px = float(px) if px is not None else None
        except (TypeError, ValueError):
            px = None
        if px is not None and px > 0 and prev is not None and prev > 0:
            out.append(px / prev - 1.0)
        prev = px if (px is not None and px > 0) else prev
    return out


def summarize(returns: Sequence[float], benchmark: Optional[Sequence[float]] = None,
              rf: float = 0.0, min_samples: int = MIN_SAMPLES) -> Dict[str, Any]:
    """一次性产出「组合风险块」所需的全部指标（供 /positions 与 /risk/portfolio 共用）。"""
    r = _to_array(returns)
    out: Dict[str, Any] = {
        "sample_days": int(r.size),
        "sample_required": int(min_samples),
        "metrics": {
            "sharpe": sharpe(r, rf=rf, min_samples=min_samples),
            "sortino": sortino(r, min_samples=min_samples),
            "omega": omega(r, min_samples=min_samples),
            "cvar_95": cvar95(r, min_samples=min_samples),
            "volatility_pct": None,
            "annualized": True,
            "risk_free_rate": float(rf),
            "note": None,
        },
    }
    if r.size >= 2:
        out["metrics"]["volatility_pct"] = round(_std(r) * math.sqrt(TRADING_DAYS) * 100.0, 4)
    notes = [m["note"] for m in (out["metrics"]["sharpe"], out["metrics"]["sortino"],
                                 out["metrics"]["omega"], out["metrics"]["cvar_95"])
             if m.get("note")]
    out["metrics"]["note"] = "；".join(dict.fromkeys(notes)) if notes else None
    if benchmark is not None:
        b = beta(r, benchmark, min_samples=min_samples)
        out.update({
            "beta": b["beta"], "r2": b["r2"], "idio_vol_pct": b["idio_vol_pct"],
            "total_risk_pct": b["total_risk_pct"], "systematic_pct": b["systematic_pct"],
            "alpha_annual": b["alpha_annual"], "regression": b["regression"],
            "beta_reason": b["reason"],
        })
    return out
