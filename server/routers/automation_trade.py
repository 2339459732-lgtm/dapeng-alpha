"""交易动作：策略开关与回测、熔断、手工下单、条件单、审计与风控健康"""

from __future__ import annotations
import asyncio
import copy
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT
from server.tencent_kline import fetch_tencent_kline
from server.trading import trading_facade
from server.trading.backtest import run_backtest
from server.trading.models import (
    AutomationConfig,
    GateState,
    OrderRequest,
    OrderStatus,
    RiskConfig,
    new_id,
    now_local_iso,
)
from server.trading.risk import CONFIRM_TEXT
from server.trading.strategies import get as get_strategy, has as has_strategy
from server.trading.strategies.panel_adapter import _PASSTHROUGH_TYPES
from server.trading.store import TradingStore
from server.trading import decision_gate
from server.quote_hub import quote_hub
from server import risk_metrics
from server.trading import review_service
from server.routers.risk import (
    DEFAULT_BENCHMARK as RISK_BENCHMARK,
    cached_portfolio_risk,
    norm_benchmark,
    risk_cache_key,
)

from server.routers.automation_common import (
    BacktestBody,
    ConditionalOrderBody,
    EnableBody,
    LiveSwitchBody,
    ManualOrderBody,
    PaperConfirmBody,
    TestMailBody,
    _fail,
    _normalize_codes,
    _ok,
    _save_strategy_specs,
    _store,
    _strategy_specs,
    _valid_code,
    logger,
)
from server.routers.automation_config import _risk_block_cached

router = APIRouter()


# ============================================================
# 5.6 GET /gate — 门禁状态总览
# ============================================================
@router.get("/gate")
async def gate_overview() -> Dict[str, Any]:
    """风控总览：总开关、各策略门禁状态、熔断与当日亏损状态、SMTP 状态、监控循环运行时状态"""
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None and trading_facade.alerter is not None
    data = await trading_facade.risk.gate_overview()
    data["smtp"] = trading_facade.alerter.smtp_state()
    data["monitor"] = (
        trading_facade.monitor.runtime_state() if trading_facade.monitor
        else {"running": False, "last_scan_at": None, "consecutive_errors": 0, "last_error": None}
    )
    return _ok(data)


# ============================================================
# 5.7 POST /gate/live-switch — 实盘总开关（二次确认）
# ============================================================
@router.post("/gate/live-switch")
async def live_switch(body: LiveSwitchBody):
    """实盘总开关（需 confirm_text 二次确认）；未接入券商通道时开启会被拒绝"""
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None
    ok, message = await trading_facade.risk.live_switch(
        on=body.on, confirm_text=body.confirm_text or "", strategy_id=body.strategy_id or "",
    )
    if not ok:
        return _fail(message)
    return _ok({"live_master_switch": body.on}, message)


# ============================================================
# 5.8 POST /strategy/backtest — 手动回测
# ============================================================
@router.post("/strategy/backtest")
async def strategy_backtest(body: BacktestBody):
    """对指定策略跑一次历史回测，结果写入该策略门禁记录（backtested）"""
    store = await _store()
    if not has_strategy(body.strategy_id):
        return _fail(f"未知策略：{body.strategy_id}")
    code = (body.code or "").strip().lower()
    if not _valid_code(code):
        return _fail("股票代码格式非法（应为 sh600519 形式）")

    # 数据源：腾讯日K + cache（§11-3）。120 根以内复用面板同款 key，
    # 更长区间使用带 days 后缀的 key，避免与面板 120 根缓存互相污染。
    if body.days <= KLINE_LIMIT:
        cache_key = f"raw_kline:{code}:day"
        fetch_limit = KLINE_LIMIT
    else:
        cache_key = f"raw_kline:{code}:day:{body.days}"
        fetch_limit = body.days
    klines = await cache.get_or_fetch(
        cache_key,
        lambda: fetch_tencent_kline(code, period="day", limit=fetch_limit),
        CACHE_TTL_KLINE,
    )
    if not klines:
        return _fail(f"无法获取 {code} 日K数据")

    strategy = get_strategy(body.strategy_id)
    params = dict(strategy.default_params)
    specs = await _strategy_specs()
    spec = next((s for s in specs if s.get("strategy_id") == body.strategy_id), None)
    if body.params:
        params.update(body.params)
    elif spec and spec.get("params"):
        params.update(spec["params"])

    try:
        metrics = run_backtest(strategy, code, klines[-body.days:], params)
    except ValueError as e:
        return _fail(str(e))
    except Exception:
        logger.error("backtest 执行异常 %s %s", body.strategy_id, code, exc_info=True)
        return _fail("回测执行失败，请查看服务端日志", status_code=500)

    # 回测完成 → gate_state: draft→backtested，指标与时间戳存 config（§6.2）
    passed_at = now_local_iso()
    if spec is not None:
        spec["params"] = params
        spec["gate_state"] = GateState.BACKTESTED.value
        spec["paper_days_done"] = 0
        spec["last_backtest"] = {
            "passed_at": passed_at,
            "code": code,
            "win_rate": metrics["win_rate"],
            "annual_return": metrics["annual_return"],
            "max_drawdown": metrics["max_drawdown"],
            "total_trades": metrics["total_trades"],
            "profit_factor": metrics["profit_factor"],
        }
        await _save_strategy_specs(specs)
    await store.audit("api", "backtest_run", {
        "strategy_id": body.strategy_id, "code": code, "days": body.days,
        "win_rate": metrics["win_rate"], "annual_return": metrics["annual_return"],
        "max_drawdown": metrics["max_drawdown"], "total_trades": metrics["total_trades"],
    })

    data = dict(metrics)
    data["gate_state"] = GateState.BACKTESTED.value
    data["passed_at"] = passed_at
    return _ok(data)


