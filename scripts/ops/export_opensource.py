#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成"可发布"的开源导出包（只含 git 已跟踪文件）。

开源发布最常见的两种翻车：
1. 直接把工作目录打包，把 `.env`、`data/users.json`、`trading.db`、`logs/` 一起发出去；
2. 用 `git archive` 但仓库本身误提交过密钥。

本脚本两道闸：
* **只取 HEAD**（`git archive`），工作区里未跟踪的敏感文件天然不进包；
* **解包前先查清单**：命中 `.env` / 数据库 / 日志 / 备份 / 导出目录即中止；
* 再对包内文本跑一次密钥扫描（复用 scan_secrets 的规则）。

用法：
    python scripts/ops/export_opensource.py                 # 输出到 dist/
    python scripts/ops/export_opensource.py --out D:/tmp     # 指定目录
    python scripts/ops/export_opensource.py --no-scan        # 跳过文本扫描（不建议）
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

FORBIDDEN_PARTS = (
    "/.env", "data/users.json", "data/trading.db", "data/payment_orders.json",
    "data/admin_logs.json", "data/review/", "backups/", "logs/", "secrets/",
)
FORBIDDEN_SUFFIX = (".db", ".db-wal", ".db-shm", ".log", ".pem", ".key", ".p12")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="ignore")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出开源发布包")
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--no-scan", action="store_true")
    args = parser.parse_args(argv)

    status = git("status", "--porcelain").stdout.strip()
    if status:
        print("[warn] 工作区有未提交改动；导出的是 HEAD，不包含这些改动。")

    tag = time.strftime("%Y%m%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"dapeng-alpha-src-{tag}.tar.gz"

    proc = git("archive", "--format=tar.gz", f"--output={archive}", "HEAD")
    if proc.returncode != 0:
        print("[error] git archive 失败：", proc.stderr.strip())
        return 1

    # 1) 清单检查
    bad = []
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getnames()
        for name in members:
            low = name.lower()
            if any(part in low for part in FORBIDDEN_PARTS) or low.endswith(FORBIDDEN_SUFFIX):
                bad.append(name)
        if bad:
            archive.unlink(missing_ok=True)
            print("[error] 导出包包含禁止文件，已删除：")
            for b in bad[:20]:
                print("  -", b)
            return 1

        # 2) 文本密钥扫描
        if not args.no_scan:
            from scan_secrets import scan  # 复用规则

            findings = []
            tmp = Path(tempfile.mkdtemp(prefix="dapeng-export-"))
            tf.extractall(tmp)
            rels = [str(p.relative_to(tmp)).replace("\\", "/")
                    for p in tmp.rglob("*") if p.is_file()]
            # scan() 以 ROOT 为基准，这里临时切换
            import scan_secrets

            old_root = scan_secrets.ROOT
            scan_secrets.ROOT = tmp
            try:
                findings = scan(rels)
            finally:
                scan_secrets.ROOT = old_root
            if findings:
                archive.unlink(missing_ok=True)
                print("[error] 导出包内检测到疑似凭据，已删除：")
                for f in findings[:20]:
                    print(f"  {f['file']}:{f['line']} {f['rule']} {f['match']}")
                return 1

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    size_mb = archive.stat().st_size / 1024 / 1024
    print(f"[ok] 导出完成：{archive}")
    print(f"     文件数 {len(members)} · {size_mb:.2f} MB")
    print(f"     sha256 {digest}")
    print("     上传方式：gh release create v0.1.0 <包> 或作为附件分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
