"""时间工具（core.timeutil.v1）。

统一时间戳格式，避免各模块各写一份 `datetime.now().isoformat()` 导致：
- 有的带时区、有的不带，跨模块比较时间时出现 8 小时偏差；
- 有的带微秒、有的截到秒，字符串排序不稳定。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def now_iso(timespec: str = "seconds", tz: bool = True) -> str:
    """当前时间 ISO 字符串。

    Args:
        timespec: "seconds" / "milliseconds" / "microseconds"
        tz: True 时带本地时区偏移（项目默认），False 为 naïve 时间。
    """
    current = datetime.now()
    if tz:
        current = current.astimezone()
    return current.isoformat(timespec=timespec)  # type: ignore[arg-type]


def iso_of(moment: Optional[datetime], timespec: str = "seconds", tz: bool = True) -> Optional[str]:
    """把指定时间格式化为同口径 ISO 字符串；None 原样返回。"""
    if moment is None:
        return None
    if tz and moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec=timespec)  # type: ignore[arg-type]
