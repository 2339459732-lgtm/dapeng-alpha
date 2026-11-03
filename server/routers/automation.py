"""自动化交易路由聚合（配置 / 持仓 / 策略 / 下单，实现见 automation_* 子路由）"""

from fastapi import APIRouter

from server.routers.automation_common import router as _r0
from server.routers.automation_config import router as _r1
from server.routers.automation_trade import router as _r2

router = APIRouter()
router.include_router(_r0)
router.include_router(_r1)
router.include_router(_r2)

from server.routers.automation_common import (
    BacktestBody,
    ConditionalOrderBody,
    ConfigUpdateBody,
    EnableBody,
    LiveSwitchBody,
    ManualOrderBody,
    PaperConfirmBody,
    TestMailBody,
    _CODE_RE,
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
from server.routers.automation_config import (
    REVIEW_METRICS_TTL,
    REVIEW_MIN_SAMPLE_DAYS,
    RISK_BLOCK_DAYS,
    _compact_risk_block,
    _compute_review_metrics,
    _ensure_risk_warm,
    _risk_block_cached,
    _risk_warm_task,
    get_config,
    list_orders,
    list_positions,
    list_signals,
    review_metrics,
    update_config,
)
from server.routers.automation_trade import (
    _order_summary,
    _reject_gate,
    cancel_conditional_order,
    cancel_order,
    check_conditional_order,
    create_conditional_order,
    decision_quality,
    gate_overview,
    init_paper,
    list_audit,
    list_conditional_orders,
    live_switch,
    manual_order,
    market_sync_view,
    notify_test,
    reset_breaker,
    risk_health,
    run_paper_cycle,
    strategy_backtest,
    strategy_enable,
    strategy_paper_confirm,
)

__all__ = ["router"]
