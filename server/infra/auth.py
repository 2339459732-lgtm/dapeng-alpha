#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""访问密钥鉴权（access-key.v2）：服务端会话 + 会话级 Cookie + 强密钥强制。

为什么从 v1 改成 v2
-------------------
v1 用「HMAC 签名 Cookie」：无状态、重启不掉线，但有两个无法接受的缺口——

1. **会话关不掉**：Cookie 带 `Max-Age`，关掉浏览器再打开仍然有效；
   要踢人只能换 key，把所有人一起踢下线。
2. **弱口令挡不住**：6 位数字口令配 72 小时会话，等于给穷举者留了长期后门。

v2 的规则：

* **服务端会话**：Cookie 只放随机 `sid`（32 字节），会话记录在内存；
  登出即删除、可单条撤销、可全部撤销。
* **会话级 Cookie**：不写 `Max-Age`/`Expires`，浏览器关闭即清除；
  再次访问必须重新输入密钥。
* **双重超时**：空闲 30 分钟自动失效（可调），绝对上限 8 小时（可调）。
* **强密钥强制**：`REQUIRE_ACCESS_KEY=1` 时，未配置或长度 < 32 直接拒绝启动；
  支持 `ACCESS_KEY_FILE` 从挂载的文件读取，密钥不必写进 `.env`。
* **无密钥不得进入**：鉴权层默认开启；只有显式 `AUTH_DISABLED=1`（仅测试）才关闭，
  且该开关会在启动日志里高亮告警。

边界（如实说明）
----------------
* 会话存在进程内存，**重启即全员下线**——这是有意的：宁可重登，不留长期票据。
* 浏览器「关闭标签页但进程还在」时，会话 Cookie 仍可能保留；要连标签页关闭都退出，
  设 `PAGE_CLOSE_LOGOUT=1`（前端 pagehide 时调用登出），代价是刷新页面也可能触发重登。
* 这是单租户闸门，不是多用户鉴权：登录后所有请求共享同一身份。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

VERSION = "access-key.v2"
COOKIE_NAME = "dapeng_sid"
DEFAULT_IDLE_MINUTES = 30
DEFAULT_ABS_HOURS = 8
MIN_KEY_LEN = 32  # 默认下限；可由 ACCESS_KEY_MIN_LEN 覆盖（部署方自担强度责任）
MAX_SESSIONS = 32

#: 无需登录即可访问的路径。只放登录本身与健康检查——健康检查要给监控/看门狗用，
#: 但它不回任何业务数据，泄露面为零。
PUBLIC_PATHS = {
    "/login",
    "/login.html",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/health",
    "/favicon.ico",
}

#: sid → {created, last_seen, ip, ua_hash}
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 密钥读取与强度
# ---------------------------------------------------------------------------
def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def access_key() -> Optional[str]:
    """读取访问密钥：优先环境变量，其次 `ACCESS_KEY_FILE` 指向的文件。"""
    value = (os.getenv("ACCESS_KEY") or os.getenv("DAPENG_ACCESS_KEY") or "").strip()
    if value:
        return value
    path = (os.getenv("ACCESS_KEY_FILE") or "").strip()
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
            return text or None
        except OSError:
            return None
    return None


def auth_enabled() -> bool:
    """鉴权是否生效。默认开启；只有显式 AUTH_DISABLED=1 才关闭（仅测试）。"""
    if _truthy("AUTH_DISABLED"):
        return False
    return access_key() is not None


def page_close_logout() -> bool:
    """关闭标签页时是否调用登出（PAGE_CLOSE_LOGOUT=1）。"""
    return _truthy("PAGE_CLOSE_LOGOUT")


def require_key() -> bool:
    """部署要求：必须配置强密钥。由 REQUIRE_ACCESS_KEY=1 开启。"""
    return _truthy("REQUIRE_ACCESS_KEY")


def min_key_len() -> int:
    """私钥长度下限。默认 32；部署方可用 ACCESS_KEY_MIN_LEN 下调（自担强度责任）。"""
    return _env_int("ACCESS_KEY_MIN_LEN", MIN_KEY_LEN, 8, 128)


