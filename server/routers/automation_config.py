"""配置与账户视图：config / signals / orders / positions / review"""

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
    ConfigUpdateBody,
    _VALID_SIGNAL_TYPES,
    _fail,
    _normalize_codes,
    _ok,
    _save_strategy_specs,
    _store,
    _strategy_specs,
    _valid_code,
    logger,
)

router = APIRouter()


# ============================================================
# 5.1 GET /config — 读取配置
# ============================================================
@router.get("/config")
async def get_config() -> Dict[str, Any]:
    """读取自动化全量配置：总开关、触发条件、风控参数、策略门禁与显示名、决策闸参数、各配置段更新时间"""
    store = await _store()
    auto = AutomationConfig.from_dict(await store.kv_get("automation"))
    risk_cfg = RiskConfig.from_dict(await store.kv_get("risk"))
    specs = await store.kv_get("strategies", default=[]) or []
    data = auto.to_dict()
    data["risk"] = risk_cfg.to_dict()
    data["strategies"] = [
        {
            "strategy_id": s.get("strategy_id"),
            "kind": s.get("kind", s.get("strategy_id")),
            # 策略显示名（e3，基类属性为 Strategy.name 中文名）
            "display_name": (get_strategy(s.get("strategy_id", "")).name
                             if has_strategy(s.get("strategy_id", "")) else s.get("strategy_id")),
            "params": s.get("params") or {},
            "gate_state": s.get("gate_state", GateState.DRAFT.value),
            "paper_days_done": int(s.get("paper_days_done", 0)),
            "paper_days_required": int(s.get("paper_days_required", 5)),
            "enabled_codes": s.get("enabled_codes") or [],
        }
        for s in specs
    ]
    # 决策闸参数（可配，缺省=默认值）与条件单过期天数
    data["decision_gate"] = await decision_gate.load_gate_config(store)
    try:
        data["conditional_expire_days"] = int(
            await store.kv_get("conditional_expire_days", 30) or 30)
    except (TypeError, ValueError):
        data["conditional_expire_days"] = 30
    # 配置新鲜度（e2）：透传各 KV 段最近更新时间
    data["updated_at"] = {
        "automation": await store.kv_updated_at("automation"),
        "risk": await store.kv_updated_at("risk"),
        "strategies": await store.kv_updated_at("strategies"),
        "decision_gate": await store.kv_updated_at("decision_gate"),
    }
    return _ok(data)


