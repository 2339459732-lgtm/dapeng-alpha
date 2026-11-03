"""BigA Pro 策略选股系统 - 增强型 TTL 内存缓存

优化点：
- LRU 淘汰策略 + 最大容量限制，防止内存泄漏
- 请求去重：同一 key 的并发请求只执行一次，后续共享结果
- 过期回收：**后台周期任务**（不再依赖写入触发）+ 写入前主动腾位 + 读取时惰性删除

v2（2026-10-22）修复的设计缺陷：
1. **过期条目无人回收**：原实现 ``_maybe_cleanup()`` 只在 ``set()`` 内被调用，
   服务一旦停止写入（夜间、周末、低峰），过期条目会一直占着容量，
   直到下一次写入才被顺带清掉。实测运行中实例 68 条里有 29 条已过期（43%）。
   现改为 ``lifespan`` 内启动后台任务周期回收，与写入解耦。
2. **腾位顺序错误**：原实现容量满时直接 ``popitem`` 淘汰 LRU，
   哪怕缓存里躺着大量已过期条目 —— 结果是「淘汰有效数据、留下垃圾」。
   现改为「先回收过期 → 仍然满才淘汰 LRU」。
3. **并发失败惊群**：原实现在 fetch 失败后，每个等待者都会各自再
   ``_do_fetch`` 一次，N 个并发请求 = N 次上游调用，与「去重」的文档承诺相反。
   现改为等待者直接复用 leader 的结果（失败即 None），不再各自重试。
4. **私有属性外泄**：``quotes.py`` / ``admin.py`` 直接读写 ``cache._store``。
   现提供 ``get_entry()`` / ``iter_entries()`` / ``purge_expired()`` / ``stats()`` 公共接口。
5. ``clear()`` 未重置清理游标；``max_size <= 0`` 时 ``set()`` 会 KeyError。
"""

import asyncio
import json
import logging
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 后台回收周期（秒）。取 30s：短于最短的业务 TTL（行情 60s），
# 保证过期条目在下一次业务读之前就被清掉，不会长时间占用容量。
DEFAULT_CLEANUP_INTERVAL = 30.0
# 默认总字节上限（64MB）：本系统缓存以行情/快照/派生分析为主，
# 单条最大约 200KB（全市场 packet），64MB 足够覆盖全部热点 key，
# 同时给「某个异常大对象反复写入」设硬天花板，防止内存无界增长。
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


@dataclass
class CacheEntry:
    """缓存条目"""
    data: Any
    timestamp: float
    ttl: float
    nbytes: int = 0

    @property
    def is_expired(self) -> bool:
        """判断缓存是否已过期"""
        return time.time() - self.timestamp > self.ttl


def estimate_size(data: Any) -> int:
    """估算对象占用字节数。

    优先用 JSON 序列化长度（对 dict/list 结构最接近真实占用），
    失败（含不可序列化对象/循环引用）才退回 sys.getsizeof。
    只在写入时计算一次，随条目缓存，不在读取路径上重复计算。
    """
    try:
        return len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        try:
            return int(sys.getsizeof(data))
        except Exception:
            return 0