def key_strength_issue(key: Optional[str] = None) -> Optional[str]:
    """密钥强度检查。返回问题描述；None 表示合格。

    两条判定：
    1. **长度**不低于 `min_key_len()`（默认 32）；
    2. **多样性**：至少 2 类字符，且不同字符数不少于长度的一半（下限 6）。
       第 2 条用来挡住 `123456789012` 这类「够长但只是顺序数字」的口令。
    """
    value = access_key() if key is None else key
    if not value:
        return "未配置 ACCESS_KEY（或 ACCESS_KEY_FILE 指向的文件为空）"
    floor = min_key_len()
    if len(value) < floor:
        return (f"ACCESS_KEY 仅 {len(value)} 字符，低于 {floor} 位下限；"
                "请用 python -c \"import secrets;print(secrets.token_urlsafe(48))\" 生成")
    classes = sum([any(c.islower() for c in value), any(c.isupper() for c in value),
                   any(c.isdigit() for c in value), any(not c.isalnum() for c in value)])
    if classes < 2 or len(set(value)) < max(6, int(len(value) * 0.5)):
        return "ACCESS_KEY 字符种类或分布过于单一，疑似弱口令；请使用随机串或长口令"
    return None


def startup_guard() -> None:
    """启动护栏：要求强密钥时，缺失/过弱直接拒绝启动。"""
    if _truthy("AUTH_DISABLED"):
        return
    if not require_key():
        return
    issue = key_strength_issue()
    if issue:
        raise RuntimeError(
            "拒绝启动：" + issue + "\n"
            "  生成：python -c \"import secrets;print(secrets.token_urlsafe(48))\"\n"
            "  写入：.env 的 ACCESS_KEY=...，或 ACCESS_KEY_FILE=/run/secrets/dapeng_key\n"
            "  如确需关闭鉴权（仅限本地测试），设 AUTH_DISABLED=1。"
        )


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(float(raw)) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def idle_seconds() -> int:
    """空闲超时（秒）：多久没有请求就失效。"""
    return _env_int("SESSION_IDLE_MINUTES", DEFAULT_IDLE_MINUTES, 5, 240) * 60


def absolute_seconds() -> int:
    """绝对超时（秒）：从登录起算，无论是否活跃都失效。"""
    return _env_int("SESSION_TTL_HOURS", DEFAULT_ABS_HOURS, 1, 24) * 3600


def _ua_hash(ua: str) -> str:
    return hashlib.sha256((ua or "").encode("utf-8", "ignore")).hexdigest()[:16]


def create_session(ip: str, user_agent: str = "") -> str:
    """创建会话，返回 sid。超过上限时淘汰最久未活动的会话。"""
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            oldest = min(_SESSIONS.items(), key=lambda kv: kv[1].get("last_seen") or 0)
            _SESSIONS.pop(oldest[0], None)
        _SESSIONS[sid] = {"created": now, "last_seen": now,
                          "ip": ip, "ua_hash": _ua_hash(user_agent)}
    return sid


def get_session(sid: Optional[str]) -> Optional[Dict[str, Any]]:
    """校验会话并刷新 last_seen；过期/不存在返回 None。"""
    if not sid:
        return None
    now = time.time()
    with _LOCK:
        rec = _SESSIONS.get(sid)
        if not rec:
            return None
        if now - float(rec.get("last_seen") or 0) > idle_seconds():
            _SESSIONS.pop(sid, None)
            return None
        if now - float(rec.get("created") or 0) > absolute_seconds():
            _SESSIONS.pop(sid, None)
            return None
        rec["last_seen"] = now
        return dict(rec)


def revoke_session(sid: Optional[str]) -> bool:
    if not sid:
        return False
    with _LOCK:
        return _SESSIONS.pop(sid, None) is not None


def revoke_all() -> int:
    with _LOCK:
        count = len(_SESSIONS)
        _SESSIONS.clear()
        return count


def session_count() -> int:
    with _LOCK:
        return len(_SESSIONS)


def session_remaining(sid: Optional[str]) -> int:
    """距离空闲超时的剩余秒数（0 = 无效）。"""
    rec = get_session(sid)
    if not rec:
        return 0
    return max(0, int(idle_seconds() - (time.time() - float(rec.get("last_seen") or 0))))


# ---------------------------------------------------------------------------
# 密钥比较与 Cookie
# ---------------------------------------------------------------------------
def check_key(candidate: Optional[str]) -> bool:
    """常量时间比较口令。未配置密钥时一律拒绝。"""
    expected = access_key()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(str(candidate), expected)


