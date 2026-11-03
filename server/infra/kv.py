"""进程内键值存储端口（infra.kv.v1）。

背景：`decision_experts` / `decision_snapshot` 需要读写 brain 的 KV（专家团缓存、
情报快照），原先直接 `from server.brain import dapeng_brain`，导致
`brain ↔ experts`、`brain ↔ snapshot` 形成模块级循环依赖，且把「研判引擎」
和「持久化」耦合在一起。

做法：把 KV 抽象成一个端口（port），底层实现（`TradingStore`）在装配期注册进来。
- 依赖方向：engine/data → infra.kv ← trading（装配期 bind）
- 未装配时读写退化为默认值，不抛异常，不影响纯函数测试。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_store_getter: Optional[Callable[[], Any]] = None


def bind_store(getter: Callable[[], Any]) -> None:
    """装配期注册存储提供者（返回带 kv_get/kv_set 的对象）。重复调用以最后一次为准。"""
    global _store_getter
    _store_getter = getter


def unbind_store() -> None:
    """主要供测试使用：解除装配，恢复默认降级行为。"""
    global _store_getter
    _store_getter = None


def _store() -> Any:
    if _store_getter is None:
        return None
    try:
        return _store_getter()
    except Exception as exc:  # 存储未就绪不应影响调用方
        logger.debug("kv store unavailable: %s", exc)
        return None


async def kv_get(key: str, default: Any = None) -> Any:
    """读取键值；存储未装配/异常时返回 default。"""
    store = _store()
    if store is None:
        return default
    try:
        return await store.kv_get(key, default)
    except Exception as exc:
        logger.debug("kv_get(%s) failed: %s", key, exc)
        return default


async def kv_set(key: str, value: Any) -> None:
    """写入键值；失败只记录 debug，不向上抛。"""
    store = _store()
    if store is None:
        return
    try:
        await store.kv_set(key, value)
    except Exception as exc:
        logger.debug("kv_set(%s) failed: %s", key, exc)
