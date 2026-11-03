"""管理后台路由聚合（看板 / 缓存 / 日志 / 调用统计，实现见 admin_* 子路由）"""

from fastapi import APIRouter

from server.routers.admin_common import router as _r0
from server.routers.admin_core import router as _r1
from server.routers.admin_ops import router as _r2
from server.routers.admin_stats import router as _r3

router = APIRouter()
router.include_router(_r0)
router.include_router(_r1)
router.include_router(_r2)
router.include_router(_r3)

from server.routers.admin_common import (
    API_CALLS_DB,
    LOGS_FILE,
    USERS_FILE,
    _CPU_MIN_INTERVAL,
    _CPU_PROC,
    _CPU_STATE,
    _add_log,
    _is_loopback,
    _load_logs,
    _load_users,
    _save_logs,
    logger,
    require_admin_access,
    sample_process_cpu_percent,
    sample_system_cpu_percent,
)
from server.routers.admin_core import (
    _file_stat,
    _read_json_meta,
    admin_dashboard,
    get_user,
    list_orders,
    list_users,
    refund_order,
    revenue_trend,
    sector_heatmap,
    system_health,
    system_status,
    toggle_user_status,
)
from server.routers.admin_ops import (
    _format_bytes,
    api_health,
    clear_all_cache,
    create_log,
    delete_cache,
    export_data,
    list_cache,
    list_logs,
    purge_expired_cache,
)
from server.routers.admin_stats import (
    AVG_MS_NOTE,
    NOTIFICATIONS_FILE,
    _AVG_MS_EXPR,
    _generate_system_notifications,
    _load_notifications,
    _query_api_calls_db,
    _save_notifications,
    admin_call_list,
    admin_call_stats,
    admin_error_ranking,
    clear_notifications,
    list_notifications,
    mark_notifications_read,
)

__all__ = ["router"]
