"""data-bus.v1 — 全局数据连接与事件总线。

解决的问题：
1. 各模块各自缓存、各自刷新，缺少统一的数据链路视图与依赖关系；
2. 上游刷新后，下游（情绪 / 专家 / 扫描）不会立即失效，最长要等自己的 TTL
   过期，导致同一屏的面板显示不同时点的数据；
3. SSE 只能按固定周期读缓存，刷新发生时无法即时推送，信号传递多一个轮询周期；
4. 缺少统一的更新触发机制与可观测状态（谁在刷、多久刷、刷完通知谁、失败几次）。

设计原则：
- **不替代 cache**：总线只做「注册 + 触发 + 失效 + 通知 + 观测」，
  数据仍存于既有 TTLCache（单一事实源，避免双缓存不一致）；
- **依赖单向**：coverage → packet → sentiment / scan → experts，禁止反向依赖；
- **级联有闸门**：下游失效受 ``min_interval_s`` 约束，避免上游高频刷新把
  下游（尤其专家团）打成重算风暴；
- **事件可追溯**：保留最近 200 条事件（key / 触发方式 / 耗时 / 失效目标）。

触发机制（四类，全部收敛到 ``refresh()``）：
- 时间触发：market_coverage 后台 worker 按交易时段节拍（120s / 1800s）刷新；
- 事件触发：上游刷新成功后 ``publish()`` → 级联失效下游 + 通知 SSE 订阅者；
- 请求触发：HTTP 读取时按各资源 TTL 自然过期后重建（torn 读由 cache 去重）；
- 手动触发：``?refresh=1`` 强制刷新单个资源。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 保留最近多少条事件用于诊断
EVENT_KEEP = 200


@dataclass
class Resource:
    key: str
    label: str
    ttl: float
    loader: Optional[Callable[[], Awaitable[Any]]] = None
    deps: Tuple[str, ...] = ()
    # 刷新成功后需要失效的下游资源 key
    invalidates: Tuple[str, ...] = ()
    # 下游失效闸门：距上次失效不足该秒数则跳过（避免重算风暴）
    min_interval_s: float = 0.0
    trigger: str = "request"  # worker / request / manual
    # 该资源在 TTLCache 里的键；为空表示「不经缓存」（如专家团走 KV）。
    # 显式声明而不是靠 key 猜：本地测试用的总线不该被进程级缓存影响。
    cache_key: Optional[str] = None
    last_refresh: float = 0.0
    last_invalidate: float = 0.0
    invalidated_at: float = 0.0
    last_ms: float = 0.0
    refresh_count: int = 0
    error_count: int = 0
    last_error: str = ""
    last_trace: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        cache_facts = self.cache_facts()
        ages: List[float] = []
        if self.last_refresh:
            ages.append(time.time() - self.last_refresh)
        if cache_facts.get("cache_age_s") is not None:
            ages.append(float(cache_facts["cache_age_s"]))
        age = round(min(ages), 1) if ages else None
        awaiting = bool(self.invalidated_at and self.invalidated_at >= self.last_refresh)
        fresh = bool(
            age is not None and self.ttl and age <= self.ttl
            and not awaiting and not cache_facts.get("cache_expired")
        )
        payload = {
            "key": self.key, "label": self.label, "ttl": self.ttl,
            "deps": list(self.deps), "invalidates": list(self.invalidates),
            "trigger": self.trigger, "last_refresh": self.last_refresh or None,
            "age_s": age,
            # 从未上报过耗时就是 null，不要用 0.0 冒充「0 毫秒构建」
            "last_ms": self.last_ms if self.last_refresh else None,
            "awaiting_rebuild": awaiting, "fresh": fresh,
            "refresh_count": self.refresh_count, "error_count": self.error_count,
            "last_error": self.last_error or None, "last_trace": self.last_trace,
        }
        payload.update(cache_facts)
        return payload

    def cache_facts(self) -> Dict[str, Any]:
        """缓存侧真值：条目年龄 / 条目 TTL / 是否已过期。

        总线时间戳只反映「谁通过 publish 上报过」，而多数读取走的是 ``cache`` 的
        TTL 路径。两者可能不同步（例如进程重启后总线时间戳为空、或某资源从不发布），
        所以新鲜度取**两者中较新的一个**，而不是只信总线。
        """
        try:
            from server.cache import cache

            if not self.cache_key:
                return {"cache_age_s": None, "cache_ttl_s": None, "cache_expired": None}
            entry = cache.get_entry(self.cache_key)
        except Exception:  # 缓存不可用不应影响观测接口
            entry = None
        if not entry:
            return {"cache_age_s": None, "cache_ttl_s": None, "cache_expired": None}
        age = entry.get("age")
        return {
            "cache_age_s": round(float(age), 1) if age is not None else None,
            "cache_ttl_s": entry.get("ttl"),
            "cache_expired": bool(entry.get("expired")),
        }


class DataBus:
    """进程内数据总线：资源注册 + 刷新触发 + 级联失效 + 事件订阅。"""

    def __init__(self) -> None:
        self._resources: Dict[str, Resource] = {}
        self._subscribers: List["asyncio.Queue[Dict[str, Any]]"] = []
        self._events: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register(
        self,
        key: str,
        label: str,
        ttl: float,
        *,
        loader: Optional[Callable[[], Awaitable[Any]]] = None,
        deps: Tuple[str, ...] = (),
        invalidates: Tuple[str, ...] = (),
        min_interval_s: float = 0.0,
        trigger: str = "request",
        cache_key: Optional[str] = None,
    ) -> Resource:
        resource = Resource(
            key=key, label=label, ttl=ttl, loader=loader, deps=deps,
            invalidates=invalidates, min_interval_s=min_interval_s, trigger=trigger,
            cache_key=cache_key,
        )
        self._resources[key] = resource
        return resource

    def get_resource(self, key: str) -> Optional[Resource]:
        return self._resources.get(key)

    # ------------------------------------------------------------------
    # 刷新与失效
    # ------------------------------------------------------------------
    async def refresh(self, key: str, force: bool = False) -> Any:
        """触发一次资源刷新（single-flight：同一 key 并发只执行一次 loader）。"""
        resource = self._resources.get(key)
        if resource is None or resource.loader is None:
            return None
        async with self._lock:
            started = time.perf_counter()
            try:
                payload = await resource.loader()
                elapsed = (time.perf_counter() - started) * 1000
                # 时间/耗时/次数的记账统一在 publish 里做，避免每个发布者手抄一遍
                await self.publish(
                    key, payload, trigger="force" if force else "bus", elapsed_ms=elapsed
                )
                return payload
            except Exception as exc:
                resource.error_count += 1
                resource.last_error = str(exc)
                logger.warning("data bus refresh %s failed: %s", key, exc)
                return None

    def invalidate(self, keys: List[str], *, reason: str, min_interval_s: float = 0.0) -> List[str]:
        """失效下游资源（删除其缓存条目），返回实际失效的 key。

        节流语义由调用方（publish）按「发布者自己的失效节拍」控制，
        本方法只做失效动作，不再二次判断，避免首次级联也被挡掉。
        """
        from server.cache import cache

        done: List[str] = []
        for key in keys:
            resource = self._resources.get(key)
            if resource is None:
                cache.delete(key)
                done.append(key)
                continue
            cache.delete(self._cache_key_of(resource))
            resource.invalidated_at = time.time()  # 标记待重建，但保留上次刷新时间用于算年龄
            done.append(key)
        if done:
            self._record({
                "type": "invalidate", "keys": done, "reason": reason, "t": time.time(),
            })
        return done

    # 缓存键的唯一来源是 Resource.cache_key（注册时声明），此处不再维护第二张映射表：
    # 曾经两张表并存，sentiment_flow / survey 的键只在一处登记，失效时静默打偏。
    def _cache_key_of(self, resource: Resource) -> str:
        return resource.cache_key or resource.key

    # ------------------------------------------------------------------
    # 发布 / 订阅
    # ------------------------------------------------------------------
    async def publish(self, key: str, payload: Any, *, trigger: str = "bus",
                      elapsed_ms: Optional[float] = None, trace: Optional[str] = None) -> None:
        """上游刷新成功：记账（时间 / 耗时 / 次数 / trace）+ 级联失效 + 通知订阅者。

        记账放这里而不是让每个调用方自己写，是因为此前五处发布点各抄一遍，
        漏掉了 ``last_ms``（页面长期显示 0ms），也漏掉了失败后的 ``last_error`` 复位。
        """
        resource = self._resources.get(key)
        if resource is not None:
            resource.last_refresh = time.time()
            resource.refresh_count += 1
            resource.last_error = ""
            if elapsed_ms is not None:
                resource.last_ms = round(float(elapsed_ms), 1)
            if trace is None and isinstance(payload, dict):
                trace = payload.get("trace_id") or payload.get("as_of")
            if trace:
                resource.last_trace = str(trace)[:80]
        event = {
            "type": "refresh", "key": key, "trigger": trigger,
            "t": time.time(), "ms": resource.last_ms if resource else None,
            "trace": resource.last_trace if resource else None,
        }
        self._record(event)
        # 级联失效（节流看发布者自己的失效节拍：首次必发，之后按 min_interval_s 收敛）
        if resource and resource.invalidates:
            now = time.time()
            gated = (
                resource.min_interval_s
                and resource.last_invalidate
                and (now - resource.last_invalidate) < resource.min_interval_s
            )
            if not gated:
                resource.last_invalidate = now
                invalidated = self.invalidate(list(resource.invalidates), reason=f"{key}.refreshed")
                if invalidated:
                    event["invalidated"] = invalidated
                    self._notify({"type": "invalidate", "keys": invalidated, "cause": key, "t": time.time()})
        self._notify(event)

    def _notify(self, event: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者：丢弃事件而不是阻塞发布方
                pass

    def _record(self, event: Dict[str, Any]) -> None:
        self._events.append(event)
        if len(self._events) > EVENT_KEEP:
            del self._events[: len(self._events) - EVENT_KEEP]

    # ------------------------------------------------------------------
    # 订阅（SSE 使用）
    # ------------------------------------------------------------------
    def subscribe(self, maxsize: int = 32) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------
    # 观测
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        resources = [r.as_dict() for r in self._resources.values()]
        return {
            "ok": True,
            "version": "data-bus.v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "resources": resources,
            "graph": [
                {"from": r.key, "to": list(r.invalidates)} for r in self._resources.values() if r.invalidates
            ],
            "subscribers": self.subscriber_count,
            "events": list(reversed(self._events[-40:])),
            "chain": self.chain_ages(resources),
        }

    @staticmethod
    def chain_ages(resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按依赖顺序输出链路年龄，供前端展示「哪一段最旧」。"""
        order = ["coverage", "packet", "sentiment", "sentiment_flow", "scan", "survey", "experts"]
        by_key = {r["key"]: r for r in resources}
        chain = []
        for key in order:
            item = by_key.get(key)
            if not item:
                continue
            chain.append({
                "key": key, "label": item["label"],
                "age_s": item["age_s"], "ttl": item["ttl"],
                "last_ms": item["last_ms"], "trigger": item["trigger"],
                "awaiting_rebuild": item.get("awaiting_rebuild", False),
                # 新鲜度由 as_dict 统一判定（综合总线时间戳与缓存条目），此处不再重算，
                # 否则两处口径会漂移：曾经出现「总线 age 92s 判 stale，但缓存其实是热的」。
                "fresh": bool(item.get("fresh")),
                "cache_age_s": item.get("cache_age_s"),
            })
        return chain