class TTLCache:
    """基于内存的 TTL + LRU 缓存

    特性：
    - LRU 淘汰：容量满时**先回收过期条目**，仍有缺口才淘汰最久未访问的条目
    - 过期回收：读取时惰性删除 + 后台周期任务（与写入解耦）+ 写入前主动腾位
    - 请求去重：同一 key 的并发请求共享结果（防缓存击穿），失败时不再各自重试
    - 可观测：命中/未命中/淘汰/回收计数，供 ``/api/admin/cache`` 与诊断使用
    """

    def __init__(
        self,
        max_size: int = 800,
        cleanup_interval: float = DEFAULT_CLEANUP_INTERVAL,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        # 容量至少为 1：max_size<=0 会让 set() 的淘汰循环在空表上 popitem 抛 KeyError
        # 可重入锁：缓存会被事件循环与线程池（asyncio.to_thread 的同步抓取）共同读写，
        # 不加锁时 OrderedDict 的 move_to_end/pop 与字节计数可能交错，出现「条目在、字节数错」。
        self._lock = threading.RLock()
        self._max_size = max(1, int(max_size))
        self._max_bytes = max(1024 * 1024, int(max_bytes))  # 下限 1MB，防止配置成 0 后疯狂淘汰
        self._bytes = 0
        self._store: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._pending: Dict[str, asyncio.Event] = {}  # 请求去重
        self._cleanup_interval = max(1.0, float(cleanup_interval))
        self._last_cleanup = time.time()
        self._cleanup_task: Optional["asyncio.Task[None]"] = None
        self._stats: Dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "expired_purged": 0,
            "oversize_rejected": 0,
        }

    # ------------------------------------------------------------------
    #  基础读写
    # ------------------------------------------------------------------
    def get(self, key: str) -> Optional[Any]:
        """获取缓存数据，过期则删除并返回 None"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None
            if entry.is_expired:
                # 必须走 _drop：直接 pop 会把 nbytes 永久留在账上，
                # 最终 bytes 虚高、触发本不该有的 LRU 淘汰（实测账面可涨到真实值的数倍）。
                self._drop(key)
                self._stats["misses"] += 1
                return None
            # LRU：移到末尾（最近访问）
            self._store.move_to_end(key)
            self._stats["hits"] += 1
            return entry.data

    def set(self, key: str, data: Any, ttl: float) -> None:
        """设置缓存数据（双上限：条目数 + 总字节数，均回收优先于淘汰）"""
        # 先估算体积再动旧值：单条超预算时应「不写入」，而不是先把上一份好数据删掉。
        # （原实现先 pop 旧值再判断，一个异常大对象就会把热点 key 清空。）
        nbytes = estimate_size(data)
        if nbytes > self._max_bytes:
            with self._lock:
                self._stats["oversize_rejected"] = self._stats.get("oversize_rejected", 0) + 1
            logger.warning(
                "Cache set(%s) rejected: entry %d bytes exceeds max_bytes %d（保留旧值）",
                key, nbytes, self._max_bytes,
            )
            return

        with self._lock:
            self._set_locked(key, data, ttl, nbytes)

    def _set_locked(self, key: str, data: Any, ttl: float, nbytes: int) -> None:
        # 如果已存在，先删除旧条目（更新位置并扣减字节）
        old = self._store.pop(key, None)
        if old is not None:
            self._bytes = max(0, self._bytes - old.nbytes)

        if len(self._store) >= self._max_size:
            # 关键顺序：先回收过期条目，避免为「垃圾」腾位而淘汰仍然有效的数据
            self.purge_expired(force=True)

        while len(self._store) >= self._max_size:
            if not self._evict_lru():
                break

        self._store[key] = CacheEntry(data=data, timestamp=time.time(), ttl=ttl, nbytes=nbytes)
        self._bytes += nbytes
        self._stats["sets"] += 1

        # 字节上限：先回收过期，再按 LRU 淘汰，直到回到预算内
        if self._bytes > self._max_bytes:
            self.purge_expired(force=True)
            while self._bytes > self._max_bytes and self._evict_lru():
                pass

        # 节流版回收：作为后台任务的兜底（后台任务被禁用时仍能收敛）
        self.purge_expired()

    def _evict_lru(self) -> bool:
        """淘汰最久未访问的一条，返回是否成功。"""
        with self._lock:
            try:
                _key, entry = self._store.popitem(last=False)
            except KeyError:
                return False
            self._bytes = max(0, self._bytes - entry.nbytes)
            self._stats["evictions"] += 1
            return True

    def _drop(self, key: str) -> Optional[CacheEntry]:
        """删除一条并同步扣减字节（所有删除路径的唯一出口）。"""
        entry = self._store.pop(key, None)
        if entry is not None:
            self._bytes = max(0, self._bytes - entry.nbytes)
        return entry

    def delete(self, key: str) -> None:
        """删除指定缓存"""
        with self._lock:
            self._drop(key)

    def contains(self, key: str) -> bool:
        """判断 key 是否存在（含已过期条目，不触碰 LRU 顺序）"""
        with self._lock:
            return key in self._store

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._store.clear()
            self._pending.clear()
            self._bytes = 0
        # 重置清理游标，避免清空后立刻又被节流挡住
        self._last_cleanup = time.time()

    # ------------------------------------------------------------------
    #  过期回收
    # ------------------------------------------------------------------
    def purge_expired(self, force: bool = False) -> int:
        """回收所有已过期条目，返回回收条数。

        ``force=False`` 时受 ``cleanup_interval`` 节流（供 ``set()`` 兜底调用）；
        ``force=True`` 立即执行（供后台任务与写入前腾位使用）。
        """
        with self._lock:
            now = time.time()
            if not force and (now - self._last_cleanup) < self._cleanup_interval:
                return 0
            self._last_cleanup = now

            expired = [k for k, e in self._store.items() if e.is_expired]
            for k in expired:
                self._drop(k)
            if expired:
                self._stats["expired_purged"] += len(expired)
                logger.debug("Cache purge: removed %d expired entries (left=%d)",
                             len(expired), len(self._store))
            return len(expired)

    async def background_cleanup(self) -> None:
        """后台周期回收任务 —— 由 ``lifespan`` 启动。

        与写入解耦：即使服务长时间无写入，过期条目也会按周期被回收。
        """
        logger.info(
            "Cache cleanup task started (interval=%.0fs, max_size=%d)",
            self._cleanup_interval, self._max_size,
        )
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                try:
                    purged = self.purge_expired(force=True)
                    if purged:
                        logger.info(
                            "Cache periodic cleanup: purged %d expired entries (size=%d/%d)",
                            purged, len(self._store), self._max_size,
                        )
                except Exception as e:  # 单次失败不能让后台任务退出
                    logger.error("Cache periodic cleanup failed: %s", e)
        except asyncio.CancelledError:
            logger.info("Cache cleanup task stopped")
            raise

    def start_background_cleanup(self) -> Optional["asyncio.Task[None]"]:
        """启动后台回收任务（幂等：已在运行则复用）"""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return self._cleanup_task
        self._cleanup_task = asyncio.create_task(self.background_cleanup())
        return self._cleanup_task

    async def stop_background_cleanup(self) -> None:
        """停止后台回收任务"""
        task = self._cleanup_task
        self._cleanup_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    #  T01：缓存元信息（供 /api/meta/capability 与前端角标使用）
    #  说明：本组方法**只读**，不触发上游、不删除过期条目（过期条目是降级回落的依据）
    # ------------------------------------------------------------------
    def get_entry(self, key: str) -> Optional[Dict[str, Any]]:
        """返回缓存条目元信息（含时间戳/年龄/TTL）。

        与 ``get()`` 的差别：``get()`` 只返回数据且遇过期即删除；本方法返回
        ``{value, stored_at, age, ttl, expired}``，**过期但仍在内存的条目照常返回**，
        供调用方区分「3 秒前」与「3 小时前」，并支持降级回落到上一份有效数据。
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            age = max(0.0, time.time() - entry.timestamp)
            self._store.move_to_end(key)      # LRU：视为一次访问
        return {
            "key": key,
            "value": entry.data,
            "stored_at": entry.timestamp,
            "age": round(age, 3),
            "ttl": entry.ttl,
            "expired": age > entry.ttl,
        }

    def iter_entries(self) -> Iterator[Dict[str, Any]]:
        """只读遍历全部条目（供管理端展示，替代外部访问 ``_store``）。

        不修改 LRU 顺序、不删除任何条目；对 dict 做快照后再迭代，
        避免调用方在循环中 delete 导致 ``RuntimeError: OrderedDict mutated``。
        """
        with self._lock:
            snapshot = list(self._store.items())
        now = time.time()
        for key, entry in snapshot:
            age = max(0.0, now - entry.timestamp)
            yield {
                "key": key,
                "data": entry.data,
                "stored_at": entry.timestamp,
                "age": round(age, 1),
                "ttl": entry.ttl,
                "remaining_ttl": round(max(0.0, entry.ttl - age), 1),
                "expired": age > entry.ttl,
                "bytes": entry.nbytes,
            }

    def find_newest(self, prefixes: Iterable[str]) -> Optional[Dict[str, Any]]:
        """按 key 前缀找**最新**的条目（同域多 code 时取最近写入的一份）。

        只读：不删除、不触发上游。无匹配返回 None。
        """
        wanted = tuple(p for p in prefixes if p)
        if not wanted:
            return None
        best: Optional[Tuple[str, CacheEntry]] = None
        with self._lock:
            items = list(self._store.items())
        for key, entry in items:
            if not key.startswith(wanted):
                continue
            if best is None or entry.timestamp > best[1].timestamp:
                best = (key, entry)
        if best is None:
            return None
        key, entry = best
        age = max(0.0, time.time() - entry.timestamp)
        return {
            "key": key,
            "value": entry.data,
            "stored_at": entry.timestamp,
            "age": round(age, 3),
            "ttl": entry.ttl,
            "expired": age > entry.ttl,
        }

    def keys(self) -> List[str]:
        """当前缓存中的所有 key（只读快照，供诊断/能力端点列举）。"""
        with self._lock:
            return list(self._store.keys())

    def stats(self) -> Dict[str, Any]:
        """缓存运行状态快照（无副作用，不做序列化估算，可高频调用）"""
        now = time.time()
        with self._lock:
            snapshot = list(self._store.items())
            current_bytes = self._bytes
        total = len(snapshot)
        expired = sum(1 for _k, e in snapshot if e.is_expired)
        hits = self._stats["hits"]
        misses = self._stats["misses"]
        lookups = hits + misses
        largest = sorted(
            ((k, e.nbytes) for k, e in snapshot),
            key=lambda kv: kv[1], reverse=True,
        )[:5]
        return {
            "size": total,
            "live": total - expired,
            "expired_now": expired,
            "max_size": self._max_size,
            "bytes": current_bytes,
            "max_bytes": self._max_bytes,
            "bytes_utilization": round(current_bytes / self._max_bytes, 4) if self._max_bytes else None,
            "largest_entries": [{"key": k, "bytes": b} for k, b in largest],
            # 账实核对：把条目实际占用重新加一遍，与累计计数比对。
            # 两者不等说明有删除路径漏扣字节（审计脚本据此判定缓存是否健康）。
            "recomputed_bytes": sum(e.nbytes for _k, e in snapshot),
            "oversize_rejected": self._stats.get("oversize_rejected", 0),
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / lookups, 4) if lookups else None,
            "sets": self._stats["sets"],
            "evictions": self._stats["evictions"],
            "expired_purged": self._stats["expired_purged"],
            "pending_fetches": len(self._pending),
            "cleanup_interval": self._cleanup_interval,
            "seconds_since_cleanup": round(max(0.0, now - self._last_cleanup), 1),
            "cleanup_task_running": bool(self._cleanup_task and not self._cleanup_task.done()),
        }

    # ------------------------------------------------------------------
    #  取数（带去重）
    # ------------------------------------------------------------------
    async def get_or_fetch(
        self,
        key: str,
        fetch_fn,
        ttl: float,
    ) -> Any:
        """获取缓存数据，不存在则调用 fetch_fn 获取并缓存。

        支持请求去重：同一 key 的并发请求只执行一次 fetch_fn，
        后续请求等待第一次的结果。

        注意：leader 取数失败时，等待者**直接复用失败结果（None）**，
        不再各自重试 —— 否则 N 个并发请求会放大成 N 次上游调用，
        与「去重」的语义相反（原实现即存在此惊群问题）。
        """
        # 1. 检查缓存
        cached = self.get(key)
        if cached is not None:
            return cached

        # 2. 请求去重：已有同 key 请求在飞 → 等它，然后复用其结果
        event = self._pending.get(key)
        if event is not None:
            await event.wait()
            # 事件完成后再次检查缓存；仍为空说明 leader 失败，不再重试
            return self.get(key)

        # 3. 首次请求：执行 fetch
        return await self._do_fetch(key, fetch_fn, ttl)

    async def _do_fetch(self, key: str, fetch_fn, ttl: float) -> Any:
        """执行实际的 fetch 操作"""
        event = asyncio.Event()
        self._pending[key] = event
        try:
            result = await fetch_fn()
            if result is not None:
                self.set(key, result, ttl)
            return result
        finally:
            event.set()
            self._pending.pop(key, None)

    # ------------------------------------------------------------------
    #  T01：带元信息的取数（供新端点 / 角标使用）
    # ------------------------------------------------------------------
    @staticmethod
    def _meta(
        stored_at: Optional[float],
        ttl: float,
        *,
        stale: bool = False,
        degraded: bool = False,
        reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构造缓存元信息（字段与 §3.1 信封一致：as_of / age / degraded / reason）。"""
        age: Optional[float] = None
        as_of: Optional[str] = None
        if stored_at:
            age = round(max(0.0, time.time() - stored_at), 3)
            as_of = datetime.fromtimestamp(stored_at).astimezone().isoformat(timespec="seconds")
        return {
            "as_of": as_of,
            "age": age,
            "ttl": ttl,
            "stale": bool(stale),
            "degraded": bool(degraded),
            "reason": reason,
            "source": source,
            "stored_at": stored_at,
        }

    async def get_or_fetch_ex(
        self,
        key: str,
        fetch_fn,
        ttl: float,
    ) -> Tuple[Any, Dict[str, Any]]:
        """取数并**同时返回元信息**：``(value, meta)``。

        三态语义（T01 验收）：
        1. **命中**：未过期 → 直接返回缓存值，meta.age 为其真实年龄，不触发上游；
        2. **未命中**：无任何缓存 → 执行 fetch_fn；成功则写入并返回，失败返回
           ``(None, meta{degraded:true, reason})``，**绝不用 0 冒充**；
        3. **过期但有旧值**：先取数；取数失败时**回落到上一份有效数据**，
           返回 ``(old_value, meta{stale:true, degraded:true, age:真实年龄, reason})``，
           而不是直接报错或返回空。

        并发去重沿用 ``_pending``（同一 key 的并发请求只打一次上游）。
        """
        entry = self.get_entry(key)
        if entry is not None and not entry["expired"]:
            return entry["value"], self._meta(entry["stored_at"], entry["ttl"])

        # 过期条目已被 get_entry 保留；这里先留一份用于失败回落
        stale_value = entry["value"] if entry is not None else None
        stale_meta: Optional[Dict[str, Any]] = None
        if entry is not None:
            stale_meta = self._meta(
                entry["stored_at"], entry["ttl"],
                stale=True, degraded=True,
                reason="上游取数失败，沿用上一份缓存（已过期）",
            )

        try:
            value = await self.get_or_fetch(key, fetch_fn, ttl)
        except Exception as exc:
            # 上游抛异常时也必须走「回落旧值」这条路：
            # 契约写的是「取数失败 → 沿用上一份缓存」，抛异常同样算失败。
            logger.warning("cache fetch(%s) raised: %s: %s", key, type(exc).__name__, exc)
            value = None
            if stale_value is not None and stale_meta is not None:
                stale_meta["reason"] = f"上游异常（{type(exc).__name__}），沿用上一份缓存"
                return stale_value, stale_meta
            return None, self._meta(None, ttl, degraded=True,
                                    reason=f"上游异常（{type(exc).__name__}）且无缓存可回落")
        if value is not None:
            fresh = self.get_entry(key)
            if fresh is not None:
                return value, self._meta(fresh["stored_at"], fresh["ttl"])
            return value, self._meta(time.time(), ttl)

        # 取数失败：过期但有旧值 → 回落旧值并标 stale（不返回空、不返回 0）
        if stale_value is not None and stale_meta is not None:
            return stale_value, stale_meta
        return None, self._meta(None, ttl, degraded=True, reason="无可用数据（缓存为空且上游取数失败）")

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        with self._lock:
            return len(self._store)


# 全局缓存实例
cache = TTLCache(max_size=800)
