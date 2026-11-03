"""估值序列：历史分位、陈旧判定"""

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

from server.fundamental_metrics import _percentile_block
from server.fundamental_periods import MIN_PERCENTILE_SAMPLE, VALUATION_STALE_DAYS

def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    """安全四舍五入：``None``/非有限值返回 ``None``。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _is_daily_stale(last_date: Optional[str]) -> Optional[bool]:
    """日频序列末值是否陈旧（距今 > :data:`VALUATION_STALE_DAYS` 天）。"""
    if not last_date:
        return None
    try:
        stamp = datetime.strptime(str(last_date)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return (datetime.now() - stamp).days > VALUATION_STALE_DAYS


# ----------------------------------------------------------------------
#  主入口 2：/api/finance/valuation
# ----------------------------------------------------------------------
async def get_valuation_history(code: str,
                                years: int = 5,
                                indicators: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """PE_TTM / PB 日频估值序列 + 窗口分位（0–100）。

    Args:
        code: 标的代码。
        years: 窗口年数（1–10）。
        indicators: 指标键序列，默认 ``("pe_ttm", "pb")``。

    Returns:
        dict: 统一信封；``data.series`` 为日频序列，``data.percentile`` 为分位块。
        一个指标都没有时 ``ok:false`` + ``reason``（绝不返回空数组冒充成功）。

    Notes:
        ``pe_ttm`` 序列同时是 P0-1 之外 ``Alpha_Value_EP`` 因子的历史输入（解决缺口 D2）。
    """
    prefixed = prefixed_code(code)
    if not prefixed:
        return {
            "ok": False,
            "data": None,
            "message": "股票代码非法",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"无法从 {code!r} 解析合法 A 股代码（需 6 位数字，可带 sh/sz/bj 前缀）",
        }

    try:
        window_years = int(years)
    except (TypeError, ValueError):
        window_years = 5
    window_years = max(1, min(10, window_years))

    wanted: List[str] = []
    for item in (indicators if indicators is not None else ("pe_ttm", "pb")):
        key = str(item or "").strip().lower()
        if key and key in BAIDU_INDICATORS and key not in wanted:
            wanted.append(key)
    if not wanted:
        return {
            "ok": False,
            "data": None,
            "message": "参数非法",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"indicators 无有效项，支持：{', '.join(sorted(BAIDU_INDICATORS))}",
        }

    result = await valuation_fetcher.fetch(
        code=prefixed, years=window_years, indicators=tuple(wanted)
    )

    if not result.available:
        return {
            "ok": False,
            "data": None,
            "message": "估值序列不可用",
            "source": result.source,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": result.reason or "百度估值未返回任何序列",
        }

    payload = result.data or {}
    series_map: Dict[str, List[Dict[str, Any]]] = payload.get("series") or {}
    last_map: Dict[str, Dict[str, Any]] = payload.get("last") or {}
    failed: Dict[str, str] = payload.get("failed") or {}

    series_out: Dict[str, List[Dict[str, Any]]] = {}
    percentile_out: Dict[str, Dict[str, Any]] = {}
    for key in wanted:
        rows = series_map.get(key) or []
        if not rows:
            continue
        series_out[key] = rows
        values = [row.get("value") for row in rows]
        # 「当前值」必须取时间序列末值（rows 已按日期升序），不能取排序后最大值
        latest_value = rows[-1].get("value")
        block = _percentile_block(values, current=latest_value)
        block.update({
            "indicator": key,
            "label": BAIDU_INDICATORS.get(key, key),
            "window_start": rows[0].get("date"),
            "window_end": rows[-1].get("date"),
            "requested_years": window_years,
        })
        if block["sample"] < MIN_PERCENTILE_SAMPLE:
            block["current_pct"] = None
            block["reason"] = f"样本 {block['sample']} < {MIN_PERCENTILE_SAMPLE}，不给出分位"
        percentile_out[key] = block

    if not series_out:
        detail = "；".join(f"{key}: {msg}" for key, msg in failed.items()) or "无序列返回"
        return {
            "ok": False,
            "data": None,
            "message": "估值序列不可用",
            "source": result.source,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": f"百度估值无可用序列（{detail}）",
        }

    missing = [key for key in wanted if key not in series_out]
    stale_flags = {
        key: _is_daily_stale((last_map.get(key) or {}).get("date"))
        for key in series_out
    }
    fetched_at = result.extra.get("fetched_at")
    age = round(time.time() - float(fetched_at), 1) if fetched_at else None

    data = {
        "code": prefixed,
        "symbol": symbol_of(prefixed),
        "years": window_years,
        "period_label": payload.get("period_label"),
        "indicators": list(series_out),
        "series": series_out,
        "percentile": percentile_out,
        "counts": {key: len(rows) for key, rows in series_out.items()},
        "last": {key: last_map.get(key) for key in series_out},
        "latest_is_stale": stale_flags,
        "missing": missing,
        "reason_by_indicator": failed,
        "scale_note": "PE_TTM 单位=倍；PB 单位=倍；分位为窗口内百分位（0–100，越高越贵）",
    }

    reason = result.reason
    if missing and not reason:
        reason = "部分指标不可用：" + "、".join(missing)

    return {
        "ok": True,
        "data": data,
        "message": "ok" if not missing else "部分指标不可用",
        "source": result.source,
        "as_of": max((item.get("date") for item in last_map.values() if item), default=None),
        "age": age,
        "degraded": bool(missing) or bool(result.degraded),
        "reason": reason,
    }
