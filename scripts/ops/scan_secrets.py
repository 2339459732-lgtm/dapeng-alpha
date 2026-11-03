#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""密钥/凭据扫描：提交前与 CI 共用的一套规则。

为什么不用只装 gitleaks 了事
----------------------------
gitleaks 很强，但它需要额外二进制；仓库里最容易漏的反而是**本项目自己的密钥**
（`.env` 里的 ACCESS_KEY、管理员令牌、模型 Key）被复制到脚本/文档/测试里。
本脚本用一组高信噪比规则覆盖这类情况，零依赖，能在 pre-commit 里 1 秒跑完。

用法：
    python scripts/ops/scan_secrets.py            # 扫描已跟踪文件
    python scripts/ops/scan_secrets.py --staged   # 只扫暂存区（pre-commit 用）
    python scripts/ops/scan_secrets.py --all      # 连未跟踪文件一起扫（慎用，data/ 很大）
退出码：0 = 干净；1 = 命中。
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

#: 高信噪比规则：只保留“几乎不可能是正常文本”的形态
PATTERNS = [
    ("openai/deepseek key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]

#: 变量赋值型：key/password/secret/token = "长随机串"
ASSIGN = re.compile(
    r"""(?ix)
    \b(api[_-]?key|access[_-]?key|secret|password|passwd|token|auth[_-]?code)
    \s*[:=]\s*
    (["'])(?P<val>[^"'\n]{12,})\2
    """
)

#: 允许的占位符/示例值
PLACEHOLDER = re.compile(
    r"(?i)^(?:your|example|placeholder|changeme|test|dummy|xxx|\*+|<.*>|\$\{.*\}|"
    r"test-key-[\w\-]+|sk-xxx.*)$"
)

SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
               ".ttf", ".eot", ".zip", ".gz", ".pdf", ".min.js", ".map")
SKIP_PATHS = ("LICENSE", "static/vendor/", "docs/assets/")


def project_key() -> str:
    """读取本项目 .env 的 ACCESS_KEY，用于精确匹配（CI 无 .env 时为空）。"""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("ACCESS_KEY=") and "ACCESS_KEY_FILE=" not in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def shannon(value: str) -> float:
    if not value:
        return 0.0
    counts = {c: value.count(c) for c in set(value)}
    return -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value.strip()))


def tracked_files() -> list[str]:
    proc = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="ignore")
    return [f for f in proc.stdout.splitlines() if f.strip()]


def staged_files() -> list[str]:
    proc = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")
    return [f for f in proc.stdout.splitlines() if f.strip()]


def all_files() -> list[str]:
    out = []
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            out.append(str(p.relative_to(ROOT)).replace("\\", "/"))
    return out


def should_skip(path: str) -> bool:
    if any(part in path for part in SKIP_PATHS):
        return True
    return path.lower().endswith(SKIP_SUFFIX)


def scan(files: Iterable[str]) -> list[dict]:
    findings = []
    exact = project_key()
    for rel in files:
        if should_skip(rel):
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:  # 压缩/生成文件，跳过
                continue
            for name, pattern in PATTERNS:
                m = pattern.search(line)
                if m:
                    findings.append({"file": rel, "line": lineno, "rule": name,
                                     "match": m.group(0)[:6] + "…"})
            # 精确匹配项目私钥：即使它长得不像随机串，只要出现在仓库里就是泄露
            if exact and exact in line and "KEY-MANAGEMENT" not in rel:
                findings.append({"file": rel, "line": lineno, "rule": "project-access-key",
                                 "match": exact[:3] + "…"})
            for m in ASSIGN.finditer(line):
                value = m.group("val")
                if is_placeholder(value):
                    continue
                # 随机串通常熵 > 3.2；普通中文/路径/句子会被过滤掉
                if shannon(value) >= 3.2 and len(set(value)) >= 8:
                    findings.append({"file": rel, "line": lineno,
                                     "rule": f"assignment:{m.group(1)}",
                                     "match": value[:4] + "…"})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="密钥扫描")
    parser.add_argument("--staged", action="store_true", help="只扫暂存区")
    parser.add_argument("--all", action="store_true", help="扫描工作区所有文件")
    args = parser.parse_args(argv)

    files = staged_files() if args.staged else (all_files() if args.all else tracked_files())
    findings = scan(files)
    if not findings:
        print(f"[secrets] 干净（扫描 {len(files)} 个文件）")
        return 0

    print(f"[secrets] 发现 {len(findings)} 处疑似凭据：")
    for f in findings:
        print(f"  {f['file']}:{f['line']}  {f['rule']}  {f['match']}")
    print("\n处理建议：移除明文，改用 .env / ACCESS_KEY_FILE；若已提交，需重写历史并轮换。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
