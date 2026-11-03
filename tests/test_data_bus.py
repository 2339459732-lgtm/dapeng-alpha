"""data-bus.v1 与缓存治理（cache-governance.v1）的单元测试。

覆盖：
- 总线刷新 → 事件发布 → 下游级联失效；
- 大流量下的级联闸门（min_interval_s）避免重算风暴；
- 订阅者收到事件、慢消费者不阻塞发布；
- 快照包含资源图与链路年龄；
- 缓存字节记账（写入/覆盖/删除/淘汰/超限拒写）；
- 缓存统计暴露字节与最大条目。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from server.cache import TTLCache, estimate_size
from server.data_bus import DataBus


# ---------------------------------------------------------------------------
# 数据总线
# ---------------------------------------------------------------------------

def _run(coro):
    """独立 event loop：避免其它测试（如 asyncio.run）关闭默认 loop 后互相干扰。"""
    return asyncio.run(coro)


def test_bus_refresh_publishes_and_cascades():
    bus = DataBus()
    calls = {"coverage": 0}

    async def load_coverage():
        calls["coverage"] += 1
        return {"ok": True, "trace_id": "cov-1"}

    bus.register("coverage", "覆盖", ttl=300, loader=load_coverage,
                 invalidates=("packet",), min_interval_s=0)
    bus.register("packet", "快照", ttl=55, deps=("coverage",))

    async def scenario():
        queue = bus.subscribe()
        await bus.refresh("coverage", force=True)
        events = []
        while not queue.empty():
            events.append(await queue.get())
        return events

    events = _run(scenario())
    kinds = [(e["type"], e.get("key") or e.get("keys")) for e in events]
    assert calls["coverage"] == 1
    assert ("refresh", "coverage") in kinds
    assert ("invalidate", ["packet"]) in kinds
    # refresh 事件带下发的失效目标，便于前端/日志追溯
    refresh_event = [e for e in events if e["type"] == "refresh"][0]
    assert refresh_event["invalidated"] == ["packet"]
    assert refresh_event["trace"] == "cov-1"


def test_bus_cascade_gate_prevents_storm():
    """闸门：上游高频刷新时，下游失效被节流，不做重复失效/重算。"""
    bus = DataBus()

    async def loader():
        return {"ok": True}

    bus.register("sentiment", "情绪", ttl=45, loader=loader,
                 invalidates=("experts",), min_interval_s=180)
    bus.register("experts", "专家", ttl=300)

    async def scenario():
        first = await bus.refresh("sentiment", force=True)
        second = await bus.refresh("sentiment", force=True)
        return first, second

    _run(scenario())
    events = bus.snapshot()["events"]
    invalidates = [e for e in events if e["type"] == "invalidate"]
    # 两次刷新只允许一次级联失效（第二次被 180s 闸门挡住）
    assert len(invalidates) == 1


def test_bus_snapshot_graph_and_chain():
    bus = DataBus()

    async def loader():
        return {"ok": True}

    for key, ttl, inv in (("coverage", 300, ("packet",)), ("packet", 55, ("sentiment", "scan")),
                          ("sentiment", 45, ("experts",)), ("scan", 120, ("experts",)),
                          ("experts", 300, ())):
        bus.register(key, key, ttl=ttl, loader=loader, invalidates=inv, trigger="worker" if key == "coverage" else "request")
    _run(bus.refresh("coverage", force=True))
    snap = bus.snapshot()
    assert snap["version"] == "data-bus.v1"
    graph = {g["from"]: g["to"] for g in snap["graph"]}
    assert graph["coverage"] == ["packet"]
    assert graph["packet"] == ["sentiment", "scan"]
    chain_keys = [c["key"] for c in snap["chain"]]
    assert chain_keys == ["coverage", "packet", "sentiment", "scan", "experts"]
    # 未刷新的资源 age 为空、fresh=False
    packet = [c for c in snap["chain"] if c["key"] == "packet"][0]
    assert packet["age_s"] is None and packet["fresh"] is False


def test_bus_slow_subscriber_does_not_block():
    """慢消费者：队列满时丢弃事件而不是阻塞发布方。"""
    bus = DataBus()

    async def loader():
        return {"ok": True}

    bus.register("r", "R", ttl=10, loader=loader)

    async def scenario():
        queue = bus.subscribe(maxsize=1)
        for _ in range(5):
            await bus.refresh("r", force=True)
        return queue.qsize()

    assert _run(scenario()) == 1  # 只留一帧，其余丢弃，没抛异常


def test_bus_invalidate_updates_cache():
    from server.cache import cache

    bus = DataBus()

    async def loader():
        return {"ok": True}

    # 缓存键必须显式声明：失效与新鲜度都依赖它，猜键会让级联静默打偏
    bus.register("packet", "快照", ttl=55, loader=loader, cache_key="decision_market_packet:v1")
    cache.set("decision_market_packet:v1", {"x": 1}, 60)
    assert cache.get_entry("decision_market_packet:v1") is not None
    removed = bus.invalidate(["packet"], reason="test")
    assert removed == ["packet"]
    assert cache.get_entry("decision_market_packet:v1") is None


# ---------------------------------------------------------------------------
# 缓存治理
# ---------------------------------------------------------------------------

def test_cache_byte_accounting():
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    c.set("a", {"x": "y" * 1000}, 60)
    first = c.stats()["bytes"]
    assert first == estimate_size({"x": "y" * 1000})

    c.set("b", {"x": "y" * 2000}, 60)
    assert c.stats()["bytes"] > first

    # 覆盖写：旧字节必须扣减，不能重复累加
    c.set("a", {"x": 1}, 60)
    stats = c.stats()
    assert stats["size"] == 2
    assert stats["bytes"] == estimate_size({"x": 1}) + estimate_size({"x": "y" * 2000})

    # 删除：字节回落
    c.delete("b")
    assert c.stats()["bytes"] == estimate_size({"x": 1})
    assert c.stats()["size"] == 1


def test_cache_evicts_on_byte_budget():
    c = TTLCache(max_size=100, max_bytes=1024 * 1024)
    blob = {"payload": "x" * 600_000}
    c.set("k1", blob, 60)
    c.set("k2", dict(blob), 60)
    stats = c.stats()
    assert stats["size"] == 1  # 超过 1MB 预算，淘汰最旧的 k1
    assert stats["evictions"] == 1
    assert stats["bytes"] <= stats["max_bytes"]


def test_cache_rejects_oversize_entry():
    c = TTLCache(max_size=100, max_bytes=1024 * 1024)
    before = c.stats()["bytes"]
    c.set("huge", {"payload": "x" * (2 * 1024 * 1024)}, 60)
    stats = c.stats()
    assert stats["oversize_rejected"] == 1
    assert stats["size"] == 0
    assert stats["bytes"] == before


def test_cache_purge_deducts_bytes():
    c = TTLCache(max_size=100, max_bytes=1024 * 1024)
    c.set("short", {"x": "y" * 500}, 0.01)
    c.set("long", {"x": "y" * 500}, 60)
    time.sleep(0.05)
    purged = c.purge_expired(force=True)
    assert purged == 1
    # 只剩 long 的字节
    assert c.stats()["bytes"] == estimate_size({"x": "y" * 500})


def test_cache_stats_observability():
    c = TTLCache(max_size=5, max_bytes=1024 * 1024)
    c.set("a", {"x": "y" * 100}, 60)
    stats = c.stats()
    for key in ("bytes", "max_bytes", "bytes_utilization", "largest_entries", "hit_rate",
                "evictions", "expired_purged", "oversize_rejected", "cleanup_interval"):
        assert key in stats
    assert stats["largest_entries"][0]["key"] == "a"
    assert 0 <= stats["bytes_utilization"] <= 1


def test_bus_invalidate_keeps_age_but_marks_awaiting():
    """失效后应保留上次刷新时间（年龄仍可读），同时标记 awaiting_rebuild。"""
    bus = DataBus()

    async def loader():
        return {"ok": True}

    bus.register("sentiment", "情绪", ttl=45, loader=loader)
    _run(bus.refresh("sentiment", force=True))
    before = bus.snapshot()["resources"][0]
    assert before["awaiting_rebuild"] is False
    assert before["age_s"] is not None

    bus.invalidate(["sentiment"], reason="test")
    after = bus.snapshot()
    item = [r for r in after["resources"] if r["key"] == "sentiment"][0]
    assert item["awaiting_rebuild"] is True
    assert item["age_s"] is not None  # 年龄不被清零
    chain_item = [c for c in after["chain"] if c["key"] == "sentiment"][0]
    assert chain_item["fresh"] is False


def test_bus_publish_records_accounting():
    """发布即记账：时间 / 次数 / 耗时 / trace 由 publish 统一写入。

    此前五个发布点各自手抄 last_refresh，其中三处漏了 last_ms，
    页面长期把这些资源显示成 0ms——记账集中在总线才能不漏。
    """
    bus = DataBus()

    async def noop():
        return None

    resource = bus.register("scan", "扫描", ttl=120, loader=noop)
    _run(bus.publish("scan", {"as_of": "2026-11-02 22:30", "chain": {"build_ms": 812.4}},
                     trigger="request", elapsed_ms=812.4))
    assert resource.last_refresh > 0
    assert resource.refresh_count == 1
    assert resource.last_ms == 812.4
    assert resource.last_trace == "2026-11-02 22:30"


def test_bus_publish_defaults_trace_from_payload():
    bus = DataBus()

    async def noop():
        return None

    resource = bus.register("sentiment_flow", "情绪追踪", ttl=60, loader=noop)
    _run(bus.publish("sentiment_flow", {"trace_id": "t-abc"}, trigger="request"))
    assert resource.last_trace == "t-abc"
    assert resource.last_ms == 0.0  # 没给耗时就不编造数字


def test_bus_freshness_uses_newer_of_bus_and_cache():
    """新鲜度取总线时间戳与缓存条目中较新的一个。

    进程重启后总线时间戳为空、或某资源从不经 publish 上报时，
    只看总线会把「缓存里明明还热」的资源误判成 stale。
    """
    from server.cache import cache

    bus = DataBus()

    async def noop():
        return None

    resource = bus.register("packet", "快照", ttl=55, loader=noop,
                            cache_key="test_bus_packet_key")
    cache.set("test_bus_packet_key", {"x": 1}, 60)
    try:
        # 总线时间戳假装是 10 分钟前（例如重启后未刷新），缓存是刚写的
        resource.last_refresh = time.time() - 600
        item = resource.as_dict()
        assert item["cache_age_s"] is not None and item["cache_age_s"] < 5
        assert item["age_s"] < 5            # 取较新的那个
        assert item["fresh"] is True        # 不能被总线时间戳拖成 stale
    finally:
        cache.delete("test_bus_packet_key")


def test_bus_expired_cache_is_not_fresh():
    from server.cache import cache

    bus = DataBus()

    async def noop():
        return None

    resource = bus.register("packet", "快照", ttl=55, loader=noop,
                            cache_key="test_bus_expired_key")
    cache.set("test_bus_expired_key", {"x": 1}, 0.01)
    try:
        time.sleep(0.05)
        item = resource.as_dict()
        assert item["cache_expired"] is True
        assert item["fresh"] is False
    finally:
        cache.delete("test_bus_expired_key")


def test_bus_without_cache_key_ignores_global_cache():
    """未声明 cache_key 的资源（专家团走 KV）不受进程级缓存影响。"""
    bus = DataBus()

    async def noop():
        return None

    resource = bus.register("experts", "专家", ttl=300, loader=noop)
    item = resource.as_dict()
    assert item["cache_age_s"] is None and item["cache_ttl_s"] is None
    assert item["fresh"] is False


def test_bus_last_ms_is_null_before_first_publish():
    """从未上报过的资源，耗时必须是 null 而不是 0.0（0 会被读成「0 毫秒构建」）。"""
    bus = DataBus()
    resource = bus.register("survey", "调查", ttl=600)
    item = resource.as_dict()
    assert item["last_ms"] is None
    assert item["age_s"] is None and item["fresh"] is False
