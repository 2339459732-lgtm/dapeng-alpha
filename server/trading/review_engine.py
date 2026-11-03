"""闭环核心件 ① —— 单笔交易自动复盘引擎（TradeReviewer）

闭环定位：执行交易 →【复盘分析】→ 策略积累 → 动态优化

职责：
  - 对每笔新闭合的模拟交易（FIFO 配对）自动归因：持仓期 MFE/MAE、
    出场效率、卖出后回升、同期基准对比 → 规则化中文结论 + 标签；
  - 幂等落 KV 'trade_reviews'（按闭合交易唯一 id 去重），无需人工整理；
  - 附带账户级工具：current_equity / record_snapshot（与 routers/review.py 同口径，
    独立实现以保持复盘路由零改动）。

被 loop_scheduler 每日收盘后调用；数据供 evolution 路由与 iterator 消费。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from server.cache import cache
from server.tencent_kline import fetch_tencent_kline

from . import costs as trading_costs
from .store import TradingStore

logger = logging.getLogger(__name__)

TRADE_REVIEWS_KEY = "trade_reviews"
SNAPSHOT_KEY = "equity_snapshots"
DECISION_QUALITY_KEY = "decision_quality"
BENCHMARK_CODE = "sh000300"
_MAX_REVIEWS = 300          # KV 中最多保留的单笔复盘条数
_POST_EXIT_BARS = 5         # 卖出后观察回升的 K 线根数


# ============================================================
# 账户级工具（与 routers/review.py 同口径）
# ============================================================

async def current_equity(store: TradingStore) -> Dict[str, float]:
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


async def record_snapshot(store: TradingStore, equity: float) -> List[Dict[str, Any]]:
    """幂等记录今日权益快照（同日重复只覆盖）"""
    snaps = await store.kv_get(SNAPSHOT_KEY, []) or []
    today = datetime.now().strftime("%Y-%m-%d")
    snaps = [s for s in snaps if s.get("date") != today]
    snaps.append({"date": today, "equity": round(equity, 2)})
    snaps = snaps[-400:]
    await store.kv_set(SNAPSHOT_KEY, snaps)
    return snaps


def round_trips_with_entry(orders: List[Dict[str, Any]], exclude_drill: bool = True) -> List[Dict[str, Any]]:
    """FIFO 配对 filled 模拟单 → 闭合交易（保留买/卖双侧时间与订单号，供复盘归因）

    **净口径**：pnl 已扣除佣金/印花税/过户费/滑点；同时给出 pnl_gross 与 fees 便于对比。
    exclude_drill=True（默认）排除演练造数订单（is_drill），避免污染策略统计/复盘归因。
    """
    filled = [o for o in orders if o.get("status") == "filled" and o.get("gateway") == "paper"]
    if exclude_drill:
        filled = [o for o in filled if not o.get("is_drill")]
    filled.sort(key=lambda o: o.get("ts") or "")
    lots: Dict[str, List[list]] = {}
    trips: List[Dict[str, Any]] = []
    for o in filled:
        code = o["code"]
        lots.setdefault(code, [])
        uc = trading_costs.unit_cost(o)      # 每股全含成本 / 净收入
        if o["side"] == "buy":
            lots[code].append([o["qty"], uc, float(o.get("price") or 0.0),
                               o.get("ts"), o.get("id"),
                               o.get("strategy_id"), o.get("signal_id")])
        else:
            remain = o["qty"]
            sell_uc = uc
            sell_gp = float(o.get("price") or 0.0)
            while remain > 0 and lots[code]:
                lot = lots[code][0]
                take = min(remain, lot[0])
                pnl_net = round((sell_uc - lot[1]) * take, 2)
                pnl_gross = round((sell_gp - lot[2]) * take, 2)
                trips.append({
                    "code": code, "name": o.get("name", ""),
                    "qty": take, "buy_price": lot[2], "sell_price": sell_gp,
                    "buy_unit_cost": lot[1], "sell_unit_cost": sell_uc,
                    "pnl": pnl_net, "pnl_gross": pnl_gross,
                    "fees": round(pnl_gross - pnl_net, 2),
                    "buy_ts": lot[3], "sell_ts": o.get("ts"), "open_ts": lot[3],
                    "close_ts": o.get("ts"),
                    "buy_order_id": lot[4], "sell_order_id": o.get("id"),
                    "strategy_id": o.get("strategy_id") or lot[5],
                    "signal_id": o.get("signal_id") or lot[6],
                    "exit_reason": o.get("exit_reason"),
                    "is_drill": bool(o.get("is_drill")),
                })
                remain -= take
                lot[0] -= take
                if lot[0] <= 0:
                    lots[code].pop(0)
    return trips


def trip_id(t: Dict[str, Any]) -> str:
    """闭合交易唯一 id（幂等键）"""
    return f"{t['code']}:{t.get('buy_ts')}:{t.get('sell_ts')}:{t['qty']}"


# ============================================================
# 单笔复盘引擎
# ============================================================

class TradeReviewer:
    """对每笔新闭合交易自动归因并生成中文结论（零 LLM 依赖，纯规则可解释）"""

    def __init__(self, store: TradingStore):
        self._store = store

    # --------------------------------------------------------
    # 主入口（幂等）：返回本次新增的复盘条数
    # --------------------------------------------------------
    async def review_new_trades(self, exclude_drill: bool = True) -> Dict[str, Any]:
        """对每笔新闭合的真实交易归因（默认排除演练造数订单）。返回新增/总数。"""
        _, orders = await self._store.list_orders(limit=10000)
        all_trips = round_trips_with_entry(orders, exclude_drill=False)
        trips = round_trips_with_entry(orders, exclude_drill=exclude_drill)
        drill_excluded = len(all_trips) - len(trips)
        reviews: List[Dict[str, Any]] = await self._store.kv_get(TRADE_REVIEWS_KEY, []) or []
        done = {r.get("id") for r in reviews}

        new_count = 0
        for t in trips:
            tid = trip_id(t)
            if tid in done:
                continue
            try:
                review = await self._review_one(tid, t)
            except Exception:
                logger.warning("TradeReviewer: 复盘 %s 失败", tid, exc_info=True)
                continue
            reviews.append(review)
            done.add(tid)
            new_count += 1

        if new_count:
            await self._store.kv_set(TRADE_REVIEWS_KEY, reviews[-_MAX_REVIEWS:])
            await self._store.audit("loop", "trade_reviews_added", {
                "count": new_count, "trades_excluded_drill": drill_excluded})
        return {"new_reviews": new_count, "total": len(reviews),
                "trades_excluded_drill": drill_excluded}

    # --------------------------------------------------------
    # 单笔归因
    # --------------------------------------------------------
    async def _review_one(self, tid: str, t: Dict[str, Any]) -> Dict[str, Any]:
        buy_date = (t.get("buy_ts") or "")[:10]
        sell_date = (t.get("sell_ts") or "")[:10]
        pnl = t["pnl"]
        buy_uc = t.get("buy_unit_cost") or t["buy_price"]
        sell_uc = t.get("sell_unit_cost") or t["sell_price"]
        pnl_pct = round((sell_uc / buy_uc - 1) * 100, 2) if buy_uc else 0.0

        # 持仓窗口 K 线（MFE/MAE、卖出后回升）
        mfe_pct: Optional[float] = None
        mae_pct: Optional[float] = None
        post_rebound_pct: Optional[float] = None
        holding_days: Optional[int] = None
        try:
            ks = await self._kline_cached(t["code"])
            hold = [k for k in ks if buy_date <= str(k.get("d", "")) <= sell_date]
            holding_days = len(hold) or None
            if hold and t["buy_price"] > 0:
                mfe_pct = round((max(float(k.get("h", k.get("c", 0))) for k in hold)
                                 / t["buy_price"] - 1) * 100, 2)
                mae_pct = round((min(float(k.get("l", k.get("c", 0))) for k in hold)
                                 / t["buy_price"] - 1) * 100, 2)
            after = [k for k in ks if str(k.get("d", "")) > sell_date][:_POST_EXIT_BARS]
            if after and t["sell_price"] > 0:
                post_rebound_pct = round(
                    (max(float(k.get("c", 0)) for k in after) / t["sell_price"] - 1) * 100, 2)
        except Exception:
            logger.info("TradeReviewer: %s K线不可用，降级为基础复盘", tid)

        # 同期基准涨跌
        bench_chg_pct: Optional[float] = None
        try:
            bs = await self._benchmark_cached()
            win = [p for p in bs if buy_date <= p["date"] <= sell_date]
            if len(win) >= 2 and win[0]["close"] > 0:
                bench_chg_pct = round((win[-1]["close"] / win[0]["close"] - 1) * 100, 2)
        except Exception:
            pass

        conclusion, tags = self._attribute(
            pnl=pnl, pnl_pct=pnl_pct, mfe=mfe_pct, mae=mae_pct,
            post_rebound=post_rebound_pct, bench_chg=bench_chg_pct,
            holding_days=holding_days, exit_reason=t.get("exit_reason"),
        )

        efficiency: Optional[float] = None
        if mfe_pct is not None and mfe_pct > 0.5:
            efficiency = round(max(min(pnl_pct / mfe_pct, 1.5), -1.5), 2)

        return {
            "id": tid, "code": t["code"], "name": t.get("name") or t["code"],
            "strategy_id": t.get("strategy_id"), "signal_id": t.get("signal_id"),
            "qty": t["qty"], "buy_price": t["buy_price"], "sell_price": t["sell_price"],
            "buy_unit_cost": round(buy_uc, 4), "sell_unit_cost": round(sell_uc, 4),
            "buy_ts": t.get("buy_ts"), "sell_ts": t.get("sell_ts"),
            "holding_days": holding_days,
            "pnl": pnl, "pnl_gross": t.get("pnl_gross"), "fees": t.get("fees"),
            "cost_basis": "net", "pnl_pct": pnl_pct,
            "exit_reason": t.get("exit_reason"),
            "verdict": "win" if pnl > 0 else "loss",
            "mfe_pct": mfe_pct, "mae_pct": mae_pct, "exit_efficiency": efficiency,
            "post_exit_rebound_pct": post_rebound_pct, "bench_chg_pct": bench_chg_pct,
            "conclusion": conclusion, "tags": tags,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _attribute(*, pnl: float, pnl_pct: float, mfe: Optional[float],
                   mae: Optional[float], post_rebound: Optional[float],
                   bench_chg: Optional[float], holding_days: Optional[int],
                   exit_reason: Optional[str] = None,
                   ) -> Tuple[str, List[str]]:
        """规则归因 → (中文结论, 标签)。可解释优先，不追求 LLM 文采。"""
        tags: List[str] = []
        parts: List[str] = []

        # 出场归因（orders.exit_reason）：区分止盈/移动止盈/固定止损/信号出场
        _EXIT_LABEL = {
            "take_profit": "固定止盈出场",
            "trailing_stop": "移动止盈出场",
            "fixed_stop": "固定止损出场",
            "signal": "策略信号出场",
        }
        if exit_reason in _EXIT_LABEL:
            label = _EXIT_LABEL[exit_reason]
            parts.append(label)
            tags.append(label)

        if pnl > 0:
            eff = (pnl_pct / mfe) if (mfe is not None and mfe > 0.5) else None
            if eff is not None and eff >= 0.7:
                tags.append("符合预期")
                parts.append(f"入场判断正确、出场时机良好，捕获了最大浮盈的 {eff * 100:.0f}%")
            elif eff is not None and eff >= 0.3:
                tags.append("止盈偏早")
                parts.append(f"方向正确但出场偏早：最大浮盈曾达 {mfe:+.1f}%，实际落袋 {pnl_pct:+.1f}%，"
                             f"可评估止盈规则是否过紧")
            elif eff is not None:
                tags.append("浮盈回吐")
                parts.append(f"最大浮盈 {mfe:+.1f}% 后明显回落才出场，仅捕获 {eff * 100:.0f}%，"
                             f"警惕'坐电梯'，可评估移动止盈")
            else:
                tags.append("符合预期")
                parts.append(f"盈利 {pnl_pct:+.1f}% 出场")
        else:
            if post_rebound is not None and post_rebound >= 3.0:
                tags.append("止损被洗")
                parts.append(f"卖出后 {_POST_EXIT_BARS} 日内价格回升 {post_rebound:+.1f}%，"
                             f"疑似被洗盘出局——入场逻辑可能对，止损偏紧或离场过早")
            elif bench_chg is not None and pnl_pct < bench_chg - 2.0:
                tags.append("个股走弱")
                parts.append(f"持有期跑输基准 {bench_chg - pnl_pct:.1f}pct，"
                             f"个股走弱而非系统性下跌，入场筛选需加强")
            elif mae is not None and mae <= -8.0:
                tags.append("波动超限")
                parts.append(f"持仓期最大浮亏曾达 {mae:+.1f}%，入场点或仓位承受了过大波动")
            else:
                tags.append("正常止损")
                parts.append("按策略规则出场，亏损在策略预期范围内")

        if holding_days is not None:
            if holding_days <= 2:
                tags.append("快进快出")
            elif holding_days >= 20:
                tags.append("长线持有")
            parts.append(f"持仓 {holding_days} 个交易日")

        return "；".join(parts) + "。", tags

    # --------------------------------------------------------
    # 数据（带缓存）
    # --------------------------------------------------------
    async def _kline_cached(self, code: str) -> List[Dict[str, Any]]:
        ck = f"review_engine:kline:{code}"
        cached = cache.get(ck)
        if cached:
            return cached
        ks = await fetch_tencent_kline(code, period="day", limit=120)
        rows = [k for k in (ks or []) if isinstance(k, dict) and "c" in k]
        if rows:
            cache.set(ck, rows, 3600)
        return rows

    async def _benchmark_cached(self) -> List[Dict[str, Any]]:
        ck = "review_engine:benchmark"
        cached = cache.get(ck)
        if cached:
            return cached
        ks = await fetch_tencent_kline(BENCHMARK_CODE, period="day", limit=130)
        series = [{"date": k.get("d", ""), "close": float(k.get("c", 0))}
                  for k in (ks or []) if isinstance(k, dict)]
        if series:
            cache.set(ck, series, 3600)
        return series


# ============================================================
# 决策质量评分（G11）：信号强度 vs 实际结果，**含被闸拒绝的决策**
# ============================================================
async def _close_series(code: str) -> List[float]:
    ck = f"decision_quality:kline:{code}"
    cached = cache.get(ck)
    if cached:
        return cached
    ks = await fetch_tencent_kline(code, period="day", limit=120)
    closes = [float(k.get("c") or 0) for k in (ks or []) if isinstance(k, dict)]
    closes = [c for c in closes if c > 0]
    if closes:
        cache.set(ck, closes, 1800)
    return closes


async def score_decisions(store: TradingStore, max_items: int = 400) -> Dict[str, Any]:
    """对每条带 decision_json 的订单（**含被闸拒绝**）评分。

    口径：以订单参考价为基准，用该股**当前最新收盘**的变动作为"决策后实际结果"。
      - allow=True 且成交：结果>0 → good；<0 → bad（技术/闸门判断 vs 实况）
      - allow=False（闸拒绝）：结果 ≤ -2% → gate_correct（拒对了，避免亏损）
                              结果 ≥ +2% → gate_too_strict（闸太严，错失上涨）
                              其余 → gate_neutral
    幂等：按 order_id 去重，落 KV 'decision_quality'。
    """
    _, orders = await store.list_orders(limit=10000)
    existing: List[Dict[str, Any]] = await store.kv_get(DECISION_QUALITY_KEY, []) or []
    done = {x.get("order_id") for x in existing}
    new = 0
    for o in orders:  # list_orders 按 ts DESC
        oid = o.get("id")
        if not oid or oid in done:
            continue
        dj = o.get("decision_json")
        if not isinstance(dj, dict):
            continue
        ref = float(o.get("price") or o.get("ref_price") or 0.0)
        code = o.get("code")
        if ref <= 0 or not code:
            continue
        allow = bool(dj.get("allow"))
        conf = dj.get("confidence")
        closes = await _close_series(code)
        if not closes:
            continue
        latest = closes[-1]
        ret = round((latest / ref - 1.0) * 100, 2)
        if allow:
            verdict = "good" if ret > 0 else ("bad" if ret < 0 else "flat")
        else:
            if ret <= -2.0:
                verdict = "gate_correct"
            elif ret >= 2.0:
                verdict = "gate_too_strict"
            else:
                verdict = "gate_neutral"
        rec = {
            "order_id": oid, "ts": o.get("ts"), "code": code, "side": o.get("side"),
            "status": o.get("status"), "source": o.get("source"),
            "allow": allow, "confidence": conf, "ref_price": ref,
            "latest_close": latest, "outcome_ret_pct": ret,
            "verdict": verdict, "is_drill": bool(o.get("is_drill")),
            "verdict_reason": dj.get("verdict_reason"),
        }
        existing.append(rec)
        done.add(oid)
        new += 1
    if new:
        await store.kv_set(DECISION_QUALITY_KEY, existing[-max_items:])
        await store.audit("loop", "decision_quality_scored", {"new": new, "total": len(existing)})
    return {"new": new, "total": len(existing)}