# ============================================================
# 5.9 POST /strategy/enable — 启用策略到模拟/实盘
# ============================================================
@router.post("/strategy/enable")
async def strategy_enable(body: EnableBody):
    """启用/停用策略并设置标的；paper 目标要求门禁已达 backtested 及以上"""
    store = await _store()
    if not has_strategy(body.strategy_id):
        return _fail(f"未知策略：{body.strategy_id}")
    if body.target not in ("paper", "live"):
        return _fail("target 仅支持 paper / live")
    codes = _normalize_codes(body.codes)

    specs = await _strategy_specs()
    spec = next((s for s in specs if s.get("strategy_id") == body.strategy_id), None)
    if spec is None:
        return _fail(f"策略 {body.strategy_id} 配置不存在")
    gate_state = spec.get("gate_state", GateState.DRAFT.value)

    if body.target == "paper":
        if gate_state not in (
            GateState.BACKTESTED.value,
            GateState.PAPER_VALIDATING.value,
            GateState.PAPER_PASSED.value,
        ):
            return _fail(f"策略当前状态 {gate_state}，须先回测通过（backtested）才能启用模拟")
        spec["gate_state"] = GateState.PAPER_VALIDATING.value
        spec["enabled_codes"] = codes
        await _save_strategy_specs(specs)
        await store.audit("api", "config_updated", {
            "strategy_id": body.strategy_id, "target": "paper", "codes": codes,
        })
        # 联动打开模拟总开关（团队决策）：启用模拟的意图就是要跑模拟单，
        # 主开关关闭会导致风控关卡 2 静默拒单；模拟盘无真实资金风险，联动安全。
        # live 绝不联动——实盘必须经 /gate/live-switch 二次确认。
        auto = AutomationConfig.from_dict(await store.kv_get("automation"))
        linked = not auto.switches.get("paper")
        if linked:
            auto.switches["paper"] = True
            await store.kv_set("automation", auto.to_dict())
            await store.audit("api", "config_updated", {
                "switches": {"paper": True}, "source": "strategy_enable",
                "note": "enable paper 联动打开模拟交易总开关",
                "strategy_id": body.strategy_id,
            })
        return _ok({"gate_state": GateState.PAPER_VALIDATING.value, "enabled_codes": codes,
                    "paper_switch_linked": linked},
                   "已启用模拟验证" + ("，已联动打开模拟交易总开关" if linked else "")
                   + "，信号提示供参考")

    # target == live（强制闸门 P0-8）
    if gate_state != GateState.PAPER_PASSED.value:
        return _fail(f"策略当前状态 {gate_state}，未完成模拟验证（paper_passed），禁止启用实盘")
    assert trading_facade.risk is not None
    risk_cfg = await trading_facade.risk.load_risk_config()
    if not risk_cfg.live_master_switch:
        return _fail("实盘总开关未开启（须先经 live-switch 二次确认），禁止启用实盘")
    spec["gate_state"] = GateState.LIVE_ENABLED.value
    spec["live_enabled"] = True
    spec["enabled_codes"] = codes or spec.get("enabled_codes") or []
    await _save_strategy_specs(specs)
    await store.audit("api", "live_enabled", {
        "strategy_id": body.strategy_id, "codes": spec["enabled_codes"],
    })
    return _ok({"gate_state": GateState.LIVE_ENABLED.value,
                "enabled_codes": spec["enabled_codes"]}, "已启用实盘，信号提示供参考")


