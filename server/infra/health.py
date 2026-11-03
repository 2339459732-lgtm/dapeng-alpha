"""健康 / 就绪 / 系统状态 / 数据完整性端点（护城河 · 观测层）

- GET  /healthz              进程存活（永远 200，供 watchdog 用）
- GET  /readyz               就绪（静态资源、图谱数据、磁盘空间；失败返回 503）
- GET  /api/system/status    运行态快照（版本/uptime/限流/缓存/日志/备份/磁盘）
- GET  /api/system/integrity 数据与静态资源完整性校验（含 docs-graph sha256）
- POST /api/system/backup    手动触发一致性备份（设置 MOAT_ADMIN_TOKEN 时需要 X-Admin-Token）
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from server.infra.settings import get_settings

router = APIRouter(tags=["system"])
_STARTED_AT = time.time()


def _docs_graph_summary() -> dict:
    s = get_settings()
    path = s.docs_graph
    if not path.is_file():
        return {"ok": False, "error": "docs-graph.json 不存在"}
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSON 解析失败: {exc}", "sha256": digest}
    nodes = data.get("nodes") or []
    links = data.get("links") or []
    return {
        "ok": True,
        "sha256": digest,
        "bytes": len(raw),
        "mtime": int(path.stat().st_mtime),
        "nodes": len(nodes),
        "links": len(links),
        "directed": sum(1 for l in links if l.get("type") == "link"),
        "generated": data.get("generated") or data.get("generated_at"),
    }


def _disk() -> dict:
    s = get_settings()
    usage = shutil.disk_usage(s.project_root)
    return {
        "total_gb": round(usage.total / 1e9, 2),
        "free_gb": round(usage.free / 1e9, 2),
        "free_pct": round(usage.free / max(1, usage.total) * 100, 1),
    }


def _latest_backup() -> dict | None:
    s = get_settings()
    if not s.backup_dir.is_dir():
        return None
    files = sorted(s.backup_dir.glob("dapeng-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    p = files[0]
    return {
        "name": p.name,
        "bytes": p.stat().st_size,
        "mtime": int(p.stat().st_mtime),
        "count": len(files),
    }


@router.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "service": "dapeng-alpha",
        "version": "4.0",
        "uptime_seconds": int(time.time() - _STARTED_AT),
    }


@router.get("/readyz")
async def readyz():
    s = get_settings()
    checks: dict[str, dict] = {}

    checks["static"] = {"ok": s.static_dir.is_dir(), "path": str(s.static_dir)}
    graph = _docs_graph_summary()
    checks["docs_graph"] = {"ok": bool(graph.get("ok")), "nodes": graph.get("nodes"), "links": graph.get("links"),
                            "error": graph.get("error")}
    disk = _disk()
    checks["disk"] = {"ok": disk["free_pct"] >= 2.0, **disk}
    checks["config"] = {"ok": not s.issues, "issues": list(s.issues)}

    ok = all(c.get("ok") for c in checks.values())
    payload = {"ok": ok, "checks": checks, "version": "4.0",
               "uptime_seconds": int(time.time() - _STARTED_AT)}
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@router.get("/api/system/status")
async def system_status(request: Request):
    from server.infra.security import get_moat_middleware

    s = get_settings()
    mw = get_moat_middleware()
    limiter = mw.limiter.stats() if mw else {}
    log_file = s.logs_dir / "app.log"
    return {
        "ok": True,
        "service": {
            "name": "dapeng-alpha",
            "version": "4.0",
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "started_at": int(_STARTED_AT),
            "uptime_seconds": int(time.time() - _STARTED_AT),
        },
        "config": s.as_public(),
        "security": {
            "rate_limit": limiter,
            "request_id": getattr(request.state, "request_id", None),
            "admin_token_configured": bool(s.admin_token),
        },
        "data": _docs_graph_summary(),
        "disk": _disk(),
        "backups": {"latest": _latest_backup(), "dir": str(s.backup_dir), "keep": s.backup_keep},
        "logs": {
            "dir": str(s.logs_dir),
            "app_log": str(log_file),
            "app_log_bytes": log_file.stat().st_size if log_file.is_file() else 0,
        },
        "cache": _cache_stats(),
    }


def _cache_stats() -> dict:
    try:
        from server.cache import cache
        return cache.stats()
    except Exception:  # noqa: BLE001
        return {}


@router.get("/api/system/integrity")
async def system_integrity():
    s = get_settings()
    graph = _docs_graph_summary()
    assets = {}
    for rel in ("plate-graph.html", "project-docs.html", "dashboard.html", "admin.html",
                "vendor/tailwind-cdn.js", "vendor/vanilla-tilt.min.js",
                "vendor/fontawesome-all.min.css", "vendor/webfonts/fa-solid-900.woff2"):
        p = s.static_dir / rel
        assets[rel] = {"ok": p.is_file(), "bytes": p.stat().st_size if p.is_file() else 0}

    graph_ok = bool(graph.get("ok"))
    nodes = graph.get("nodes") or 0
    links = graph.get("links") or 0
    schema_ok = True
    try:
        raw = s.docs_graph.read_text(encoding="utf-8")
        data = json.loads(raw)
        ids = [n.get("id") for n in data.get("nodes", [])]
        schema_ok = (
            isinstance(data.get("nodes"), list)
            and isinstance(data.get("links"), list)
            and len(ids) == len(set(ids))
            and all(l.get("type") in {"link", "data_link"} for l in data.get("links", []))
        )
    except Exception:  # noqa: BLE001
        schema_ok = False

    assets_ok = all(v["ok"] for v in assets.values())
    ok = graph_ok and schema_ok and assets_ok
    return JSONResponse(status_code=200 if ok else 503, content={
        "ok": ok,
        "docs_graph": graph,
        "schema_ok": schema_ok,
        "counts": {"nodes": nodes, "links": links},
        "assets": assets,
    })


@router.post("/api/system/backup")
async def system_backup(x_admin_token: str | None = Header(default=None)):
    s = get_settings()
    if s.admin_token and x_admin_token != s.admin_token:
        return JSONResponse(status_code=401, content={"ok": False, "message": "需要有效的 X-Admin-Token"})
    from server.infra.backup import create_backup
    result = create_backup()
    return JSONResponse(status_code=200 if result.get("ok") else 500, content=result)
