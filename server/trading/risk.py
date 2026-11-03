"""自动交易核心包 - 风控层（所有下单动作的唯一安检口）

架构蓝图 §6：
- 9 关卡检查链（门禁/总开关/白名单/单笔限额/单日限额/持仓上限/连败熔断/日亏熔断/下单冷却），
  任一不过即拒单；每次判定无论通过与否都写 audit_log（含 9 项判定明细），
  拒单同时落 orders(status='rejected')——台账里看得见每一次"想下没下成"的单
- 连败熔断：连续下单失败 ≥3 → 熔断停机 + 邮件告警，须人工复位
- 实盘二次确认：broker_adapter=null 时 live-switch 永远拒绝（D1 硬约束）
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import constraints
from . import decision_gate
from . import gateway
from . import marks
from . import strategies as strategies_registry
from .alerts import EmailAlerter
from .models import (
    AutomationConfig,
    BUY_SIGNAL_TYPES,
    SELL_SIGNAL_TYPES,
    GateState,
    OrderRequest,
    OrderResult,
    OrderStatus,
    RiskConfig,
    RiskState,
    Signal,
    exit_reason_of,
    now_local_iso,
    parse_strategy_source,
)
from .store import TradingStore

logger = logging.getLogger(__name__)

CONFIRM_TEXT = "确认开启实盘交易"  # §5.7 二次确认精确匹配文本

_PAPER_OK_STATES = {
    GateState.PAPER_VALIDATING.value,
    GateState.PAPER_PASSED.value,
    GateState.LIVE_ENABLED.value,
}
_LIVE_OK_STATES = {GateState.PAPER_PASSED.value, GateState.LIVE_ENABLED.value}


class RiskManager:
    """风控层：限额/持仓上限/白名单/日亏熔断/连败熔断/门禁强制校验/审计落库"""

    def __init__(self, store: TradingStore, alerter: Optional[EmailAlerter] = None):
        self._store = store
        self._alerter = alerter

    # --------------------------------------------------------
    # 配置与运行态
    # --------------------------------------------------------
    async def load_risk_config(self) -> RiskConfig:
        return RiskConfig.from_dict(await self._store.kv_get("risk"))

    async def save_risk_config(self, cfg: RiskConfig) -> None:
        await self._store.kv_set("risk", cfg.to_dict())

    async def load_state(self) -> RiskState:
        """加载运行态并按本地日切：新的一天清零当日计数并锚定日初权益"""
        state = RiskState.from_dict(await self._store.kv_get("risk_state"))
        today = datetime.now().strftime("%Y-%m-%d")
        if state.day_date != today:
            state.rollover_if_new_day(today)
            paper = gateway.get_gateway("paper")
            if paper is not None:
                try:
                    acc = await paper.query_account()
                    state.day_start_equity = float(acc.get("total_equity", 0.0))
                except Exception:
                    logger.warning("RiskManager: 日初权益锚定失败", exc_info=True)
            await self._store.kv_set("risk_state", state.to_dict())
        return state

    async def _save_state(self, state: RiskState) -> None:
        await self._store.kv_set("risk_state", state.to_dict())

    async def _strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        specs = await self._store.kv_get("strategies", default=[]) or []
        for spec in specs:
            if spec.get("strategy_id") == strategy_id:
                return spec
        return None

    # --------------------------------------------------------
    # 信号 → 订单 编排（monitor ⑤b 调用）
    # --------------------------------------------------------
    async def execute_signal(
        self, sig: Signal, cfg: AutomationConfig, allow_t0: bool = False
    ) -> OrderResult:
        """信号 → OrderRequest → 决策闸 → 9 关卡 → 网关 → 台账/熔断计数

        allow_t0=True 仅用于虚拟盘演练强制平仓（paper_drill）：绕过 T+1 可卖数量限制。
        """
        strategy_id = parse_strategy_source(sig.source) or ""
        if sig.signal_type in BUY_SIGNAL_TYPES:
            side = "buy"
        elif sig.signal_type in SELL_SIGNAL_TYPES:
            side = "sell"
        else:
            return OrderResult(ok=False, status=OrderStatus.REJECTED.value,
                               code=sig.code, name=sig.name, side="",
                               fail_reason=f"非交易类信号 {sig.signal_type}",
                               signal_id=sig.id, strategy_id=strategy_id)

        exit_reason = exit_reason_of(sig.signal_type) if side == "sell" else None
        order = OrderRequest(
            code=sig.code, name=sig.name, side=side, price=sig.price, qty=0,
            signal_id=sig.id, strategy_id=strategy_id, allow_t0=allow_t0,
            is_drill=marks.drill_active(), exit_reason=exit_reason, source="signal",
        )
        return await self.execute_order(order, cfg, signal_type=sig.signal_type,
                                        signal_score=sig.score)

    async def execute_order(
        self, order: OrderRequest, cfg: AutomationConfig,
        signal_type: Optional[str] = None, signal_score: Optional[int] = None,
    ) -> OrderResult:
        """订单编排（**信号单 / 手动单 / 条件单共用同一套**：决策闸 → 9 关卡 → 网关 → 台账）。

        手动单/条件单绝不绕过风控：白名单/额度/涨跌停/停牌/T+1 一个都不能跳。
        """
        # P0 目标网关：live 总开关打开且适配器存在 → live，否则 paper
        risk_cfg = await self.load_risk_config()
        target = order.gateway or "paper"
        if target == "live" and not (risk_cfg.live_master_switch and gateway.broker_adapter() is not None):
            target = "paper"
        order.gateway = target
        side = order.side

        # 出场归因：显式优先 → 信号类型映射 → 手动兜底
        exit_reason = order.exit_reason or (exit_reason_of(signal_type) if (signal_type and side == "sell") else None)
        if side == "sell" and exit_reason is None:
            exit_reason = "manual" if order.source == "manual" else (order.source or "signal")
        order.exit_reason = exit_reason
        order.is_drill = bool(order.is_drill) or marks.drill_active()

        # 决策闸：价值+事件+板块+技术 四维 → allow/confidence/理由（可追溯）。
        #   买入必过闸（拒绝是一种能力）；卖出只为记录理由、永不拦截（否则持仓锁死）。
        try:
            gate_cfg = await decision_gate.load_gate_config(self._store)
            decision = await decision_gate.evaluate(
                code=order.code, name=order.name, side=side, signal_type=signal_type or "",
                signal_score=signal_score, price=order.price, gate_cfg=gate_cfg,
            )
        except Exception:
            logger.warning("RiskManager: 决策闸异常，降级放行（记录原因）", exc_info=True)
            decision = {"allow": side != "buy", "side": side, "confidence": None,
                        "reasons": {}, "verdict_reason": "决策闸异常，降级", "size_weight": 1.0}
        order.decision = decision
        order.sizing_weight = float(decision.get("size_weight") or 1.0)
        await self._store.audit(
            "risk", "decision_allow" if decision.get("allow") else "decision_deny",
            {"code": order.code, "side": side, "signal_type": signal_type,
             "source": order.source,
             "confidence": decision.get("confidence"),
             "size_weight": order.sizing_weight,
             "verdict_reason": decision.get("verdict_reason"),
             "reasons": decision.get("reasons")},
            result="ok" if decision.get("allow") else "denied",
        )
        if side == "buy" and not decision.get("allow", True):
            result = OrderResult(
                ok=False, status=OrderStatus.REJECTED.value, code=order.code, name=order.name,
                side="buy", price=order.price, gateway=target,
                # 被拦下也要保留「用户请求的数量/金额」，不能让 qty 归零丢失原始意图
                qty=order.qty, req_qty=order.qty,
                req_amount=round(order.qty * order.price, 2) if order.qty else 0.0,
                fail_reason=f"决策闸拒绝：{decision.get('verdict_reason')}",
                signal_id=order.signal_id, strategy_id=order.strategy_id, source=order.source,
            )
            result.is_drill, result.exit_reason = order.is_drill, None
            result.decision_json = decision
            await self._store.insert_order(result.to_row())
            return result

        result = await self.check_order(order, cfg, risk_cfg)
        result.is_drill, result.exit_reason = order.is_drill, exit_reason
        result.decision_json = decision
        result.source = order.source
        if result.status == OrderStatus.REJECTED.value:
            await self._store.insert_order(result.to_row())
            return result

        gw = gateway.current(target)
        if gw is None:
            result.ok = False
            result.status = OrderStatus.REJECTED.value
            result.fail_reason = "目标网关不可用（实盘适配器不存在）"
            await self._store.insert_order(result.to_row())
            await self._store.audit("risk", "order_rejected", result.to_row(), result="denied")
            return result

        try:
            fill = await (gw.buy(order) if side == "buy" else gw.sell(order))
        except Exception as e:
            fill = OrderResult(ok=False, status=OrderStatus.FAILED.value,
                               code=order.code, name=order.name, side=side,
                               gateway=target, fail_reason=f"网关异常：{type(e).__name__} {str(e)[:150]}",
                               signal_id=order.signal_id, strategy_id=order.strategy_id)
        fill.signal_id, fill.strategy_id = order.signal_id, order.strategy_id
        fill.checks = result.checks
        fill.is_drill, fill.exit_reason = order.is_drill, exit_reason
        fill.decision_json = decision
        fill.source = order.source
        # 风控换算后的数量传给网关前的 OrderRequest 已被 check_order 改写；以成交结果为准
        await self._store.insert_order(fill.to_row())

        state = await self.load_state()
        if fill.ok:
            await self._store.audit("gateway", "order_filled", fill.to_row())
            state.consecutive_failures = 0
            if side == "buy":
                state.day_amount = round(state.day_amount + fill.amount, 2)
            await self._save_state(state)
        else:
            await self._store.audit("gateway", "order_failed", fill.to_row(), result="error")
            await self._register_failure(state, fill.fail_reason or "下单失败")
        return fill

    async def _register_failure(self, state: RiskState, reason: str) -> None:
        """连败计数 +1；≥阈值 → 熔断停机 + 邮件告警（§6.1 关卡 7）"""
        risk_cfg = await self.load_risk_config()
        state.consecutive_failures += 1
        if state.consecutive_failures >= risk_cfg.breaker_fail_threshold and not state.breaker_tripped:
            state.breaker_tripped = True
            state.breaker_tripped_at = now_local_iso()
            await self._store.audit("risk", "breaker_tripped", {
                "consecutive_failures": state.consecutive_failures,
                "threshold": risk_cfg.breaker_fail_threshold, "reason": reason,
            }, result="error")
            if self._alerter is not None:
                auto_cfg = AutomationConfig.from_dict(await self._store.kv_get("automation"))
                await self._alerter.send_plain(
                    auto_cfg.email.get("recipient", ""),
                    "【信号提示】自动交易熔断已触发",
                    f"连续下单失败 {state.consecutive_failures} 次（阈值 {risk_cfg.breaker_fail_threshold}），"
                    f"已停止一切自动交易，须人工复位。\n最近失败原因：{reason}",
                )
            logger.error("RiskManager: 熔断触发，停止一切自动交易")
        await self._save_state(state)

    # --------------------------------------------------------
    # 9 关卡检查链（§6.1，顺序执行，任一不过即拒单）
    # --------------------------------------------------------
    async def check_order(
        self,
        order: OrderRequest,
        cfg: Optional[AutomationConfig] = None,
        risk_cfg: Optional[RiskConfig] = None,
    ) -> OrderResult:
        cfg = cfg or AutomationConfig.from_dict(await self._store.kv_get("automation"))
        risk_cfg = risk_cfg or await self.load_risk_config()
        state = await self.load_state()
        target = order.gateway

        result = OrderResult(
            ok=False, status=OrderStatus.REJECTED.value, code=order.code, name=order.name,
            side=order.side, price=order.price, gateway=target,
            # 把关卡拦截前的「用户请求数量/金额」先落到 result 上：本函数后续会把 order.qty
            # 改写为风控换算后的数量，届时原始请求值就取不到了。被拒单必须留下原始意图，
            # 否则复盘无法回放「如果当时买了会怎样」。
            qty=order.qty, req_qty=order.qty,
            req_amount=round(order.qty * order.price, 2) if order.qty else 0.0,
            signal_id=order.signal_id, strategy_id=order.strategy_id,
            source=order.source or "signal",
        )
        checks: Dict[str, Any] = {}

        # 关卡 1：门禁状态（信号源策略）
        #   手动单/条件单无策略来源 → 不受策略门禁约束（仍受其余 8 关 + 决策闸约束）；
        #   信号单必须策略门禁在允许状态。
        spec = await self._strategy_spec(order.strategy_id)
        gate_state = (spec or {}).get("gate_state", GateState.DRAFT.value)
        ok_states = _LIVE_OK_STATES if target == "live" else _PAPER_OK_STATES
        if order.strategy_id:
            gate1_ok = gate_state in ok_states
        else:
            gate1_ok = True
            gate_state = "manual"
        checks["1_gate_state"] = {"pass": gate1_ok, "gate_state": gate_state,
                                  "target": target, "source": order.source}
        if not gate1_ok:
            return await self._finish_reject(result, checks, "1_gate_state",
                                       f"策略门禁状态 {gate_state} 不允许 {target} 下单")

        # 关卡 2：总开关
        if target == "live":
            gate2_ok = risk_cfg.live_master_switch and gateway.broker_adapter() is not None
            reason2 = "实盘总开关未开启或实盘适配器不存在"
        else:
            gate2_ok = bool(cfg.switches.get("paper"))
            reason2 = "模拟交易总开关未开启"
        checks["2_master_switch"] = {"pass": gate2_ok, "target": target}
        if not gate2_ok:
            return await self._finish_reject(result, checks, "2_master_switch", reason2)

        # 关卡 3：白名单（空 = 全部只提醒，最保守起步）
        gate3_ok = order.code in risk_cfg.whitelist
        checks["3_whitelist"] = {"pass": gate3_ok, "whitelist_size": len(risk_cfg.whitelist)}
        if not gate3_ok:
            return await self._finish_reject(result, checks, "3_whitelist",
                                       f"标的 {order.code} 不在风控白名单内，只发提醒不下单")

        # 关卡 3a/3b：真实约束（涨跌停 / 停牌）——数据不可靠时降级为不校验 + warning
        snapshot: Optional[Dict[str, Any]] = None
        if risk_cfg.check_price_limit or risk_cfg.check_suspension:
            snapshot = await constraints.fetch_market_snapshot(order.code)

        if risk_cfg.check_suspension:
            suspended = constraints.is_suspended(snapshot)
            suspend_ok = suspended is not True
            checks["3b_suspension"] = {
                "pass": suspend_ok,
                "suspended": suspended,  # True 停牌 / False 正常 / None 未知(降级)
                "data_ok": snapshot is not None,
            }
            if suspended is None:
                self._log_degrade("3b_suspension", order, "停牌数据不可用，降级为不校验")
            elif not suspend_ok:
                return await self._finish_reject(
                    result, checks, "3b_suspension",
                    f"标的 {order.code} 疑似停牌（当日成交量/成交额为 0），拒绝下单")

        if risk_cfg.check_price_limit:
            limit_reason = constraints.check_price_limit(order.side, order.price, snapshot)
            limit_ok = limit_reason is None
            checks["3a_price_limit"] = {
                "pass": limit_ok or limit_reason == "",
                "reason": limit_reason or None,
                "limit_up": (snapshot or {}).get("limit_up"),
                "limit_down": (snapshot or {}).get("limit_down"),
                "limit_source": (snapshot or {}).get("limit_source"),
                "price": order.price,
            }
            if limit_reason == "":
                self._log_degrade("3a_price_limit", order, "涨跌停价不可用，降级为不校验")
            elif not limit_ok:
                return await self._finish_reject(result, checks, "3a_price_limit", limit_reason)

        # 关卡 7（提前判定，熔断优先级高于金额换算）：连败熔断
        gate7_ok = not state.breaker_tripped
        checks["7_breaker"] = {"pass": gate7_ok, "consecutive_failures": state.consecutive_failures}
        if not gate7_ok:
            return await self._finish_reject(result, checks, "7_breaker",
                                       f"连败熔断已触发（{state.breaker_tripped_at}），须人工复位")

        # 关卡 8：日亏熔断（只许平仓）
        day_loss_hit = (
            state.day_pnl_pct <= -abs(risk_cfg.daily_loss_limit_pct)
            and state.day_start_equity > 0
        )
        gate8_ok = not (day_loss_hit and order.side == "buy")
        checks["8_daily_loss"] = {"pass": gate8_ok, "day_pnl_pct": state.day_pnl_pct,
                                  "limit_pct": risk_cfg.daily_loss_limit_pct}
        if not gate8_ok:
            return await self._finish_reject(result, checks, "8_daily_loss",
                                       f"当日浮动盈亏 {state.day_pnl_pct:.2f}% 触及日亏熔断 "
                                       f"-{risk_cfg.daily_loss_limit_pct}%，当日停止开新仓")

        price = float(order.price or 0)
        positions = await self._store.list_positions("paper")

        # 关卡 4：单笔金额上限 → 数量换算（买入，按决策闸置信度×波动率加权）
        if order.side == "buy":
            if price <= 0:
                return await self._finish_reject(result, checks, "4_amount_per_order", "价格异常，无法换算数量")
            weight = max(0.1, min(1.0, float(getattr(order, "sizing_weight", 1.0) or 1.0)))
            cap_qty = math.floor(risk_cfg.max_amount_per_order / price / 100) * 100
            target_amount = risk_cfg.max_amount_per_order * weight
            sized_qty = math.floor(target_amount / price / 100) * 100
            if sized_qty <= 0 and target_amount >= price * 100:
                sized_qty = 100  # 权重小但金额仍够 1 手 → 至少 1 手
            max_qty = sized_qty
            qty = min(order.qty, max_qty) if order.qty > 0 else max_qty
            gate4_ok = qty > 0
            checks["4_amount_per_order"] = {
                "pass": gate4_ok, "max_amount": risk_cfg.max_amount_per_order,
                "price": price, "qty": qty,
                "sizing_weight": round(weight, 3),
                "target_amount": round(target_amount, 2),
                "cap_qty": cap_qty, "sized_qty": sized_qty,
                "sizing_basis": (order.decision or {}).get("verdict_reason") if getattr(order, "decision", None) else None,
                "confidence": (order.decision or {}).get("confidence") if getattr(order, "decision", None) else None,
                "volatility_pct": (order.decision or {}).get("volatility_pct") if getattr(order, "decision", None) else None,
            }
            if not gate4_ok:
                return await self._finish_reject(
                    result, checks, "4_amount_per_order",
                    f"单笔金额超上限 {risk_cfg.max_amount_per_order:.0f} 元或价格过高，换算数量为 0")
            order.qty = qty
            amount = round(qty * price, 2)

            # 关卡 5：单日累计上限
            gate5_ok = state.day_amount + amount <= risk_cfg.max_amount_per_day
            checks["5_amount_per_day"] = {
                "pass": gate5_ok, "day_amount": state.day_amount,
                "this_amount": amount, "max_amount": risk_cfg.max_amount_per_day,
            }
            if not gate5_ok:
                return await self._finish_reject(
                    result, checks, "5_amount_per_day",
                    f"单日累计金额将达 {state.day_amount + amount:.0f} 元，"
                    f"超上限 {risk_cfg.max_amount_per_day:.0f} 元")

            # 关卡 6：持仓数上限（新买入）
            held_codes = {p.code for p in positions}
            gate6_ok = order.code in held_codes or len(positions) < risk_cfg.max_positions
            checks["6_max_positions"] = {"pass": gate6_ok, "current": len(positions),
                                         "max": risk_cfg.max_positions}
            if not gate6_ok:
                return await self._finish_reject(result, checks, "6_max_positions",
                                           f"持仓数已达上限 {risk_cfg.max_positions}，禁止开新仓")
        else:
            # 卖出：数量来自持仓（qty=0 → 全部平仓），并做 T+1 可卖数量校验
            held = next((p for p in positions if p.code == order.code), None)
            if held is None or held.qty <= 0:
                return await self._finish_reject(result, checks, "4_position", "无持仓可卖")
            today = datetime.now().strftime("%Y-%m-%d")
            # 历史持仓（非当日买入）全部可卖；当日买入部分计入 sellable_qty（T+1）
            sellable = held.qty if held.buy_date != today else int(held.sellable_qty or 0)
            if order.allow_t0:
                sellable = held.qty  # 演练强制平仓：绕过 T+1（审计留痕）
                checks["4b_t1"] = {"pass": True, "bypass_t1": True,
                                   "buy_date": held.buy_date, "held_qty": held.qty}
            else:
                t1_ok = sellable > 0
                checks["4b_t1"] = {
                    "pass": t1_ok, "bypass_t1": False, "buy_date": held.buy_date,
                    "held_qty": held.qty, "sellable_qty": sellable,
                }
                if not t1_ok:
                    return await self._finish_reject(
                        result, checks, "4b_t1",
                        f"T+1 限制：持仓为当日买入（{held.buy_date}），{held.qty} 股今日不可卖出")
            want = order.qty if order.qty > 0 else sellable
            qty = min(want, sellable, held.qty)
            if qty <= 0:
                return await self._finish_reject(result, checks, "4b_t1", "可卖数量为 0，无法卖出")
            order.qty = qty
            amount = round(qty * price, 2) if price > 0 else 0.0
            checks["4_position"] = {"pass": True, "qty": qty}

        # 关卡 9：下单冷却（同标的同方向，复用信号冷却分钟数 + side 维度）
        cooldown_minutes = int(cfg.cooldown_minutes or 30)
        last_ts = await self._store.last_filled_order_ts(order.code, order.side, target)
        gate9_ok = True
        if last_ts:
            try:
                elapsed = (datetime.now() - datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%S")).total_seconds()
                gate9_ok = elapsed >= cooldown_minutes * 60
            except ValueError:
                gate9_ok = True
        checks["9_order_cooldown"] = {"pass": gate9_ok, "last_filled_ts": last_ts,
                                      "cooldown_minutes": cooldown_minutes}
        if not gate9_ok:
            return await self._finish_reject(result, checks, "9_order_cooldown",
                                       f"同标的同方向 {cooldown_minutes} 分钟内不重复下单")

        # 全部通过
        result.ok = True
        result.status = "passed"
        result.qty = order.qty
        result.amount = amount
        result.checks = checks
        await self._store.audit("risk", "order_submit", {
            "code": order.code, "side": order.side, "qty": order.qty,
            "price": price, "amount": amount, "gateway": target, "checks": checks,
        })
        return result

    async def _finish_reject(
        self, result: OrderResult, checks: Dict[str, Any], gate: str, reason: str
    ) -> OrderResult:
        """拒单收尾：标记判定明细 + 全量审计（拒单台账由调用方落 orders 表）"""
        checks[gate] = {**(checks.get(gate) or {}), "pass": False, "reason": reason}
        result.ok = False
        result.status = OrderStatus.REJECTED.value
        result.fail_reason = reason
        result.checks = checks
        await self._store.audit("risk", "order_rejected", {
            "code": result.code, "side": result.side, "gateway": result.gateway,
            "gate": gate, "reason": reason, "checks": checks,
        }, result="denied")
        return result

    @staticmethod
    def _log_degrade(gate: str, order: OrderRequest, msg: str) -> None:
        """真实约束数据不可靠 → 降级为不校验，记 warning（如实标注，不假装校验）"""
        logger.warning("RiskManager: 关卡 %s 降级（不校验）标的 %s：%s",
                       gate, order.code, msg)

    # --------------------------------------------------------
    # 实盘总开关（§5.7 四重校验链）
    # --------------------------------------------------------
    async def live_switch(
        self, on: bool, confirm_text: str, strategy_id: str
    ) -> Tuple[bool, str]:
        risk_cfg = await self.load_risk_config()

        if not on:
            # 关闭无需确认、立即生效——止损永远比开仓容易
            risk_cfg.live_master_switch = False
            await self.save_risk_config(risk_cfg)
            specs = await self._store.kv_get("strategies", default=[]) or []
            for spec in specs:
                if spec.get("gate_state") == GateState.LIVE_ENABLED.value:
                    spec["gate_state"] = GateState.PAPER_PASSED.value
                    spec["live_enabled"] = False
            await self._store.kv_set("strategies", specs)
            await self._store.audit("api", "config_updated", {"live_master_switch": False})
            return True, "实盘总开关已关闭"

        # 校验 1：实盘网关适配器存在（当前 broker_adapter=null → 永远拒绝，D1 硬约束）
        if gateway.broker_adapter() is None:
            await self._store.audit("api", "live_enable_blocked", {
                "strategy_id": strategy_id, "reason": "broker_adapter=null",
            }, result="denied")
            return False, "当前无可用实盘网关适配器，实盘交易物理不可达"

        # 校验 2：目标策略 gate_state == paper_passed
        spec = await self._strategy_spec(strategy_id)
        if spec is None or spec.get("gate_state") != GateState.PAPER_PASSED.value:
            await self._store.audit("api", "live_enable_blocked", {
                "strategy_id": strategy_id,
                "gate_state": (spec or {}).get("gate_state"),
            }, result="denied")
            return False, f"策略 {strategy_id} 未完成模拟验证（paper_passed），禁止开启实盘"

        # 校验 3：confirm_text 精确匹配
        if confirm_text != CONFIRM_TEXT:
            await self._store.audit("api", "live_enable_blocked", {
                "strategy_id": strategy_id, "reason": "confirm_text_mismatch",
            }, result="denied")
            return False, f"二次确认文本不匹配，请准确输入「{CONFIRM_TEXT}」"

        # 校验 4：熔断未触发
        state = await self.load_state()
        if state.breaker_tripped:
            await self._store.audit("api", "live_enable_blocked", {
                "strategy_id": strategy_id, "reason": "breaker_tripped",
            }, result="denied")
            return False, "熔断已触发，须先人工复位"

        risk_cfg.live_master_switch = True
        await self.save_risk_config(risk_cfg)
        await self._store.audit("api", "live_enabled", {"strategy_id": strategy_id})
        if self._alerter is not None:
            auto_cfg = AutomationConfig.from_dict(await self._store.kv_get("automation"))
            await self._alerter.send_plain(
                auto_cfg.email.get("recipient", ""),
                "【信号提示】实盘总开关已开启",
                f"策略 {strategy_id} 实盘交易已开启，请密切关注账户与风险。",
            )
        return True, "实盘总开关已开启"

    # --------------------------------------------------------
    # 熔断复位（§5.11）
    # --------------------------------------------------------
    async def reset_breaker(self) -> None:
        state = await self.load_state()
        state.consecutive_failures = 0
        state.breaker_tripped = False
        state.breaker_tripped_at = None
        await self._save_state(state)
        await self._store.audit("api", "breaker_reset", {})
        if self._alerter is not None:
            auto_cfg = AutomationConfig.from_dict(await self._store.kv_get("automation"))
            await self._alerter.send_plain(
                auto_cfg.email.get("recipient", ""),
                "【信号提示】熔断已复位",
                "连败计数与熔断标志已复位，自动交易恢复。",
            )

    # --------------------------------------------------------
    # 收盘扫描后处理（monitor 调用）
    # --------------------------------------------------------
    async def on_close_scan(self) -> None:
        """模拟交易日推进（paper_days_done 计数）+ 持仓市价刷新 + 日亏统计"""
        paper = gateway.get_gateway("paper")
        if paper is None:
            return
        advance = await paper.advance_trading_day()
        if advance.get("strategies"):
            logger.info("RiskManager: 模拟交易日推进 %s", advance)
        pnl_pct = await paper.refresh_positions_price()
        state = await self.load_state()
        state.day_pnl_pct = pnl_pct
        await self._save_state(state)

    # --------------------------------------------------------
    # 门禁总览（gate 端点数据源）
    # --------------------------------------------------------
    async def gate_overview(self) -> Dict[str, Any]:
        risk_cfg = await self.load_risk_config()
        state = await self.load_state()
        broker = gateway.broker_adapter()
        specs = await self._store.kv_get("strategies", default=[]) or []

        strategies_view: List[Dict[str, Any]] = []
        for spec in specs:
            gate_state = spec.get("gate_state", GateState.DRAFT.value)
            done = int(spec.get("paper_days_done", 0))
            required = int(spec.get("paper_days_required", 5))
            can_live = (
                gate_state == GateState.PAPER_PASSED.value
                and broker is not None
                and not state.breaker_tripped
            )
            if gate_state != GateState.PAPER_PASSED.value:
                if gate_state == GateState.PAPER_VALIDATING.value:
                    block_reason = f"模拟验证未完成：{done}/{required} 交易日"
                elif gate_state == GateState.LIVE_ENABLED.value:
                    block_reason = ""
                else:
                    block_reason = "尚未通过回测与模拟验证"
            elif broker is None:
                block_reason = "无实盘网关适配器，实盘物理不可达"
            elif state.breaker_tripped:
                block_reason = "熔断已触发，须先复位"
            else:
                block_reason = ""
            sid = spec.get("strategy_id", "")
            strategies_view.append({
                "strategy_id": sid,
                # 中文显示名（e3）：策略注册表为唯一真相源，未注册回退 id
                "name": (strategies_registry.get(sid).name
                         if strategies_registry.has(sid) else sid),
                "gate_state": gate_state,
                "backtest": spec.get("last_backtest"),
                "paper_days_done": done,
                "paper_days_required": required,
                "can_enable_live": can_live,
                "block_reason": block_reason,
            })

        return {
            "live_master_switch": risk_cfg.live_master_switch,
            "broker_adapter": broker.id if broker is not None else None,
            "strategies": strategies_view,
            "breaker": {
                "tripped": state.breaker_tripped,
                "tripped_at": state.breaker_tripped_at,
                "consecutive_failures": state.consecutive_failures,
            },
        }
