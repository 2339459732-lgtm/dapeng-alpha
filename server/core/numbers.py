"""数值解析（core.numbers.v1）——全系统唯一的「字符串/None → 数值」入口。

设计原则（来自多次线上事故）：
1. **缺失就是缺失**：None、空串、"-"、"--"、NaN、±inf 一律视为无数据，
   由调用方决定回退值，禁止在解析层偷换成 0。
2. **bool 不是数字**：`True` 作为 1 参与计算在行情/统计里几乎总是 bug，统一拒绝。
3. **两种返回形态**：`to_float` 返回 Optional（需要区分缺失），
   `safe_float` 返回 float（有明确业务默认值）。不要再用第三种写法。
"""
from __future__ import annotations

import math
from typing import Any, Optional

# 上游 CSV / JSON / 表格里常见的「空值字符串」
_MISSING_STRINGS = {"", "-", "--", "—", "n/a", "na", "null", "none", "nan"}


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """解析为 float；不可解析、NaN、±inf、bool 均返回 default（默认 None）。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in _MISSING_STRINGS:
            return default
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def safe_float(value: Any, default: float = 0.0) -> float:
    """解析为 float；失败返回业务默认值（默认 0.0）。适合「缺失按 0 计」的统计场景。"""
    parsed = to_float(value, None)
    return default if parsed is None else parsed


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """解析为 int（截断小数）；不可解析返回 default。"""
    parsed = to_float(value, None)
    if parsed is None:
        return default
    try:
        return int(parsed)
    except (OverflowError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """把数值夹到 [low, high]，用于评分/百分比边界。"""
    return max(low, min(high, value))
