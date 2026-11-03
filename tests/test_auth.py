"""访问密钥鉴权 v2：服务端会话、会话级 Cookie、强密钥强制。

守的是四条硬要求：

1. **关掉浏览器必须重新认证**：Cookie 不能带 `Max-Age`/`Expires`（会话级），
   且会话在服务端可撤销——不是「签个名就永久有效」。
2. **弱口令不得进入**：`REQUIRE_ACCESS_KEY=1` 时，缺失或 <32 位直接拒绝启动；
   字符种类过少（疑似弱口令）同样拒绝。
3. **拒绝方式分明**：API 回 401 JSON，页面 302 到 /login；跨站写请求 403。
4. **会话可审计、可撤销**：登出即删；`revoke-all` 一键下线全部设备；
   审计日志只记 IP 与结果，绝不出现密钥明文。
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.infra import auth  # noqa: E402
from server.routers import auth as auth_router  # noqa: E402

KEY = "test-key-0123456789-ABCDEFGHIJ-abcdefghij"  # 44 chars, 3 classes


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.AccessKeyMiddleware)
    app.include_router(auth_router.router)
    app.include_router(auth_router.login_page)

    @app.get("/api/protected")
    async def protected():
        return {"ok": True, "scope": "api"}

    @app.get("/dashboard.html")
    async def page():
        return {"ok": True, "scope": "page"}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    auth.revoke_all()
    auth_router._LOGIN_LIMITER._hits.clear()
    auth_router._FAILS.clear()
    auth_router._IP_FAILS.clear()
    return TestClient(_make_app(), follow_redirects=False)


# ---------------------------------------------------------------------------
# 密钥强度与启动护栏
# ---------------------------------------------------------------------------
def test_key_strength_rejects_short(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "071215")
    issue = auth.key_strength_issue()
    assert issue and "字符" in issue


def test_key_strength_rejects_low_entropy(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "a" * 40)
    assert auth.key_strength_issue() is not None  # 字符种类 <2
    monkeypatch.setenv("ACCESS_KEY", "12345678901234567890123456789012")  # 32 位纯数字
    assert auth.key_strength_issue() is not None


def test_key_strength_accepts_random(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    assert auth.key_strength_issue() is None


def test_key_from_file(monkeypatch, tmp_path):
    secret = tmp_path / "key.txt"
    secret.write_text(KEY + "\n", encoding="utf-8")
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("DAPENG_ACCESS_KEY", raising=False)
    monkeypatch.setenv("ACCESS_KEY_FILE", str(secret))
    assert auth.access_key() == KEY
    assert auth.auth_enabled() is True


def test_startup_guard_blocks_missing_key(monkeypatch):
    monkeypatch.setenv("REQUIRE_ACCESS_KEY", "1")
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("ACCESS_KEY_FILE", raising=False)
    with pytest.raises(RuntimeError, match="未配置 ACCESS_KEY"):
        auth.startup_guard()


def test_startup_guard_blocks_weak_key(monkeypatch):
    monkeypatch.setenv("REQUIRE_ACCESS_KEY", "1")
    monkeypatch.setenv("ACCESS_KEY", "071215")
    with pytest.raises(RuntimeError, match="低于"):
        auth.startup_guard()


def test_startup_guard_allows_strong_key(monkeypatch):
    monkeypatch.setenv("REQUIRE_ACCESS_KEY", "1")
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth.startup_guard()  # 不抛异常


def test_no_key_means_no_auth_but_guard_catches_it(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("ACCESS_KEY_FILE", raising=False)
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    assert auth.auth_enabled() is False


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------
def test_session_create_and_touch(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    sid = auth.create_session("1.2.3.4", "UA")
    rec = auth.get_session(sid)
    assert rec and rec["ip"] == "1.2.3.4"
    assert auth.session_count() >= 1
    assert auth.session_remaining(sid) > 0


def test_session_idle_timeout(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    monkeypatch.setenv("SESSION_IDLE_MINUTES", "5")
    sid = auth.create_session("1.2.3.4", "UA")
    auth._SESSIONS[sid]["last_seen"] = time.time() - auth.idle_seconds() - 1
    assert auth.get_session(sid) is None
    assert auth.session_remaining(sid) == 0


def test_session_absolute_timeout(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    monkeypatch.setenv("SESSION_TTL_HOURS", "1")
    sid = auth.create_session("1.2.3.4", "UA")
    auth._SESSIONS[sid]["created"] = time.time() - auth.absolute_seconds() - 1
    assert auth.get_session(sid) is None


def test_session_revoke(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    sid = auth.create_session("1.2.3.4", "UA")
    assert auth.revoke_session(sid) is True
    assert auth.get_session(sid) is None
    assert auth.revoke_session(sid) is False


def test_revoke_all(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    for _ in range(3):
        auth.create_session("1.2.3.4", "UA")
    assert auth.revoke_all() >= 3
    assert auth.session_count() == 0


def test_session_cap_evicts_oldest(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth.revoke_all()
    for _ in range(auth.MAX_SESSIONS + 2):
        auth.create_session("1.2.3.4", "UA")
    assert auth.session_count() == auth.MAX_SESSIONS


# ---------------------------------------------------------------------------
# 中间件与登录流程
# ---------------------------------------------------------------------------
def test_api_without_session_401(client):
    r = client.get("/api/protected")
    assert r.status_code == 401
    assert r.json()["ok"] is False
    assert r.headers.get("www-authenticate") == "AccessKey"


def test_page_without_session_redirects(client):
    r = client.get("/dashboard.html")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=")


def test_login_sets_session_cookie_without_max_age(client):
    """关键断言：不能带 Max-Age/Expires，否则关掉浏览器仍能进。"""
    r = client.post("/api/auth/login", json={"key": KEY})
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "")
    assert auth.COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "max-age" not in cookie.lower()
    assert "expires" not in cookie.lower()


def test_login_grants_access_and_logout_revokes(client):
    r = client.post("/api/auth/login", json={"key": KEY})
    sid = r.cookies.get(auth.COOKIE_NAME)
    assert client.get("/api/protected", cookies={auth.COOKIE_NAME: sid}).status_code == 200

    out = client.post("/api/auth/logout", cookies={auth.COOKIE_NAME: sid})
    assert out.status_code == 200 and out.json()["revoked"] is True
    assert client.get("/api/protected", cookies={auth.COOKIE_NAME: sid}).status_code == 401


def test_revoke_all_endpoint_kills_every_session(client):
    s1 = client.post("/api/auth/login", json={"key": KEY}).cookies.get(auth.COOKIE_NAME)
    s2 = client.post("/api/auth/login", json={"key": KEY}).cookies.get(auth.COOKIE_NAME)
    r = client.post("/api/auth/revoke-all", cookies={auth.COOKIE_NAME: s1})
    assert r.status_code == 200 and r.json()["revoked"] >= 2
    assert client.get("/api/protected", cookies={auth.COOKIE_NAME: s2}).status_code == 401


def test_login_wrong_key_401(client):
    r = client.post("/api/auth/login", json={"key": "wrong"})
    assert r.status_code == 401
    assert r.json()["message"] == "密钥不正确"
    assert auth.COOKIE_NAME not in r.cookies


def test_status_and_whoami(client):
    d = client.get("/api/auth/status").json()
    assert d["enabled"] is True and d["authenticated"] is False
    assert d["idle_minutes"] > 0 and d["absolute_hours"] > 0

    sid = client.post("/api/auth/login", json={"key": KEY}).cookies.get(auth.COOKIE_NAME)
    d2 = client.get("/api/auth/status", cookies={auth.COOKIE_NAME: sid}).json()
    assert d2["authenticated"] is True and d2["remaining_seconds"] > 0

    # 新客户端（无 Cookie）才是真正的「未登录」；同一 TestClient 会保留上次登录的 Cookie
    assert TestClient(_make_app(), follow_redirects=False).get("/api/auth/whoami").status_code == 401
    w = client.get("/api/auth/whoami", cookies={auth.COOKIE_NAME: sid}).json()
    assert w["ok"] is True and w["remaining_seconds"] > 0


def test_public_paths_stay_open(client):
    assert client.get("/api/auth/status").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/api/health").status_code in (200, 503)


# ---------------------------------------------------------------------------
# 防爆破与部署面
# ---------------------------------------------------------------------------
def test_login_rate_limited_then_locked(client):
    codes = [client.post("/api/auth/login", json={"key": "wrong"}).status_code for _ in range(10)]
    assert codes[:5] == [401] * 5
    assert set(codes[5:]) == {429}
    assert client.post("/api/auth/login", json={"key": "wrong"}).headers.get("retry-after")


def test_ip_lockout_window_expires(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth_router._IP_FAILS.clear()
    now = time.time()
    for _ in range(auth_router._IP_FAIL_LIMIT):
        auth_router._record_ip_failure("1.2.3.4", now)
    assert auth_router._ip_locked("1.2.3.4", now) > 0
    assert auth_router._ip_locked("5.6.7.8", now) == 0
    assert auth_router._ip_locked("1.2.3.4", now + auth_router._IP_FAIL_WINDOW + 1) == 0
    auth_router._IP_FAILS.clear()


def test_ip_allowlist_blocks_outsiders(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    monkeypatch.setenv("ACCESS_IP_ALLOWLIST", "127.0.0.1,10.0.0.0/8")
    assert auth.ip_allowed("127.0.0.1") and auth.ip_allowed("10.2.3.4")
    assert not auth.ip_allowed("8.8.8.8")
    c = TestClient(_make_app(), follow_redirects=False)
    assert c.get("/api/protected").status_code == 403


def test_client_ip_ignores_forwarded_for_by_default(monkeypatch):
    from starlette.requests import Request

    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    raw = [(b"x-forwarded-for", b"8.8.8.8")]
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": raw,
                   "client": ("1.2.3.4", 1), "scheme": "http", "query_string": b""})
    assert auth.client_ip(req) == "1.2.3.4"


def test_cross_site_post_rejected(client):
    r = client.post("/api/auth/login", json={"key": KEY}, headers={"Origin": "https://evil.test"})
    assert r.status_code == 403
    assert "来源校验" in r.json()["message"]


def test_audit_log_never_contains_key(client, monkeypatch, tmp_path):
    log = tmp_path / "auth.log"
    monkeypatch.setattr(auth_router, "_AUDIT_LOG", str(log))
    client.post("/api/auth/login", json={"key": "super-secret-attempt"})
    client.post("/api/auth/login", json={"key": KEY})
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "super-secret-attempt" not in text
    assert KEY not in text
    assert "login_ok" in text and "login_failed" in text


# ---------------------------------------------------------------------------
# 长度下限可配置：部署方自定下限时，仍需挡住低熵口令
# ---------------------------------------------------------------------------
def test_min_len_configurable_accepts_12_char_mixed(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY_MIN_LEN", "12")
    monkeypatch.setenv("ACCESS_KEY", "test-key-0712-abcd")
    assert auth.min_key_len() == 12
    assert auth.key_strength_issue() is None


def test_min_len_configurable_still_rejects_sequential_digits(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY_MIN_LEN", "12")
    monkeypatch.setenv("ACCESS_KEY", "123456789012")  # 12 位但只有数字且分布单一
    assert auth.key_strength_issue() is not None


def test_min_len_default_is_32(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY_MIN_LEN", raising=False)
    assert auth.min_key_len() == 32
    monkeypatch.setenv("ACCESS_KEY", "test-key-0712-abcd")
    assert auth.key_strength_issue() is not None
