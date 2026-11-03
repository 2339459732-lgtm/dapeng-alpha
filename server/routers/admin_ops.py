"""运维操作：缓存管理、API 健康、日志与数据导出"""

import csv
import io
import json
import logging
import os
import platform
import psutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
import hmac
from server.cache import cache
from server.config import SECTORS, DB_DIR, PRICING
from server.payment.service import _load_orders, OrderStatus

from server.routers.admin_common import _add_log, _load_logs, _load_users, require_admin_access
from server.routers.admin_stats import AVG_MS_NOTE, _AVG_MS_EXPR, _query_api_calls_db

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


@router.get("/cache")
async def list_cache():
    """获取缓存列表（key, 大小估算, 创建时间, TTL剩余）

    只读：走 ``cache.iter_entries()`` 快照遍历，不触碰 LRU 顺序、不删除条目，
    避免在迭代过程中被后台回收任务修改而抛 ``OrderedDict mutated``。
    """
    items = []
    for entry in cache.iter_entries():
        key = entry["key"]
        # Estimate size by serializing data
        try:
            size_bytes = len(json.dumps(entry["data"], ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            size_bytes = 0

        # Extract prefix (before first colon)
        prefix = key.split(":")[0] if ":" in key else "other"

        items.append({
            "key": key,
            "prefix": prefix,
            "size_bytes": size_bytes,
            "size_display": _format_bytes(size_bytes),
            "created_at": datetime.fromtimestamp(entry["stored_at"]).isoformat(),
            "age": entry["age"],
            "ttl": entry["ttl"],
            "remaining_ttl": entry["remaining_ttl"],
            "is_expired": entry["expired"],
        })

    # Sort by creation time descending
    items.sort(key=lambda x: x["created_at"], reverse=True)

    # Stats by prefix
    prefix_stats = {}
    for item in items:
        p = item["prefix"]
        if p not in prefix_stats:
            prefix_stats[p] = {"count": 0, "total_bytes": 0}
        prefix_stats[p]["count"] += 1
        prefix_stats[p]["total_bytes"] += item["size_bytes"]

    for p in prefix_stats:
        prefix_stats[p]["total_display"] = _format_bytes(prefix_stats[p]["total_bytes"])

    st = cache.stats()
    return {
        "ok": True,
        "data": {
            "total": len(items),
            "items": items,
            "prefix_stats": prefix_stats,
            "max_size": st["max_size"],
            # 运行态指标：命中率 / 淘汰 / 已回收过期数 / 后台任务是否在跑
            "stats": st,
        },
    }


def _format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


@router.delete("/cache")
async def clear_all_cache():
    """清空所有缓存"""
    cleared_count = cache.size
    cache.clear()
    _add_log("CACHE_CLEAR", f"清空所有缓存，共 {cleared_count} 条", "admin")
    return {"ok": True, "data": {"cleared": cleared_count}}


@router.post("/cache/purge-expired")
async def purge_expired_cache():
    """手动回收过期缓存（后台任务每 30s 自动执行，此处供运维即时清理）"""
    before = cache.size
    purged = cache.purge_expired(force=True)
    _add_log("CACHE_PURGE", f"回收过期缓存 {purged} 条（{before} → {cache.size}）", "admin")
    return {
        "ok": True,
        "data": {"purged": purged, "size_before": before, "size_after": cache.size},
    }


@router.delete("/cache/{key:path}")
async def delete_cache(key: str):
    """删除单个缓存"""
    if not cache.contains(key):
        return {"ok": False, "message": "缓存键不存在"}
    cache.delete(key)
    _add_log("CACHE_DELETE", f"删除缓存键: {key}", "admin")
    return {"ok": True, "data": {"key": key}}


# ===== API Health Monitoring =====

@router.get("/api-health")
async def api_health():
    """API 健康检查：基于真实调用日志聚合，不生成估算或随机数据。"""
    from server.main import app

    stats_rows = _query_api_calls_db(
        f"""SELECT endpoint, method, COUNT(*) AS calls,
                  SUM(CASE WHEN is_success = 0 THEN 1 ELSE 0 END) AS errors,
                  {_AVG_MS_EXPR} AS avg_ms,
                  MAX(timestamp) AS last_called
           FROM api_call_logs
           GROUP BY endpoint, method"""
    )
    stats_map = {(row["endpoint"], row["method"]): row for row in stats_rows}

    endpoints = []
    seen = set()
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        if route.path.startswith("/api/admin") or route.path == "/api/debug-kline":
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            key = (route.path, method)
            if key in seen:
                continue
            seen.add(key)
            row = stats_map.get(key)
            calls = int(row["calls"] or 0) if row else 0
            errors = int(row["errors"] or 0) if row else 0
            avg_ms = round(float(row["avg_ms"] or 0), 1) if row else 0.0
            if calls == 0:
                status = "NO_DATA"
            elif errors == calls:
                status = "DOWN"
            elif errors > 0 or avg_ms > 1000:
                status = "SLOW"
            else:
                status = "UP"
            endpoints.append({
                "path": route.path,
                "method": method,
                "status": status,
                "calls": calls,
                "errors": errors,
                "avg_response_ms": avg_ms,
                "last_called": row["last_called"] if row else None,
            })

    total = len(endpoints)
    healthy = len([e for e in endpoints if e["status"] == "UP"])
    slow = len([e for e in endpoints if e["status"] == "SLOW"])
    down = len([e for e in endpoints if e["status"] == "DOWN"])
    measured = [e for e in endpoints if e["calls"] > 0]
    avg_ms = round(sum(e["avg_response_ms"] for e in measured) / len(measured), 1) if measured else 0.0

    return {
        "ok": True,
        "note": "响应时间来自 api_calls.db；NO_DATA 表示尚无调用记录",
        "avg_note": AVG_MS_NOTE,
        "data": {
            "total": total,
            "healthy": healthy,
            "slow": slow,
            "down": down,
            "no_data": total - len(measured),
            "avg_response_ms": avg_ms,
            "avg_note": AVG_MS_NOTE,
            "endpoints": endpoints,
        },
    }


# ===== Operation Logs =====

@router.get("/logs")
async def list_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None),
):
    """操作日志列表（分页+筛选）"""
    logs = _load_logs()
    logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    if action:
        logs = [l for l in logs if l.get("action") == action]

    total = len(logs)
    start = (page - 1) * size
    end = start + size

    # Compute stats
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_ago = (datetime.now(timezone.utc).timestamp() - 7 * 86400)
    today_count = len([l for l in logs if l.get("timestamp", "").startswith(today)])
    week_count = len([l for l in logs if l.get("timestamp", "") >= datetime.fromtimestamp(week_ago, tz=timezone.utc).isoformat()])

    # Action distribution
    action_dist = {}
    for l in logs:
        a = l.get("action", "UNKNOWN")
        action_dist[a] = action_dist.get(a, 0) + 1

    return {
        "ok": True,
        "data": {
            "total": total,
            "page": page,
            "size": size,
            "items": logs[start:end],
            "today_count": today_count,
            "week_count": week_count,
            "action_distribution": action_dist,
        },
    }


