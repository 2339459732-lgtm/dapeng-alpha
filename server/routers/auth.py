# -*- coding: utf-8 -*-
"""访问密钥登录接口（access-key.v1）。

    GET  /api/auth/status   当前是否启用、是否已登录（公开）
    POST /api/auth/login    {"key": "..."} → 下发签名 Cookie
    POST /api/auth/logout   清除 Cookie
    GET  /login             登录页（静态文件）

设计要点
--------
* **不区分错误类型**：密钥错误统一回 `密钥不正确`；未配置密钥时登录接口直接回
  `未启用访问密钥`（此时中间件本来就不拦，登录页会自动跳走）。
* **登录单独限流**：`RateLimiter(10/min/IP)`。全局限流是 600/min，对爆破太宽松；
  登录口必须比业务口严格一个数量级。
* **不记录密钥**：日志里只记 IP 与成功/失败，绝不写 candidate。
* 成功后 `303` 语义交给前端：这里只回 JSON，由 `/login` 页面决定跳转目标，
  避免「表单 POST 后刷新重复提交」的老问题。
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from typing import Any, Deque, Dict

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from server.infra import auth
from server.infra.security import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

#: 登录尝试限流：每个 IP 每分钟 10 次。失败与成功都计数——
#: 只计失败会让「成功后再爆破」绕过限制。
_LOGIN_LIMITER = RateLimiter(limit_per_min=10)

#: 全局失败闸门：10 分钟内累计 60 次失败 → 全体暂停登录 10 分钟。
#: 单 IP 限流挡不住分布式爆破，这一层是对「很多 IP 各试几次」的兜底。
_GLOBAL_FAIL_LIMIT = 60
_GLOBAL_FAIL_WINDOW = 600.0
_FAILS: Deque[float] = deque()

_STATIC_AFTER_LOGIN = "/dashboard.html"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATIC_DIR = os.path.join(_ROOT, "static")
_AUDIT_LOG = os.path.join(_ROOT, "logs", "auth.log")
_AUDIT_MAX_BYTES = 5 * 1024 * 1024


class LoginRequest(BaseModel):
    key: str = ""


def _audit(ip: str, action: str, detail: str = "") -> None:
    """登录审计：只记 IP/UA/结果，**绝不记录密钥**。写盘失败不影响登录。"""
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        if os.path.exists(_AUDIT_LOG) and os.path.getsize(_AUDIT_LOG) > _AUDIT_MAX_BYTES:
            os.replace(_AUDIT_LOG, _AUDIT_LOG + ".1")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ip": ip,
               "action": action, "detail": detail}
        with open(_AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover - 审计失败不阻断业务
        pass


def _global_locked(now: float | None = None) -> int:
    """全局失败闸门：返回剩余锁定秒数（0 = 未锁）。"""
    now = now if now is not None else time.time()
    while _FAILS and _FAILS[0] < now - _GLOBAL_FAIL_WINDOW:
        _FAILS.popleft()
    if len(_FAILS) >= _GLOBAL_FAIL_LIMIT:
        return max(1, int(_FAILS[0] + _GLOBAL_FAIL_WINDOW - now))
    return 0


def _record_failure(now: float | None = None) -> None:
    _FAILS.append(now if now is not None else time.time())


@router.get("/status")
async def status(request: Request) -> Dict[str, Any]:
    """公开状态：前端据此决定「直接进」还是「去登录」。"""
    enabled = auth.auth_enabled()
    authenticated = (not enabled) or auth.verify_session(request.cookies.get(auth.COOKIE_NAME))
    return {
        "ok": True,
        "version": auth.VERSION,
        "enabled": enabled,
        "authenticated": bool(authenticated),
        "ttl_hours": round(auth.ttl_seconds() / 3600, 2),
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request):
    ip = auth.client_ip(request)
    if not auth.auth_enabled():
        # 未启用时不该有人走到这里；前端会先看 status
        return JSONResponse(status_code=400, content={
            "ok": False, "message": "未启用访问密钥（服务端未配置 ACCESS_KEY）"})

    locked = _global_locked()
    if locked:
        logger.warning("auth login globally locked ip=%s retry=%ss", ip, locked)
        _audit(ip, "globally_locked", f"retry={locked}s")
        return JSONResponse(
            status_code=429,
            content={"ok": False, "message": f"失败次数过多，登录已暂停 {locked} 秒",
                     "retry_after": locked},
            headers={"Retry-After": str(locked)},
        )

    allowed, retry_after = _LOGIN_LIMITER.allow(ip)
    if not allowed:
        logger.warning("auth login rate-limited ip=%s", ip)
        _audit(ip, "rate_limited", f"retry={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={"ok": False, "message": f"尝试过于频繁，请 {retry_after} 秒后再试",
                     "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    if not auth.check_key(payload.key):
        _record_failure()
        logger.warning("auth login failed ip=%s", ip)
        _audit(ip, "login_failed")
        return JSONResponse(status_code=401, content={"ok": False, "message": "密钥不正确"})

    value, ttl = auth.issue_session()
    resp = JSONResponse(content={"ok": True, "message": "ok", "expires_in": ttl})
    auth.set_session_cookie(resp, value, ttl, auth.cookie_secure(request))
    resp.headers["Cache-Control"] = "no-store"
    logger.info("auth login ok ip=%s ttl=%ss", ip, ttl)
    _audit(ip, "login_ok", f"ttl={ttl}s")
    return resp


@router.post("/logout")
async def logout(request: Request) -> Any:
    resp = JSONResponse(content={"ok": True, "message": "已退出"})
    auth.clear_session_cookie(resp)
    resp.headers["Cache-Control"] = "no-store"
    _audit(auth.client_ip(request), "logout")
    return resp


@router.get("/whoami")
async def whoami(request: Request) -> Dict[str, Any]:
    """已登录时返回会话到期时间，未登录 401（前端做「会话剩余」提示用）。"""
    token = request.cookies.get(auth.COOKIE_NAME)
    if not auth.verify_session(token):
        return JSONResponse(status_code=401, content={"ok": False, "message": "未登录"})
    try:
        exp = int(str(token).split(".")[1])
    except (IndexError, ValueError):
        exp = None
    return {"ok": True, "expires_at": exp, "ttl_hours": round(auth.ttl_seconds() / 3600, 2)}


# 登录页：不挂在 /api 下，单独一条路由（StaticFiles 里也有 login.html，
# 但用户更可能直接输 /login）
login_page = APIRouter(tags=["auth"])


@login_page.get("/login", include_in_schema=False, response_model=None)
async def login_page_view() -> Response:
    path = os.path.join(_STATIC_DIR, "login.html")
    if not os.path.isfile(path):
        return JSONResponse(status_code=404, content={"ok": False, "message": "登录页缺失"})
    return FileResponse(path, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


__all__ = ["router", "login_page"]