# ============================================================
# 5.2 POST /config — 更新配置（部分字段合并）
# ============================================================
@router.post("/config")
async def update_config(body: ConfigUpdateBody):
    """合并更新自动化配置（风控/触发/决策闸/条件单过期天数）；实盘开关不接受此端点，须走 /gate/live-switch 二次确认"""
    store = await _store()

    # 实盘只能走 live-switch 专用端点（带二次确认），此端点一律拒绝
    if body.switches and body.switches.get("live") is True:
        return _fail("实盘开关只能经 /api/automation/gate/live-switch 二次确认后开启")

    diff: Dict[str, Any] = {}

    # ---- automation KV 合并 ----
    auto = AutomationConfig.from_dict(await store.kv_get("automation"))
    # 深拷贝快照：switches/scope/trigger/quiet/email 均为原地修改的嵌套 dict，
    # 浅引用会导致 new==old 恒等、kv_set 与审计被跳过（仅改 dict 字段时不落库的既有 bug）
    old_auto = copy.deepcopy(auto.to_dict())
    if body.switches:
        for key in ("notify", "paper"):
            if key in body.switches:
                auto.switches[key] = bool(body.switches[key])
    if body.scope:
        if "mode" in body.scope:
            if body.scope["mode"] not in ("pool", "watchlist"):
                return _fail("scope.mode 仅支持 pool / watchlist")
            auto.scope["mode"] = body.scope["mode"]
        if "watchlist" in body.scope:
            auto.scope["watchlist"] = _normalize_codes(body.scope["watchlist"] or [])
    if body.trigger:
        if "score_threshold" in body.trigger:
            auto.trigger["score_threshold"] = int(body.trigger["score_threshold"])
        if "signal_types" in body.trigger:
            types = [str(t) for t in body.trigger["signal_types"] or []]
            invalid = [t for t in types if t not in _VALID_SIGNAL_TYPES]
            if invalid:
                return _fail(f"非法信号类型：{invalid}（合法值：{sorted(_VALID_SIGNAL_TYPES)}）")
            auto.trigger["signal_types"] = types
        if "chg_pct_threshold" in body.trigger:
            auto.trigger["chg_pct_threshold"] = float(body.trigger["chg_pct_threshold"])
    if body.cooldown_minutes is not None:
        auto.cooldown_minutes = max(1, int(body.cooldown_minutes))
    if body.quiet:
        for key in ("trading_only", "premarket_digest"):
            if key in body.quiet:
                auto.quiet[key] = bool(body.quiet[key])
    if body.email:
        if "recipient" in body.email:
            auto.email["recipient"] = str(body.email["recipient"] or "").strip()
        if "per_stock_per_day" in body.email:
            auto.email["per_stock_per_day"] = max(1, int(body.email["per_stock_per_day"]))
        if "global_per_hour" in body.email:
            auto.email["global_per_hour"] = max(1, int(body.email["global_per_hour"]))
    new_auto = auto.to_dict()
    if new_auto != old_auto:
        diff["automation"] = {"old": old_auto, "new": new_auto}
        await store.kv_set("automation", new_auto)

    # ---- risk KV 合并 ----
    if body.risk:
        risk_cfg = RiskConfig.from_dict(await store.kv_get("risk"))
        old_risk = risk_cfg.to_dict()
        if "max_amount_per_order" in body.risk:
            risk_cfg.max_amount_per_order = float(body.risk["max_amount_per_order"])
        if "max_amount_per_day" in body.risk:
            risk_cfg.max_amount_per_day = float(body.risk["max_amount_per_day"])
        if "max_positions" in body.risk:
            risk_cfg.max_positions = max(1, int(body.risk["max_positions"]))
        if "whitelist" in body.risk:
            risk_cfg.whitelist = _normalize_codes(body.risk["whitelist"] or [])
        if "breaker_fail_threshold" in body.risk:
            risk_cfg.breaker_fail_threshold = max(1, int(body.risk["breaker_fail_threshold"]))
        if "daily_loss_limit_pct" in body.risk:
            risk_cfg.daily_loss_limit_pct = float(body.risk["daily_loss_limit_pct"])
        # 成交成本模型
        for key in ("slippage_bps", "commission_rate", "commission_min",
                    "stamp_tax_rate", "transfer_fee_rate",
                    "take_profit_pct", "trailing_stop_pct"):
            if key in body.risk:
                risk_cfg.__dict__[key] = float(body.risk[key])
        # 真实约束校验开关
        for key in ("check_price_limit", "check_suspension"):
            if key in body.risk:
                risk_cfg.__dict__[key] = bool(body.risk[key])
        new_risk = risk_cfg.to_dict()
        if new_risk != old_risk:
            diff["risk"] = {"old": old_risk, "new": new_risk}
            await store.kv_set("risk", new_risk)

    # ---- strategies KV 合并（参数变更 → gate_state 强制回退 draft）----
    if body.strategies:
        specs = await store.kv_get("strategies", default=[]) or []
        by_id = {s.get("strategy_id"): s for s in specs}
        for item in body.strategies:
            sid = item.get("strategy_id", "")
            spec = by_id.get(sid)
            if spec is None or not has_strategy(sid):
                return _fail(f"未知策略：{sid}")
            strategy = get_strategy(sid)
            if "params" in item and item["params"] is not None:
                merged = dict(strategy.default_params)
                merged.update(item["params"] or {})
                if merged != (spec.get("params") or {}):
                    # 参数变了，回测/模拟结论作废，重走全链（§6.2 回退条件）
                    diff.setdefault("strategies", []).append({
                        "strategy_id": sid,
                        "old_params": spec.get("params"),
                        "new_params": merged,
                        "gate_state": f"{spec.get('gate_state')}→{GateState.DRAFT.value}",
                    })
                    spec["params"] = merged
                    spec["gate_state"] = GateState.DRAFT.value
                    spec["last_backtest"] = None
                    spec["paper_days_done"] = 0
                    spec["live_enabled"] = False
            if "paper_days_required" in item:
                spec["paper_days_required"] = max(1, int(item["paper_days_required"]))
        await _save_strategy_specs(specs)

    # ---- 决策闸参数（KV decision_gate，逐项校验合并，异常项丢弃）----
    if body.decision_gate:
        old_gate = decision_gate.normalize_gate_config(await store.kv_get("decision_gate"))
        merged = dict(old_gate)
        for k in ("weights", "allow_threshold", "target_daily_vol_pct",
                  "vol_factor_min", "vol_factor_max", "tech_base"):
            if k in body.decision_gate:
                merged[k] = body.decision_gate[k]
        new_gate = decision_gate.normalize_gate_config(merged)
        if new_gate != old_gate:
            diff["decision_gate"] = {"old": old_gate, "new": new_gate}
            await store.kv_set("decision_gate", new_gate)
            decision_gate.bust_gate_config_cache()

    # ---- 条件单自动过期天数（KV conditional_expire_days）----
    if body.conditional_expire_days is not None:
        days = int(body.conditional_expire_days)
        old_days = await store.kv_get("conditional_expire_days", 30)
        if days != old_days:
            diff["conditional_expire_days"] = {"old": old_days, "new": days}
            await store.kv_set("conditional_expire_days", days)

    if diff:
        await store.audit("api", "config_updated", diff)

    return await get_config()


