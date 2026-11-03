"""
DAPENG Alpha - 自主虚拟盘演练（Paper Trading Drill）V1.0

把「自主虚拟盘买卖 → 总结复盘」完整串起来，任何时段可手动/自动触发，用于：
1. ensure_paper_ready() —— 一键初始化虚拟盘：开启 paper 总开关、白名单覆盖全股票池、
   为「已回测/验证中」的策略启用默认标的集、复位熔断；
2. run_paper_cycle()   —— 跑一轮完整自主交易闭环：
   a) 扫描策略 → 生成真实信号（strategy_buy 等）
   b) 走风控 9 关卡 → 模拟盘真实成交（腾讯现价，落 orders + positions）
   c) 自动平仓（对现有持仓按现价卖出，落 sell 订单，FIFO 配对出闭合交易）
   d) 日结复盘（TradeReviewer 自动归因 → 生成中文复盘结论） + 迭代决策
   → 返回本轮买入/卖出/闭合交易/复盘新增条数，数据记录完整、任务执行彻底。

复用 trading_facade 与 risk.execute_signal 全链，不另造数据。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.cache import cache
from server.config import get_all_stock_codes
from server.realtime import fetch_single_quote
from server.tencent_kline import fetch_tencent_kline

from .models import AutomationConfig, GateState, RiskConfig, Signal, now_local_iso, strategy_source
from . import marks
from .strategies import get as get_strategy, list_enabled as list_enabled_strategies
from .strategies.base import StrategyContext

logger = logging.getLogger(__name__)

_KLINE_LIMIT = 120

# 默认启用标的池（优先取沪深流动性较好的核心资产；白名单覆盖全池）
_DEFAULT_POOL = [
    "sh600519", "sz300750", "sh601318", "sz000858", "sh600036",
    "sz002594", "sh601012", "sz000333", "sh600900", "sz002475",
]


async def ensure_paper_ready(store) -> Dict[str, Any]:
    """一键初始化虚拟盘：paper 开关、白名单、启用策略与标的、复位熔断、放宽演练限额。"""
    from .risk import RiskManager

    # 1) paper 总开关
    auto = AutomationConfig.from_dict(await store.kv_get("automation"))
    auto.switches["paper"] = True
    await store.kv_set("automation", auto.to_dict())

    # 2) 白名单 = 全股票池（使启用的标的能过风控关卡 3）
    risk_cfg = RiskConfig.from_dict(await store.kv_get("risk"))
    risk_cfg.whitelist = list(get_all_stock_codes())
    # 3) 放宽演练限额（模拟盘无真实资金风险，保证一轮闭环能覆盖默认标的池）
    risk_cfg.max_amount_per_order = 50000.0
    risk_cfg.max_amount_per_day = 500000.0
    risk_cfg.max_positions = 20
    await store.kv_set("risk", risk_cfg.to_dict())

    # 4) 为「已回测/验证中」的策略覆盖式启用默认标的集（覆盖遗留的旧单标的配置）
    specs = await store.kv_get("strategies", default=[]) or []
    enabled_ids: List[str] = []
    for spec in specs:
        gate = spec.get("gate_state", GateState.DRAFT.value)
        if gate in (GateState.BACKTESTED.value, GateState.PAPER_VALIDATING.value,
                    GateState.PAPER_PASSED.value):
            spec["enabled_codes"] = list(_DEFAULT_POOL)
            enabled_ids.append(spec["strategy_id"])
    await store.kv_set("strategies", specs)

    # 5) 复位熔断
    risk = RiskManager(store, None)
    await risk.reset_breaker()

    return {"paper_switch": True, "whitelist_size": len(risk_cfg.whitelist),
            "enabled_strategies": enabled_ids,
            "default_pool": _DEFAULT_POOL}


async def _kline(code: str) -> List[Dict[str, Any]]:
    ck = f"paper_drill:kline:{code}"
    cached = cache.get(ck)
    if cached:
        return cached
    ks = await fetch_tencent_kline(code, period="day", limit=_KLINE_LIMIT)
    rows = [k for k in (ks or []) if isinstance(k, dict)]
    if rows:
        cache.set(ck, rows, 3600)
    return rows


async def run_paper_cycle(store, risk, paper, loop, close_positions: bool = True,
                          drill: bool = False) -> Dict[str, Any]:
    """演练闭环入口：进入 drill 上下文（本次产生的所有订单自动 is_drill=1），
    跑完在 finally 复位，避免演练造数污染真实策略统计。"""
    token = marks.begin_drill()
    try:
        return await _run_paper_cycle_inner(
            store, risk, paper, loop, close_positions=close_positions, drill=drill)
    finally:
        marks.end_drill(token)


async def _run_paper_cycle_inner(store, risk, paper, loop, close_positions: bool = True,
                                 drill: bool = False) -> Dict[str, Any]:
    """跑一轮完整自主虚拟盘闭环：买入 →（可选）平仓 → 复盘。

    drill=True 时，对未持仓且无自然买入信号的启用标的补「演练建仓」单，
    保证一轮循环能产出完整的买入→卖出→复盘数据（用于验证与演示闭环）。
    """
    specs = await store.kv_get("strategies", default=[]) or []
    enabled = list_enabled_strategies(specs)
    cfg = AutomationConfig.from_dict(await store.kv_get("automation"))
    now = datetime.now()

    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    bought: set = set()

    # ---- a) 策略扫描 → 买入信号 → 风控下单 ----
    for st in enabled:
        spec = next((s for s in specs if s.get("strategy_id") == st.id), {})
        for code in (spec.get("enabled_codes") or []):
            try:
                klines = await _kline(code)
                if not klines:
                    continue
                quote = await fetch_single_quote(code)
                position = await store.get_position(code, "paper")
                ctx = StrategyContext(quote=quote, position=position,
                                      params=spec.get("params") or st.default_params, now=now)
                sig = st.evaluate(code, klines, ctx)
                if sig is None:
                    continue
                if sig.signal_type != "strategy_buy":
                    continue  # 卖出/止损信号留给持仓退出阶段
                sig.name = (quote or {}).get("name") or sig.name or code
                result = await risk.execute_signal(sig, cfg)
                if result.ok:
                    buys.append({"code": code, "name": sig.name, "qty": result.qty,
                                 "price": result.price, "amount": result.amount, "mode": "signal"})
                    bought.add(code)
            except Exception as exc:
                logger.debug("paper drill buy %s failed: %s", code, exc)

    # ---- a') 演练建仓：drill 模式下为未持仓的启用标的补买入 ----
    if drill:
        for st in enabled:
            spec = next((s for s in specs if s.get("strategy_id") == st.id), {})
            for code in (spec.get("enabled_codes") or []):
                if code in bought:
                    continue
                try:
                    pos = await store.get_position(code, "paper")
                    if pos is not None and pos.qty > 0:
                        continue
                    quote = await fetch_single_quote(code)
                    price = float((quote or {}).get("price") or 0)
                    if price <= 0:
                        continue
                    sig = Signal(
                        code=code, name=(quote or {}).get("name") or code,
                        signal_type="strategy_buy", source=strategy_source(st.id),
                        price=price, chg_pct=0.0,
                        detail={"label": "演练建仓", "desc": "虚拟盘演练补仓，用于完整闭环验证"},
                        ts=now_local_iso(),
                    )
                    result = await risk.execute_signal(sig, cfg)
                    if result.ok:
                        buys.append({"code": code, "name": sig.name, "qty": result.qty,
                                     "price": result.price, "amount": result.amount, "mode": "drill"})
                except Exception as exc:
                    logger.debug("paper drill force-buy %s failed: %s", code, exc)

    # ---- b) 平仓（优先走策略出场逻辑；无出场信号才 drill_forced，并显式标注）----
    if close_positions:
        from .models import BUY_SIGNAL_TYPES, SELL_SIGNAL_TYPES
        # 卖出信号来源：优先用持仓记录的建仓策略 id（paper.buy 已落库），
        # 兜底取首个已启用策略；避免硬编码策略处于 draft 时被门禁关卡 1 拒单。
        fallback_sid = enabled[0].id if enabled else None
        positions = await store.list_positions("paper")
        for pos in positions:
            try:
                sid = pos.strategy_id or fallback_sid
                if not sid:
                    continue
                quote = await fetch_single_quote(pos.code)
                price = float((quote or {}).get("price") or pos.last_price or pos.avg_cost or 0)
                if price <= 0:
                    continue
                # ① 先走策略出场逻辑（现有 *_stop / 策略卖出信号）
                exit_sig = None
                try:
                    strat = get_strategy(sid)
                    spec = next((s for s in specs if s.get("strategy_id") == sid), {})
                    klines = await _kline(pos.code)
                    if klines:
                        ctx = StrategyContext(
                            quote=quote, position=pos,
                            params=spec.get("params") or strat.default_params, now=now)
                        cand = strat.evaluate(pos.code, klines, ctx)
                        if (cand is not None and cand.signal_type in SELL_SIGNAL_TYPES
                                and cand.signal_type not in BUY_SIGNAL_TYPES):
                            exit_sig = cand
                except Exception:
                    exit_sig = None  # 策略未注册/评估失败 → 走强制平仓
                # ② 无策略出场信号 → 演练强制平仓（exit_reason=drill_forced，供复盘区分）
                if exit_sig is None:
                    exit_sig = Signal(
                        code=pos.code, name=pos.name, signal_type="drill_exit",
                        source=strategy_source(sid), price=price, chg_pct=0.0,
                        detail={"label": "演练强制平仓",
                                "desc": "策略未给出场信号，为产出闭合样本强制平仓（drill_forced）"},
                        ts=now_local_iso(),
                    )
                exit_sig.name = pos.name
                # allow_t0=True 绕过 T+1（否则当日建仓当日无法卖出，演练无法产出闭合样本）；
                # 该绕过在风控 4b_t1 关卡审计中留痕。
                result = await risk.execute_signal(exit_sig, cfg, allow_t0=True)
                if result.ok:
                    sells.append({"code": pos.code, "name": pos.name, "qty": result.qty,
                                  "price": result.price, "amount": result.amount,
                                  "exit_reason": exit_sig.signal_type})
            except Exception as exc:
                logger.debug("paper drill sell %s failed: %s", pos.code, exc)

    # ---- c) 复盘 + 迭代（日结例程，幂等）----
    review = {"new_reviews": 0, "total": 0}
    iteration = {"actions": []}
    if loop is not None:
        try:
            routine = await loop.run_daily_routine(trigger="paper_drill", full=False)
            review = routine.get("trade_reviews", review)
            iteration = routine.get("iteration", iteration)
        except Exception as exc:
            logger.warning("paper drill review failed: %s", exc)

    return {
        "ok": True,
        "buys": buys,
        "sells": sells,
        "closed_trades": len(sells),
        "new_reviews": review.get("new_reviews", 0),
        "total_reviews": review.get("total", 0),
        "iteration_actions": len(iteration.get("actions", [])),
        "now": now_local_iso(),
    }