# ============================================================
# 5.10 POST /strategy/paper/confirm — 手动确认模拟通过
# ============================================================
@router.post("/strategy/paper/confirm")
async def strategy_paper_confirm(body: PaperConfirmBody):
    """人工确认纸面验证通过，把策略门禁从 paper_validating 推进到 paper_passed"""
    store = await _store()
    if not has_strategy(body.strategy_id):
        return _fail(f"未知策略：{body.strategy_id}")
    specs = await _strategy_specs()
    spec = next((s for s in specs if s.get("strategy_id") == body.strategy_id), None)
    if spec is None:
        return _fail(f"策略 {body.strategy_id} 配置不存在")
    gate_state = spec.get("gate_state", GateState.DRAFT.value)
    if gate_state == GateState.PAPER_PASSED.value:
        return _ok({"gate_state": gate_state}, "策略已处于 paper_passed")
    if gate_state != GateState.PAPER_VALIDATING.value:
        return _fail(f"策略当前状态 {gate_state}，须处于模拟验证中（paper_validating）才能确认")
    spec["gate_state"] = GateState.PAPER_PASSED.value
    await _save_strategy_specs(specs)
    await store.audit("api", "gate_advanced", {
        "strategy_id": body.strategy_id, "via": "manual_confirm",
        "paper_days_done": spec.get("paper_days_done", 0),
    })
    return _ok({"gate_state": GateState.PAPER_PASSED.value}, "已确认模拟通过")


# ============================================================
# 5.11 POST /risk/reset-breaker — 熔断复位
# ============================================================
@router.post("/risk/reset-breaker")
async def reset_breaker() -> Dict[str, Any]:
    """复位熔断计数与当日亏损状态（人工确认后执行，留痕）"""
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None
    await trading_facade.risk.reset_breaker()
    return _ok({"breaker": {"tripped": False, "consecutive_failures": 0}}, "熔断已复位")


# ============================================================
# 5.12 POST /notify/test — 测试邮件
# ============================================================
@router.post("/notify/test")
async def notify_test(body: TestMailBody):
    """发送测试邮件，验证 SMTP 配置与收件人是否可达"""
    await trading_facade.ensure_ready()
    assert trading_facade.alerter is not None
    recipient = (body.to or "").strip()
    if not recipient:
        auto = AutomationConfig.from_dict(await trading_facade.store.kv_get("automation"))
        recipient = (auto.email.get("recipient") or "").strip()
    if not recipient:
        # 与 alerts._resolve_recipient 同一回退：发件账户自发自收
        import os
        recipient = (os.environ.get("QQ_EMAIL_ACCOUNT") or "").strip()
    if not recipient:
        return _fail("缺少收件邮箱（请在请求或配置中提供）")
    sent, message = await trading_facade.alerter.send_test_email(recipient)
    return _ok({"sent": sent, "to": recipient}, message)


# ============================================================
# 5.13 POST /init-paper — 一键初始化自主虚拟盘
# ============================================================
@router.post("/init-paper")
async def init_paper():
    """开启 paper 总开关、白名单覆盖全池、为已回测/验证中策略启用默认标的、复位熔断。"""
    from server.trading import paper_drill
    store = await _store()
    data = await paper_drill.ensure_paper_ready(store)
    return _ok(data, "虚拟盘已初始化：自动买卖链路就绪（信号提示，供参考）")


# ============================================================
# 5.14 POST /run-cycle — 跑一轮自主虚拟盘买卖 + 总结复盘
# ============================================================
@router.post("/run-cycle")
async def run_paper_cycle(drill: bool = True):
    """执行一轮完整闭环：策略信号→模拟成交→自动平仓→单笔复盘→迭代决策。

    drill=true 时为未持仓的启用标的补「演练建仓」单，保证产出完整买入→卖出→复盘数据。
    """
    from server.trading import paper_drill
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None and trading_facade.paper is not None
    data = await paper_drill.run_paper_cycle(
        trading_facade.store, trading_facade.risk, trading_facade.paper, trading_facade.loop,
        close_positions=True, drill=drill,
    )
    return _ok(data, "一轮自主虚拟盘买卖 + 复盘已执行完成")