# ============================================================
# 5.3 GET /signals
# ============================================================
@router.get("/signals")
async def list_signals(code: Optional[str] = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """信号分页查询（可按标的过滤），返回 total + items"""
    store = await _store()
    if code:
        code = code.strip().lower()
        if not _valid_code(code):
            return _fail("股票代码格式非法（应为 sh600519 形式）")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total, items = await store.list_signals(code=code or None, limit=limit, offset=offset)
    return _ok({"total": total, "items": items})


# ============================================================
# 5.4 GET /orders
# ============================================================
@router.get("/orders")
async def list_orders(code: Optional[str] = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """订单分页查询（可按标的过滤，含拒绝单与失败原因），返回 total + items"""
    store = await _store()
    if code:
        code = code.strip().lower()
        if not _valid_code(code):
            return _fail("股票代码格式非法（应为 sh600519 形式）")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total, items = await store.list_orders(code=code or None, limit=limit, offset=offset)
    return _ok({"total": total, "items": items})


# ============================================================
# 5.5 GET /positions
# ============================================================
RISK_BLOCK_DAYS = 180          # /positions 里 risk 块使用的回归窗口（交易日）
_risk_warm_task: Optional["asyncio.Task[Any]"] = None


def _risk_block_cached() -> Optional[Dict[str, Any]]:
    """只读 risk 缓存（不触发计算/上游）。"""
    entry = cache.get_entry(risk_cache_key(RISK_BENCHMARK, RISK_BLOCK_DAYS))
    return entry["value"] if entry else None


def _ensure_risk_warm() -> None:
    """后台预热组合风险快照（幂等）。避免 /positions 因首次计算而变慢。"""
    global _risk_warm_task
    if _risk_warm_task is not None and not _risk_warm_task.done():
        return

    async def _run() -> None:
        try:
            await cached_portfolio_risk(RISK_BLOCK_DAYS, RISK_BENCHMARK)
        except Exception as exc:  # pragma: no cover - 预热失败不影响主流程
            logger.warning("risk warm failed: %s", exc)

    _risk_warm_task = asyncio.create_task(_run())


def _compact_risk_block(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把组合风险快照裁剪成 /positions 需要的 risk 块（字段与 §3.5 对齐）。"""
    if not payload:
        return {
            "state": "computing",
            "available": False,
            "benchmark": RISK_BENCHMARK,
            "beta": None, "r2": None, "idiosyncratic_risk": None,
            "sample_days": 0, "sample_required": risk_metrics.MIN_SAMPLES,
            "metrics": {"sharpe": None, "sortino": None, "omega": None,
                        "cvar_95": None, "volatility": None,
                        "risk_free_rate": 0.0,
                        "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化"},
            "as_of": None,
            "reason": "组合风险首次计算中（后台执行，约 1–2s 后刷新即得）",
        }
    m = payload.get("metrics") or {}
    return {
        "state": "ok" if payload.get("available") else "unavailable",
        "available": bool(payload.get("available")),
        "benchmark": payload.get("benchmark"),
        "benchmark_name": payload.get("benchmark_name"),
        "beta": payload.get("beta"),
        "r2": payload.get("r2"),
        "idiosyncratic_risk": payload.get("idiosyncratic_risk"),
        "total_risk": payload.get("total_risk"),
        "systematic_pct": payload.get("systematic_pct"),
        "alpha_annual": payload.get("alpha_annual"),
        "sample_days": payload.get("sample_days"),
        "sample_required": payload.get("sample_required"),
        "metrics": {
            "sharpe": m.get("sharpe"),
            "sortino": m.get("sortino"),
            "omega": m.get("omega"),
            "cvar_95": m.get("cvar_95"),
            "volatility": m.get("volatility"),
            "risk_free_rate": 0.0,
            "note": m.get("note") or "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化",
            "reasons": m.get("reasons") or {},
        },
        "as_of": payload.get("as_of"),
        "reason": payload.get("reason"),
    }


@router.get("/positions")
async def list_positions() -> Dict[str, Any]:
    """当前纸面持仓（成本/最新价/浮动盈亏）+ 账户权益 + 纸面运行天数进度

    T04 追加（**不改既有字段**）：
    - 每只持仓：``weight_pct`` / ``price_available`` / ``price_source`` /
      ``price_stale`` / ``price_stale_age`` / ``beta``；
    - 顶层 ``risk`` 块：组合 β / R² / 特质风险 / 夏普 / 索提诺 / Omega / CVaR95。
      风险指标由后台预热并缓存（TTL 60s），**本端点不做同步计算**，保证耗时稳定。
    """
    await trading_facade.ensure_ready()
    assert trading_facade.paper is not None
    acc = await trading_facade.paper.query_account()
    positions = await trading_facade.store.list_positions("paper")
    specs = await _strategy_specs()
    paper_days_done = max([int(s.get("paper_days_done", 0)) for s in specs] or [0])
    paper_days_required = max([int(s.get("paper_days_required", 5)) for s in specs] or [5])
    items = [
        {
            "code": p.code, "name": p.name, "qty": p.qty,
            "avg_cost": round(p.avg_cost, 3), "last_price": p.last_price,
            "pnl_pct": p.pnl_pct,
        }
        for p in positions
    ]

    # ---- T04：权重 + 价格新鲜度（只读 QuoteHub 缓存，不触发上游）----
    total_mv = 0.0
    for p in positions:
        px = float(p.last_price or 0) or float(p.avg_cost or 0)
        total_mv += max(0.0, float(p.qty or 0)) * px
    risk_payload = _risk_block_cached()
    per_code_beta = (risk_payload or {}).get("per_code_beta") or {}
    for it, p in zip(items, positions):
        px = float(p.last_price or 0) or float(p.avg_cost or 0)
        mv = max(0.0, float(p.qty or 0)) * px
        it["weight_pct"] = round(mv / total_mv * 100.0, 2) if total_mv > 0 else None

        meta = quote_hub.cached_meta(it["code"])
        if meta and meta.get("price"):
            it["price_available"] = True
            it["price_source"] = "腾讯实时（QuoteHub）"
            it["price_stale"] = bool(meta.get("stale"))
            it["price_stale_age"] = meta.get("stale_age")
        else:
            has_stored = bool(it.get("last_price") and it["last_price"] > 0)
            it["price_available"] = has_stored
            it["price_source"] = "持仓快照（最近一次写入）"
            it["price_stale"] = True if has_stored else None
            it["price_stale_age"] = None
            if not has_stored:
                it["price_reason"] = "无有效现价（停牌或从未刷新）"

        pb = per_code_beta.get(it["code"]) or {}
        it["beta"] = pb.get("beta")
        if it["beta"] is None and pb.get("reason"):
            it["beta_reason"] = pb.get("reason")

    # ---- T04：risk 块（缓存命中直接给值；未命中则后台预热并如实标 computing）----
    if not positions:
        risk_block = {
            "state": "unavailable",
            "available": False,
            "benchmark": RISK_BENCHMARK,
            "beta": None, "r2": None, "idiosyncratic_risk": None,
            "sample_days": 0, "sample_required": risk_metrics.MIN_SAMPLES,
            "metrics": {"sharpe": None, "sortino": None, "omega": None,
                        "cvar_95": None, "volatility": None,
                        "risk_free_rate": 0.0,
                        "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化"},
            "as_of": None,
            "reason": "当前无持仓",
        }
    else:
        if risk_payload is None:
            _ensure_risk_warm()
        risk_block = _compact_risk_block(risk_payload)

    return _ok({
        # 自证：本面板为本地虚拟盘（paper）模拟账户，非真实券商账户
        "source": "paper_sim",
        "gateway": "paper",
        "cash": acc["cash"],
        # 虚拟盘初始资金（paper.py _DEFAULT_CASH=1,000,000）——此前被本端点丢弃，前端只能硬编码
        "initial_cash": acc.get("initial_cash"),
        "trading_days": acc.get("trading_days", 0),
        "market_value": acc["market_value"],
        "total_equity": acc["total_equity"],
        "items": items,
        "paper_days_done": paper_days_done,
        # 模拟验证所需交易日（来自策略配置），供前端替代硬编码的 /5
        "paper_days_required": paper_days_required,
        # T04 追加：组合风险块（β/R²/特质风险 + 夏普/索提诺/Omega/CVaR95）
        "risk": risk_block,
    })


# ============================================================
# 5.5b GET /review/metrics — 复盘指标条（T04 新增，7 项）
# ============================================================
REVIEW_MIN_SAMPLE_DAYS = risk_metrics.MIN_SAMPLES     # 60 个交易日
REVIEW_METRICS_TTL = 60.0                             # 秒


async def _compute_review_metrics(days: int) -> Dict[str, Any]:
    """复盘指标（交易质量 + 权益曲线风险调整指标），样本不足项一律 null + reason。"""
    store = await _store()
    trips = await review_service.round_trips(store)
    eq = await review_service.current_equity(store)
    snaps = await review_service.record_snapshot(store, eq["equity"])

    # 权益曲线：起点（初始资金）+ 最近 days 天快照，按日期去重升序
    by_date: Dict[str, float] = {}
    if eq.get("start_date"):
        by_date[str(eq["start_date"])] = float(eq["initial"])
    for snap in (snaps or [])[-int(days):]:
        d = str(snap.get("date") or "")
        try:
            v = float(snap.get("equity"))
        except (TypeError, ValueError):
            continue
        if d and v > 0:
            by_date[d] = v
    dates = sorted(by_date)
    points = [by_date[d] for d in dates]
    eq_returns: List[float] = []
    for i in range(1, len(points)):
        if points[i - 1] > 0:
            eq_returns.append(points[i] / points[i - 1] - 1.0)
    sample_days = len(eq_returns)

    # 交易质量（闭合交易，FIFO 净口径，已扣费用）
    pnls = [float(t.get("pnl") or 0.0) for t in trips]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    win_rate = round(len(wins) / len(pnls) * 100.0, 2) if pnls else None
    profit_factor = round(gross_win / gross_loss, 4) if gross_loss > 0 else None

    mdd = risk_metrics.max_drawdown(points) if len(points) >= 2 else {
        "value": None, "reason": "权益曲线不足 2 点，无法计算回撤"}
    net_pnl = (round(float(eq["equity"]) - float(eq["initial"]), 2)
               if eq.get("initial") else None)

    sharpe_r = risk_metrics.sharpe(eq_returns)
    sortino_r = risk_metrics.sortino(eq_returns)
    omega_r = risk_metrics.omega(eq_returns)
    cvar_r = risk_metrics.cvar95(eq_returns)

    reasons: Dict[str, Any] = {
        "win_rate": None if win_rate is not None else "无闭合交易",
        "profit_factor": None if profit_factor is not None else (
            "无亏损交易，盈亏比无定义" if pnls else "无闭合交易"),
        "max_drawdown": mdd.get("reason"),
        "net_pnl": None if net_pnl is not None else "初始资金缺失",
        "sharpe": sharpe_r.get("reason"),
        "sortino": sortino_r.get("reason"),
        "omega": omega_r.get("reason"),
        "cvar_95": cvar_r.get("reason"),
    }
    sample_insufficient = sample_days < REVIEW_MIN_SAMPLE_DAYS
    degraded = sample_insufficient or not trips
    reason_list = []
    if sample_insufficient:
        reason_list.append(f"样本 {sample_days}/{REVIEW_MIN_SAMPLE_DAYS} 个交易日，不足")
    for k in ("win_rate", "profit_factor", "max_drawdown", "net_pnl"):
        if reasons.get(k):
            reason_list.append(f"{k}: {reasons[k]}")

    return {
        "trade_count": len(pnls),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": mdd.get("value"),
        "net_pnl": net_pnl,
        "sharpe": sharpe_r.get("value"),
        "sortino": sortino_r.get("value"),
        "omega": omega_r.get("value"),
        "cvar_95": cvar_r.get("value"),
        "sample_days": sample_days,
        "sample_required": REVIEW_MIN_SAMPLE_DAYS,
        "sample_insufficient": sample_insufficient,
        "degraded": degraded,
        "reasons": reasons,
        "risk_free_rate": 0.0,
        "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化；回撤为负值（相对峰值）",
        "as_of": dates[-1] if dates else None,
    }


@router.get("/review/metrics")
async def review_metrics(
    days: int = 90,
    benchmark: str = RISK_BENCHMARK,
) -> Dict[str, Any]:
    """复盘指标条 7 项：交易笔数 / 胜率 / 盈亏比 / 最大回撤 / 夏普 / 索提诺 / Omega（+ CVaR95）。

    - 收益序列 = 纸面账户**每日权益**（``equity_snapshots``），样本 < 60 个交易日时
      ``sharpe/sortino/omega/cvar_95`` 全部 ``null`` + ``reason``（**绝不用短样本硬算**）；
    - 无闭合交易时胜率/盈亏比为 ``null`` + reason；
    - 结果缓存 60s。
    """
    bench = norm_benchmark(benchmark)
    key = f"review:metrics:{int(days)}:{bench}"
    data, meta = await cache.get_or_fetch_ex(
        key, lambda: _compute_review_metrics(int(days)), REVIEW_METRICS_TTL
    )
    if data is None:
        return {
            "ok": True,
            "data": {
                "trade_count": 0, "win_rate": None, "profit_factor": None,
                "max_drawdown": None, "net_pnl": None,
                "sharpe": None, "sortino": None, "omega": None, "cvar_95": None,
                "sample_days": 0, "sample_required": REVIEW_MIN_SAMPLE_DAYS,
                "sample_insufficient": True, "degraded": True,
                "reasons": {"error": "指标计算失败（无可用数据）"},
                "risk_free_rate": 0.0,
                "note": "无风险利率取 0（rf_annual=0.0）；按 252 交易日年化",
                "as_of": None,
            },
            "message": "ok",
            "source": "equity_snapshots",
            "as_of": now_local_iso(),
            "age": None,
            "degraded": True,
            "reason": "指标计算失败（无可用数据）",
        }
    return {
        "ok": True,
        "data": data,
        "message": "ok",
        "source": "equity_snapshots",
        "as_of": data.get("as_of") or now_local_iso(),
        "age": meta.get("age"),
        "degraded": bool(data.get("degraded")),
        "reason": ("；".join(filter(None, [
            f"样本 {data.get('sample_days')}/{data.get('sample_required')} 个交易日，不足"
            if data.get("sample_insufficient") else None,
        ])) or None),
    }