@router.post("/logs")
async def create_log(action: str = Query(...), detail: str = Query(...), operator: str = Query("admin")):
    """手动记录操作日志"""
    _add_log(action, detail, operator)
    return {"ok": True, "message": "日志已记录"}


# ===== CSV Export =====

@router.get("/export")
async def export_data(type: str = Query(..., pattern="^(users|orders)$")):
    """导出CSV文件"""
    if type == "users":
        users = _load_users()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "姓名", "手机", "套餐", "状态", "创建时间"])
        for u in users.values():
            writer.writerow([
                u.get("id", ""),
                u.get("guardian_name", ""),
                u.get("guardian_phone", ""),
                u.get("plan", ""),
                u.get("status", ""),
                u.get("created_at", ""),
            ])
        output.seek(0)

        def generate():
            yield output.getvalue()

        headers = {
            "Content-Disposition": f'attachment; filename="users_{datetime.now().strftime("%Y%m%d%H%M%S")}.csv"'
        }
        return StreamingResponse(generate(), media_type="text/csv", headers=headers)

    elif type == "orders":
        orders = _load_orders()
        items = list(orders.values())
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["订单号", "用户名", "手机", "套餐", "金额(分)", "渠道", "状态", "创建时间"])
        for o in items:
            writer.writerow([
                o.get("order_id", ""),
                o.get("user_name", ""),
                o.get("user_phone", ""),
                o.get("plan", ""),
                o.get("amount_fen", 0),
                o.get("gateway", ""),
                o.get("status", ""),
                o.get("created_at", ""),
            ])
        output.seek(0)

        def generate():
            yield output.getvalue()

        headers = {
            "Content-Disposition": f'attachment; filename="orders_{datetime.now().strftime("%Y%m%d%H%M%S")}.csv"'
        }
        return StreamingResponse(generate(), media_type="text/csv", headers=headers)

    return {"ok": False, "message": "不支持的导出类型"}
