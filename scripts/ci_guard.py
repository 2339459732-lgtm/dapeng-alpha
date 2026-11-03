#!/usr/bin/env python3
"""提交前守卫（ci-guard.v1）。

一条命令跑完三道闸门，任一失败即退出非 0：

1. **架构测绘**：``tools/arch_report.py --check`` —— 依赖环、跨层反向依赖、
   超大模块（>900 行）；
2. **全量测试**：``pytest tests -q`` —— 含路由快照与 OpenAPI 可生成守卫；
3. **模块拆分一致性**：``tools/split_module.py --list`` 能解析（拆分计划未腐坏）。

用法：
    python scripts/ci_guard.py            # 全跑
    python scripts/ci_guard.py --fast     # 跳过 pytest（仅架构与拆分计划）

接入 CI：把该脚本作为唯一入口，避免流水线与本地判定漂移。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(label: str, args: list) -> bool:
    print(f"\n=== {label} ===")
    proc = subprocess.run([PY] + args, cwd=ROOT)
    ok = proc.returncode == 0
    print(f"--- {label}: {'PASS' if ok else 'FAIL'} (exit {proc.returncode})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="提交前守卫")
    ap.add_argument("--fast", action="store_true", help="跳过 pytest")
    args = ap.parse_args()

    checks = [
        ("架构测绘", ["tools/arch_report.py", "--check"]),
        ("拆分计划", ["tools/split_module.py", "--list"]),
    ]
    if not args.fast:
        checks.append(("全量测试", ["-m", "pytest", "tests", "-q"]))

    results = [(label, run(label, cmd)) for label, cmd in checks]
    failed = [label for label, ok in results if not ok]
    print("\n=== 汇总 ===")
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if failed:
        print(f"\n守卫未通过：{', '.join(failed)}")
        return 1
    print("\n守卫全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
