# -*- coding: utf-8 -*-
"""DAPENG Alpha — 最小 Fetcher 协议（统一数据源返回契约）。

设计边界（见 ``docs/10-architecture/openbb-dsa-integration-design.md`` §1.5）：

- 只做两件事：**统一返回契约** + **超时/异常兜底**。
- 不装任何第三方抽象框架，不做 provider registry 的插件发现机制。
- ``Fetcher`` 的子类**绝不允许把异常抛给路由层**：任何上游错误都必须转成
  ``SourceResult(data=None, degraded=True, reason=<具体原因>)``。

缺数纪律（§1.6 硬规则）：
- 取不到就 ``data=None`` + ``reason``；
- **禁止**行业均值回填、历史插值、``0`` 占位、默认值兜底。
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 单次上游取数超时上限（秒）。财务类接口串行两个请求约 11s，故并行化后仍 <= 8s/次。
DEFAULT_TIMEOUT: float = 8.0

#: 全进程上游取数并发上限。
#:
#: 实测（本机 → 新浪/百度/巨潮）：单次请求 2.7–5.5s；**4 路并发会把 8s 超时全部打穿**
#: （三个数据源同时返回「调用超时」）。2 路并发是稳定点：新浪 2.7s + 巨潮 3.8s 可并行，
#: 百度两条序列 5.5s 内部并行，整体冷启动 ≈10s，且每路都留有 ~40% 余量。
MAX_CONCURRENT_UPSTREAM: int = 2

_SEMAPHORE: Optional[asyncio.Semaphore] = None
_SEMAPHORE_LOOP: Any = None


def _upstream_semaphore() -> asyncio.Semaphore:
    """取（并惰性创建）上游并发信号量。

    按事件循环惰性重建：``asyncio.Semaphore`` 首次使用后会绑定该循环，
    同一进程内多次 ``asyncio.run()``（测试场景）需要新实例，否则会抛
    "is bound to a different event loop"。
    """
    global _SEMAPHORE, _SEMAPHORE_LOOP
    try:
        loop: Any = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _SEMAPHORE is None or _SEMAPHORE_LOOP is not loop:
        _SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_UPSTREAM)
        _SEMAPHORE_LOOP = loop
    return _SEMAPHORE

#: ``SourceResult`` 的字段集合，供 ``from_dict`` 做白名单过滤。
_SOURCE_RESULT_FIELDS: tuple[str, ...] = (
    "data",
    "source",
    "as_of",
    "degraded",
    "reason",
    "extra",
    "stale",
)


@dataclass
class SourceResult:
    """统一数据源返回契约。

    Attributes:
        data: 数据本体。``None`` 表示该源本次**未取到数据**（不得用 0/空列表代替）。
        source: 数据来源的人类可读标识，如 ``"AkShare/新浪·财务摘要"``。
        as_of: 数据本身的时点（报告期 ``YYYY-MM-DD`` 或交易日 ``YYYY-MM-DD``）。
        degraded: 是否降级（取数失败、部分字段缺失、或使用陈旧缓存）。
        reason: 降级原因（``degraded=True`` 时必须非空）。
        extra: 供聚合层使用的附加信息（如 ``fetched_at`` 时间戳、分项失败明细）。
    """

    data: Any = None
    source: Optional[str] = None
    as_of: Optional[str] = None
    degraded: bool = False
    reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    #: 是否来自「已过期但仍可用」的上一份快照（T01 新增；降级回落时置 True）
    stale: bool = False

    # ------------------------------------------------------------------
    #  便捷判定
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """是否取到了数据本体（不看 degraded）。"""
        return self.data is not None

    @property
    def value(self) -> Any:
        """``data`` 的别名（T01 兼容写法）。"""
        return self.data

    @property
    def usable(self) -> bool:
        """是否取到且未降级。"""
        return self.data is not None and not self.degraded

    @property
    def ok(self) -> bool:
        """``usable`` 的别名（与端点信封的 ``ok`` 语义对齐）。"""
        return self.usable

    # ------------------------------------------------------------------
    #  序列化（缓存需要存 dict，避免跨进程/跨版本 class 反序列化问题）
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """转为可缓存/可 JSON 化的 dict。"""
        payload = asdict(self)
        payload["extra"] = dict(self.extra or {})
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "SourceResult":
        """从缓存 dict 还原；格式异常时返回一个降级的占位结果而非抛错。"""
        if isinstance(raw, SourceResult):
            return raw
        if not isinstance(raw, dict):
            return cls(degraded=True, reason="缓存载荷格式异常，已忽略")
        kwargs: Dict[str, Any] = {}
        for key in _SOURCE_RESULT_FIELDS:
            if key in raw:
                kwargs[key] = raw[key]
        if kwargs.get("extra") is None:
            kwargs["extra"] = {}
        elif not isinstance(kwargs.get("extra"), dict):
            kwargs["extra"] = {}
        return cls(**kwargs)

    # ------------------------------------------------------------------
    #  工厂：降级结果（唯一允许的「取不到」表达方式）
    # ------------------------------------------------------------------
    @classmethod
    def unavailable(cls, source: Optional[str], reason: str,
                    **extra: Any) -> "SourceResult":
        """构造一个如实降级的结果。``reason`` 必须写明具体原因。"""
        return cls(
            data=None,
            source=source,
            as_of=None,
            degraded=True,
            reason=str(reason) if reason else "原因未知",
            extra=dict(extra),
        )


class Fetcher(ABC):
    """数据源取数器协议。

    子类需声明：
        name: 注册名（全局唯一）
        source_label: 人类可读来源（写进 ``SourceResult.source``）
        ttl: 成功结果的缓存时长（秒）
        timeout: 单次上游调用超时（秒，默认 8）

    子类只需实现 :meth:`fetch`。上游取数请走 :meth:`call_upstream`，
    它负责超时、异常兜底，保证**永不抛异常到路由层**。
    """

    name: str = ""
    source_label: str = ""
    ttl: float = 21600.0
    timeout: float = DEFAULT_TIMEOUT
    #: 允许回落的陈旧上限（秒）；T01 能力端点用它与 age 比较判定 stale
    stale_max: float = 86400.0

    # ---- T01：健康计数（由 call_upstream 自动维护，子类无需关心）----
    _calls: int = 0
    _failures: int = 0
    _consecutive_failures: int = 0
    _last_error: Optional[str] = None
    _last_ok_at: Optional[float] = None

    @abstractmethod
    async def fetch(self, **params: Any) -> SourceResult:
        """取数。实现方不得抛出异常，失败必须返回 :meth:`SourceResult.unavailable`。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    #  上游调用包装（超时 + 异常收敛）
    # ------------------------------------------------------------------
    async def call_upstream(self, fn: Callable[..., Any], *args: Any,
                            **kwargs: Any) -> SourceResult:
        """在工作线程里执行同步取数函数，带超时与异常兜底。

        Args:
            fn: 同步取数函数（内部完成网络请求 + 解析）。
            *args: 透传位置参数。
            **kwargs: 透传关键字参数。

        Returns:
            ``SourceResult``。成功时 ``data`` 为 ``fn`` 的返回值；
            超时/异常/空结果时 ``data=None`` 且 ``degraded=True``。
        """
        label = self.source_label or self.name or "未知数据源"
        try:
            # 并发闸门只约束「在飞的上游请求数」，排队等待时间不计入 8s 超时。
            async with _upstream_semaphore():
                value = await asyncio.wait_for(
                    asyncio.to_thread(fn, *args, **kwargs), timeout=self.timeout
                )
        except asyncio.TimeoutError:
            reason = f"{label} 调用超时（{self.timeout:g}s 未返回）"
            logger.warning("%s: %s", self.name, reason)
            self._note_failure(reason)
            return SourceResult.unavailable(label, reason, failure="timeout")
        except ImportError as exc:  # 依赖缺失（akshare/pandas 未安装）
            reason = f"{label} 依赖缺失：{exc}"
            logger.error("%s: %s", self.name, reason)
            self._note_failure(reason)
            return SourceResult.unavailable(label, reason, failure="import_error")
        except ValueError as exc:
            # Fetcher 内部主动抛的 ValueError 一律携带面向用户的中文原因
            # （如「百度估值对该标的无覆盖」「巨潮返回空表，通常表示无分红记录」），
            # 此时不再叠加异常类名，直接透出，理由更可读。
            detail = str(exc).strip() or "上游返回数据不符合预期"
            reason = f"{label}：{detail}"
            logger.warning("%s: %s", self.name, reason)
            self._note_failure(reason)
            return SourceResult.unavailable(
                label, reason, failure="data_unavailable", error_type="ValueError",
            )
        except Exception as exc:  # noqa: BLE001 —— 上游任何异常都降级，绝不外抛
            # 异常文本常带完整 URL + query（如 ProxyError），截断到 120 字符保持可读；
            # 卡片级 reason 另有长度上限，避免信封被一条超长 URL 撑爆。
            detail = " ".join(str(exc).split())[:120] or exc.__class__.__name__
            reason = f"{label} 取数失败：{exc.__class__.__name__}: {detail}"
            logger.warning("%s: %s", self.name, reason)
            self._note_failure(reason)
            return SourceResult.unavailable(
                label, reason, failure="exception",
                error_type=exc.__class__.__name__,
            )

        if value is None:
            reason = f"{label} 返回空结果"
            logger.warning("%s: %s", self.name, reason)
            self._note_failure(reason)
            return SourceResult.unavailable(label, reason, failure="empty")
        self._note_ok()
        return SourceResult(data=value, source=label)

    # ------------------------------------------------------------------
    #  T01：健康计数与快照（熔断器留给后续任务在同一 health() 上扩展）
    # ------------------------------------------------------------------
    def _note_ok(self) -> None:
        self._calls += 1
        self._failures = 0
        self._consecutive_failures = 0
        self._last_error = None
        self._last_ok_at = time.time()

    def _note_failure(self, reason: str) -> None:
        self._calls += 1
        self._failures += 1
        self._consecutive_failures += 1
        self._last_error = str(reason)[:300]

    def record_ok(self) -> None:
        """记录一次成功（子类若绕过 call_upstream 自行取数时手动调用）。"""
        self._note_ok()

    def record_failure(self, reason: str) -> None:
        """记录一次失败（同上）。"""
        self._note_failure(reason)

    def health(self) -> Dict[str, Any]:
        """数据源健康快照（只读，不触发取数）。"""
        if self._calls == 0:
            state = "unknown"
        elif self._consecutive_failures >= 3:
            state = "unavailable"
        elif self._consecutive_failures > 0:
            state = "degraded"
        else:
            state = "ok"
        return {
            "name": self.name,
            "source": self.source_label or None,
            "ttl": self.ttl,
            "stale_max": self.stale_max,
            "state": state,
            "calls": self._calls,
            "failures": self._failures,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "last_ok_at": (
                datetime.fromtimestamp(self._last_ok_at).astimezone().isoformat(timespec="seconds")
                if self._last_ok_at else None
            ),
        }


