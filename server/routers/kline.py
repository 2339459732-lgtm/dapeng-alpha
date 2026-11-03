"""K 线路由聚合（K 线 / 分时 / 共振 / 指标，实现见 kline_* 子路由）"""

from fastapi import APIRouter

from server.routers.kline_common import router as _r0
from server.routers.kline_series import router as _r1
from server.routers.kline_analysis import router as _r2

router = APIRouter()
router.include_router(_r0)
router.include_router(_r1)
router.include_router(_r2)

from server.routers.kline_common import (
    CACHE_TTL_INTRADAY,
    INTRADAY_PERIODS,
    KLINE_LIMIT_MAX,
    KLINE_LIMIT_MIN,
    _build_kline_response,
    _empty_kline_response,
    _ttl_for_period,
    logger,
)
from server.routers.kline_series import (
    SINA_M1_URL,
    TENCENT_MINUTE_URL,
    TIMESHARE_LIMIT,
    TIMESHARE_TTL_CLOSED,
    TIMESHARE_TTL_OPEN,
    _TIMESHARE_HEADERS,
    _bars_to_points,
    _fmt_hhmm,
    _http_json,
    _http_json_text,
    _load_timeshare,
    _prev_close_from_hub,
    _timeshare_date,
    _timeshare_payload,
    _timeshare_sina_m1,
    _timeshare_tencent_m1,
    _timeshare_tencent_minute,
    get_kline,
    get_kline_quote,
    get_timeshare,
)
from server.routers.kline_analysis import (
    CACHE_TTL_INDICATORS,
    INDICATOR_LIMIT_DEFAULT,
    INDICATOR_LIMIT_MAX,
    INDICATOR_LIMIT_MIN,
    INDICATOR_SOURCE,
    PERIOD_LABELS,
    RESONANCE_PERIODS,
    _assess_period,
    _assess_resonance,
    _build_period_insufficient,
    _expand_names,
    _indicator_payload,
    _klines_as_of,
    _load_indicator_klines,
    _split_names,
    get_indicators_catalog,
    get_kline_indicators,
    get_multi_period_resonance,
)

__all__ = ["router"]