def cookie_secure(request: Request) -> bool:
    if _truthy("COOKIE_SECURE"):
        return True
    return request.url.scheme == "https"


def set_session_cookie(response: Response, sid: str, secure: bool) -> None:
    """写入**会话 Cookie**：不设 Max-Age/Expires，浏览器关闭即清除。"""
    response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax",
                        secure=secure, path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# 部署面加固：真实 IP、来源校验、IP 白名单
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    """解析客户端 IP：默认取 TCP 对端，只有可信代理才采信 X-Forwarded-For。"""
    peer = (request.client.host if request.client else "") or "unknown"
    trusted = _truthy("TRUST_PROXY")
    if not trusted:
        allowed = {x.strip() for x in (os.getenv("TRUSTED_PROXIES") or "").split(",") if x.strip()}
        trusted = peer in allowed
    if trusted:
        chain = (request.headers.get("x-forwarded-for") or "").split(",")
        first = chain[0].strip() if chain else ""
        if first:
            return first
    return peer


def origin_allowed(request: Request) -> bool:
    """写操作来源校验：带 Origin 且与本站不同源 → 拒绝；不带 Origin 放行。"""
    if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return True
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return True
    allowed = {o.strip().rstrip("/") for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True

    # 反代场景：应用看到的 Host 常是内网地址，浏览器发的 Origin 是公网域名。
    # 只比较 hostname，不比较 scheme（TLS 在反代终止），并采信可信代理的
    # X-Forwarded-Host，否则登录会被自己的 CSRF 校验挡成 403。
    from urllib.parse import urlsplit

    origin_host = urlsplit(origin).hostname
    if not origin_host:
        return False
    hosts = set()
    host = (request.headers.get("host") or "").strip()
    if host:
        hosts.add(host.split(":")[0].lower())
    trusted = _truthy("TRUST_PROXY")
    if not trusted:
        peer = (request.client.host if request.client else "") or ""
        proxies = {x.strip() for x in (os.getenv("TRUSTED_PROXIES") or "").split(",") if x.strip()}
        trusted = peer in proxies
    if trusted:
        for item in (request.headers.get("x-forwarded-host") or "").split(","):
            item = item.strip()
            if item:
                hosts.add(item.split(":")[0].lower())
    return origin_host.lower() in hosts


def allowed_ips() -> List[Any]:
    """IP 白名单（`ACCESS_IP_ALLOWLIST`，逗号分隔，支持 CIDR）。空 = 不限制。"""
    raw = (os.getenv("ACCESS_IP_ALLOWLIST") or "").strip()
    if not raw:
        return []
    import ipaddress

    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def ip_allowed(ip: str) -> bool:
    nets = allowed_ips()
    if not nets:
        return True
    import ipaddress

    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    return any(addr in net for net in nets)


class AccessKeyMiddleware(BaseHTTPMiddleware):
    """未登录：API 回 401，页面 302 到 /login；写操作额外校验来源。"""

    async def dispatch(self, request: Request, call_next):
        if not ip_allowed(client_ip(request)):
            return JSONResponse(status_code=403, content={
                "ok": False, "message": "当前 IP 不在访问白名单内", "version": VERSION})

        if not origin_allowed(request):
            return JSONResponse(status_code=403, content={
                "ok": False, "message": "来源校验失败（跨站写请求已被拒绝）",
                "version": VERSION})

        if not auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)

        if get_session(request.cookies.get(COOKIE_NAME)):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "message": "需要访问密钥", "login": "/login",
                         "version": VERSION},
                headers={"WWW-Authenticate": "AccessKey"},
            )

        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(url="/login?next=" + quote(nxt, safe=""), status_code=302)


__all__ = ["VERSION", "COOKIE_NAME", "auth_enabled", "require_key", "access_key",
           "check_key", "key_strength_issue", "startup_guard",
           "create_session", "get_session", "revoke_session", "revoke_all",
           "session_count", "session_remaining", "idle_seconds", "absolute_seconds",
           "cookie_secure", "set_session_cookie", "clear_session_cookie",
           "client_ip", "origin_allowed", "allowed_ips", "ip_allowed", "page_close_logout",
           "AccessKeyMiddleware", "PUBLIC_PATHS"]
