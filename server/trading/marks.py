"""演练（paper_drill）标记上下文 —— 让演练造数订单自动带上 is_drill 标记

问题：`paper_drill` 为了演示闭环，会真下模拟单（买入→强制平仓），这些"造数订单"
与真实信号产生的订单混在同一张 orders 表；若参与策略指标统计/迭代决策，就是用
假交易评估真策略。

方案：用 contextvar 表达"当前处于演练会话"。paper_drill 在跑一轮闭环的整个期间
把上下文置为 True；OrderResult → store.insert_order 的写入路径在缺省 is_drill 时
自动读取该上下文打标，**不依赖调用方记得传参**。normal 信号路径上下文为 False。

contextvar 是 task-local 的：一次 HTTP 请求/一个 asyncio 任务设置后不会泄漏到其他
请求；paper_drill 仍以 try/finally 复位，双保险。
"""

from __future__ import annotations

import contextvars
from typing import Any

_DRILL_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "trading_drill_active", default=False
)


def drill_active() -> bool:
    """当前任务是否处于演练会话"""
    return bool(_DRILL_ACTIVE.get())


def begin_drill() -> contextvars.Token:
    """标记进入演练会话（返回 token，供 end_drill 复位）"""
    return _DRILL_ACTIVE.set(True)


def end_drill(token: Any) -> None:
    """复位演练会话"""
    try:
        _DRILL_ACTIVE.reset(token)
    except (ValueError, LookupError):
        _DRILL_ACTIVE.set(False)
