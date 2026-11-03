#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决策台延迟预算实测（perf-budget.v1）。

为什么不是「跑一次看快不快」
--------------------------
延迟是架构约束，不是感觉。这里把关键读路径的 p50/p95 与**预算**逐项对比，
让「低延迟」变成可以写进验收标准、可以在改动后复跑的数字。

测法
----
* 每个端点先打一次（预热，把冷启动/首次构造成本单独记为 cold），
  再顺序打 N 次，取 p50 / p95 / max；
* 顺序而非并发：目标是**单请求延迟预算**，不是吞吐上限（吞吐另由并发脚本覆盖）；
* 默认只报告，`--gate` 时任何一项 p95 超预算即退出码 1（供 CI 用）。

用法
----
    python scripts/verify/perf_budget.py                 # 报告
    python scripts/verify/perf_budget.py --n 9           # 更多样本
    python scripts/verify/perf_budget.py --gate          # 超预算即失败
    python scripts/verify/perf_budget.py --json out.json # 落盘原始样本
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from _auth import auth_headers

BASE = "http://127.0.0.1:8900"

#: (路径, 标签, p95 预算毫秒, 说明)
#: 预算的定法：事实层允许一次全市场聚合；派生层必须是毫秒级缓存读；
#: 需要跑六专家的路径给到秒级，但不得超过一轮轮询间隔（60s）的 1/10。
ENDPOINTS: List[Tuple[str, str, float, str]] = [
    ("/api/health", "健康检查", 120, "进程存活探针，只做内存读"),
    ("/api/brain/config", "配置身份", 250, "配置校验 + 摘要 + 历史尾"),
    ("/api/decision/bus", "总线链路", 200, "资源图 + 事件环 + 新鲜度"),
    ("/api/brain/alignment?light=true", "三层对证（轻量）", 400, "事实↔判断，不跑六专家"),
    ("/api/decision/sentiment", "统一情绪", 600, "sentiment.v1 六维"),
    ("/api/decision/sentiment/flow", "情绪追踪", 500, "派生：变化量/斜率/拐点"),
    ("/api/market-dashboard", "事实层聚合", 1800, "全市场广度 + 板块 + 三锚点"),
    ("/api/brain/capabilities", "能力图", 500, "15 项能力可用性 + 任务表"),
    ("/api/decision/experts", "六专家", 2500, "规则研判 + 互评 + 账本"),
    # 龙虎榜复盘默认只读落盘 JSON（不打上游），所以预算是毫秒级；
    # 若哪天改成同步上游，这一项会立刻超标——这正是把它放进预算的目的。
    ("/api/decision/lhb-review", "龙虎榜复盘", 300, "只读落盘报告，未生成时秒回 ok:false"),
    ("/api/brain/alignment?light=false", "三层对证（完整）", 3200, "含决策层比对"),
]


#: 登录态 Cookie（未配置 ACCESS_KEY 时为空字典）
_AUTH: Dict[str, str] = {}


def _get(path: str, timeout: float = 30.0) -> Tuple[float, int, int]:
    """返回 (毫秒, 状态码, 响应字节数)。"""
    started = time.perf_counter()
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json", **_AUTH})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        code = exc.code
    except Exception:
        return (-1.0, 0, 0)
    return ((time.perf_counter() - started) * 1000, code, len(body))


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return -1.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def measure(n: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path, label, budget, note in ENDPOINTS:
        cold, cold_code, cold_bytes = _get(path)
        samples: List[float] = []
        codes: List[int] = []
        for _ in range(max(1, n)):
            ms, code, _size = _get(path)
            if ms >= 0:
                samples.append(ms)
            codes.append(code)
        rows.append({
            "path": path, "label": label, "budget_ms": budget, "note": note,
            "cold_ms": round(cold, 1) if cold >= 0 else None,
            "p50_ms": round(percentile(samples, 0.50), 1),
            "p95_ms": round(percentile(samples, 0.95), 1),
            "max_ms": round(percentile(samples, 1.0), 1),
            "samples": len(samples),
            "status_ok": all(200 <= c < 300 for c in codes) and bool(codes),
            "bytes": cold_bytes,
        })
    return rows


def render(rows: List[Dict[str, Any]]) -> None:
    header = f"{'端点':<34}{'冷启':>9}{'p50':>9}{'p95':>9}{'峰值':>9}{'预算':>9}  判定"
    print(header)
    print("-" * len(header))
    for row in rows:
        verdict = "OK" if row["p95_ms"] <= row["budget_ms"] else "OVER"
        if not row["status_ok"]:
            verdict = "HTTP-ERR"
        print(f"{row['label'] + ' ' + row['path']:<34}"
              f"{_fmt(row['cold_ms']):>9}{row['p50_ms']:>9}{row['p95_ms']:>9}"
              f"{row['max_ms']:>9}{int(row['budget_ms']):>9}  {verdict}")
    worst = max(rows, key=lambda r: r["p95_ms"] / max(1.0, r["budget_ms"]))
    over = [r for r in rows if r["p95_ms"] > r["budget_ms"] or not r["status_ok"]]
    print()
    print(f"最紧的一项：{worst['label']} p95={worst['p95_ms']}ms "
          f"预算={int(worst['budget_ms'])}ms 占用={worst['p95_ms'] / max(1.0, worst['budget_ms']):.0%}")
    print(f"超标 {len(over)} 项 / 共 {len(rows)} 项")


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.0f}"


