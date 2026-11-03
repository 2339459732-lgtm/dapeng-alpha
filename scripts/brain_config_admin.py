#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主脑配置的升级 / 回滚 / 留痕（brain-config 运维入口）。

为什么需要它
------------
``server/brain_config.py`` 只保证「读的时候坏了不炸」（mtime 热重载 + 回落默认值），
但「改的时候」没有任何护栏：手改 JSON 写错一个逗号，运行期静默回落默认值，
对证结果随之漂移，却没人知道改了什么、从哪一版改的。

本工具把改动收敛成一条可审计的路径：

    validate  → 先按运行期同一套判据预检（不落盘）
    apply     → 备份当前版本 → 原子写入 → 追加 history 索引（含前后 sha256）
    rollback  → 从留档里取回上一版 → 同样先预检再写

约定
----
* **失败绝不写入**：预检不通过就退出码 2，磁盘保持原样；
* **无需重启**：``brain_config`` 按 mtime 重载，写完下一次读取即生效；
* **留档可追**：``data/brain_config_history/`` 存快照，``brain_config_history.jsonl`` 存索引；
* **未知键只告警**：可能是拼写错误，但不阻断（避免把自己锁死）。

用法
----
    python scripts/brain_config_admin.py show
    python scripts/brain_config_admin.py validate --file /tmp/candidate.json
    python scripts/brain_config_admin.py apply --set crosscheck.number_tolerance=0.8 --note "放宽数值容差"
    python scripts/brain_config_admin.py apply --file /tmp/new.json --dry-run
    python scripts/brain_config_admin.py rollback --steps 1
    python scripts/brain_config_admin.py history -n 5
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import brain_config as bc  # noqa: E402

KEEP_SNAPSHOTS = 50


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 的根节点不是对象")
    return payload