# ============================================================
# 5.15 手动下单 / 撤单（走同一套风控 9 关 + 决策闸，绝不绕行）
# ============================================================
def _reject_gate(checks: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for name, c in (checks or {}).items():
        if isinstance(c, dict) and c.get("pass") is False:
            return {"gate": name, "reason": c.get("reason")}
    return None


def _order_summary(res) -> Dict[str, Any]:
    return {
        "order_id": res.order_id, "ok": res.ok, "status": res.status,
        "code": res.code, "side": res.side, "qty": res.qty, "price": res.price,
        # 用户请求数量/金额（换算前）。被拦下时 qty 可能为 0，前端应显示 req_qty 以还原用户意图。
        "req_qty": int(getattr(res, "req_qty", 0) or 0),
        "req_amount": round(float(getattr(res, "req_amount", 0.0) or 0.0), 2),
        "amount": res.amount, "gateway": res.gateway, "source": getattr(res, "source", "signal"),
        "fail_reason": res.fail_reason, "exit_reason": res.exit_reason,
        "rejected_gate": _reject_gate(res.checks),
        "decision": res.decision_json,
        # 成本明细（成交成功时回显给前端）
        "ref_price": res.ref_price, "commission": res.commission, "stamp_tax": res.stamp_tax,
        "transfer_fee": res.transfer_fee, "slippage_cost": res.slippage_cost,
        "total_cost": res.total_cost,
    }


@router.post("/orders")
async def manual_order(body: ManualOrderBody):
    """手动下单：与信号单**共用**决策闸 + 风控 9 关（白名单/额度/涨跌停/停牌/T+1 一个都不绕）。"""
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None
    code = (body.code or "").strip().lower()
    if not _valid_code(code):
        return _fail("股票代码格式非法（应为 sh600519 形式）")
    if body.side not in ("buy", "sell"):
        return _fail("side 仅支持 buy / sell")

    store = trading_facade.store
    price = float(body.price or 0.0)
    name = body.name or ""
    if price <= 0:
        try:
            from server.realtime import fetch_single_quote
            q = await fetch_single_quote(code)
            price = float((q or {}).get("price") or 0.0)
            name = name or (q or {}).get("name") or ""
        except Exception:
            pass
    if price <= 0:
        return _fail("无法获取有效现价，且未提供参考价 price")

    order = OrderRequest(code=code, name=name, side=body.side, price=price,
                         qty=max(0, int(body.qty or 0)), source="manual",
                         is_drill=bool(body.is_drill))
    cfg = AutomationConfig.from_dict(await store.kv_get("automation"))
    res = await trading_facade.risk.execute_order(order, cfg)
    await store.audit("api", "order_manual_submit", {
        "order_id": res.order_id, "code": code, "side": body.side, "status": res.status,
        "fail_reason": res.fail_reason, "rejected_gate": _reject_gate(res.checks),
    }, result="ok" if res.ok else "denied")
    return _ok(_order_summary(res),
               "手动单已提交（信号提示，供参考）" if res.ok else f"手动单被拒：{res.fail_reason}")


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str):
    """撤单：只能撤未成交单。已成交/已拒绝 → 明确拒绝（不静默成功）。"""
    store = await _store()
    ok, message, order = await store.cancel_order(order_id)
    await store.audit("api", "order_canceled",
                      {"order_id": order_id, "ok": ok, "message": message},
                      result="ok" if ok else "denied")
    if not ok:
        return _fail(message)
    return _ok({"order_id": order_id, "status": (order or {}).get("status")}, message)