#: 并发压测的读路径组合：只读、可缓存、不含六专家（专家团有 KV 落盘，不适合高频压）
MIX = [
    "/api/health",
    "/api/brain/config",
    "/api/decision/bus",
    "/api/brain/alignment?light=true",
    "/api/decision/sentiment/flow",
    "/api/market-dashboard",
]


def stress(concurrency: int, rounds: int) -> Dict[str, Any]:
    """固定读组合的并发压测：吞吐 / 延迟分位 / 错误率。

    场景是「多个页面同时轮询」，不是「单用户点一下」——
    前者才检验事件循环有没有被同步阻塞拖死。
    """
    import threading

    latencies: List[float] = []
    codes: List[int] = []
    lock = threading.Lock()
    started = time.perf_counter()

    def worker(worker_id: int) -> None:
        local: List[float] = []
        local_codes: List[int] = []
        for i in range(rounds):
            path = MIX[(worker_id + i) % len(MIX)]
            ms, code, _ = _get(path)
            if ms >= 0:
                local.append(ms)
            local_codes.append(code)
        with lock:
            latencies.extend(local)
            codes.extend(local_codes)

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(max(1, concurrency))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    total = len(codes)
    ok = sum(1 for c in codes if 200 <= c < 300)
    return {
        "concurrency": concurrency, "rounds": rounds,
        "requests": total, "elapsed_s": round(elapsed, 2),
        "rps": round(total / elapsed, 1) if elapsed > 0 else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "max_ms": round(percentile(latencies, 1.0), 1),
        "error_rate": round(1 - ok / total, 4) if total else 1.0,
    }


def render_stress(result: Dict[str, Any]) -> None:
    print()
    print(f"并发压测：{result['concurrency']} 并发 × {result['rounds']} 轮 × {len(MIX)} 端点"
          f" = {result['requests']} 请求")
    print(f"  吞吐 {result['rps']} req/s · p50 {result['p50_ms']}ms · p95 {result['p95_ms']}ms"
          f" · 峰值 {result['max_ms']}ms · 错误率 {result['error_rate']:.2%}"
          f" · 总耗时 {result['elapsed_s']}s")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="决策台延迟预算实测")
    parser.add_argument("--n", type=int, default=7, help="每个端点的采样次数（默认 7）")
    parser.add_argument("--gate", action="store_true", help="有超预算项则退出码 1")
    parser.add_argument("--json", help="把原始结果写入该文件")
    parser.add_argument("--concurrency", type=int, default=0,
                        help="附带并发压测的并发数（0 表示不压测）")
    parser.add_argument("--rounds", type=int, default=6, help="并发压测每线程轮数（默认 6）")
    args = parser.parse_args(argv)

    _AUTH.update(auth_headers(BASE))

    if _get("/api/health")[1] != 200:
        print(f"服务未就绪：{BASE}/api/health 不可用")
        return 2

    rows = measure(max(1, args.n))
    render(rows)
    stress_result = None
    if args.concurrency > 0:
        stress_result = stress(args.concurrency, max(1, args.rounds))
        render_stress(stress_result)

    if args.json:
        payload = {
            "version": "perf-budget.v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base": BASE, "samples": args.n, "rows": rows,
            "stress": stress_result,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\n原始样本已写入 {args.json}")

    bad = [r for r in rows if not r["status_ok"] or r["p95_ms"] > r["budget_ms"]]
    if stress_result and stress_result["error_rate"] > 0.01:
        bad.append({"label": "并发压测错误率", "p95_ms": 0, "budget_ms": 0,
                    "status_ok": False})
    if bad and args.gate:
        print("\n超预算：")
        for row in bad:
            print(f"  - {row['label']} p95={row['p95_ms']}ms 预算={int(row['budget_ms'])}ms")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