# ----------------------------------------------------------------------
#  进程内注册表（极简：dict + 2 个函数）
# ----------------------------------------------------------------------
_REGISTRY: Dict[str, Fetcher] = {}


def register(fetcher: Fetcher) -> Fetcher:
    """注册一个 Fetcher 实例（同名覆盖），并原样返回便于装饰器式使用。"""
    if not isinstance(fetcher, Fetcher):
        raise TypeError(f"register() 需要 Fetcher 实例，收到 {type(fetcher)!r}")
    if not fetcher.name:
        raise ValueError("Fetcher 必须有非空 name")
    _REGISTRY[fetcher.name] = fetcher
    return fetcher


def get_fetcher(name: str) -> Optional[Fetcher]:
    """按注册名取 Fetcher；不存在返回 ``None``。"""
    return _REGISTRY.get(str(name or ""))


def registered_names() -> List[str]:
    """已注册的 Fetcher 名（排序后，便于诊断端点输出）。"""
    return sorted(_REGISTRY.keys())


def list_fetchers() -> List[Dict[str, Any]]:
    """列出已注册数据源及其健康快照（只读，供 /api/meta 与诊断端点使用）。"""
    return [f.health() for f in _REGISTRY.values()]


def clear_registry() -> None:
    """清空注册表（测试用）。"""
    _REGISTRY.clear()