# ============================================================
# 5.16 条件单（价格触发 / 时间触发）——由 SignalMonitor 轮询触发
# ============================================================
@router.post("/conditional-orders")
async def create_conditional_order(body: ConditionalOrderBody):
    """新建条件单（价格 >=/<= 或定时触发）；触发时仍走风控 9 关 + 决策闸"""
    await trading_facade.ensure_ready()
    code = (body.code or "").strip().lower()
    if not _valid_code(code):
        return _fail("股票代码格式非法（应为 sh600519 形式）")
    if body.side not in ("buy", "sell"):
        return _fail("side 仅支持 buy / sell")
    if body.trigger_type not in ("price", "time"):
        return _fail("trigger_type 仅支持 price / time")
    if body.trigger_type == "price":
        if body.trigger_op not in (">=", "<=") or body.trigger_price is None:
            return _fail("price 触发需提供 trigger_op(>=/<=) 与 trigger_price")
    else:
        if not body.trigger_at:
            return _fail("time 触发需提供 trigger_at（YYYY-MM-DDTHH:MM:SS）")

    store = trading_facade.store
    cid = new_id("cond")
    row = {
        "id": cid, "ts": now_local_iso(), "code": code, "name": body.name or code,
        "side": body.side, "qty": max(0, int(body.qty or 0)),
        "trigger_type": body.trigger_type, "trigger_op": body.trigger_op,
        "trigger_price": body.trigger_price, "trigger_at": body.trigger_at,
        "status": "pending", "note": None, "source": "manual",
    }
    await store.insert_conditional(row)
    await store.audit("api", "conditional_created", {
        "conditional_id": cid, "code": code, "side": body.side,
        "trigger_type": body.trigger_type, "trigger_op": body.trigger_op,
        "trigger_price": body.trigger_price, "trigger_at": body.trigger_at,
    })
    return _ok(row, "条件单已创建（待触发；触发后仍走风控+决策闸）")


@router.get("/conditional-orders")
async def list_conditional_orders(status: Optional[str] = None):
    """条件单列表（可按 status 过滤：pending / triggered / expired / canceled）"""
    store = await _store()
    items = await store.list_conditionals(status)
    return _ok({"total": len(items), "items": items})


@router.delete("/conditional-orders/{conditional_id}")
async def cancel_conditional_order(conditional_id: str):
    """撤销未触发的条件单，并写审计留痕"""
    store = await _store()
    ok, message = await store.cancel_conditional(conditional_id)
    await store.audit("api", "conditional_canceled",
                      {"conditional_id": conditional_id, "ok": ok, "message": message},
                      result="ok" if ok else "denied")
    if not ok:
        return _fail(message)
    return _ok({"conditional_id": conditional_id, "status": "canceled"}, message)


@router.post("/conditional-orders/{conditional_id}/check")
async def check_conditional_order(conditional_id: str):
    """按当前价即时评估一条条件单（便于验证/手动触发）；满足则走风控+决策闸下单。"""
    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None
    store = trading_facade.store
    cond = await store.get_conditional(conditional_id)
    if cond is None:
        return _fail("条件单不存在")
    if cond.get("status") != "pending":
        return _fail(f"条件单状态 {cond.get('status')}，不可触发")
    from server.trading import conditional as cond_mod
    from datetime import datetime
    now = datetime.now()
    try:
        from server.realtime import fetch_single_quote
        q = await fetch_single_quote(cond["code"])
    except Exception:
        q = None
    price = float((q or {}).get("price") or 0) if q else None
    hit = cond_mod.triggered(cond, price if (price and price > 0) else None, now)
    if hit is not True:
        return _ok({"conditional_id": conditional_id, "triggered": False, "price": price},
                   "条件未满足")
    cfg = AutomationConfig.from_dict(await store.kv_get("automation"))
    order = cond_mod.build_order(cond, price or 0)
    res = await trading_facade.risk.execute_order(order, cfg)
    await store.mark_conditional_triggered(conditional_id, res.order_id, now_local_iso(), price or 0.0)
    await store.audit("api", "conditional_triggered", {
        "conditional_id": conditional_id, "code": cond["code"], "price": price,
        "order_id": res.order_id, "order_status": res.status,
    })
    return _ok({"conditional_id": conditional_id, "triggered": True, **_order_summary(res)},
               "条件满足，已触发下单")


@router.get("/decision-quality")
async def decision_quality(limit: int = 200):
    """决策质量评分（含被闸拒绝的决策）：信号强度 vs 实际结果。"""
    store = await _store()
    items = await store.kv_get("decision_quality", []) or []
    return _ok({"total": len(items), "items": items[-max(1, min(limit, 500)):]})


