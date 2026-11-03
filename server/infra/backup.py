"""一致性备份（护城河 · 恢复层）

备份内容（缺失自动跳过）：
- static/docs-graph.json     图谱唯一事实源
- data/trading.db            交易/复盘状态
- data/api_calls.db          API 调用追踪
- static/admin.html          管理页（含本地化 vendor 引用）
- .env.example               配置样例（不备份 .env，避免密钥进备份包）

产物：backups/dapeng-backup-YYYYmmdd-HHMMSS.zip
      backups/latest.json（最近一次清单，含每个文件的 sha256 / 大小）
留存：MOAT_BACKUP_KEEP（默认 14 份）
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path

from server.infra.settings import get_settings

_TARGETS = [
    ("static/docs-graph.json", "file"),
    ("data/trading.db", "sqlite"),
    ("data/api_calls.db", "sqlite"),
    ("static/admin.html", "file"),
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_copy_sqlite(src: Path, dest: Path) -> None:
    """用 sqlite3 backup API 复制，保证 WAL 下的一致性。"""
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def create_backup() -> dict:
    s = get_settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = s.backup_dir / f"dapeng-backup-{stamp}.zip"
    manifest = {"created_at": int(time.time()), "created": stamp, "files": [], "schema": "backup/1"}
    tmp_dir = s.backup_dir / f".tmp-{stamp}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        for rel, kind in _TARGETS:
            src = s.project_root / rel
            if not src.is_file():
                manifest["files"].append({"path": rel, "ok": False, "reason": "missing"})
                continue
            digest = _sha256(src)
            manifest["files"].append({"path": rel, "ok": True, "bytes": src.stat().st_size, "sha256": digest})
            if kind == "sqlite":
                copy = tmp_dir / Path(rel).name
                _safe_copy_sqlite(src, copy)
                with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(copy, rel)
                copy.unlink(missing_ok=True)
            else:
                with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(src, rel)
        with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        latest = {"ok": True, "zip": zip_path.name, "bytes": zip_path.stat().st_size, "manifest": manifest}
        (s.backup_dir / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
        _prune(s)
        return latest
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            for p in tmp_dir.glob("*"):
                p.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass


def _prune(s) -> None:
    files = sorted(s.backup_dir.glob("dapeng-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[max(1, s.backup_keep):]:
        try:
            old.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    print(json.dumps(create_backup(), ensure_ascii=False, indent=1))
