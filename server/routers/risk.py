# -*- coding: utf-8 -*-
"""组合风险视图（T04）：`GET /api/risk/portfolio`。

契约见 `docs/10-architecture/openbb-dsa-integration-design.md` §3.7：

    data = {beta, alpha_annual, r2, idiosyncratic_risk, total_risk, systematic_pct,
            sample_days, regression{intercept, slope, stderr}, as_of, ...}

实现要点：
- 组合日收益 = 各持仓日收益按**当前市值权重**加权（权重在响应里透出 `weights`）；
- 基准日收益来自沪深300（默认 ``sh000300``）日 K，走 ``cache.get_or_fetch_ex``（TTL 10 分钟）；
- **样本不足（< 60 交易日）→ β/R²/特质风险全 null + reason，绝不用短样本硬算**；
- 无持仓 → ``ok:true`` + 各值 null + ``reason:"当前无持仓"``（不是 500）；
- 结果整体缓存 60s（``risk:portfolio:{benchmark}:{days}``），供 /positions 复用。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import CACHE_TTL_KLINE, find_stock_name
from server.risk_metrics import (
    MIN_SAMPLES,
    TRADING_DAYS,
    beta,
    portfolio_return_series,
    returns_from_closes,
    summarize,
)
from server.tencent_kline import fetch_tencent_kline
from server.trading import trading_facade
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/risk", tags=["risk"])

DEFAULT_BENCHMARK = "sh000300"
BENCHMARK_NAMES = {"sh000300": "沪深300", "sh000001": "上证指数", "sz399001": "深证成指"}
RISK_CACHE_TTL = 60.0          # 组合风险快照缓存（秒）
MAX_WINDOW = 500
MIN_WINDOW = 10               # 允许小于 60（用于验证「样本不足→null」）


def norm_benchmark(benchmark: Optional[str]) -> str:
    """归一化基准代码：兼容 ``000300`` / ``sh000300``。"""
    b = str(benchmark or DEFAULT_BENCHMARK).strip().lower()
    if not b:
        return DEFAULT_BENCHMARK
    if b.isdigit() and len(b) == 6:
        return ("sh" if b.startswith(("000", "5", "6")) else "sz") + b
    return b


def risk_cache_key(benchmark: str, days: int, codes: str = "") -> str:
    return f"risk:portfolio:{benchmark}:{int(days)}:{codes or 'live'}"


def parse_codes_override(raw: Optional[str]) -> Dict[str, float]:
    """解析 ``?codes=sh600519:0.6,sz000858:0.4``（省略权重=等权）。

    用途：不依赖纸面持仓，对任意组合做**只读**风险试算（QA 验收 / 预案推演）。
    """
    out: Dict[str, float] = {}
    parts = [p.strip().lower() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return out
    has_weight = any(":" in p for p in parts)
    for p in parts:
        code, _, w = p.partition(":")
        code = code.strip()
        if not code:
            continue
        try:
            weight = float(w) if w else (1.0 / len(parts))
        except ValueError:
            weight = 1.0 / len(parts)
        out[code] = out.get(code, 0.0) + max(0.0, weight)
    if not has_weight:
        return {c: 1.0 / len(out) for c in out}
    return out


async def load_closes(code: str, limit: int) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """取某标的日收盘序列 ``{date: close}``，并返回缓存元信息（不重复打上游）。"""
    key = f"kline_closes:{code}:{int(limit)}"

    async def _fetch():
        bars = await fetch_tencent_kline(code, period="day", limit=int(limit), timeout=6.0)
        if not bars:
            return None
        out: Dict[str, float] = {}
        for b in bars:
            d = str(b.get("d") or "")[:10]
            try:
                c = float(b.get("c"))
            except (TypeError, ValueError):
                continue
            if d and c > 0:
                out[d] = c
        return out or None

    value, meta = await cache.get_or_fetch_ex(key, _fetch, CACHE_TTL_KLINE)
    return (value or {}), meta


def _empty_payload(benchmark: str, days: int, reason: str) -> Dict[str, Any]:
    """无持仓 / 基准不可用时的标准空态：字段齐全、全部 null + reason。"""
    return {
        "available": False,
        "benchmark": benchmark,
        "benchmark_name": BENCHMARK_NAMES.get(benchmark, benchmark),
        "days": int(days),
        "sample_days": 0,
        "sample_required": MIN_SAMPLES,
        "beta": None,
        "alpha_annual": None,
        "r2": None,
        "idiosyncratic_risk": None,
        "total_risk": None,
        "systematic_pct": None,
        "regression": {"intercept": None, "slope": None, "stderr": None},
        "metrics": {
            "sharpe": None, "sortino": None, "omega": None, "cvar_95": None,
            "volatility": None, "risk_free_rate": 0.0,
            "annualized": True, "trading_days": TRADING_DAYS,
            "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化",
            "reasons": {},
        },
        "weights": {},
        "coverage": 0.0,
        "per_code_beta": {},
        "as_of": None,
        "degraded": True,
        "reason": reason,
    }


async def compute_portfolio_risk(days: int = 180,
                                 benchmark: str = DEFAULT_BENCHMARK,
                                 codes_override: Optional[Dict[str, float]] = None
                                 ) -> Dict[str, Any]:
    """计算组合风险快照（供端点与 /positions 共用）。**不做缓存**，由调用方决定。

    Args:
        codes_override: 指定组合（``{code: weight}``）时跳过纸面持仓，纯只读试算；
            None 表示按当前纸面持仓计算。
    """
    benchmark = norm_benchmark(benchmark)
    await trading_facade.ensure_ready()
    store = trading_facade.store
    if codes_override:
        positions = []          # 试算模式：不使用账户持仓
        weighted = {c: float(w) for c, w in codes_override.items() if float(w) > 0}
        codes = list(weighted.keys())
        price_missing: List[str] = []
        if not codes:
            return _empty_payload(benchmark, days, "codes 参数未解析出可用标的")
        total_mv = sum(weighted.values())
    else:
        positions = await store.list_positions("paper")
        if not positions:
            return _empty_payload(benchmark, days, "当前无持仓")

        # 权重：优先实时价，缺失则退回成本价（并在 degraded 里说明）
        weighted = {}
        codes = []
        price_missing = []
        for p in positions:
            px = float(p.last_price or 0)
            if px <= 0:
                px = float(p.avg_cost or 0)
                price_missing.append(p.code)
            mv = max(0.0, float(p.qty or 0)) * px
            if mv <= 0:
                continue
            weighted[p.code] = weighted.get(p.code, 0.0) + mv
            if p.code not in codes:
                codes.append(p.code)
        total_mv = sum(weighted.values())
    if total_mv <= 0 or not codes:
        return _empty_payload(benchmark, days, "持仓市值为 0，无法计算组合收益")

    weights = {c: v / total_mv for c, v in weighted.items()}
    limit = int(days) + 5

    # 批量并发取数（基准 + 各持仓），全部走缓存，不循环单打上游
    tasks = [load_closes(benchmark, limit)] + [load_closes(c, limit) for c in codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    bench_closes: Dict[str, float] = {}
    closes_by_code: Dict[str, Dict[str, float]] = {}
    fetch_errors: List[str] = []
    for idx, res in enumerate(results):
        code = benchmark if idx == 0 else codes[idx - 1]
        if isinstance(res, Exception):
            fetch_errors.append(f"{code}:{type(res).__name__}")
            data = {}
        else:
            data = res[0]
        if idx == 0:
            bench_closes = data
        else:
            closes_by_code[code] = data

    if not bench_closes:
        return _empty_payload(benchmark, days,
                              f"基准指数日 K 不可得（{benchmark}）：无法计算 β")

    dates = sorted(bench_closes.keys())[-(int(days) + 1):]
    series = portfolio_return_series(closes_by_code, weights, dates)
    if not series["returns"]:
        reason = series.get("reason") or "组合收益序列为空"
        return _empty_payload(benchmark, days, reason)

    # 基准收益与组合收益使用同一组日期（portfolio_return_series 已返回 used_dates）
    used_dates = series.get("used_dates") or []
    bench_returns = returns_from_closes(used_dates, bench_closes)

    n = min(len(series["returns"]), len(bench_returns))
    port_returns = series["returns"][:n]
    bench_returns = bench_returns[:n]

    summary = summarize(port_returns, bench_returns, rf=0.0, min_samples=MIN_SAMPLES)
    m = summary["metrics"]

    # 每只持仓的 β（同一批收盘序列，零额外成本）
    per_code_beta: Dict[str, Dict[str, Any]] = {}
    for c in codes:
        cr = returns_from_closes(used_dates, closes_by_code.get(c) or {})
        k = min(len(cr), len(bench_returns))
        b = beta(cr[:k], bench_returns[:k], min_samples=MIN_SAMPLES)
        per_code_beta[c] = {
            "name": find_stock_name(c),
            "beta": b["beta"], "r2": b["r2"], "samples": b["samples"],
            "reason": b["reason"],
        }

    degraded = bool(series.get("missing")) or bool(fetch_errors) or bool(price_missing)
    reasons: List[str] = []
    if series.get("missing"):
        reasons.append(f"缺价格序列：{','.join(series['missing'])}")
    if fetch_errors:
        reasons.append(f"取数失败：{','.join(fetch_errors)}")
    if price_missing:
        reasons.append(f"缺实时价（按成本计权）：{','.join(price_missing)}")
    if summary.get("beta_reason"):
        reasons.append(str(summary["beta_reason"]))

    payload = {
        "available": True,
        "benchmark": benchmark,
        "benchmark_name": BENCHMARK_NAMES.get(benchmark, benchmark),
        "days": int(days),
        "sample_days": int(summary["sample_days"]),
        "sample_required": int(MIN_SAMPLES),
        "beta": summary.get("beta"),
        "alpha_annual": summary.get("alpha_annual"),
        "r2": summary.get("r2"),
        # 小数口径（0.0221 = 2.21%），与 §3.7 示例一致
        "idiosyncratic_risk": (None if summary.get("idio_vol_pct") is None
                               else round(summary["idio_vol_pct"] / 100.0, 6)),
        "total_risk": (None if summary.get("total_risk_pct") is None
                       else round(summary["total_risk_pct"] / 100.0, 6)),
        "systematic_pct": summary.get("systematic_pct"),
        "regression": summary.get("regression") or {"intercept": None, "slope": None, "stderr": None},
        "metrics": {
            "sharpe": m["sharpe"]["value"],
            "sortino": m["sortino"]["value"],
            "omega": m["omega"]["value"],
            "cvar_95": m["cvar_95"]["value"],
            "volatility": m.get("volatility_pct"),
            "risk_free_rate": 0.0,
            "annualized": True,
            "trading_days": TRADING_DAYS,
            "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化",
            "reasons": {
                "sharpe": m["sharpe"].get("reason"),
                "sortino": m["sortino"].get("reason"),
                "omega": m["omega"].get("reason"),
                "cvar_95": m["cvar_95"].get("reason"),
            },
        },
        "weights": {c: round(w, 6) for c, w in weights.items()},
        "coverage": series.get("coverage"),
        "per_code_beta": per_code_beta,
        "as_of": used_dates[-1] if used_dates else None,
        "degraded": degraded,
        "reason": "；".join(reasons) if reasons else None,
    }
    return payload


async def cached_portfolio_risk(days: int = 180,
                              benchmark: str = DEFAULT_BENCHMARK,
                              codes_override: Optional[Dict[str, float]] = None
                              ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """带缓存的组合风险快照 ``(data, meta)``（TTL 60s）。

    /api/risk/portfolio 与 /api/automation/positions 的预热任务共用同一份缓存，
    避免同一份 β 计算重复打上游。
    """
    bench = norm_benchmark(benchmark)
    n_days = max(MIN_WINDOW, min(MAX_WINDOW, int(days)))
    codes_sig = ",".join(f"{c}:{w:g}" for c, w in sorted((codes_override or {}).items()))
    data, meta = await cache.get_or_fetch_ex(
        risk_cache_key(bench, n_days, codes_sig),
        lambda: compute_portfolio_risk(n_days, bench, codes_override),
        RISK_CACHE_TTL,
    )
    if data is None:
        data = _empty_payload(bench, n_days, "组合风险计算失败（无可用数据）")
    return data, meta


@router.get("/portfolio")
async def get_portfolio_risk(
    benchmark: str = Query(DEFAULT_BENCHMARK, description="基准指数，默认 sh000300（沪深300）"),
    window: int = Query(180, ge=MIN_WINDOW, le=MAX_WINDOW, description="回归窗口（交易日）"),
    days: Optional[int] = Query(None, ge=MIN_WINDOW, le=MAX_WINDOW,
                                description="window 的别名（前端更常用 days）"),
    codes: str = Query("", description="可选：组合试算，如 sh600519:0.6,sz000858:0.4（省略权重=等权）"),
) -> Dict[str, Any]:
    """组合风险视图：β / R² / 特质风险 / 夏普 / 索提诺 / Omega / CVaR95。

    - ``days``（或 ``window``）可小于 60：此时 β/R²/特质风险**全部 null** + reason「样本 N/60」；
    - 无持仓 → ``ok:true`` + 各值 null + ``reason:"当前无持仓"``；
    - 响应附带 ``rf_annual: 0.0`` 语义（``metrics.risk_free_rate`` + ``metrics.note``）。
    """
    bench = norm_benchmark(benchmark)
    n_days = int(days if days is not None else window)
    n_days = max(MIN_WINDOW, min(MAX_WINDOW, n_days))

    override = parse_codes_override(codes)
    data, meta = await cached_portfolio_risk(n_days, bench, override or None)
    if data is None:
        data = _empty_payload(bench, n_days, "组合风险计算失败（无可用数据）")

    return {
        "ok": True,
        "data": data,
        "message": "ok",
        "source": "kline+positions",
        "as_of": data.get("as_of") or _now_iso(),
        "age": meta.get("age"),
        "degraded": bool(data.get("degraded")),
        "reason": data.get("reason"),
    }
