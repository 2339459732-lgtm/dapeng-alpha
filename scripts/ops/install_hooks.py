#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安装 git pre-commit 钩子：提交前自动扫一遍密钥。

为什么用钩子而不是只靠 CI：CI 发现时密钥已经进了分支；钩子在 `git commit`
落盘前就拦住，成本 1 秒。钩子调用 `scripts/ops/scan_secrets.py --staged`，
与 CI 同一套规则，避免"本地过、线上挂"。

用法：
    python scripts/ops/install_hooks.py          # 安装/覆盖
    python scripts/ops/install_hooks.py --remove # 卸载（仅当钩子由本脚本生成）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".git" / "hooks" / "pre-commit"
MARKER = "# managed-by: dapeng-open-source"

BODY = """#!/bin/sh
{marker}
# 提交前密钥扫描：命中即阻止提交（可用 git commit --no-verify 跳过，但不建议）
python scripts/ops/scan_secrets.py --staged || {{
  echo ""
  echo "提交被阻止：检测到疑似凭据。"
  echo "  查看：python scripts/ops/scan_secrets.py --staged"
  echo "  如确认是误报，请改写为占位符或加入扫描器白名单。"
  exit 1
}}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 git 钩子")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)

    if not (ROOT / ".git").is_dir():
        print("当前目录不是 git 仓库")
        return 1
    HOOK.parent.mkdir(parents=True, exist_ok=True)

    if args.remove:
        if HOOK.is_file() and MARKER in HOOK.read_text(encoding="utf-8", errors="ignore"):
            HOOK.unlink()
            print("已卸载 pre-commit 钩子")
            return 0
        print("未找到由本脚本生成的钩子，未做修改")
        return 0

    HOOK.write_text(BODY.format(marker=MARKER), encoding="utf-8", newline="\n")
    try:
        HOOK.chmod(0o755)
    except OSError:
        pass
    print(f"已安装 pre-commit 钩子 → {HOOK}")
    print("验证：修改任意文件后 git add，再 git commit，应看到 [secrets] 扫描输出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
