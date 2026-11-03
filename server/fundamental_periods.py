"""报告期工具：季度键、TTM、滞后判定、功能开关"""

from __future__ import annotations
import asyncio
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from server.cache import cache  # noqa: F401  —— 取数缓存由 Fetcher 层使用，此处保持依赖可见
from server.providers.akshare_fundamental import (
    BAIDU_INDICATORS,
    dividend_fetcher,
    financial_abstract_fetcher,
    prefixed_code,
    symbol_of,
    valuation_fetcher,
)
from server.providers.base import SourceResult

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
#  常量
# ----------------------------------------------------------------------
#: 六张卡的键与顺序（前端按此顺序渲染）
CARD_KEYS: Tuple[str, ...] = (
    "pe_ttm",
    "pb",
    "roe_ttm",
    "revenue_yoy",
    "profit_yoy",
    "dividend_yield",
)

CARD_UNITS: Dict[str, str] = {
    "pe_ttm": "倍",
    "pb": "倍",
    "roe_ttm": "%",
    "revenue_yoy": "%",
    "profit_yoy": "%",
    "dividend_yield": "%",
}

#: 实际生效的缓存时长（秒）。**唯一事实源是各 Fetcher 的 ``ttl`` 类属性**，
#: 这里只是把它导出给路由层/文档引用，避免两处硬编码漂移。
#: 注意：不读 ``config.CACHE_TTL_*`` —— 其中 ``CACHE_TTL_DIVIDEND=43200``（12h）是
#: T01 能力端点用的口径，与 P0-1 要求的「分红 24h」不同，混用会造成口径混乱。
CACHE_TTL_FUNDAMENTAL: float = financial_abstract_fetcher.ttl   # 6h
CACHE_TTL_VALUATION: float = valuation_fetcher.ttl             # 12h
CACHE_TTL_DIVIDEND: float = dividend_fetcher.ttl               # 24h

#: 估值序列最小样本数（分位数在此之下不给值，避免「分位被一周数据决定」）
MIN_PERCENTILE_SAMPLE: int = 30

#: 估值末值允许的最大滞后天数（超过则视为陈旧：日频序列应有近 10 个自然日内的点）
VALUATION_STALE_DAYS: int = 10

#: 允许「尚未发布」的最近一个季度，故实际容忍落后 = 1 + 1 = 2 个季度
PERIOD_STALE_ALLOWANCE_QUARTERS: int = 1

#: 功能开关键（写在 ``config.FEATURE_FLAGS`` 里；本模块不改 config.py）
FEATURE_FLAG_KEY: str = "finance_v2_enabled"
FEATURE_FLAG_ENV: str = "FINANCE_V2_ENABLED"
FEATURE_FLAG_DEFAULT: bool = True

#: 「占位 False 已对齐」告警只打一次，避免每个请求刷日志
_FLAG_ALIGNED: bool = False

_QUARTER_MMDD: Dict[int, str] = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
_MMDD_QUARTER: Dict[str, int] = {value: key for key, value in _QUARTER_MMDD.items()}


#: 单条 ``reason`` 的最大长度（字符）。上游异常常带完整 URL，必须截断到可读长度。
MAX_REASON_CHARS: int = 240

#: 信封级 ``reason`` 的最大长度（比卡片级更短，保证前端一行能显示）
MAX_ENVELOPE_REASON_CHARS: int = 320


