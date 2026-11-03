"""自动交易核心包 - PaperGateway：模拟成交

架构蓝图 §1.2 / §3 ⑤b：
- 即时价成交（腾讯现价）；持仓/资金落 positions 表 + config KV paper_account
- 模拟交易日推进（收盘扫描时调用）：per-strategy paper_days_done 计数，
  满 paper_days_required 自动 gate_state → paper_passed（门禁计数数据源）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.realtime import fetch_single_quote

from . import costs
from .gateway import OrderGateway
from .models import (
    DEFAULT_INITIAL_CASH,
    GateState,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    RiskConfig,
    now_local_iso,
)
from .store import TradingStore

logger = logging.getLogger(__name__)

_DEFAULT_CASH = DEFAULT_INITIAL_CASH
_ACCOUNT_KEY = "paper_account"


class PaperGateway(OrderGateway):
    """模拟盘网关：即时价成交、持仓台账、模拟交易日推进"""

    id = "paper"

    def __init__(self, store: TradingStore):
        self._store = store

    # --------------------------------------------------------
    # 账户（config KV paper_account）
    # --------------------------------------------------------
    async def _account(self) -> Dict[str, Any]:
        acc = await self._store.kv_get(_ACCOUNT_KEY)
        if not acc:
            acc = {
                "cash": _DEFAULT_CASH,
                "initial_cash": _DEFAULT_CASH,
                "trading_days": 0,
                "start_date": now_local_iso()[:10],
            }
            await self._store.kv_set(_ACCOUNT_KEY, acc)
        return acc

    async def query_account(self) -> Dict[str, Any]:
        acc = await self._account()
        positions = await self._store.list_positions("paper")
        market_value = sum(p.qty * (p.last_price or p.avg_cost) for p in positions)
        return {
            "gateway": "paper",
            "cash": round(float(acc["cash"]), 2),
            "initial_cash": acc["initial_cash"],
            "market_value": round(market_value, 2),
            "total_equity": round(float(acc["cash"]) + market_value, 2),
            "trading_days": int(acc.get("trading_days", 0)),
        }

    async def query_position(self, code: Optional[str] = None) -> List[Position]:
        if code:
            pos = await self._store.get_position(code, "paper")
            return [pos] if pos else []
        return await self._store.list_positions("paper")

    async def _risk_config(self) -> RiskConfig:
        """读取成交成本参数（滑点/佣金/印花税/过户费）"""
        return RiskConfig.from_dict(await self._store.kv_get("risk"))

    # --------------------------------------------------------
    # 成交
    # --------------------------------------------------------
    async def buy(self, order: OrderRequest) -> OrderResult:
        """模拟买入：腾讯现价 + 滑点成交，计入佣金/过户费；资金/持仓落库"""
        result = OrderResult(
            ok=False, status=OrderStatus.FAILED.value, code=order.code, name=order.name,
            side="buy", gateway="paper", signal_id=order.signal_id,
            strategy_id=order.strategy_id,
        )
        if order.qty <= 0:
            result.fail_reason = "买入数量必须为正整数（100 股整数倍）"
            return result

        quote = await fetch_single_quote(order.code)
        price = float((quote or {}).get("price") or order.price or 0)
        if price <= 0:
            result.fail_reason = "无法获取有效现价，模拟成交失败"
            return result

        risk_cfg = await self._risk_config()
        cost = costs.compute_buy(price, order.qty, risk_cfg)
        total_cost = float(cost["total_cost"])
        fill = float(cost["fill_price"])

        acc = await self._account()
        if total_cost > float(acc["cash"]):
            result.fail_reason = (
                f"模拟资金不足：需 {total_cost:.2f} 元（含佣金/过户费），"
                f"可用 {float(acc['cash']):.2f} 元")
            return result

        # 更新资金与持仓（含滑点成交价；加权平均成本）
        acc["cash"] = round(float(acc["cash"]) - total_cost, 2)
        await self._store.kv_set(_ACCOUNT_KEY, acc)

        today = datetime.now().strftime("%Y-%m-%d")
        pos = await self._store.get_position(order.code, "paper")
        if pos and pos.qty > 0:
            # T+1：历史持仓（非当日买入）在买入前解锁为可卖
            if pos.buy_date != today:
                pos.sellable_qty = pos.qty
            new_qty = pos.qty + order.qty
            new_cost = (pos.avg_cost * pos.qty + fill * order.qty) / new_qty
            pos.qty = new_qty
            pos.avg_cost = round(new_cost, 4)
            pos.last_price = fill
            pos.buy_date = today
            pos.peak_price = max(float(pos.peak_price or 0.0), fill)
            if order.strategy_id:
                pos.strategy_id = order.strategy_id
            pos.updated_at = now_local_iso()
        else:
            pos = Position(
                code=order.code, name=order.name, qty=order.qty,
                avg_cost=fill, last_price=fill, gateway="paper",
                strategy_id=order.strategy_id, buy_date=today,
                sellable_qty=0, peak_price=fill,
            )
        await self._store.upsert_position(pos)

        result.ok = True
        result.status = OrderStatus.FILLED.value
        result.qty, result.price, result.amount = order.qty, fill, cost["amount"]
        result.ref_price = float(cost["ref_price"])
        result.commission = float(cost["commission"])
        result.stamp_tax = float(cost["stamp_tax"])
        result.transfer_fee = float(cost["transfer_fee"])
        result.slippage_cost = float(cost["slippage_cost"])
        result.total_cost = total_cost
        return result

    async def sell(self, order: OrderRequest) -> OrderResult:
        """模拟卖出：腾讯现价 − 滑点成交，扣除佣金/印花税/过户费；qty=0 表示全部平仓"""
        result = OrderResult(
            ok=False, status=OrderStatus.FAILED.value, code=order.code, name=order.name,
            side="sell", gateway="paper", signal_id=order.signal_id,
            strategy_id=order.strategy_id,
        )
        pos = await self._store.get_position(order.code, "paper")
        if pos is None or pos.qty <= 0:
            result.fail_reason = "无持仓可卖"
            return result

        qty = order.qty if order.qty > 0 else pos.qty
        qty = min(qty, pos.qty)

        quote = await fetch_single_quote(order.code)
        price = float((quote or {}).get("price") or order.price or 0)
        if price <= 0:
            result.fail_reason = "无法获取有效现价，模拟成交失败"
            return result

        risk_cfg = await self._risk_config()
        cost = costs.compute_sell(price, qty, risk_cfg)
        net = float(cost["total_cost"])       # 净收入（已扣佣金/印花税/过户费）
        fill = float(cost["fill_price"])

        acc = await self._account()
        acc["cash"] = round(float(acc["cash"]) + net, 2)
        await self._store.kv_set(_ACCOUNT_KEY, acc)

        today = datetime.now().strftime("%Y-%m-%d")
        remaining = pos.qty - qty
        if remaining > 0:
            pos.qty, pos.last_price = remaining, fill
            if pos.buy_date == today:
                pos.sellable_qty = max(0, int(pos.sellable_qty or 0) - qty)
            else:
                pos.sellable_qty = remaining  # 历史持仓全部解锁
            pos.updated_at = now_local_iso()
            await self._store.upsert_position(pos)
        else:
            await self._store.delete_position(order.code, "paper")

        result.ok = True
        result.status = OrderStatus.FILLED.value
        result.qty, result.price, result.amount = qty, fill, cost["amount"]
        result.ref_price = float(cost["ref_price"])
        result.commission = float(cost["commission"])
        result.stamp_tax = float(cost["stamp_tax"])
        result.transfer_fee = float(cost["transfer_fee"])
        result.slippage_cost = float(cost["slippage_cost"])
        result.total_cost = net
        return result

    async def cancel(self, order_id: str) -> bool:
        """模拟盘即时成交，无挂单可撤"""
        return False

    # --------------------------------------------------------
    # 模拟交易日推进（收盘扫描调用，§6.2 门禁计数数据源）
    # --------------------------------------------------------
    async def advance_trading_day(self) -> Dict[str, Any]:
        """每收盘 +1：账户交易日计数 + 各 paper_validating 策略 paper_days_done，
        满 paper_days_required 自动 → paper_passed（写审计 gate_advanced）。"""
        acc = await self._account()
        acc["trading_days"] = int(acc.get("trading_days", 0)) + 1
        await self._store.kv_set(_ACCOUNT_KEY, acc)

        advanced: List[Dict[str, Any]] = []
        specs = await self._store.kv_get("strategies", default=[]) or []
        changed = False
        for spec in specs:
            if spec.get("gate_state") != GateState.PAPER_VALIDATING.value:
                continue
            spec["paper_days_done"] = int(spec.get("paper_days_done", 0)) + 1
            changed = True
            info = {
                "strategy_id": spec.get("strategy_id"),
                "paper_days_done": spec["paper_days_done"],
                "paper_days_required": spec.get("paper_days_required", 5),
            }
            if spec["paper_days_done"] >= int(spec.get("paper_days_required", 5)):
                spec["gate_state"] = GateState.PAPER_PASSED.value
                info["gate_state"] = GateState.PAPER_PASSED.value
                await self._store.audit("paper", "gate_advanced", info)
            advanced.append(info)
        if changed:
            await self._store.kv_set("strategies", specs)
        return {"trading_days": acc["trading_days"], "strategies": advanced}

    async def refresh_positions_price(self, quotes: Optional[Dict[str, Dict[str, Any]]] = None) -> float:
        """每轮扫描刷新持仓市价（供浮盈展示与日亏统计），并更新持仓期间最高价。

        返回当前浮动盈亏 %（相对 initial_cash）。"""
        positions = await self._store.list_positions("paper")
        if not positions:
            return 0.0
        for pos in positions:
            quote = (quotes or {}).get(pos.code)
            if not quote:
                quote = await fetch_single_quote(pos.code)
            price = float((quote or {}).get("price") or 0)
            if price > 0:
                pos.last_price = price
                pos.peak_price = max(float(pos.peak_price or 0.0), price)
                pos.updated_at = now_local_iso()
                await self._store.upsert_position(pos)
        acc = await self._account()
        initial = float(acc.get("initial_cash") or _DEFAULT_CASH)
        market_value = sum(p.qty * (p.last_price or p.avg_cost) for p in positions)
        equity = float(acc["cash"]) + market_value
        return round((equity - initial) / initial * 100, 2) if initial > 0 else 0.0
