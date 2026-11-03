"""QlibFactorEngine：筛选、IC 检验、分层回测、因子组合与结果整形"""

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
    QLIB_BENCHMARKS,
    QLIB_FACTOR_LIBRARY,
    _AVAILABLE_IDS,
    _FACTOR_BY_ID,
    _FACTOR_COMPUTE,
    _FACTOR_DIR,
    _FACTOR_SCALE,
    _KLINE_LIMIT,
    _MIN_BACKTEST_PERIODS,
    _MIN_CROSS_SECTION,
    _MIN_IC_PERIODS,
    _MIN_QUANT_SAMPLES,
    logger,
)
from server.qlib_factor_stats import (
    _DEFAULT_SELECTED,
    _DEFAULT_START_DATE,
    _DEFAULT_TOP_K,
    _Panel,
    _benchmark_closes,
    _build_panel,
    _composite_matrix,
    _kline_cached,
    _load_universe,
    _market_returns,
    _pearson,
    _quantile_table,
    _spearman,
    _summarize_ic,
)

class QlibFactorEngine:
    """动态因子引擎：实时选股 + 缠论结构 + 真实截面因子评估与回测。"""

    @classmethod
    def get_factor_library(cls) -> Dict[str, Any]:
        """返回因子库（含真实公式、可用性与不可用原因；IC/IR 由回测实算，此处为 null）。"""
        return {
            "ok": True,
            "engine": "Dynamic Factor + Chan Theory (Real-Data V18)",
            "total_factors": len(QLIB_FACTOR_LIBRARY),
            "available_count": len(_AVAILABLE_IDS),
            "factors": QLIB_FACTOR_LIBRARY,
            "categories": sorted({f["category"] for f in QLIB_FACTOR_LIBRARY}),
            "dynamic": True,
            "note": "因子 IC/IR 由逐期截面评估实算；不可用因子返回 null + reason，绝不补假数",
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @classmethod
    def get_meta(cls) -> Dict[str, Any]:
        """返回前端动态化所需的元信息（因子/基准/数据区间/默认参数）。

        其中 available_factor_ids 与引擎实际可算出非 null 值的因子集合完全一致。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        return {
            "ok": True,
            "engine": "Dynamic Factor + Chan Theory (Real-Data V18)",
            "factors": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "category": f["category"],
                    "formula_desc": f["formula_desc"],
                    "available": f["available"],
                    "unavailable_reason": f["unavailable_reason"],
                    "direction": f["direction"],
                    "default_weight": f["default_weight"],
                }
                for f in QLIB_FACTOR_LIBRARY
            ],
            "available_factor_ids": list(_AVAILABLE_IDS),
            "benchmarks": QLIB_BENCHMARKS,
            "date_range": {
                "min": None,
                "max": today,
                "note": "回测区间受标的可用日K历史限制（约 260 个交易日深度）",
            },
            "defaults": {
                "selected_factor_ids": list(_DEFAULT_SELECTED),
                "weights": {f["id"]: f["default_weight"] for f in QLIB_FACTOR_LIBRARY
                            if f["available"]},
                "benchmark": "sh000300",
                "top_k": _DEFAULT_TOP_K,
                "start_date": _DEFAULT_START_DATE,
                "end_date": today,
            },
            "params_schema": {
                "weights": "字典，key 为因子 id，value 为非负权重（将自动归一化）",
                "selected_factor_ids": "仅这些因子参与综合分与选股",
                "start_date/end_date": "YYYY-MM-DD，约束回测区间",
                "top_k": "每期选股数量",
            },
            "min_samples": {
                "cross_section": _MIN_CROSS_SECTION,
                "ic_periods": _MIN_IC_PERIODS,
                "quantile_samples": _MIN_QUANT_SAMPLES,
                "backtest_periods": _MIN_BACKTEST_PERIODS,
            },
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @classmethod
    async def dynamic_screen(cls, limit: int = 12) -> Dict[str, Any]:
        """动态因子选股：真实行情 + 真实K线因子 + 缠论结构 → 实时排名。"""
        universe = await _load_universe()
        if not universe:
            return {"ok": True, "dynamic": True, "total": 0, "universe_size": 0,
                    "items": [], "available_factor_ids": list(_AVAILABLE_IDS),
                    "message": "实时行情暂不可用，暂无动态选股结果",
                    "update_time": datetime.now().strftime("%H:%M:%S")}

        # 基准（供特质波动率因子使用）
        bench_klines: List[Dict[str, Any]] = []
        try:
            bench_klines = await _kline_cached("sh000300")
        except Exception:
            bench_klines = []
        mret = _market_returns(bench_klines)

        rows: List[Dict[str, Any]] = []
        for st in universe:
            klines = st["klines"]
            try:
                latest = _factor_latest(klines, mret)
                chan = chan_engine.analyze(klines)
                if not chan.get("available"):
                    continue
                cur = chan["current"]
                factor_items = _factor_items(latest)
                combined = _combined_score(latest, chan)
                rows.append({
                    "code": st["code"],
                    "name": st["name"],
                    "price": round(st["price"], 2),
                    "chg_pct": round(st["chg"], 2),
                    "amount_yi": round(st["amount"] / 1e8, 2),
                    "score": combined,
                    "signal": _signal_from(combined, chan),
                    "factor_scores": factor_items,
                    "chan": {
                        "stroke_dir": cur.get("stroke_dir"),
                        "trend": cur.get("trend"),
                        "divergence": cur.get("divergence"),
                        "buy_point": cur.get("buy_point"),
                        "sell_point": cur.get("sell_point"),
                        "pivot": cur.get("pivot"),
                        "desc": cur.get("desc"),
                    },
                })
            except Exception as exc:
                logger.debug("dynamic_screen %s failed: %s", st["code"], exc)

        rows.sort(key=lambda r: r["score"], reverse=True)
        return {
            "ok": True,
            "dynamic": True,
            "total": len(rows),
            "universe_size": len(universe),
            "items": rows[:limit],
            "available_factor_ids": list(_AVAILABLE_IDS),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "综合因子库真实因子得分 + 缠论结构（分型/笔/中枢/背驰）买卖点",
        }

    @classmethod
    async def evaluate_factor_combination(
        cls,
        selected_factor_ids: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
        top_k: int = 8,
        benchmark: str = "sh000300",
        start_date: str = _DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        days: int = 90,
        include_debug: bool = False,
    ) -> Dict[str, Any]:
        """逐期截面因子回测与评估（真实 IC/IR/分位，参数真实生效）。"""
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date

        param_warnings: List[str] = []

        # ---- 参数解析 ----
        requested = list(selected_factor_ids) if selected_factor_ids else list(_DEFAULT_SELECTED)
        selected: List[str] = []
        for fid in requested:
            if fid not in _FACTOR_BY_ID:
                param_warnings.append(f"未知因子 id 已忽略：{fid}")
                continue
            if fid not in _AVAILABLE_IDS:
                reason = _FACTOR_BY_ID[fid].get("unavailable_reason") or "数据不可用"
                param_warnings.append(f"因子 {fid} 不可用，已忽略：{reason}")
                continue
            if fid not in selected:
                selected.append(fid)
        if not selected:
            selected = [fid for fid in _DEFAULT_SELECTED if fid in _AVAILABLE_IDS]
            param_warnings.append("所选因子均不可用，回退到默认可用因子集合")

        # 权重：仅使用选中因子，归一化；未知 key 提示
        raw_w: Dict[str, float] = {}
        for fid in selected:
            default_w = _FACTOR_BY_ID[fid].get("default_weight", 1.0) or 1.0
            if weights and fid in weights:
                raw_w[fid] = max(0.0, _finite(weights.get(fid), default_w))
            else:
                raw_w[fid] = default_w
        if weights:
            for k in weights:
                if k not in _FACTOR_BY_ID:
                    param_warnings.append(f"weights 含未知因子 key 已忽略：{k}")
        total_w = sum(raw_w.values())
        if total_w <= 0:
            raw_w = {fid: 1.0 for fid in selected}
            total_w = float(len(selected))
        norm_w = {fid: raw_w[fid] / total_w for fid in selected}

        top_k = max(1, int(top_k or _DEFAULT_TOP_K))
        days = max(0, int(days or 0))

        # ---- 数据 ----
        universe = await _load_universe()
        if len(universe) < _MIN_CROSS_SECTION:
            return cls._empty_result(selected, norm_w, benchmark, top_k, start_date, end_date,
                                     param_warnings,
                                     "实时选股域不足（< %d 只），无法回测" % _MIN_CROSS_SECTION)

        bench_klines: List[Dict[str, Any]] = []
        bench_reason: Optional[str] = None
        try:
            bench_klines = await fetch_tencent_kline(benchmark, period="day", limit=_KLINE_LIMIT)
        except Exception as exc:
            bench_reason = f"基准行情抓取失败：{exc}"
        if not bench_klines:
            bench_reason = bench_reason or "基准行情不可用（返回空）"

        mret = _market_returns(bench_klines)
        panel = _build_panel(universe, mret)
        if panel.reason:
            return cls._empty_result(selected, norm_w, benchmark, top_k, start_date, end_date,
                                     param_warnings,
                                     f"截面面板构造失败：{panel.reason}")

        # ---- 区间约束（真实生效：改变 start/end 即改变结果）----
        window = [t for t, d in enumerate(panel.dates) if start_date <= d <= end_date]
        if len(window) < _MIN_BACKTEST_PERIODS + 1:
            param_warnings.append(
                f"区间 {start_date}~{end_date} 内仅 {len(window)} 个交易日，样本不足")
            return cls._empty_result(selected, norm_w, benchmark, top_k, start_date, end_date,
                                     param_warnings,
                                     "所选区间内交易日不足，无法回测（请放宽 start_date/end_date）")

        # ---- 综合因子截面矩阵 ----
        comp = _composite_matrix(panel, selected, norm_w)
        window_dates = [panel.dates[t] for t in window]

        # ---- 综合因子 IC / IR ----
        comp_ic_series: List[Dict[str, Any]] = []
        comp_ic_vals: List[float] = []
        comp_ric_vals: List[float] = []
        for t in window:
            if t >= panel.forward.shape[0]:
                continue
            ic_t, _ = _pearson(comp[t, :], panel.forward[t, :])
            ric_t, n = _spearman(comp[t, :], panel.forward[t, :])
            if ic_t is not None:
                comp_ic_vals.append(ic_t)
            if ric_t is not None:
                comp_ric_vals.append(ric_t)
            if ic_t is not None or ric_t is not None:
                comp_ic_series.append({
                    "date": panel.dates[t],
                    "ic": round(ic_t, 6) if ic_t is not None else None,
                    "rank_ic": round(ric_t, 6) if ric_t is not None else None,
                    "n": n,
                })
        combined_ic, _, ic_reason = _summarize_ic(comp_ic_vals)
        _, combined_ir, ir_reason = _summarize_ic(comp_ric_vals)

        # ---- 逐期截面回测（无前视：每期只用当期可见因子选股，按下一期收益结算）----
        nav: List[float] = [1.0]
        period_rets: List[float] = []
        period_dates: List[str] = [window_dates[0]]
        held_counts: List[int] = []
        for t in window[:-1]:
            scores = comp[t, :]
            fwd = panel.forward[t, :]
            valid_idx = [i for i in range(len(scores))
                         if np.isfinite(scores[i]) and np.isfinite(fwd[i])]
            if len(valid_idx) < min(top_k, _MIN_CROSS_SECTION):
                continue
            valid_idx.sort(key=lambda i: scores[i], reverse=True)
            picks = valid_idx[:top_k]
            period_ret = float(np.mean([fwd[i] for i in picks]))
            period_rets.append(period_ret)
            nav.append(nav[-1] * (1.0 + period_ret))
            period_dates.append(panel.dates[t + 1])
            held_counts.append(len(picks))

        n_pts = len(nav)
        if n_pts < _MIN_BACKTEST_PERIODS:
            param_warnings.append("回测有效期数不足，净值序列过短")
            return cls._empty_result(selected, norm_w, benchmark, top_k, start_date, end_date,
                                     param_warnings,
                                     "回测有效期数不足（< %d）" % _MIN_BACKTEST_PERIODS)

        # ---- 指标 ----
        strat_returns = period_rets
        total_return = (nav[-1] - 1.0) * 100.0
        annual_return = ((nav[-1]) ** (252.0 / max(1, len(strat_returns))) - 1.0) * 100.0 \
            if nav[-1] > 0 else None

        mean_r = float(np.mean(strat_returns)) if strat_returns else None
        std_r = float(np.std(strat_returns, ddof=1)) if len(strat_returns) > 1 else 0.0
        annual_vol = round(std_r * math.sqrt(252) * 100.0, 2) if std_r > 0 else 0.0
        sharpe = round((mean_r / std_r) * math.sqrt(252), 2) if (mean_r is not None and std_r > 1e-12) else None

        peak = 1.0
        max_dd = 0.0
        for v in nav:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0.0)
        max_dd_pct = round(max_dd * 100.0, 2)
        calmar = round(annual_return / max_dd_pct, 2) if (annual_return is not None and max_dd_pct > 1e-6) else None
        win_rate = round(sum(1 for r in strat_returns if r > 0) / len(strat_returns) * 100.0, 1) \
            if strat_returns else None

        # ---- 基准净值（失败则 null，不写死全 1）----
        benchmark_nav: Optional[List[float]] = None
        bench_total: Optional[float] = None
        excess: Optional[float] = None
        if bench_klines:
            bmap = _benchmark_closes(bench_klines)
            raw_bc = [bmap.get(d) for d in period_dates]
            present = [v for v in raw_bc if v is not None]
            coverage = len(present) / max(1, len(raw_bc))
            if len(present) >= 2 and coverage >= 0.8:
                # 前向填充缺口
                filled: List[float] = []
                last = present[0]
                for v in raw_bc:
                    if v is not None:
                        last = v
                    filled.append(last)
                base = filled[0]
                if base > 0:
                    benchmark_nav = [round(v / base, 6) for v in filled]
                    bench_total = round((benchmark_nav[-1] - 1.0) * 100.0, 2)
                    excess = round(total_return - bench_total, 2)
            else:
                bench_reason = bench_reason or "基准日K与回测区间覆盖率不足"
        if benchmark_nav is None:
            bench_reason = bench_reason or "基准数据不可用"

        # ---- 分位（真实分组）----
        quantiles, q_reason = _quantile_table(comp, panel.forward)
        quantile_reason = q_reason
        if quantiles is not None:
            for g in quantiles:
                g["ic"] = combined_ic
        # 区间聚焦版本：只统计窗口内的样本
        win_factor = comp[window[0]:window[-1] + 1, :]
        win_fwd = panel.forward[window[0]:window[-1] + 1, :]
        q_win, q_win_reason = _quantile_table(win_factor, win_fwd)
        if q_win is not None:
            quantiles = q_win
            quantile_reason = None
        elif quantiles is not None:
            # 窗口内样本不足，退回全样本分组；如实标注口径
            quantile_reason = "window_insufficient_using_full_sample"

        # ---- 每因子 IC/IR 实算 ----
        factor_stats: Dict[str, Any] = {}
        for fid in _AVAILABLE_IDS:
            ic_list: List[float] = []
            ric_list: List[float] = []
            for t in window:
                ic_t, _ = _pearson(panel.factors[fid][t, :], panel.forward[t, :])
                ric_t, _ = _spearman(panel.factors[fid][t, :], panel.forward[t, :])
                if ic_t is not None:
                    ic_list.append(ic_t)
                if ric_t is not None:
                    ric_list.append(ric_t)
            f_ic, _, f_ic_reason = _summarize_ic(ic_list)
            f_ric, f_ir, f_ir_reason = _summarize_ic(ric_list)
            factor_stats[fid] = {
                "name": _FACTOR_BY_ID[fid]["name"],
                "ic": f_ic,
                "rank_ic": f_ric,
                "ir": f_ir,
                "ic_periods": len(ic_list),
                "status": "ok" if f_ic is not None else "degraded",
                "reason": f_ic_reason,
                "selected": fid in selected,
                "weight": round(norm_w.get(fid, 0.0), 4) if fid in selected else 0.0,
            }
        for fid in _FACTOR_BY_ID:
            if fid in factor_stats:
                continue
            factor_stats[fid] = {
                "name": _FACTOR_BY_ID[fid]["name"],
                "ic": None, "rank_ic": None, "ir": None, "ic_periods": 0,
                "status": "unavailable",
                "reason": _FACTOR_BY_ID[fid].get("unavailable_reason"),
                "selected": False, "weight": 0.0,
            }

        # ---- Top 组合（最后一期截面）----
        top_stocks = cls._latest_top(panel, comp, window[-1], top_k, selected, norm_w, universe)

        metrics = {
            "total_return": round(total_return, 2),
            "annual_return": round(annual_return, 2) if annual_return is not None else None,
            "excess_return": excess,
            "benchmark_return": bench_total,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd_pct,
            "calmar_ratio": calmar,
            "annual_volatility": annual_vol,
            "win_rate": win_rate,
            "combined_ic": combined_ic,
            "combined_ir": combined_ir,
            "ic_reason": ic_reason,
            "ir_reason": ir_reason,
            "quantile_reason": quantile_reason,
            "portfolio_size": len(panel.codes),
            "sample_days": len(period_rets),
        }

        caveat = (
            "逐期截面回测：每期仅使用当期可见因子值选股，按下一期真实收益结算，已消除选股层面"
            "的前视偏差。仍残留的偏差：股票池取自「今日」实时行情（流动性/异动 Top），"
            "尚未使用逐期动态成分股，故存在少量幸存者/选择偏差（survivorship & universe selection bias）。"
        )

        result: Dict[str, Any] = {
            "ok": True,
            "engine": "Dynamic Factor Backtest (Real-Data Cross-Sectional V18)",
            "params": {
                "factors": selected,
                "weights": {fid: round(norm_w[fid], 4) for fid in selected},
                "benchmark": benchmark,
                "top_k": top_k,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
            },
            "metrics": metrics,
            "factor_stats": factor_stats,
            "chart_data": {
                "dates": period_dates,
                "strategy": [round(v, 6) for v in nav],
                "benchmark": benchmark_nav,
                "excess": ([round(nav[i] / benchmark_nav[i], 6) for i in range(n_pts)]
                           if benchmark_nav is not None else None),
                "benchmark_reason": bench_reason if benchmark_nav is None else None,
            },
            "quantiles": quantiles,
            "top_stocks": top_stocks,
            "ic_series": comp_ic_series,
            "param_warnings": param_warnings,
            "backtest_caveat": caveat,
            "available_factor_ids": list(_AVAILABLE_IDS),
        }

        if include_debug:
            result["debug"] = {
                "dates": period_dates,
                "codes": panel.codes,
                "composite_ic_input": [
                    {"date": panel.dates[t],
                     "factor": [float(v) if np.isfinite(v) else None for v in comp[t, :]],
                     "forward_return": [float(v) if np.isfinite(v) else None
                                        for v in panel.forward[t, :]]}
                    for t in window
                ],
                "factor_matrices": {
                    fid: [[float(panel.factors[fid][t, i])
                           if np.isfinite(panel.factors[fid][t, i]) else None
                           for i in range(len(panel.codes))]
                          for t in window]
                    for fid in selected
                },
            }

        return result

    # ------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------

    @classmethod
    def _empty_result(cls, selected: List[str], norm_w: Dict[str, float], benchmark: str,
                      top_k: int, start_date: str, end_date: str,
                      warnings: List[str], message: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "engine": "Dynamic Factor Backtest (Real-Data Cross-Sectional V18)",
            "message": message,
            "params": {
                "factors": selected,
                "weights": {fid: round(norm_w[fid], 4) for fid in selected},
                "benchmark": benchmark, "top_k": top_k,
                "start_date": start_date, "end_date": end_date,
            },
            "metrics": {
                "total_return": None, "annual_return": None, "excess_return": None,
                "benchmark_return": None, "sharpe_ratio": None, "max_drawdown": None,
                "calmar_ratio": None, "annual_volatility": None, "win_rate": None,
                "combined_ic": None, "combined_ir": None,
                "ic_reason": "insufficient_samples", "ir_reason": "insufficient_samples",
                "quantile_reason": "insufficient_samples", "portfolio_size": 0,
                "sample_days": 0,
            },
            "factor_stats": {},
            "chart_data": {"dates": [], "strategy": [], "benchmark": None, "excess": None},
            "quantiles": None,
            "top_stocks": [],
            "ic_series": [],
            "param_warnings": warnings,
            "backtest_caveat": None,
            "available_factor_ids": list(_AVAILABLE_IDS),
        }

    @classmethod
    def _latest_top(cls, panel: _Panel, comp: np.ndarray, t: int, top_k: int,
                    selected: List[str], norm_w: Dict[str, float],
                    universe: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """最后一期截面 Top 组合（含每因子真实值、展示分、缠论结构与信号）。"""
        T, N = comp.shape
        if t >= T:
            return []
        uni_by_code = {u["code"]: u for u in universe}
        scores = comp[t, :]
        order = [i for i in range(N) if np.isfinite(scores[i])]
        order.sort(key=lambda i: scores[i], reverse=True)
        picks = order[:top_k]
        if not picks:
            return []
        smin = min(scores[i] for i in picks)
        smax = max(scores[i] for i in picks)
        span = smax - smin
        out: List[Dict[str, Any]] = []
        for i in picks:
            code = panel.codes[i]
            u = uni_by_code.get(code) or {}
            factor_items: List[Dict[str, Any]] = []
            for fid in _AVAILABLE_IDS:
                raw = panel.factors[fid][t, i]
                raw_v = round(float(raw), 6) if np.isfinite(raw) else None
                factor_items.append({
                    "id": fid,
                    "name": _FACTOR_BY_ID[fid]["name"],
                    "category": _FACTOR_BY_ID[fid]["category"],
                    "raw": raw_v,
                    "score": _norm_score(fid, raw_v),
                    "selected": fid in selected,
                })
            display_score = 50.0 if span < 1e-12 else 100.0 * (scores[i] - smin) / span
            chan = {}
            try:
                if u.get("klines"):
                    chan = chan_engine.analyze(u["klines"])
            except Exception as exc:  # pragma: no cover - 防御性
                logger.debug("chan analyze failed for %s: %s", code, exc)
            cur = (chan or {}).get("current") or {}
            out.append({
                "code": code,
                "name": u.get("name") or code,
                "price": round(_finite(u.get("price")), 2),
                "chg_pct": round(_finite(u.get("chg")), 2),
                "score": round(display_score, 1),
                "composite_z": round(float(scores[i]), 4),
                "signal": _signal_from(display_score, chan) if chan.get("available") else "观望",
                "factor_scores": factor_items,
                "chan": {
                    "trend": cur.get("trend"),
                    "divergence": cur.get("divergence"),
                    "buy_point": cur.get("buy_point"),
                    "sell_point": cur.get("sell_point"),
                    "desc": cur.get("desc"),
                },
            })
        return out


def _factor_latest(klines: List[Dict[str, Any]],
                   mret: Dict[str, float]) -> Dict[str, Optional[float]]:
    """计算单只股票各可用因子的最新（末日）真实值；不可算返回 None。"""
    closes = np.array([_finite(k.get("c")) for k in klines], dtype=float)
    vols = np.array([_finite(k.get("v")) for k in klines], dtype=float)
    dates = [str(k.get("d")) for k in klines]
    out: Dict[str, Optional[float]] = {}
    for fid, fn in _FACTOR_COMPUTE.items():
        try:
            arr = fn(closes, vols, dates, mret)
            last = arr[-1] if len(arr) else np.nan
            out[fid] = float(last) if np.isfinite(last) else None
        except Exception as exc:  # pragma: no cover - 防御性
            logger.debug("factor latest %s failed: %s", fid, exc)
            out[fid] = None
    return out


def _norm_score(fid: str, raw: Optional[float]) -> Optional[float]:
    """把因子真实值映射为 0~100 展示分（tanh 压缩，方向已并入）。raw 为 None 时返回 None。"""
    if raw is None:
        return None
    scale = _FACTOR_SCALE.get(fid, 1.0)
    direction = _FACTOR_DIR.get(fid, 1.0)
    z = raw / scale if scale else raw
    return round(50.0 + 45.0 * math.tanh(direction * z), 1)


def _factor_items(latest: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    """构造前端展示用的因子条目列表（含不可用因子的 null + reason）。"""
    items: List[Dict[str, Any]] = []
    for f in QLIB_FACTOR_LIBRARY:
        fid = f["id"]
        if f["available"]:
            raw = latest.get(fid)
            items.append({
                "id": fid, "name": f["name"], "category": f["category"],
                "available": True, "raw": (round(raw, 6) if raw is not None else None),
                "score": _norm_score(fid, raw),
                "reason": None if raw is not None else "insufficient_samples",
            })
        else:
            items.append({
                "id": fid, "name": f["name"], "category": f["category"],
                "available": False, "raw": None, "score": None,
                "reason": f.get("unavailable_reason"),
            })
    return items


def _combined_score(latest: Dict[str, Optional[float]], chan: Dict[str, Any]) -> float:
    """加权综合因子分（0~100）+ 缠论买卖点调整。仅用库中可用且算出值的因子。"""
    weights = {f["id"]: f.get("default_weight", 0.0)
               for f in QLIB_FACTOR_LIBRARY if f.get("available")}
    acc = 0.0
    wsum = 0.0
    for fid, w in weights.items():
        sc = _norm_score(fid, latest.get(fid))
        if sc is None or w <= 0:
            continue
        acc += w * sc
        wsum += w
    base = (acc / wsum) if wsum > 0 else 50.0

    cur = chan.get("current") or {}
    if cur.get("buy_point"):
        base += 8
    if cur.get("sell_point"):
        base -= 8
    if cur.get("divergence") == "bottom":
        base += 5
    elif cur.get("divergence") == "top":
        base -= 5
    return round(max(0.0, min(100.0, base)), 1)


def _signal_from(score: float, chan: Dict[str, Any]) -> str:
    cur = chan.get("current") or {}
    if cur.get("buy_point") and score >= 60:
        return "缠论买点"
    if cur.get("sell_point"):
        return "缠论卖点"
    if score >= 75:
        return "强力关注"
    if score >= 60:
        return "关注"
    return "观望"


qlib_engine = QlibFactorEngine()
