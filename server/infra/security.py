"""安全中间件（护城河 · 边界层）

纯 ASGI 实现（不用 BaseHTTPMiddleware），因此不会缓冲 SSE / 流式响应。
职责：
1. 请求 ID：透传或生成 X-Request-ID，方便日志与前端排障；
2. 安全响应头：nosniff / frame / referrer / permissions / CSP / COOP；
3. 请求体上限：Content-Length 超限直接 413，避免大包打满内存；
4. 简易限流：/api/* 按客户端 IP 滑动窗口限流（默认 600 次/分钟），429 + Retry-After；
5. 路径卫生：拒绝含 `..` 的路径，避免静态文件穿越尝试。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from server.infra.settings import Settings, get_settings


def _json_response(status: int, payload: dict, extra_headers: list[tuple[bytes, bytes]] | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8"),
               (b"content-length", str(len(body)).encode())]
    if extra_headers:
        headers.extend(extra_headers)
    return status, headers, body


class RateLimiter:
    """按 IP 的滑动窗口限流器（进程内，足够单机部署）。"""

    def __init__(self, limit_per_min: int, window_seconds: float = 60.0):
        self.limit = max(1, int(limit_per_min))
        self.window = float(window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._denied = 0
        self._allowed = 0

    def allow(self, key: str, now: float | None = None) -> tuple[bool, int]:
        now = now if now is not None else time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                self._denied += 1
                retry_after = max(1, int(self.window - (now - q[0])))
                return False, retry_after
            q.append(now)
            self._allowed += 1
            if len(self._hits) > 4096:  # 防字典无限增长
                for k in list(self._hits.keys()):
                    if not self._hits[k]:
                        self._hits.pop(k, None)
            return True, 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "window_seconds": self.window,
                "limit_per_min": self.limit,
                "allowed": self._allowed,
                "denied": self._denied,
                "tracked_clients": len(self._hits),
            }


def _security_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
    if not settings.security_headers:
        return []
    return [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"content-security-policy", settings.csp.encode()),
    ]


class MoatMiddleware:
    def __init__(self, app, settings: Settings | None = None):
        self.app = app
        self.settings = settings or get_settings()
        self.limiter = RateLimiter(self.settings.rate_limit_per_min)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")
        method: str = scope.get("method", "GET")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        rid = (headers.get(b"x-request-id") or b"").decode("latin-1") or uuid.uuid4().hex[:16]

        # 1) 路径卫生
        if ".." in path or "%2e%2e" in path.lower():
            status, hdrs, body = _json_response(400, {"ok": False, "message": "非法路径", "request_id": rid})
            await self._send_raw(send, status, hdrs, body, rid)
            return

        # 2) 请求体上限（仅有 Content-Length 时可直接判定；chunked 交给下游限制）
        cl = headers.get(b"content-length")
        if cl:
            try:
                if int(cl) > self.settings.max_body_bytes:
                    status, hdrs, body = _json_response(
                        413, {"ok": False, "message": "请求体过大", "limit": self.settings.max_body_bytes, "request_id": rid})
                    await self._send_raw(send, status, hdrs, body, rid)
                    return
            except ValueError:
                pass

        # 3) /api 限流
        if path.startswith("/api/") and path not in ("/api/stream",):
            client = scope.get("client") or ("unknown", 0)
            ip = client[0] if client else "unknown"
            ok, retry_after = self.limiter.allow(ip)
            if not ok:
                extra = [(b"retry-after", str(retry_after).encode())]
                status, hdrs, body = _json_response(
                    429, {"ok": False, "message": "请求过于频繁", "retry_after": retry_after, "request_id": rid}, extra)
                await self._send_raw(send, status, hdrs, body, rid)
                return

        request_id = rid.encode()
        sec_headers = _security_headers(self.settings)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers_list = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers_list}
                if b"x-request-id" not in existing:
                    headers_list.append((b"x-request-id", request_id))
                for k, v in sec_headers:
                    if k not in existing:
                        headers_list.append((k, v))
                message = {**message, "headers": headers_list}
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _send_raw(self, send, status: int, headers, body: bytes, rid: str) -> None:
        headers = list(headers) + [(b"x-request-id", rid.encode())] + _security_headers(self.settings)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})


_moat_middleware: MoatMiddleware | None = None


def get_moat_middleware() -> MoatMiddleware | None:
    return _moat_middleware


def set_moat_middleware(mw: MoatMiddleware) -> None:
    global _moat_middleware
    _moat_middleware = mw
