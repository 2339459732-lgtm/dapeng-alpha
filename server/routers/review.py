"""北极星复盘系统 — 只读指标计算 + 周报复盘（v1.0）

目标口径（docs/product-roadmap-2026.md 〇章）：
  纸面账户 90 天滚动窗口持续跑赢沪深300（≥基准+3pct），且每笔交易 100% 可回溯。

数据来源（全部已具备，零新表）：
  - orders（filled 成交）→ FIFO 配对出闭合交易，算胜率/盈亏比
  - positions + paper_account KV → 当前权益
  - config KV 'equity_snapshots' → 每日权益快照（本模块幂等记录，随调用逐日累积）
  - 腾讯日K（sh000300）→ 沪深300 基准收益与回撤
  - signals/orders 关联 → 可回溯率

端点：
  GET /api/review/summary?days=90   北极星指标总览（含达标判定）
  GET /api/review/equity?days=90    净值曲线 vs 基准（归一化）
  GET /api/review/weekly            本周复盘报告（自动生成并落 KV，供连续周数统计）
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from server.cache import cache
from server.tencent_kline import fetch_tencent_kline
from server.trading import costs as trading_costs
from server.trading import trading_facade
from server.trading.store import TradingStore

logger = logging.getLogger(__name__)

router = APIRouter()

_CODE_RE = re.compile(r"^(sh|sz|hk)\d{5,6}$")

BENCHMARK_CODE = "sh000300"
BENCHMARK_NAME = "沪深300"
SNAPSHOT_KEY = "equity_snapshots"
WEEKLY_KEY = "weekly_reports"

# 北极星达标线（与 roadmap 〇章一致）
TARGET_EXCESS_PCT = 3.0        # 90 天超额收益 ≥ +3pct
TARGET_MAXDD_PCT = 10.0        # 最大回撤 ≤ 10%
TARGET_WIN_RATE_PCT = 55.0     # 胜率 ≥ 55%
TARGET_PROFIT_FACTOR = 1.5     # 盈亏比 ≥ 1.5
MIN_TRADES_FOR_STATS = 5       # 不足 5 笔闭合交易时质量指标记 na


# ============================================================
# 数据装配
# ============================================================

# 数据装配已下沉到 server/services/review.py（供路由与交易调度器共用）。
# 以下别名保持历史引用可用（automation.py 等仍以 _xxx 形式调用）。
from server.trading.review_service import (  # noqa: E402
    BENCHMARK_CODE,
    BENCHMARK_NAME,
    MIN_TRADES_FOR_STATS,
    SNAPSHOT_KEY,
    TARGET_EXCESS_PCT,
    TARGET_MAXDD_PCT,
    TARGET_PROFIT_FACTOR,
    TARGET_WIN_RATE_PCT,
    WEEKLY_KEY,
    benchmark_series as _benchmark_series,
    current_equity as _current_equity,
    max_drawdown_pct as _max_drawdown_pct,
    record_snapshot as _record_snapshot,
    round_trips as _round_trips,
    sharpe_and_vol as _sharpe_and_vol,
    get_store as _store,
    weekly_report,
    window_maxdd_pct as _window_maxdd_pct,
    window_return_pct as _window_return_pct,
)
from server.contracts.envelope import fail


# ============================================================
# 端点
# ============================================================

@router.get("/review/summary")
async def review_summary(days: int = Query(90, ge=7, le=365),
                         include_drill: bool = Query(False)):
    """北极星指标总览：超额收益 / 回撤 / 胜率盈亏比 / 可回溯率 + 逐项达标判定

    include_drill=false（默认）排除演练造数订单，仅统计真实交易；返回体给出排除条数。
    """
    store = await _store()
    eq = await _current_equity(store)
    snaps = await _record_snapshot(store, eq["equity"])
    trips = await _round_trips(store, include_drill=include_drill)
    all_trips = await _round_trips(store, include_drill=True)
    drill_excluded = len(all_trips) - len(trips)
    exit_reasons: Dict[str, int] = {}
    for t in trips:
        key = t.get("exit_reason") or "unlabeled"
        exit_reasons[key] = exit_reasons.get(key, 0) + 1

    # 收益与回撤（净值曲线：快照 + 起始日锚点）
    curve = [{"date": eq["start_date"], "equity": eq["initial"]}] if eq["start_date"] else []
    curve += [s for s in snaps if not eq["start_date"] or s["date"] > eq["start_date"]]
    curve = curve[-(days + 1):]
    equity_pts = [p["equity"] for p in curve]
    total_return_pct = round((eq["equity"] / eq["initial"] - 1) * 100, 2) if eq["initial"] else 0.0
    maxdd_pct = _max_drawdown_pct(equity_pts)
    sharpe, ann_vol_pct, sharpe_reason = _sharpe_and_vol(equity_pts)

    # 基准
    bench = await _benchmark_series(days)
    bench_return_pct = _window_return_pct(bench, days)
    bench_maxdd_pct = _window_maxdd_pct(bench, days)
    excess_pct = round(total_return_pct - bench_return_pct, 2) if bench_return_pct is not None else None

    # 质量指标（**净口径**：盈亏已扣除佣金/印花税/过户费）
    n = len(trips)
    wins = [t for t in trips if t["pnl"] > 0]
    gross_profit = sum(t["pnl"] for t in trips if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trips if t["pnl"] < 0))
    pnl_gross_total = round(sum(t.get("pnl_gross", t["pnl"]) for t in trips), 2)
    fees_total = round(sum(t.get("fees", 0.0) for t in trips), 2)
    win_rate_pct = round(len(wins) / n * 100, 1) if n else None
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if n else None)
    stats_ready = n >= MIN_TRADES_FOR_STATS

    # 过程指标：可回溯率（filled 订单中挂 signal_id 的比例；无订单时视为 100%）
    _, orders = await store.list_orders(limit=10000)
    filled_orders = [o for o in orders if o.get("status") == "filled" and o.get("gateway") == "paper"]
    if filled_orders:
        traced = sum(1 for o in filled_orders if o.get("signal_id"))
        traceability_pct = round(traced / len(filled_orders) * 100, 1)
    else:
        traceability_pct = 100.0

    weekly_reports = await store.kv_get(WEEKLY_KEY, []) or []
    weekly_streak = len(weekly_reports)

    # 逐项达标判定（数据不足记 na，不算失败）
    def stat(ok: Optional[bool]) -> str:
        return "na" if ok is None else ("pass" if ok else "fail")

    targets = [
        {"key": "excess", "label": f"{days}天超额收益 vs {BENCHMARK_NAME}", "target": f"≥+{TARGET_EXCESS_PCT}pct",
         "value": excess_pct, "unit": "pct",
         "status": stat(None if excess_pct is None or len(equity_pts) < 20 else excess_pct >= TARGET_EXCESS_PCT)},
        {"key": "maxdd", "label": "最大回撤", "target": f"≤{TARGET_MAXDD_PCT}% 且≤基准",
         "value": maxdd_pct, "unit": "%",
         "status": stat(None if bench_return_pct is None else (maxdd_pct <= TARGET_MAXDD_PCT and maxdd_pct <= bench_maxdd_pct))},
        {"key": "win_rate", "label": "胜率", "target": f"≥{TARGET_WIN_RATE_PCT}%",
         "value": win_rate_pct, "unit": "%",
         "status": stat(None if not stats_ready else win_rate_pct >= TARGET_WIN_RATE_PCT)},
        {"key": "profit_factor", "label": "盈亏比", "target": f"≥{TARGET_PROFIT_FACTOR}",
         "value": profit_factor, "unit": "",
         "status": stat(None if not stats_ready or profit_factor is None else profit_factor >= TARGET_PROFIT_FACTOR)},
        {"key": "traceability", "label": "交易可回溯率", "target": "100%",
         "value": traceability_pct, "unit": "%",
         "status": stat(traceability_pct >= 100.0)},
        {"key": "weekly_streak", "label": "周复盘连续性", "target": "每周不缺",
         "value": weekly_streak, "unit": "周",
         "status": stat(weekly_streak >= 1 if trips else None)},
    ]

    note = ("纸面账户尚未产生闭合交易，指标随交易发生自动累积。"
            if n == 0 else
            f"已闭合 {n} 笔交易；盈亏为**净口径**（已扣佣金/印花税/过户费/滑点），"
            f"毛盈亏 {pnl_gross_total:+.2f} 元、交易成本 {fees_total:.2f} 元、净盈亏 {round(pnl_gross_total - fees_total, 2):+.2f} 元。")

    return {"ok": True, "data": {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window_days": days,
        "equity": {"now": round(eq["equity"], 2), "initial": eq["initial"],
                   "cash": round(eq["cash"], 2), "market_value": round(eq["market_value"], 2),
                   "total_return_pct": total_return_pct, "start_date": eq["start_date"]},
        "benchmark": {"code": BENCHMARK_CODE, "name": BENCHMARK_NAME,
                      "return_pct": bench_return_pct, "maxdd_pct": bench_maxdd_pct},
        "excess_return_pct": excess_pct,
        "risk": {"maxdd_pct": maxdd_pct, "curve_points": len(equity_pts),
                 "sharpe": sharpe, "annual_vol_pct": ann_vol_pct,
                 "sharpe_reason": sharpe_reason},
        "quality": {"trades": n, "win_rate_pct": win_rate_pct, "profit_factor": profit_factor,
                    "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
                    "pnl_basis": "net",
                    "fees_total": fees_total, "pnl_gross_total": pnl_gross_total,
                    "pnl_net_total": round(pnl_gross_total - fees_total, 2),
                    "include_drill": include_drill,
                    "drill_trades_excluded": drill_excluded,
                    "exit_reasons": exit_reasons,
                    "stats_ready": stats_ready},
        "process": {"traceability_pct": traceability_pct, "weekly_streak": weekly_streak},
        "targets": targets,
        "note": note,
    }}


@router.get("/review/equity")
async def review_equity(days: int = Query(90, ge=7, le=365)):
    """净值曲线 vs 沪深300（窗口首日归一化为 1.0）"""
    store = await _store()
    eq = await _current_equity(store)
    snaps = await _record_snapshot(store, eq["equity"])

    curve = [{"date": eq["start_date"], "equity": eq["initial"]}] if eq["start_date"] else []
    curve += [s for s in snaps if not eq["start_date"] or s["date"] > eq["start_date"]]
    curve = curve[-(days + 1):]

    bench = await _benchmark_series(days)
    bench_pts = [p for p in bench if p.get("close", 0) > 0][-(days + 1):]

    def normalize(points: List[float]) -> List[float]:
        if not points or points[0] <= 0:
            return []
        base = points[0]
        return [round(v / base, 4) for v in points]

    dates = [p["date"] for p in curve]
    bench_dates = [p["date"] for p in bench_pts]
    return {"ok": True, "data": {
        "window_days": days,
        "equity": {"dates": dates, "values": normalize([p["equity"] for p in curve])},
        "benchmark": {"code": BENCHMARK_CODE, "name": BENCHMARK_NAME,
                      "dates": bench_dates, "values": normalize([p["close"] for p in bench_pts])},
    }}


@router.get("/review/weekly")
async def review_weekly(include_drill: bool = Query(False)):
    """本周复盘报告：自动生成 + 落 KV（同周重复调用覆盖，供连续周数统计）。"""
    return {"ok": True, "data": await weekly_report(include_drill=include_drill)}


# ============================================================
# 闭合交易明细（供 K 线买卖点标注）
# ============================================================

def _valid_code(code: str) -> bool:
    return bool(isinstance(code, str) and _CODE_RE.match(code))


#: 统一失败信封来自 contracts（contract-envelope.v1）
_fail = fail


@router.get("/review/round-trips")
async def review_round_trips(
    code: str = Query(..., description="标的代码，形如 sh600519"),
    include_drill: bool = Query(False, description="是否包含演练造数订单"),
    limit: int = Query(500, ge=1, le=5000),
):
    """指定标的的闭合交易（FIFO 净口径）——每笔含 K 线买卖点标注字段。

    复用 `_round_trips` 同一配对与费用口径，保证与复盘完全一致（不另造算法）。
    默认排除演练造数订单（is_drill=1）；include_drill=true 时纳入并在每条带 is_drill。
    返回值字段：code/name/qty/open_ts/close_ts/buy_price/sell_price/pnl/pnl_gross/fees/
    strategy_id/is_drill/exit_reason（历史订单 exit_reason 可能为 null，前端显示"未标注"）。
    """
    store = await _store()
    c = (code or "").strip().lower()
    if not _valid_code(c):
        return _fail("股票代码格式非法（应为 sh600519 形式）")
    trips = await _round_trips(store, include_drill=include_drill)
    code_trips = [t for t in trips if t.get("code") == c]
    items = code_trips[:limit]
    return {"ok": True, "data": {"code": c, "total": len(code_trips), "items": items}}


# ============================================================
# 单笔复盘与历史周报透出（自动化中心复盘区数据源）
# ============================================================

@router.get("/review/trade-reviews")
async def review_trade_reviews(
    code: Optional[str] = Query(None, description="按标的过滤，形如 sh600519"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """单笔复盘列表（KV trade_reviews，review_engine.TradeReviewer 产出）。

    每条含 mfe_pct/mae_pct/exit_efficiency/post_exit_rebound_pct/bench_chg_pct/
    conclusion/tags/verdict 等归因字段；按 created_at 倒序。
    """
    store = await _store()
    reviews = await store.kv_get("trade_reviews", []) or []
    if code:
        c = code.strip().lower()
        reviews = [r for r in reviews if r.get("code") == c]
    reviews = sorted(reviews, key=lambda r: r.get("created_at") or "", reverse=True)
    total = len(reviews)
    return {"ok": True, "data": {"total": total, "items": reviews[offset:offset + limit]}}


@router.get("/review/weekly-list")
async def review_weekly_list(limit: int = Query(60, ge=1, le=60)):
    """历史周报列表（KV weekly_reports，每周由 /review/weekly 幂等落一份），新周在前。"""
    store = await _store()
    reports = await store.kv_get(WEEKLY_KEY, []) or []
    reports = sorted(reports, key=lambda r: r.get("week") or "", reverse=True)
    return {"ok": True, "data": {"total": len(reports), "items": reports[:limit]}}