def _coerce(text: str) -> Any:
    """把命令行里的值转成合适的类型：能按 JSON 解析就按 JSON，否则当字符串。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _dotted_set(target: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    if not all(parts):
        raise ValueError(f"非法路径：{path}")
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _snapshot_dir() -> str:
    return bc.history_dir()


def _index_path() -> str:
    return bc.history_index()


def _current_payload() -> Dict[str, Any]:
    """当前磁盘上的原始配置（不合并默认值），文件不存在时返回默认值副本。"""
    try:
        return _read_json(bc.CONFIG_PATH)
    except (OSError, ValueError):
        return json.loads(json.dumps(bc.DEFAULTS))


def _atomic_write(path: str, payload: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _backup(payload: Dict[str, Any], tag: str) -> str:
    """把一份配置写进留档目录，返回快照路径。"""
    directory = _snapshot_dir()
    os.makedirs(directory, exist_ok=True)
    sha = bc.digest() or "nofile"
    name = f"{_stamp()}-{tag}-{sha}.json"
    path = os.path.join(directory, name)
    _atomic_write(path, payload)
    _prune_snapshots(directory)
    return path


def _prune_snapshots(directory: str) -> None:
    files = sorted(
        (f for f in os.listdir(directory) if f.endswith(".json")),
        key=lambda f: os.path.getmtime(os.path.join(directory, f)),
    )
    for name in files[:-KEEP_SNAPSHOTS]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


def _append_index(row: Dict[str, Any]) -> None:
    directory = os.path.dirname(_index_path())
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(_index_path(), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _report(validation: Dict[str, Any], label: str) -> None:
    issues = validation.get("issues") or []
    warnings = validation.get("warnings") or []
    print(f"[{label}] ok={validation.get('ok')} version={validation.get('version')} "
          f"issues={len(issues)} warnings={len(warnings)}")
    for item in issues:
        print(f"  ✗ {item.get('path')}: {item.get('reason')} (value={item.get('value')})")
    for item in warnings:
        print(f"  ! {item.get('path')}: {item.get('reason')}")


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def cmd_show(_args: argparse.Namespace) -> int:
    meta = bc.meta()
    effective = bc.load(force=True)
    print("配置身份：")
    for key in ("path", "exists", "bytes", "mtime", "sha256"):
        print(f"  {key:<8} {meta.get(key)}")
    print("\n生效值（已与内置默认值深合并）：")
    print(json.dumps(effective, ensure_ascii=False, indent=2))
    _report(bc.validate(), "validate")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if args.file:
        try:
            candidate = _read_json(args.file)
        except (OSError, ValueError) as exc:
            print(f"读取失败：{exc}")
            return 2
        label = args.file
    else:
        candidate = bc.load(force=True)
        label = bc.CONFIG_PATH
    result = bc.validate_payload(candidate)
    _report(result, label)
    return 0 if result["ok"] else 2


def _build_candidate(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], List[str], str]:
    """按参数构造候选配置，返回 (候选, 变更路径, 变更来源说明)。"""
    source = "set"
    if args.file:
        candidate = _read_json(args.file)
        source = f"file:{os.path.basename(args.file)}"
        changed: List[str] = []
    else:
        candidate = _current_payload()
        changed = []

    for item in args.set or []:
        if "=" not in item:
            raise ValueError(f"--set 需要 path=value 形式，收到 {item!r}")
        path, raw = item.split("=", 1)
        path = path.strip()
        _dotted_set(candidate, path, _coerce(raw.strip()))
        changed.append(path)
    return candidate, changed, source


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.file and not args.set:
        print("没有要应用的变更：请使用 --set path=value 或 --file <json>")
        return 1
    try:
        candidate, changed, source = _build_candidate(args)
    except (OSError, ValueError) as exc:
        print(f"构造候选配置失败：{exc}")
        return 2

    validation = bc.validate_payload(candidate)
    _report(validation, "预检")
    if not validation["ok"]:
        print("\n预检未通过，磁盘未改动。修正后重试。")
        return 2
    if args.dry_run:
        print("\n--dry-run：通过预检，未写入。")
        print(json.dumps(candidate, ensure_ascii=False, indent=2))
        return 0

    before_sha = bc.digest()
    previous = _current_payload()
    snapshot = _backup(previous, "before-apply") if os.path.exists(bc.CONFIG_PATH) else None
    _atomic_write(bc.CONFIG_PATH, candidate)
    after_sha = bc.digest()
    bc.load(force=True)  # 立刻按 mtime 重载，让本次进程内也是新值
    _append_index({
        "ts": _now(), "action": "apply", "source": source,
        "note": args.note or "", "changed": changed,
        "from_sha": before_sha, "to_sha": after_sha,
        "snapshot": os.path.basename(snapshot) if snapshot else None,
        "issues": 0, "warnings": len(validation.get("warnings") or []),
    })
    print(f"\n已写入 {bc.CONFIG_PATH}")
    print(f"  sha256 {before_sha} → {after_sha}")
    if snapshot:
        print(f"  上一版快照 {snapshot}")
    print("  服务无需重启：brain_config 按 mtime 重载，下一次读取即生效。")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    directory = _snapshot_dir()
    if not os.path.isdir(directory):
        print(f"没有留档目录：{directory}")
        return 1
    files = sorted(
        (f for f in os.listdir(directory) if f.endswith(".json")),
        key=lambda f: os.path.getmtime(os.path.join(directory, f)),
    )
    if not files:
        print("留档为空，无可回滚版本。")
        return 1
    steps = max(1, int(args.steps))
    if steps > len(files):
        print(f"只找到 {len(files)} 份留档，无法回滚 {steps} 步。")
        return 1
    target = os.path.join(directory, files[-steps])
    try:
        candidate = _read_json(target)
    except (OSError, ValueError) as exc:
        print(f"快照不可读：{exc}")
        return 2

    validation = bc.validate_payload(candidate)
    _report(validation, f"预检 {os.path.basename(target)}")
    if not validation["ok"]:
        print("\n该快照自身不合规，拒绝回滚（避免把坏配置带回来）。")
        return 2
    if args.dry_run:
        print("\n--dry-run：通过预检，未写入。")
        return 0

    before_sha = bc.digest()
    _backup(_current_payload(), "before-rollback")
    _atomic_write(bc.CONFIG_PATH, candidate)
    after_sha = bc.digest()
    bc.load(force=True)
    _append_index({
        "ts": _now(), "action": "rollback", "source": f"snapshot:{os.path.basename(target)}",
        "note": args.note or "", "changed": [], "steps": steps,
        "from_sha": before_sha, "to_sha": after_sha,
        "issues": 0, "warnings": len(validation.get("warnings") or []),
    })
    print(f"\n已回滚到 {os.path.basename(target)}")
    print(f"  sha256 {before_sha} → {after_sha}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    rows = bc.history_tail(limit=max(1, int(args.n)))
    if not rows:
        print("暂无变更记录。")
        return 0
    print(f"{'时间':<20} {'动作':<10} {'sha':<18} {'备注'}")
    for row in rows:
        sha = f"{row.get('from_sha') or '-'}→{row.get('to_sha') or '-'}"
        print(f"{row.get('ts', ''):<20} {row.get('action', ''):<10} {sha:<18} "
              f"{row.get('note') or row.get('source') or ''}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain_config_admin",
        description="主脑配置的升级 / 回滚 / 留痕（预检不通过绝不落盘）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="查看当前生效配置与身份信息")
    p_show.set_defaults(func=cmd_show)

    p_val = sub.add_parser("validate", help="预检当前配置或指定文件")
    p_val.add_argument("--file", help="待检的候选配置")
    p_val.set_defaults(func=cmd_validate)

    p_apply = sub.add_parser("apply", help="应用变更（先预检，再备份，再原子写入）")
    p_apply.add_argument("--set", action="append", metavar="PATH=VALUE",
                         help="按点路径改一个值，可重复，例如 --set crosscheck.warn_factor=2.5")
    p_apply.add_argument("--file", help="用整份文件替换（仍会预检）")
    p_apply.add_argument("--note", help="本次变更说明，写入历史索引")
    p_apply.add_argument("--dry-run", action="store_true", help="只预检并打印结果，不写盘")
    p_apply.set_defaults(func=cmd_apply)

    p_back = sub.add_parser("rollback", help="回滚到留档中的第 N 近版本")
    p_back.add_argument("--steps", type=int, default=1, help="回退几步（默认 1，即最近一份快照）")
    p_back.add_argument("--note", help="回滚说明")
    p_back.add_argument("--dry-run", action="store_true", help="只预检，不写盘")
    p_back.set_defaults(func=cmd_rollback)

    p_hist = sub.add_parser("history", help="查看变更记录")
    p_hist.add_argument("-n", type=int, default=10, help="显示最近 N 条")
    p_hist.set_defaults(func=cmd_history)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
