"""复盘与周报服务（trading.review_service.v1）。

职责：纸面账户的权益、闭合交易配对、基准对比与周报生成。
属于交易域（与 review_engine 同级），不依赖 FastAPI；供 HTTP 路由与
交易调度器共用，消除 `loop_scheduler → routers.review` 的反向依赖。

口径与路由层完全一致：FIFO 净口径（费用已扣）、演练单默认排除。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from server.cache import cache
from server.tencent_kline import fetch_tencent_kline
from server.trading import costs as trading_costs
from server.trading import trading_facade
from server.trading.store import TradingStore

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^(sh|sz|hk)\d{5,6}$")

BENCHMARK_CODE = "sh000300"
BENCHMARK_NAME = "沪深300"
SNAPSHOT_KEY = "equity_snapshots"
WEEKLY_KEY = "weekly_reports"

TARGET_EXCESS_PCT = 3.0
TARGET_MAXDD_PCT = 10.0
TARGET_WIN_RATE_PCT = 55.0
TARGET_PROFIT_FACTOR = 1.5
MIN_TRADES_FOR_STATS = 5


async def get_store() -> TradingStore:
    await trading_facade.ensure_ready()
    return trading_facade.store


async def current_equity(store) -> Dict[str, float]:
    """当前权益 = 现金 + 持仓市值（last_price 缺失时按成本计）"""
    account = await store.kv_get("paper_account", {}) or {}
    cash = float(account.get("cash", 0.0))
    initial = float(account.get("initial_cash", 1000000.0))
    positions = await store.list_positions("paper")
    mv = 0.0
    for p in positions:
        px = p.last_price if p.last_price and p.last_price > 0 else p.avg_cost
        mv += p.qty * px
    return {"cash": cash, "market_value": mv, "equity": cash + mv, "initial": initial,
            "start_date": account.get("start_date", "")}


async def record_snapshot(store, equity: float) -> List[Dict[str, Any]]:
    """幂等记录今日权益快照（同日重复调用只覆盖，不追加）"""
    snaps = await store.kv_get(SNAPSHOT_KEY, []) or []
    today = datetime.now().strftime("%Y-%m-%d")
    snaps = [s for s in snaps if s.get("date") != today]
    snaps.append({"date": today, "equity": round(equity, 2)})
    snaps = snaps[-400:]
    await store.kv_set(SNAPSHOT_KEY, snaps)
    return snaps


async def round_trips(store, include_drill: bool = False) -> List[Dict[str, Any]]:
    """FIFO 配对 filled 买卖单 → 闭合交易列表（**净口径**：已扣佣金/印花税/过户费/滑点）

    每笔订单保留 price（含滑点成交价）与 total_cost（买入=含费总成本，卖出=扣费净收入）；
    配对时按每股全含成本计算净盈亏；无费用字段的历史订单纯按 price 计算（兼容）。
    include_drill=False（默认）排除演练造数订单（is_drill=1）。
    """
    _, orders = await store.list_orders(limit=10000)
    filled = [o for o in orders if o.get("status") == "filled" and o.get("gateway") == "paper"]
    if not include_drill:
        filled = [o for o in filled if not o.get("is_drill")]
    filled.sort(key=lambda o: o.get("ts") or "")
    lots: Dict[str, List[list]] = {}
    trips: List[Dict[str, Any]] = []
    for o in filled:
        code = o["code"]
        lots.setdefault(code, [])
        uc = trading_costs.unit_cost(o)          # 每股全含成本 / 净收入
        gp = float(o.get("price") or 0.0)        # 含滑点成交价（毛价）
        if o["side"] == "buy":
            lots[code].append([o["qty"], uc, gp, o.get("ts"),
                               o.get("strategy_id"), bool(o.get("is_drill"))])
        else:  # sell
            remain = o["qty"]
            while remain > 0 and lots[code]:
                lot = lots[code][0]
                take = min(remain, lot[0])
                pnl_net = round((uc - lot[1]) * take, 2)
                pnl_gross = round((gp - lot[2]) * take, 2)
                trips.append({
                    "code": code, "name": o.get("name", ""),
                    "qty": take, "buy_price": lot[2], "sell_price": gp,
                    "open_ts": lot[3], "close_ts": o.get("ts"),
                    "pnl": pnl_net, "pnl_gross": pnl_gross,
                    "fees": round(pnl_gross - pnl_net, 2),
                    "strategy_id": o.get("strategy_id") or lot[4],
                    "signal_id": o.get("signal_id"),
                    "exit_reason": o.get("exit_reason"),
                    "is_drill": bool(o.get("is_drill") or lot[5]),
                })
                remain -= take
                lot[0] -= take
                if lot[0] <= 0:
                    lots[code].pop(0)
                else:
                    lots[code][0][0] = lot[0]
    return trips


def max_drawdown_pct(points: List[float]) -> float:
    """最大回撤（百分比），不足 2 点返回 0"""
    if len(points) < 2:
        return 0.0
    peak = points[0]
    mdd = 0.0
    for v in points:
        if v > peak:
            peak = v
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak * 100.0)
    return round(abs(mdd), 2)


async def benchmark_series(days: int) -> List[Dict[str, Any]]:
    """沪深300 日K（缓存 1 小时）"""
    ck = f"review:benchmark:{days}"
    cached = cache.get(ck)
    if cached:
        return cached
    try:
        ks = await fetch_tencent_kline(BENCHMARK_CODE, period="day", limit=days + 15)
        series = [{"date": k.get("d", ""), "close": float(k.get("c", 0))} for k in (ks or []) if isinstance(k, dict)]
    except Exception as e:
        logger.warning("benchmark kline fetch failed: %s", e)
        series = []
    if series:
        cache.set(ck, series, 3600)
    return series


def window_return_pct(series: List[Dict[str, Any]], days: int) -> Optional[float]:
    """窗口收益（首→末收盘），数据不足返回 None"""
    s = [p for p in series if p.get("close", 0) > 0][-(days + 1):]
    if len(s) < 2:
        return None
    return round((s[-1]["close"] / s[0]["close"] - 1) * 100, 2)


def window_maxdd_pct(series: List[Dict[str, Any]], days: int) -> float:
    s = [p["close"] for p in series if p.get("close", 0) > 0][-(days + 1):]
    return max_drawdown_pct(s)


def sharpe_and_vol(equity_pts: List[float]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """夏普比率（年化，无风险利率按 0 计）与年化波动率（%）。

    数据源为权益快照日频序列；点数 < 5 时返回 (None, None, reason)——
    如实降级，不编造风险指标。
    """
    pts = [float(v) for v in equity_pts if v and v > 0]
    if len(pts) < 5:
        return None, None, f"权益快照不足（{len(pts)}<5 点），夏普/波动率暂不可算"
    rets = [pts[i] / pts[i - 1] - 1.0 for i in range(1, len(pts))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    sd = var ** 0.5
    ann_vol_pct = round(sd * (252 ** 0.5) * 100, 2)
    if sd <= 0:
        return None, ann_vol_pct, "日收益方差为 0（权益曲线无波动），夏普比率不适用"
    sharpe = round(mean / sd * (252 ** 0.5), 2)
    return sharpe, ann_vol_pct, None


async def weekly_report(include_drill: bool = False) -> Dict[str, Any]:
    """生成本周复盘并幂等落 KV（同周覆盖），返回报告体。"""
    store = await get_store()
    eq = await current_equity(store)
    trips = await round_trips(store, include_drill=include_drill)

    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_label = now.strftime("%G-W%V")

    week_trips = [t for t in trips if (t.get("close_ts") or "") >= week_start]
    n = len(week_trips)
    pnl = round(sum(t["pnl"] for t in week_trips), 2)
    wins = [t for t in week_trips if t["pnl"] > 0]
    win_rate_pct = round(len(wins) / n * 100, 1) if n else None

    snaps = await record_snapshot(store, eq["equity"])
    prev = next((s for s in reversed(snaps) if s["date"] < week_start), None)
    equity_change_pct = round((eq["equity"] / prev["equity"] - 1) * 100, 2) if prev and prev["equity"] else 0.0

    bench = await benchmark_series(10)
    bench_week = [p for p in bench if p["date"] >= week_start and p.get("close", 0) > 0]
    bench_change_pct = round((bench_week[-1]["close"] / bench_week[0]["close"] - 1) * 100, 2) if len(bench_week) >= 2 else None

    if n == 0:
        narrative = f"本周（{week_start} 起）无闭合交易。当前权益 ¥{eq['equity']:,.0f}，较上周 {equity_change_pct:+.2f}%。保持策略在线，等待信号触发。"
    else:
        best = max(week_trips, key=lambda t: t["pnl"])
        worst = min(week_trips, key=lambda t: t["pnl"])
        narrative = (f"本周闭合 {n} 笔，合计盈亏 ¥{pnl:+,.0f}，胜率 {win_rate_pct}%。"
                     f"最佳：{best['name']} ¥{best['pnl']:+,.0f}；最差：{worst['name']} ¥{worst['pnl']:+,.0f}。")

    report = {"week": week_label, "from": week_start, "trades": n, "pnl": pnl,
              "win_rate_pct": win_rate_pct, "equity_change_pct": equity_change_pct,
              "benchmark_change_pct": bench_change_pct, "narrative": narrative,
              "generated_at": now.strftime("%Y-%m-%d %H:%M")}

    reports = await store.kv_get(WEEKLY_KEY, []) or []
    reports = [r for r in reports if r.get("week") != week_label]
    reports.append(report)
    await store.kv_set(WEEKLY_KEY, reports[-60:])

    return report
