"""指标计算：单季/ TTM 取值、分位、卡片装配"""

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

from server.fundamental_periods import (
    CARD_UNITS,
    MIN_PERCENTILE_SAMPLE,
    _clip,
    _period_key,
    _period_str,
    _shift_quarter,
)

# ----------------------------------------------------------------------
#  TTM 聚合（滚动 4 季）
# ----------------------------------------------------------------------
def single_quarter_value(series: Dict[str, Optional[float]], period: str) -> Optional[float]:
    """从累计值序列还原单季值；缺上一季累计值时返回 ``None``（不插值）。"""
    key = _period_key(period)
    if key is None:
        return None
    cumulative = series.get(period)
    if cumulative is None:
        return None
    year, quarter = key
    if quarter == 1:
        return float(cumulative)
    prev_period = _period_str(*_shift_quarter(year, quarter, -1))
    prev_cumulative = series.get(prev_period)
    if prev_cumulative is None:
        return None
    return float(cumulative) - float(prev_cumulative)


def ttm_value(series: Dict[str, Optional[float]], period: str) -> Optional[float]:
    """滚动 4 季合计；任一季缺失返回 ``None``。"""
    key = _period_key(period)
    if key is None:
        return None
    total = 0.0
    for offset in range(4):
        target = _period_str(*_shift_quarter(key[0], key[1], -offset))
        quarter_value = single_quarter_value(series, target)
        if quarter_value is None:
            return None
        total += quarter_value
    return total


def _ratio_pct(numerator: Optional[float],
               denominator: Optional[float]) -> Optional[float]:
    """百分比比率；分母为 0/None 时返回 ``None``（不返回 0 或 inf）。"""
    if numerator is None or denominator is None:
        return None
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return None
    if denominator == 0:
        return None
    return numerator / denominator * 100.0


# ----------------------------------------------------------------------
#  分位计算
# ----------------------------------------------------------------------
def _pct_rank(series: Sequence[Optional[float]],
              value: Optional[float]) -> Optional[float]:
    """当前值在历史序列中的百分位（0–100）。

    口径：``小于等于当前值的样本数 / 总样本数 × 100``。
    样本数 < :data:`MIN_PERCENTILE_SAMPLE` 或当前值为空 → ``None``（不给分位）。
    """
    if value is None:
        return None
    values: List[float] = []
    for item in series:
        if item is None:
            continue
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    if len(values) < MIN_PERCENTILE_SAMPLE:
        return None
    below_or_equal = sum(1 for item in values if item <= value)
    return round(below_or_equal / len(values) * 100.0, 2)


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """线性插值分位数（入参必须已升序）。"""
    if not sorted_values:
        return None
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    position = (len(sorted_values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _percentile_block(values: Sequence[float],
                      current: Optional[float] = None) -> Dict[str, Any]:
    """构造分位块（p10/p50/p90/min/max/current/current_pct/sample）。

    Args:
        values: 历史序列（**顺序无所谓**，函数内部自行排序）。
        current: 当前值（必须由调用方按**时间顺序**给出，即序列末值）。
            缺省时退化为取序列最大值——仅用于没有时间序信息的场景；
            有 ``[{date, value}]`` 序列时必须显式传入末值，否则「当前值」会
            错成 5 年最高值，分位恒定 100（这是必须避免的方向性错误）。
    """
    clean = sorted(float(item) for item in values if item is not None and math.isfinite(float(item)))
    if not clean:
        return {
            "p10": None, "p50": None, "p90": None, "min": None, "max": None,
            "current": None, "current_pct": None, "sample": 0,
        }
    if current is None or not math.isfinite(float(current)):
        current = clean[-1]
    actual = float(current)
    return {
        "p10": round(_quantile(clean, 0.10) or 0.0, 4),
        "p50": round(_quantile(clean, 0.50) or 0.0, 4),
        "p90": round(_quantile(clean, 0.90) or 0.0, 4),
        "min": round(clean[0], 4),
        "max": round(clean[-1], 4),
        "current": round(actual, 4),
        "current_pct": _pct_rank(clean, actual),
        "sample": len(clean),
    }


# ----------------------------------------------------------------------
#  卡片构造
# ----------------------------------------------------------------------
def _card(value: Optional[float],
          card_key: str,
          source: Optional[str] = None,
          as_of: Optional[str] = None,
          period_label: Optional[str] = None,
          degraded: bool = False,
          reason: Optional[str] = None,
          latest_period_is_stale: Optional[bool] = None,
          stale_basis: Optional[str] = None,
          formula: Optional[str] = None,
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """装配单张指标卡。``value`` 必须是有限数或 ``None``（禁止 0 占位）。"""
    if value is not None and not math.isfinite(value):
        value = None
    card: Dict[str, Any] = {
        "value": round(value, 4) if value is not None else None,
        "unit": CARD_UNITS.get(card_key),
        "source": source,
        "as_of": as_of,
        "period_label": period_label,
        "percentile_5y": None,
        "latest_period_is_stale": latest_period_is_stale,
        "stale_basis": stale_basis,
        "degraded": bool(degraded) or value is None,
        "reason": _clip(reason) if (value is None or reason) else None,
        "formula": formula,
    }
    if extra:
        card["extra"] = extra
    return card


def _unavailable_card(card_key: str, reason: str,
                      source: Optional[str] = None,
                      stale_basis: Optional[str] = None) -> Dict[str, Any]:
    """构造「取不到」卡：``value=None`` + 具体 ``reason``（绝不为 0）。"""
    return _card(
        None, card_key,
        source=source,
        as_of=None,
        period_label=None,
        degraded=True,
        reason=reason or "数据源未返回该指标",
        latest_period_is_stale=None,
        stale_basis=stale_basis,
    )
