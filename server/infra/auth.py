#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""访问密钥鉴权（access-key.v1）：开源版「有钥匙才能进」。

为什么要这一层
--------------
代码开源之后，**代码是公开的，跑起来的实例不是**。这套系统里没有任何用户体系
（只有限流 + 管理端 token），一旦把 `HOST` 改成 `0.0.0.0` 暴露到公网，任何拿到
地址的人都能直接看行情、调接口、改配置。所以需要一个与应用解耦的入口闸门。

设计取舍
--------
1. **单口令，不做账号体系**。目标使用者是「自己 + 几个朋友」，账号、找回密码、
   邀请注册都是负资产；一个足够长的 `ACCESS_KEY` 已经能挡住扫描器。
2. **会话用 HMAC 签名 Cookie，不用服务端 session 表**。无状态、重启不掉线、
   零存储；代价是无法单独踢人——要踢就换 key（所有会话立即失效，这正是预期）。
3. **签名密钥由 ACCESS_KEY 派生**（可用 `SESSION_SECRET` 覆盖）。这样轮换 key
   会自动作废全部旧会话，不需要额外的吊销逻辑。
4. **未配置 ACCESS_KEY 时整层关闭**。本地开发、pytest、CI 都不受影响；
   只有显式配置后才启用，避免「装上就进不去」。
5. **失败信息统一**：key 错误只回「密钥不正确」，不区分「没配 / 配了但错」，
   比较用 `hmac.compare_digest` 防时序侧信道；登录接口单独限流，防爆破。

边界
----
* 这是**单租户闸门**，不是多用户鉴权：登录后所有请求共享同一身份；
* Cookie 默认 `HttpOnly` + `SameSite=Lax`；HTTPS 下自动加 `Secure`；
* 不做 CSRF token：所有写操作都是同源 JSON 请求，且 `SameSite=Lax` 已挡掉
  跨站表单提交；若将来加第三方嵌入，需要补 CSRF。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional, Tuple
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

VERSION = "access-key.v1"
COOKIE_NAME = "dapeng_session"
SESSION_VERSION = "v1"
DEFAULT_TTL_HOURS = 72

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


def access_key() -> Optional[str]:
    """读取访问密钥；空字符串视为未配置。"""
    value = (os.getenv("ACCESS_KEY") or os.getenv("DAPENG_ACCESS_KEY") or "").strip()
    return value or None


def auth_enabled() -> bool:
    return access_key() is not None


def ttl_seconds() -> int:
    raw = (os.getenv("SESSION_TTL_HOURS") or "").strip()
    try:
        hours = float(raw) if raw else DEFAULT_TTL_HOURS
    except ValueError:
        hours = DEFAULT_TTL_HOURS
    # 下限 1 小时、上限 30 天：太短会频繁重登，太长等于永不掉线
    return int(min(max(hours, 1.0), 720.0) * 3600)


def _secret() -> bytes:
    """签名密钥：优先 SESSION_SECRET，否则由 ACCESS_KEY 派生。

    派生而不是直接用 key：Cookie 里不会出现与口令同源的原始字节，
    且轮换 ACCESS_KEY 会同时作废所有旧会话。
    """
    explicit = (os.getenv("SESSION_SECRET") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    key = access_key() or ""
    return hashlib.sha256(("dapeng-session|" + key).encode("utf-8")).digest()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_session(exp: int) -> str:
    """生成 `v1.<过期时间戳>.<签名>`。"""
    payload = f"{SESSION_VERSION}.{int(exp)}"
    return f"{payload}.{_sign(payload)}"


def verify_session(token: Optional[str], now: Optional[float] = None) -> bool:
    """校验会话：签名必须匹配、必须未过期。任何异常一律视为无效。"""
    if not token:
        return False
    parts = str(token).split(".")
    if len(parts) != 3 or parts[0] != SESSION_VERSION:
        return False
    try:
        exp = int(parts[1])
    except (TypeError, ValueError):
        return False
    if exp <= int(now if now is not None else time.time()):
        return False
    expected = _sign(f"{SESSION_VERSION}.{exp}")
    return hmac.compare_digest(expected, parts[2])


def check_key(candidate: Optional[str]) -> bool:
    """常量时间比较口令。未配置密钥时一律拒绝（中间件此时本来就不会拦）。"""
    expected = access_key()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(str(candidate), expected)


def issue_session() -> Tuple[str, int]:
    """签发会话，返回 (cookie 值, 有效秒数)。"""
    ttl = ttl_seconds()
    return sign_session(int(time.time()) + ttl), ttl


def cookie_secure(request: Request) -> bool:
    """HTTPS 或显式要求时给 Cookie 加 Secure。"""
    if (os.getenv("COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return request.url.scheme == "https"


# ---------------------------------------------------------------------------
# 部署面加固：真实 IP、来源校验、口令强度
# ---------------------------------------------------------------------------
def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def client_ip(request: Request) -> str:
    """解析客户端 IP：默认取 TCP 对端，只有可信代理才采信 X-Forwarded-For。

    为什么不能无条件读 XFF：那是客户端可伪造的请求头，拿它做限流键等于让攻击者
    每次换一个「IP」，10 次/分钟的登录限制立刻失效。只有当我们确实部署在反向代理
    后面（`TRUST_PROXY=1`，或对端 IP 在 `TRUSTED_PROXIES` 里）才采信。
    """
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
    """写操作的来源校验（CSRF 纵深防御）。

    规则：**带 Origin 且与本站不同源 → 拒绝**；不带 Origin 的放行（curl、服务端
    回调、微信/支付宝异步通知都不带 Origin，不能误伤）。
    与 SameSite=Lax 叠加：一个挡跨站表单，一个挡跨站 XHR/fetch。
    """
    if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
        return True
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return True
    allowed = {o.strip().rstrip("/") for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True
    host = (request.headers.get("host") or "").strip()
    if not host:
        return False
    return origin.rstrip("/") in {f"http://{host}", f"https://{host}"}


def key_strength_issue(key: Optional[str] = None) -> Optional[str]:
    """口令强度检查：太短直接提示（不阻止启动，避免把已有部署锁死）。"""
    value = access_key() if key is None else key
    if not value:
        return None
    if len(value) < 16:
        return f"ACCESS_KEY 仅 {len(value)} 字符，建议 ≥32（python -c \"import secrets;print(secrets.token_urlsafe(32))\"）"
    return None


class AccessKeyMiddleware(BaseHTTPMiddleware):
    """未登录：API 回 401，页面 302 到 /login；写操作额外校验来源。"""

    async def dispatch(self, request: Request, call_next):
        if not origin_allowed(request):
            return JSONResponse(status_code=403, content={
                "ok": False, "message": "来源校验失败（跨站写请求已被拒绝）",
                "version": VERSION})

        if not auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/auth/"):
            return await call_next(request)

        if verify_session(request.cookies.get(COOKIE_NAME)):
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


def set_session_cookie(response: Response, value: str, ttl: int, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME, value,
        max_age=ttl, httponly=True, samesite="lax", secure=secure, path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


__all__ = ["VERSION", "COOKIE_NAME", "auth_enabled", "access_key", "check_key",
           "issue_session", "verify_session", "sign_session", "ttl_seconds",
           "cookie_secure", "set_session_cookie", "clear_session_cookie",
           "client_ip", "origin_allowed", "key_strength_issue",
           "AccessKeyMiddleware", "PUBLIC_PATHS"]