# ============================================================
# GET /audit — 操作留痕查询（audit_log 透出，全程可追溯）
# ============================================================
@router.get("/audit")
async def list_audit(action: Optional[str] = None, actor: Optional[str] = None,
                     result: Optional[str] = None,
                     limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """审计日志查询：决策闸判定/下单/拒单/撤单/配置变更/条件单触发等全量留痕。

    过滤参数均为可选精确匹配；返回 {total, items[]}，items 按时间倒序，
    payload 已解析为结构化对象。
    """
    store = await _store()
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total, items = await store.list_audit(action=action, actor=actor, result=result,
                                          limit=limit, offset=offset)
    return _ok({"total": total, "items": items,
                "filters": {"action": action, "actor": actor, "result": result}})


# ============================================================
# 5.13 GET /risk/health — 风控体检（逐条约束 + 修复建议）
# ============================================================
@router.get("/risk/health")
async def risk_health() -> Dict[str, Any]:
    """把风控各道关卡、策略门禁、熔断、行情时段、近期拒单拼成一份可执行的体检报告。

    只读：不修改配置、不触发订单；每条不过项都带 ``fix`` 修复建议。
    """
    from collections import Counter

    from server.trading import market_sync
    from server.trading.models import AutomationConfig

    await trading_facade.ensure_ready()
    assert trading_facade.risk is not None
    store = await _store()
    cfg = RiskConfig.from_dict(await store.kv_get("risk"))
    auto_cfg = AutomationConfig.from_dict(await store.kv_get("automation"))
    gate = await trading_facade.risk.gate_overview()
    positions = await store.list_positions("paper")
    total_orders, orders = await store.list_orders(limit=200)
    rejects = [o for o in orders if o.get("status") == "rejected"]
    filled = [o for o in orders if o.get("status") == "filled"]
    counter = Counter((o.get("fail_reason") or "未知原因")[:60] for o in rejects)
    top_rejects = [{"reason": k, "n": v} for k, v in counter.most_common(3)]

    checks: List[market_sync.Check] = []

    # 1) 白名单
    wl = cfg.whitelist or []
    checks.append(market_sync.Check(
        "whitelist", "风控白名单", "ok" if wl else "warn", bool(wl),
        f"{len(wl)} 只",
        "" if wl else "白名单为空：手工单会被风控第 3 关直接拒绝（自动信号同理）。",
        "在「配置 → 风控」填入白名单，或点「一键初始化模拟盘」按全池覆盖。",
    ))

    # 2) 额度上限
    ok_limits = cfg.max_amount_per_order > 0 and cfg.max_amount_per_day > 0
    checks.append(market_sync.Check(
        "limits", "单笔 / 单日额度", "ok" if ok_limits else "fail", ok_limits,
        f"单笔 ¥{cfg.max_amount_per_order:,.0f} / 单日 ¥{cfg.max_amount_per_day:,.0f}",
        "" if ok_limits else "额度必须为正数，否则任何订单都会被第 5 关拦截。",
        "在「配置 → 风控」调整 max_amount_per_order / max_amount_per_day。",
    ))

    # 3) 持仓上限
    used, cap = len(positions), max(1, int(cfg.max_positions))
    checks.append(market_sync.Check(
        "positions", "持仓数量", "ok" if used < cap else "warn", used < cap,
        f"{used} / {cap} 只",
        "" if used < cap else "已达持仓上限：新的买入会被第 6 关拦截。",
        "先减仓，或在配置中调高 max_positions。",
    ))

    # 4) 熔断
    breaker = (gate.get("breaker") or {})
    tripped = bool(breaker.get("tripped"))
    fails = int(breaker.get("consecutive_failures") or 0)
    checks.append(market_sync.Check(
        "breaker", "熔断状态", "fail" if tripped else "ok", not tripped,
        "已熔断" if tripped else f"正常（连续失败 {fails} / {cfg.breaker_fail_threshold}）",
        "熔断期间所有自动下单都会被拦截。" if tripped else "",
        "确认原因后调用「复位熔断」(POST /api/automation/risk/reset-breaker)。" if tripped else "",
    ))

    # 5) 策略门禁
    strategies = gate.get("strategies") or []
    drafts = [s.get("name") or s.get("strategy_id") for s in strategies if s.get("gate_state") == "draft"]
    live_ok = [s for s in strategies if s.get("can_enable_live")]
    checks.append(market_sync.Check(
        "strategy_gate", "策略门禁", "warn" if drafts else "ok", not drafts,
        f"{len(strategies)} 个策略 / draft {len(drafts)} / 可上实盘 {len(live_ok)}",
        ("draft 策略不允许 paper 下单：" + "、".join([d for d in drafts[:4] if d])) if drafts else "",
        "在「配置 → 策略门禁」完成回测 + 5 日模拟后逐级晋级（回测 → 已回测 → 模拟中 → 模拟通过）。",
    ))

    # 6) 组合风险指标
    risk_payload = _risk_block_cached()
    risk_state = (risk_payload or {}).get("state") or ("computing" if risk_payload is None else "unavailable")
    risk_reason = (risk_payload or {}).get("reason") or ("首次计算中，稍后刷新" if risk_payload is None else "")
    sample_days = int((risk_payload or {}).get("sample_days") or 0)
    checks.append(market_sync.Check(
        "risk_metrics", "组合风险指标",
        "ok" if risk_state == "ok" else "info", risk_state == "ok",
        f"{risk_state} · 样本 {sample_days}/{risk_metrics.MIN_SAMPLES} 日",
        risk_reason,
        "保持模拟盘运行以积累样本；样本不足时指标标 degraded，不作为硬门槛。",
    ))

    # 7) 行情时段（提示项，不阻断纸面演练）
    ctx = market_sync.decision_context(
        float((auto_cfg.trigger or {}).get("score_threshold") or 80),
        None, None,
    )
    sess = ctx["session"]
    checks.append(market_sync.Check(
        "market_session", "行情时段", "ok" if sess["can_order"] else "info", sess["can_order"],
        f"{sess['label']}（{sess['now'][11:16]}）",
        "" if sess["can_order"] else "非连续竞价时段：信号仍会生成并记录，实盘申报会被交易所拒绝。",
        "在交易时段（09:30–11:30 / 13:00–15:00）执行实盘；纸面演练不受限。",
    ))

    # 8) 近期拒单归因
    if top_rejects:
        top = top_rejects[0]
        fix = "按原因处理：加白名单 / 调额度 / 晋级策略 / 等冷却或日内损失重置。"
        if "白名单" in top["reason"]:
            fix = "把标的加入风控白名单（配置 → 风控 → 白名单）。"
        elif "单笔金额" in top["reason"]:
            fix = "在配置中调高单笔上限，或用「按权重」下单让系统按上限自动拆手。"
        elif "策略门" in top["reason"]:
            fix = "在策略门禁完成回测与模拟晋级，draft 状态不会放行 paper 下单。"
        elif "冷却" in top["reason"]:
            fix = "等待冷却窗口结束，或调整 cooldown_minutes。"
        checks.append(market_sync.Check(
            "reject_attribution", "近期拒单归因", "warn", False,
            f"拒单 {len(rejects)} 笔（成交 {len(filled)}）",
            f"首要原因：{top['reason']} × {top['n']}",
            fix,
        ))

    summary = market_sync.summarize_checks(checks)
    return _ok({
        **summary,
        "market": ctx,
        "rejects": top_rejects,
        "order_stats": {"total": total_orders, "filled": len(filled), "rejected": len(rejects)},
        "risk_block_state": risk_state,
        "generated_at": now_local_iso(),
    })


# ============================================================
# 5.14 GET /market-sync — 市场同步（时段 / 市况 / 阈值建议）
# ============================================================
@router.get("/market-sync")
async def market_sync_view(
    index_pct: Optional[float] = None,
    breadth: Optional[float] = None,
) -> Dict[str, Any]:
    """返回交易时段、市况状态与动态阈值建议。

    ``index_pct`` / ``breadth`` 可由前端或行情任务传入；缺省时读取
    ``store.kv['market_regime']``（若有），否则按中性处理。
    """
    from server.trading import market_sync
    from server.trading.models import AutomationConfig

    store = await _store()
    auto_cfg = AutomationConfig.from_dict(await store.kv_get("automation"))
    base = float((auto_cfg.trigger or {}).get("score_threshold") or 80)
    kv = await store.kv_get("market_regime", default=None) or {}
    ip = index_pct if index_pct is not None else kv.get("index_pct")
    bd = breadth if breadth is not None else kv.get("breadth")
    ctx = market_sync.decision_context(base, ip, bd)
    ctx["source"] = "query" if (index_pct is not None or breadth is not None) else ("kv" if kv else "default")
    return _ok(ctx)