def _clip(text: Optional[str], limit: int = MAX_REASON_CHARS) -> Optional[str]:
    """把原因文本压成单行并截断到 ``limit`` 字符。"""
    if text is None:
        return None
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def is_finance_v2_enabled() -> bool:
    """``finance_v2_enabled`` 开关（P0-1 新版链路的唯一总闸）。

    优先级：环境变量 ``FINANCE_V2_ENABLED`` > ``config.FEATURE_FLAGS[finance_v2_enabled]``
    > 默认 ``True``。

    ⚠️ **与 T01 的占位值对齐（必须知情）**：
    ``server/config.py`` 由另一位工程师维护，其中 ``FEATURE_FLAGS`` 的注释是
    「默认全关，逐条打开验证」，因此 ``finance_v2_enabled`` 的**占位值是 False**。
    但本开关的**所有者是 T02**，既定默认是 ``True``（新版生效；关闭时回退的旧链路
    本身也是全 null，回退只能用来紧急止损）。若照搬占位 False，P0-1 会永久不可用。
    故此处把 False 对齐为本模块默认值，并打 WARNING 留痕；**不修改 config.py 源文件**。

    紧急回滚（不改任何文件）：设环境变量 ``FINANCE_V2_ENABLED=0`` 后重启服务。
    建议 T01 后续把 config 里该键的默认改为 ``True``，届时可删除本对齐逻辑。
    """
    env = os.environ.get(FEATURE_FLAG_ENV)
    if env is not None:
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    try:
        from server import config

        flags = getattr(config, "FEATURE_FLAGS", None)
        if isinstance(flags, dict):
            if FEATURE_FLAG_KEY not in flags:
                try:
                    flags[FEATURE_FLAG_KEY] = FEATURE_FLAG_DEFAULT
                except Exception:  # noqa: BLE001 —— 只读 mapping 之类
                    pass
                return FEATURE_FLAG_DEFAULT
            value = bool(flags.get(FEATURE_FLAG_KEY))
            if not value:
                global _FLAG_ALIGNED
                try:
                    flags[FEATURE_FLAG_KEY] = FEATURE_FLAG_DEFAULT
                except Exception:  # noqa: BLE001
                    return FEATURE_FLAG_DEFAULT
                if not _FLAG_ALIGNED:
                    _FLAG_ALIGNED = True
                    logger.warning(
                        "config.FEATURE_FLAGS['%s'] 的占位值为 False（T01「默认全关」），"
                        "已按 T02 既定默认对齐为 %s（P0-1 新版链路生效）。"
                        "紧急回滚请设环境变量 %s=0 后重启。",
                        FEATURE_FLAG_KEY, FEATURE_FLAG_DEFAULT, FEATURE_FLAG_ENV,
                    )
                return FEATURE_FLAG_DEFAULT
            return True
    except Exception:  # noqa: BLE001
        pass
    return FEATURE_FLAG_DEFAULT


# ----------------------------------------------------------------------
#  报告期工具
# ----------------------------------------------------------------------
def _period_key(period: str) -> Optional[Tuple[int, int]]:
    """``"20260630"`` → ``(2026, 2)``；非法返回 ``None``。

    只接受四个合法季末（0331 / 0630 / 0930 / 1231），防止把
    ``"20260631"`` 之类脏数据当成报告期。
    """
    text = str(period or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    quarter = _MMDD_QUARTER.get(text[4:])
    if quarter is None:
        return None
    return int(text[:4]), quarter


def _period_str(year: int, quarter: int) -> str:
    """``(2026, 2)`` → ``"20260630"``。"""
    mmdd = _QUARTER_MMDD.get(quarter)
    if mmdd is None:
        return ""
    return f"{year}{mmdd}"


def _shift_quarter(year: int, quarter: int, delta: int) -> Tuple[int, int]:
    """季度序号平移（可跨年，delta 可为负）。"""
    index = year * 4 + (quarter - 1) + delta
    new_year, rem = divmod(index, 4)
    return new_year, rem + 1


def period_iso(period: str) -> Optional[str]:
    """``"20260630"`` → ``"2026-06-30"``。"""
    text = str(period or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def period_quarter_label(period: str) -> Optional[str]:
    """``"20260630"`` → ``"2026Q2"``。"""
    key = _period_key(period)
    if key is None:
        return None
    return f"{key[0]}Q{key[1]}"


def period_cn_label(period: str) -> Optional[str]:
    """``"20260630"`` → ``"2026 中报"``。"""
    key = _period_key(period)
    if key is None:
        return None
    year, quarter = key
    suffix = {1: "一季报", 2: "中报", 3: "三季报", 4: "年报"}[quarter]
    return f"{year} {suffix}"


def quarters_behind(period: str, today: Optional[datetime] = None) -> Optional[int]:
    """报告期相对当前时点，已过去多少个季度。"""
    key = _period_key(period)
    if key is None:
        return None
    now = today or datetime.now()
    now_quarter = (now.month - 1) // 3 + 1
    delta = (now.year * 4 + (now_quarter - 1)) - (key[0] * 4 + (key[1] - 1))
    return max(0, delta)


def is_period_stale(period: str, today: Optional[datetime] = None) -> Tuple[Optional[bool], Optional[int]]:
    """判断报告期是否陈旧。

    Returns:
        ``(is_stale, behind_publishable)``。``behind_publishable`` 为扣掉「最近一个
        已结束但尚未到披露截止日的季度」后的落后季度数；``> 1`` 判为陈旧。
    """
    behind = quarters_behind(period, today)
    if behind is None:
        return None, None
    behind_publishable = max(0, behind - PERIOD_STALE_ALLOWANCE_QUARTERS)
    return behind_publishable > 1, behind_publishable
