#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键体检：把「改完代码到底还正不正常」收敛成一条命令。

跑四件事，任何一项失败即退出码 1：

1. `pytest tests/`           —— 单元与契约测试
2. `scripts/ci_guard.py`     —— 架构测绘 / 拆分管线 / 全量测试三守卫
3. `system_audit.py`         —— 39 项运行态体检（需服务在跑）
4. `perf_budget.py`          —— 11 个端点延迟预算（需服务在跑）

为什么单独做一个入口：这四步以前分散在文档与习惯里，换人维护时最容易「只跑 pytest
就发版」。把它做成命令后，CI、发布前自检、接手第一天的环境确认都走同一条路径。

用法：
    python scripts/verify/checkup.py            # 全量
    python scripts/verify/checkup.py --offline  # 跳过需要服务的两项
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(name: str, cmd: list[str], timeout: int = 900) -> tuple[str, bool, str]:
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="ignore", timeout=timeout)
    except subprocess.TimeoutExpired:
        return name, False, f"超时（>{timeout}s）"
    elapsed = time.time() - started
    lines = [ln.strip() for ln in (proc.stdout or proc.stderr or "").splitlines()]
    lines = [ln for ln in lines if ln and set(ln) != {"="} and set(ln) != {"-"}]
    detail = lines[-1] if lines else ""
    return name, proc.returncode == 0, f"{elapsed:.1f}s · {detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAPENG Alpha 一键体检")
    parser.add_argument("--offline", action="store_true", help="跳过需要运行中服务的检查")
    args = parser.parse_args(argv)

    py = sys.executable
    steps = [
        ("私钥自检", [py, "scripts/ops/key_status.py", "--allow-missing"]),
        ("单元与契约测试", [py, "-m", "pytest", "tests/", "-q"]),
        ("架构守卫", [py, "scripts/ci_guard.py"]),
    ]
    if not args.offline:
        steps += [
            ("运行态体检", [py, "scripts/verify/system_audit.py", "--gate"]),
            ("性能预算", [py, "scripts/verify/perf_budget.py", "--gate"]),
        ]

    print("=" * 62)
    print("  DAPENG Alpha 一键体检")
    print("=" * 62)
    results = []
    for name, cmd in steps:
        print(f"\n▶ {name} …", flush=True)
        results.append(run(name, cmd))

    print("\n" + "-" * 62)
    failed = 0
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<12} {detail}")
        failed += 0 if ok else 1
    print("-" * 62)
    print(f"  {len(results) - failed}/{len(results)} 通过")
    if failed:
        print("\n失败项请单独重跑对应命令查看完整输出。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
