"""共享层：CPU 采样、管理员鉴权、文件路径与日志/用户读写"""

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

logger = logging.getLogger(__name__)


# ============================================================
# CPU 采样（非阻塞 + 不返回假零）
# ============================================================
# 历史缺陷：system_status()（async def）里直接调用 process.cpu_percent(interval=0.5)，
# 会把**整个事件循环阻塞 500ms**（同时有 SSE 推流时，所有请求与推送一起卡）；
# 且 psutil 在未建立基线时恒返回 0.0，被当成真实测量值返回 → 前端显示「CPU 0%」（假零）。
#
# 现在的做法：
#   1) 非阻塞 —— interval=None，比较的是「距上次调用」的差值，不 sleep、不阻塞事件循环；
#   2) 时间闸 —— 距上次采样不足 _CPU_MIN_INTERVAL 秒时直接复用缓存，避免相邻两次调用
#      因 CPU 时间几乎未增长而读出接近 0 的伪值；
#   3) 基线未建立（psutil 首帧恒 0.0）时返回 None，由调用方如实表示「暂不可得」，
#      **绝不用 0 冒充真实值**。
_CPU_PROC = psutil.Process(os.getpid())
_CPU_MIN_INTERVAL = 5.0
_CPU_STATE = {"ts": 0.0, "value": None, "primed": False}


def sample_process_cpu_percent() -> Optional[float]:
    """非阻塞读取**本进程** CPU 占用率（%）。

    返回 None 表示「基线尚未建立 / 采样失败」——调用方应显示「--」而非 0。
    """
    now = time.time()
    if _CPU_STATE["value"] is not None and (now - _CPU_STATE["ts"]) < _CPU_MIN_INTERVAL:
        return _CPU_STATE["value"]
    try:
        v = _CPU_PROC.cpu_percent(interval=None)
    except Exception:
        logger.warning("admin: process.cpu_percent 采样失败", exc_info=True)
        return _CPU_STATE["value"]
    if v is None:
        return _CPU_STATE["value"]
    v = round(float(v), 2)
    if not _CPU_STATE["primed"]:
        # 首帧：psutil 借此建立基线，返回值恒为 0.0，是测量伪影而非真实占用 → 不采信
        _CPU_STATE["primed"] = True
        _CPU_STATE["ts"] = now
        return None
    _CPU_STATE["value"] = v
    _CPU_STATE["ts"] = now
    return v


def sample_system_cpu_percent() -> Optional[float]:
    """非阻塞读取**整机** CPU 占用率（%）。

    与 sample_process_cpu_percent() 语义不同（整机 vs 本进程），调用方须标明口径，
    不得混用作比较。返回 None 表示基线未建立。
    """
    now = time.time()
    if _CPU_STATE.get("sys_value") is not None and (now - _CPU_STATE.get("sys_ts", 0.0)) < _CPU_MIN_INTERVAL:
        return _CPU_STATE["sys_value"]
    try:
        v = psutil.cpu_percent(interval=None)
    except Exception:
        return _CPU_STATE.get("sys_value")
    if v is None:
        return _CPU_STATE.get("sys_value")
    v = round(float(v), 2)
    if not _CPU_STATE.get("sys_primed"):
        _CPU_STATE["sys_primed"] = True
        _CPU_STATE["sys_ts"] = now
        return None
    _CPU_STATE["sys_value"] = v
    _CPU_STATE["sys_ts"] = now
    return v


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "").lower()
    return host in {"127.0.0.1", "::1", "localhost"}


def require_admin_access(
    request: Request,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """管理 API 访问控制：配置令牌后必须校验；未配置时仅允许本机访问。"""
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if expected:
        if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
            raise HTTPException(status_code=401, detail="管理令牌无效或缺失")
        return True
    if not _is_loopback(request):
        raise HTTPException(status_code=403, detail="管理接口仅允许本机访问，请配置 ADMIN_TOKEN")
    return True


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)

USERS_FILE = DB_DIR / "users.json"
LOGS_FILE = DB_DIR / "admin_logs.json"
API_CALLS_DB = DB_DIR / "api_calls.db"


# ===== Operation Log Infrastructure =====

def _load_logs() -> list:
    """Load operation logs from JSON file."""
    if LOGS_FILE.exists():
        try:
            return json.loads(LOGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_logs(logs: list):
    """Save operation logs to JSON file."""
    LOGS_FILE.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")


def _add_log(action: str, detail: str, operator: str = "admin"):
    """Record an operation log entry."""
    logs = _load_logs()
    logs.append({
        "id": f"LOG{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{len(logs)}",
        "action": action,
        "detail": detail,
        "operator": operator,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Keep max 1000 entries
    if len(logs) > 1000:
        logs = logs[-1000:]
    _save_logs(logs)


def _load_users() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}
