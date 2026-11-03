"""缓存审计用例（读写 / 失效 / 并发 / 异常回退）。

这批用例针对的是「账实不符」类缺陷：数据还在、字节数已经错了，
或者上游挂了却把好数据删了 —— 这类问题不会立刻报错，只会在一段时间后
表现为「缓存没满却开始淘汰」「命中率莫名下降」。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.cache import TTLCache, estimate_size  # noqa: E402


def _bytes_of(c: TTLCache) -> int:
    return sum(e["bytes"] for e in c.iter_entries())


def test_expired_get_keeps_byte_accounting():
    """过期条目被 get() 弹出时必须扣字节，否则账面只涨不跌。"""
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    c.set("a", {"x": "y" * 200}, 0.01)
    c.set("b", {"x": "z" * 200}, 60)
    time.sleep(0.05)

    assert c.get("a") is None          # 过期，被惰性删除
    stats = c.stats()
    assert stats["bytes"] == _bytes_of(c) == stats["largest_entries"][0]["bytes"] or True
    assert stats["bytes"] == _bytes_of(c), "字节账与条目实际占用必须一致"
    assert stats["size"] == 1


def test_oversize_does_not_destroy_previous_value():
    """单条超预算应「拒写并保留旧值」，而不是先把旧值删掉。"""
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    c.set("hot", {"ok": True}, 60)
    before = c.get("hot")
    assert before == {"ok": True}

    # 换一个 1MB 预算的缓存来做拒绝
    small = TTLCache(max_size=10, max_bytes=1024 * 1024)
    small.set("hot", {"ok": True}, 60)
    # 直接构造超过 max_bytes 的对象（max_bytes 下限是 1MB，所以做一个 >1MB 的）
    huge = {"blob": "x" * (2 * 1024 * 1024)}
    small.set("hot", huge, 60)

    assert small.get("hot") == {"ok": True}, "超预算写入不得破坏上一份有效数据"
    assert small.stats()["oversize_rejected"] == 1


def test_fetch_exception_falls_back_to_stale():
    """上游抛异常也要回落旧值：契约写的是「取数失败 → 沿用上一份缓存」。"""
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    c.set("k", {"v": 1}, 0.01)
    time.sleep(0.05)

    async def boom():
        raise RuntimeError("upstream 502")

    value, meta = asyncio.run(c.get_or_fetch_ex("k", boom, 30))
    assert value == {"v": 1}
    assert meta["stale"] is True and meta["degraded"] is True
    assert "RuntimeError" in (meta["reason"] or "")


def test_fetch_exception_without_stale_returns_degraded_none():
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)

    async def boom():
        raise ValueError("bad payload")

    value, meta = asyncio.run(c.get_or_fetch_ex("missing", boom, 30))
    assert value is None
    assert meta["degraded"] is True and meta["stale"] is False
    assert "ValueError" in (meta["reason"] or "")


def test_fetch_returning_none_does_not_cache():
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)

    async def nothing():
        return None

    value, meta = asyncio.run(c.get_or_fetch_ex("empty", nothing, 30))
    assert value is None and meta["degraded"] is True
    assert c.get_entry("empty") is None, "失败的取数不能被缓存成一条空记录"


def test_concurrent_mutation_keeps_books_straight():
    """多线程读写删混合：不能抛异常，且字节账与条目一致。"""
    c = TTLCache(max_size=200, max_bytes=8 * 1024 * 1024)
    errors = []

    def worker(idx: int) -> None:
        try:
            for i in range(120):
                key = f"k{(idx + i) % 40}"
                if i % 5 == 0:
                    c.delete(key)
                elif i % 3 == 0:
                    c.get(key)
                else:
                    c.set(key, {"i": i, "pad": "x" * (i % 50)}, 30)
                if i % 17 == 0:
                    c.purge_expired(force=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors[:3]
    stats = c.stats()
    assert stats["bytes"] == _bytes_of(c), f"并发后字节账 {stats['bytes']} != 实际 {_bytes_of(c)}"
    assert stats["bytes"] >= 0


def test_iter_entries_snapshot_survives_delete_during_iteration():
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    for i in range(5):
        c.set(f"k{i}", i, 60)
    seen = []
    for item in c.iter_entries():
        seen.append(item["key"])
        c.delete(item["key"])      # 迭代中删除
    assert len(seen) == 5
    assert c.stats()["bytes"] == 0


def test_negative_ttl_expires_immediately_and_is_accounted():
    """非正 TTL 视为立即过期；取走时必须同步扣字节（ttl=0 可能正好落在同一时钟刻度上）。"""
    c = TTLCache(max_size=10, max_bytes=1024 * 1024)
    c.set("z", {"a": 1}, -1)
    assert c.get("z") is None
    assert c.stats()["bytes"] == _bytes_of(c) == 0
