#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""私钥自检：只看状态，不改密码、不回显明文。

为什么要单独做一个命令
----------------------
私钥放在 `.env`，最容易出的事不是"被黑客攻破"，而是：
1. 被误提交进仓库；
2. 换机器时忘了带，服务起不来；
3. 长度/强度不达标，启动护栏直接拒绝；
4. `.env` 权限过宽，同机其他账户可读。

本命令逐项检查并给出可执行建议。**默认不打印私钥**，只打印指纹（sha256 前 8 位）。

用法：
    python scripts/ops/key_status.py            # 人类可读
    python scripts/ops/key_status.py --json     # 机器可读
退出码：0 = 正常；1 = 存在需要处理的问题。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"


def read_env() -> dict:
    data = {}
    try:
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")


def appears_in_repo(key: str) -> tuple[bool, str]:
    """在**已跟踪文件**里查私钥（用临时文件传给 git grep -f，避免明文进进程列表）。"""
    if not key:
        return False, "无密钥"
    fd, path = tempfile.mkstemp(prefix="key-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key + "\n")
        proc = git("grep", "-l", "-F", "-f", path, "--", ".")
        files = [f for f in (proc.stdout or "").splitlines() if f.strip()]
        return bool(files), ", ".join(files[:5])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="私钥状态自检")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--allow-missing", action="store_true",
                        help="无 .env 时视为正常（CI/开发机用；生产不要加）")
    args = parser.parse_args(argv)

    env = read_env()
    key = (env.get("ACCESS_KEY") or "").strip()
    key_file = (env.get("ACCESS_KEY_FILE") or "").strip()
    min_len = int(env.get("ACCESS_KEY_MIN_LEN") or 32)
    require = (env.get("REQUIRE_ACCESS_KEY") or "0").lower() in ("1", "true", "yes", "on")

    issues: list[str] = []
    notes: list[str] = []
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:8] if key else None
    length = len(key)
    classes = sum([any(c.islower() for c in key), any(c.isupper() for c in key),
                   any(c.isdigit() for c in key), any(not c.isalnum() for c in key)]) if key else 0

    if not key and not key_file:
        if args.allow_missing or os.getenv("CI"):
            notes.append("当前环境没有 .env（CI/开发机），跳过私钥检查")
        else:
            issues.append("未配置 ACCESS_KEY / ACCESS_KEY_FILE")
    if key and length < min_len:
        issues.append(f"私钥长度 {length} < 下限 {min_len}")
    if key and classes < 2:
        issues.append("私钥字符种类 < 2（疑似弱口令）")
    if require and not key and not key_file:
        issues.append("REQUIRE_ACCESS_KEY=1 但没有任何私钥来源，服务会拒绝启动")

    leaked, where = appears_in_repo(key)
    if leaked:
        issues.append(f"私钥出现在已跟踪文件中：{where}")

    ignored = git("check-ignore", "-q", ".env").returncode == 0
    if not ignored:
        issues.append(".env 未被 .gitignore 忽略")

    history = git("log", "--all", "--oneline", "--", ".env").stdout.strip()
    if history:
        issues.append(".env 曾进入 git 历史，需要清理历史后再开源")

    if key_file:
        p = Path(key_file)
        notes.append(f"ACCESS_KEY_FILE 指向 {key_file}（存在={p.is_file()}）")

    age_days = None
    try:
        age_days = round((datetime.now().timestamp() - ENV_PATH.stat().st_mtime) / 86400, 1)
    except OSError:
        pass

    backups = sorted((ROOT / "backups").glob(".env.bak-*")) if (ROOT / "backups").is_dir() else []
    acl = ""
    try:
        acl = subprocess.run(["icacls", str(ENV_PATH)], capture_output=True, text=True,
                             encoding="utf-8", errors="ignore").stdout
    except OSError:
        pass
    broad = [line.strip() for line in acl.splitlines()
             if ("Users:" in line or "Everyone" in line or "Sandbox" in line)]
    if broad:
        notes.append(" .env 仍对 Users/Everyone/Sandbox 可读，建议收紧 ACL")

    payload = {
        "ok": not issues,
        "fingerprint": fingerprint,
        "length": length,
        "classes": classes,
        "min_len": min_len,
        "require_key": require,
        "source": "ACCESS_KEY" if key else ("ACCESS_KEY_FILE" if key_file else None),
        "env_age_days": age_days,
        "backups": len(backups),
        "git_ignored": ignored,
        "in_history": bool(history),
        "leaked_in_tracked_files": leaked,
        "issues": issues,
        "notes": notes,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=" * 58)
        print("  私钥状态自检（不显示明文）")
        print("=" * 58)
        print(f"  指纹        {fingerprint or '--'}（sha256 前 8 位）")
        print(f"  长度/种类   {length} / {classes}（下限 {min_len}）")
        print(f"  来源        {payload['source'] or '--'}")
        print(f"  .env 修改于 {age_days if age_days is not None else '--'} 天前")
        print(f"  备份份数    {len(backups)}")
        print(f"  仓库泄露    {'是' if leaked else '否'}")
        print(f"  历史提交    {'有' if history else '无'}")
        for n in notes:
            print(f"  提示        {n.strip()}")
        print("-" * 58)
        if issues:
            print("  需要处理：")
            for i in issues:
                print(f"   ✗ {i}")
        else:
            print("  状态正常：私钥未泄露、长度达标、.env 已被忽略。")
        print("=" * 58)

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
