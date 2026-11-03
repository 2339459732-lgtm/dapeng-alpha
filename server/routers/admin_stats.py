"""统计与通知：站内通知、调用统计、错误排行"""

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

from server.routers.admin_common import API_CALLS_DB, require_admin_access, sample_system_cpu_percent

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


NOTIFICATIONS_FILE = DB_DIR / "admin_notifications.json"


def _load_notifications() -> list:
    """Load notifications from JSON file."""
    if NOTIFICATIONS_FILE.exists():
        try:
            return json.loads(NOTIFICATIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_notifications(notifications: list):
    """Save notifications to JSON file."""
    NOTIFICATIONS_FILE.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _generate_system_notifications() -> list:
    """Generate system alerts based on current metrics."""
    alerts = []
    try:
        # 整机 CPU：非阻塞采样。样本无效（None）时**不做阈值比较**，避免把「未采样到」
        # 误判成 0% 而静默放过真实告警。
        cpu_pct = sample_system_cpu_percent()
        mem_pct = psutil.virtual_memory().percent

        if cpu_pct is not None and cpu_pct > 80:
            alerts.append({
                "id": f"NTF{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}CPU",
                "type": "alert",
                "title": "CPU 告警",
                "message": f"CPU 使用率超过 80%，当前 {cpu_pct:.1f}%",
                "read": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        if mem_pct > 90:
            alerts.append({
                "id": f"NTF{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}MEM",
                "type": "alert",
                "title": "内存告警",
                "message": f"内存使用率超过 90%，当前 {mem_pct:.1f}%",
                "read": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    except Exception:
        pass
    return alerts


@router.get("/notifications")
async def list_notifications():
    """获取通知列表"""
    notifications = _load_notifications()

    # Merge with any new system alerts
    new_alerts = _generate_system_notifications()
    for alert in new_alerts:
        # Avoid duplicates
        if not any(n.get("id") == alert["id"] for n in notifications):
            notifications.insert(0, alert)

    if new_alerts:
        _save_notifications(notifications)

    unread_count = len([n for n in notifications if not n.get("read", False)])

    return {
        "ok": True,
        "data": {
            "total": len(notifications),
            "unread": unread_count,
            "items": notifications[:50],  # Max 50 items
        },
    }


@router.post("/notifications/read")
async def mark_notifications_read(notification_id: Optional[str] = Query(None)):
    """标记通知已读（指定ID或全部）"""
    notifications = _load_notifications()
    for n in notifications:
        if notification_id is None or n.get("id") == notification_id:
            n["read"] = True
    _save_notifications(notifications)
    return {"ok": True, "message": "已标记已读"}


@router.delete("/notifications")
async def clear_notifications():
    """清空所有通知"""
    _save_notifications([])
    return {"ok": True, "message": "通知已清空"}


# ===== API Call Tracking (api_calls.db) =====

# 平均耗时统计口径：**只计正常样本**——排除流式/长连接（is_streaming=1，
# 其 response_time_ms 记的是连接存活时长）与量级离群样本（is_outlier=1）。
# 调用次数/错误数仍统计全部请求，仅耗时聚合走此口径。
_AVG_MS_EXPR = (
    "AVG(CASE WHEN is_streaming = 0 AND is_outlier = 0 "
    "AND response_time_ms IS NOT NULL THEN response_time_ms END)"
)
AVG_MS_NOTE = "平均耗时已排除流式请求与异常离群样本"


def _query_api_calls_db(query: str, params: list = None) -> list:
    """Execute a read-only query against api_calls.db and return rows as dicts.

    Returns an empty list if the database file does not exist yet.
    """
    if not API_CALLS_DB.exists():
        return []
    conn = sqlite3.connect(str(API_CALLS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params or []).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/stats")
async def admin_call_stats(
    start: Optional[str] = Query(None, description="ISO 8601 start timestamp"),
    end: Optional[str] = Query(None, description="ISO 8601 end timestamp"),
):
    """API调用统计汇总 — 总调用/成功率/平均响应/错误数"""
    conditions = []
    params = []
    if start:
        conditions.append("timestamp >= ?")
        params.append(start)
    if end:
        conditions.append("timestamp <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = _query_api_calls_db(
        f"SELECT COUNT(*) AS total, "
        f"COALESCE(SUM(is_success), 0) AS success, "
        f"COALESCE({_AVG_MS_EXPR}, 0) AS avg_ms "
        f"FROM api_call_logs{where}",
        params,
    )

    if rows:
        row = rows[0]
        total = row["total"] or 0
        success = row["success"] or 0
        errors = total - success
        avg_ms = round(row["avg_ms"], 1) if row["avg_ms"] else 0.0
        success_rate = round(success / total * 100, 1) if total > 0 else 0.0
    else:
        total = success = errors = 0
        avg_ms = 0.0
        success_rate = 0.0

    return {
        "ok": True,
        "data": {
            "total_calls": total,
            "success_count": success,
            "error_count": errors,
            "success_rate": success_rate,
            "avg_response_ms": avg_ms,
            "avg_note": AVG_MS_NOTE,
        },
    }


@router.get("/calls")
async def admin_call_list(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    endpoint: Optional[str] = Query(None, description="Filter by exact endpoint path"),
    start: Optional[str] = Query(None, description="ISO 8601 start timestamp"),
    end: Optional[str] = Query(None, description="ISO 8601 end timestamp"),
):
    """API调用明细分页查询"""
    conditions = []
    params = []
    if endpoint:
        conditions.append("endpoint = ?")
        params.append(endpoint)
    if start:
        conditions.append("timestamp >= ?")
        params.append(start)
    if end:
        conditions.append("timestamp <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_rows = _query_api_calls_db(
        f"SELECT COUNT(*) AS cnt FROM api_call_logs{where}", params
    )
    total = count_rows[0]["cnt"] if count_rows else 0

    offset = (page - 1) * size
    items = _query_api_calls_db(
        f"SELECT id, endpoint, method, status_code, response_time_ms, "
        f"is_success, timestamp, params_summary, is_streaming, is_outlier "
        f"FROM api_call_logs{where} "
        f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [size, offset],
    )

    return {
        "ok": True,
        "data": {
            "total": total,
            "page": page,
            "size": size,
            "items": items,
        },
    }


@router.get("/error-ranking")
async def admin_error_ranking(
    start: Optional[str] = Query(None, description="ISO 8601 start timestamp"),
    end: Optional[str] = Query(None, description="ISO 8601 end timestamp"),
):
    """API调用错误率排行 TOP 10 — 按端点聚合"""
    conditions = []
    params = []
    if start:
        conditions.append("timestamp >= ?")
        params.append(start)
    if end:
        conditions.append("timestamp <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = _query_api_calls_db(
        f"""SELECT endpoint,
                  COUNT(*) AS total_calls,
                  SUM(CASE WHEN is_success = 0 THEN 1 ELSE 0 END) AS error_count,
                  {_AVG_MS_EXPR} AS avg_response_ms
           FROM api_call_logs{where}
           GROUP BY endpoint
           ORDER BY error_count DESC
           LIMIT 10""",
        params,
    )

    items = []
    for r in rows:
        total = r["total_calls"] or 0
        errors = r["error_count"] or 0
        items.append({
            "endpoint": r["endpoint"],
            "total_calls": total,
            "error_count": errors,
            "error_rate": round(errors / total * 100, 2) if total > 0 else 0.0,
            "avg_response_ms": round(r["avg_response_ms"], 1) if r["avg_response_ms"] else 0.0,
        })

    return {"ok": True, "data": items}
