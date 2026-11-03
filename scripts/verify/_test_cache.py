# -*- coding: utf-8 -*-
"""TTLCache v2 行为验证（纯 stdlib，不依赖运行中的服务）"""
import asyncio
import sys
import time
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "cache_mod", pathlib.Path(__file__).parent / "server" / "cache.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TTLCache = m.TTLCache

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


print("== 1. 基本读写与命中统计 ==")
c = TTLCache(max_size=10, cleanup_interval=0.2)
c.set("a", 1, 60)
check("get 命中", c.get("a") == 1)
check("miss 返回 None", c.get("nope") is None)
st = c.stats()
check("hits=1 misses=1", st["hits"] == 1 and st["misses"] == 1, st)
check("hit_rate=0.5", st["hit_rate"] == 0.5, st["hit_rate"])

print("== 2. 过期后 get 返回 None 并删除 ==")
c2 = TTLCache(max_size=10, cleanup_interval=0.2)
c2.set("x", "v", 0.05)
time.sleep(0.12)
check("过期后 get=None", c2.get("x") is None)
check("过期条目已被惰性删除", not c2.contains("x"))

print("== 3. purge_expired 回收过期条目 ==")
c3 = TTLCache(max_size=10, cleanup_interval=999)
for i in range(5):
    c3.set(f"old{i}", i, 0.05)
c3.set("keep", 1, 999)
time.sleep(0.12)
check("过期仍在内存（未被访问）", c3.stats()["expired_now"] == 5, c3.stats())
n = c3.purge_expired(force=True)
check("purge 返回 5", n == 5, n)
check("purge 后 size=1", c3.size == 1, c3.size)
check("有效条目保留", c3.get("keep") == 1)

print("== 4. 容量满时「先回收过期、再淘汰 LRU」 ==")
# 构造：容量 3，先塞 2 条过期 + 2 条有效。
# 写第 2 条有效数据时容量刚好满(3)，此时 2 条已过期 ——
# 正确行为是回收过期腾位，而不是淘汰仍然有效的 live1。
c4 = TTLCache(max_size=3, cleanup_interval=999)
c4.set("exp1", 3, 0.05)
c4.set("exp2", 4, 0.05)
time.sleep(0.12)                       # exp1/exp2 已过期，但仍占内存
c4.set("live1", 1, 999)                # 2 < 3，直接写入（节流版 purge 不生效）
c4.set("live2", 2, 999)                # 3 >= 3 → 触发「先回收过期」
check("过期条目被回收而非淘汰有效数据", c4.stats()["evictions"] == 0, c4.stats())
check("live1 存活（未被误淘汰）", c4.get("live1") == 1, c4.keys())
check("live2 存活", c4.get("live2") == 2, c4.keys())
check("过期条目已清空", c4.stats()["expired_now"] == 0, c4.stats())
check("回收计数=2", c4.stats()["expired_purged"] == 2, c4.stats()["expired_purged"])

print("== 5. 后台任务在「零写入」下自动回收 ==")


async def t5():
    # 注意：cleanup_interval 有 1.0s 下界保护（防热循环），故此处按 1.0s 测试
    c = TTLCache(max_size=50, cleanup_interval=1.0)
    for i in range(4):
        c.set(f"k{i}", i, 0.05)
    task = c.start_background_cleanup()
    await asyncio.sleep(0.1)
    before = c.size
    running = c.stats()["cleanup_task_running"]
    await asyncio.sleep(1.4)          # 期间完全不写入，只靠后台 tick
    after = c.size
    await c.stop_background_cleanup()
    return before, after, c.stats(), running


b, a, st, running = asyncio.run(t5())
check("后台任务已启动", running is True, running)
check("零写入下后台任务仍回收（4 → 0）", b == 4 and a == 0, f"before={b} after={a}")
check("expired_purged 累计 4", st["expired_purged"] == 4, st["expired_purged"])
check("停止后任务不再运行", st["cleanup_task_running"] is False, st)

print("== 6. get_or_fetch 并发去重（成功） ==")


async def t6():
    c = TTLCache(max_size=10)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "value"

    res = await asyncio.gather(*[c.get_or_fetch("k", fetch, 60) for _ in range(8)])
    return calls["n"], res


n, res = asyncio.run(t6())
check("8 并发只打 1 次上游", n == 1, n)
check("8 个结果全部一致", all(r == "value" for r in res), res)

print("== 7. get_or_fetch 失败时等待者不再各自重试（惊群修复） ==")


async def t7():
    c = TTLCache(max_size=10)
    calls = {"n": 0}

    async def fetch_fail():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return None

    res = await asyncio.gather(*[c.get_or_fetch("bad", fetch_fail, 60) for _ in range(8)])
    return calls["n"], res


n, res = asyncio.run(t7())
check("失败场景只打 1 次上游（修复前为 8）", n == 1, n)
check("全部返回 None", all(r is None for r in res), res)

print("== 8. clear / max_size<=0 / iter_entries ==")
c8 = TTLCache(max_size=0, cleanup_interval=1)
c8.set("a", 1, 60)
check("max_size<=0 不崩溃", True)
check("max_size 归一化为 1", c8.stats()["max_size"] == 1, c8.stats())

c8b = TTLCache(max_size=5)
c8b.set("p", 1, 60)
c8b.set("q", 2, 0.05)
rows = list(c8b.iter_entries())
check("iter_entries 返回全部", len(rows) == 2, len(rows))
check("iter_entries 字段完整", {"key", "age", "ttl", "remaining_ttl", "expired"} <= set(rows[0].keys()), rows[0].keys())
c8b.clear()
check("clear 后 size=0", c8b.size == 0)
check("clear 后 pending 清空", c8b.stats()["pending_fetches"] == 0)

print("== 9. get_entry 保留过期条目（降级回落依据） ==")
c9 = TTLCache(max_size=5)
c9.set("e", "old", 0.05)
time.sleep(0.12)
ent = c9.get_entry("e")
check("get_entry 仍返回过期条目", ent is not None and ent["expired"] is True, ent)
check("get() 此时才删除", c9.get("e") is None)

print()
print(f"结果：PASS {ok} / FAIL {fail}")
sys.exit(1 if fail else 0)
