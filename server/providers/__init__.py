# -*- coding: utf-8 -*-
"""DAPENG Alpha — 数据源 Provider 层（最小实现）。

本包只提供**一个薄接口**：统一的数据源返回契约 + Fetcher 协议 + 进程内注册表。
不引入依赖注入容器、不做插件发现、不建 Data 模型继承树。

当前成员：
- :mod:`server.providers.base` —— ``SourceResult`` / ``Fetcher`` / ``register`` / ``get_fetcher``
- :mod:`server.providers.akshare_fundamental` —— AkShare 财务/估值/分红三个 Fetcher
  （**显式禁用 ``*_em`` 东财系接口**，本机 403）

对外只导出 base 的四个符号，避免调用方依赖具体实现模块。
"""

from server.providers.base import (  # noqa: F401
    Fetcher,
    SourceResult,
    clear_registry,
    get_fetcher,
    list_fetchers,
    register,
    registered_names,
)

__all__ = [
    "Fetcher",
    "SourceResult",
    "get_fetcher",
    "register",
    "registered_names",
    # T01 追加：健康快照 / 测试清理
    "list_fetchers",
    "clear_registry",
]