# 全局单例
bus = DataBus()


def register_default_resources() -> None:
    """注册决策闭环的默认资源图。

    依赖链（v2，2026-11-02 并入情绪追踪与市场调查）：
        coverage → packet → {sentiment, scan} → sentiment_flow → survey → experts → memo

    级联闸门：
    - packet 刷新 → sentiment/scan 失效，闸门 40s（快照 TTL 55s，避免重复失效）；
    - sentiment 刷新 → sentiment_flow 失效，闸门 30s（追踪只做派生，重算便宜）；
    - sentiment_flow / scan 刷新 → survey 与 experts 失效，闸门 180s
      （专家团含互评与账本落盘，重算最重，必须挡住重算风暴）。
    """
    bus.register(
        "coverage", "全市场覆盖", ttl=300, trigger="worker",
        invalidates=("packet",), min_interval_s=60,
        # coverage 走模块内 dict 缓存，不在 TTLCache 里 —— 不声明 cache_key，
        # 否则会给观测接口一个查不到东西的键（新鲜度反而失真）。
    )
    bus.register(
        "packet", "共享快照", ttl=55, trigger="request",
        deps=("coverage",), invalidates=("sentiment", "scan"), min_interval_s=40,
        cache_key="decision_market_packet:v1",
    )
    bus.register(
        "sentiment", "统一情绪", ttl=45, trigger="request",
        deps=("packet",), invalidates=("experts",), min_interval_s=180,
        cache_key="decision_sentiment:v1",
    )
    bus.register(
        "scan", "全市场扫描", ttl=120, trigger="request",
        deps=("coverage",), invalidates=("experts",), min_interval_s=180,
        cache_key="market_scan:v1",
    )
    bus.register(
        "sentiment_flow", "情绪追踪", ttl=60, trigger="request",
        deps=("sentiment",), invalidates=("survey",), min_interval_s=30,
        cache_key="sentiment_flow:v1",
    )
    bus.register(
        "survey", "动态市场调查", ttl=600, trigger="request",
        deps=("packet", "sentiment_flow", "scan"),
    )
    bus.register(
        "experts", "六专家研判", ttl=300, trigger="request",
        deps=("packet", "sentiment", "sentiment_flow", "scan"),
        # 专家团走 KV（含互评与账本），不进 TTLCache，故不声明 cache_key
    )
