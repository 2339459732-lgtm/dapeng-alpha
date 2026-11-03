"""访问密钥鉴权（access-key.v1）：签名、校验、中间件、登录限流。

守的是「开源后实例不被白嫖」这件事：

1. **签名必须不可伪造**：改一个字节、改过期时间、换版本前缀，都必须失效；
2. **轮换密钥即刻踢人**：签名密钥由 ACCESS_KEY 派生，换 key 后旧 Cookie 全部作废；
3. **未配置时整层关闭**：本地开发、pytest、CI 不受影响（否则装上就进不去）；
4. **API 与页面的拒绝方式不同**：API 回 401 JSON，页面回 302 到 /login；
5. **登录口必须限流**：全局限流 600/min 对爆破太宽松，登录单独 10/min。
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

KEY = "test-access-key-please-change"


# ---------------------------------------------------------------------------
# 纯函数：签名与校验
# ---------------------------------------------------------------------------
def test_sign_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    token = auth.sign_session(int(time.time()) + 60)
    assert auth.verify_session(token)


def test_tampered_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    token = auth.sign_session(int(time.time()) + 60)
    head, exp, sig = token.split(".")
    forged = f"{head}.{exp}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}"
    assert not auth.verify_session(forged)


def test_expired_session_is_rejected(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    token = auth.sign_session(int(time.time()) - 1)
    assert not auth.verify_session(token)


def test_wrong_version_prefix_is_rejected(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    token = auth.sign_session(int(time.time()) + 60)
    assert not auth.verify_session("v2" + token[2:])


def test_malformed_tokens_are_rejected(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    for bad in ("", "a", "a.b", "v1.abc.def", "v1..", None):
        assert not auth.verify_session(bad)


def test_rotating_access_key_invalidates_sessions(monkeypatch):
    """换口令 = 全部下线：签名密钥由 ACCESS_KEY 派生，这正是预期行为。"""
    monkeypatch.setenv("ACCESS_KEY", KEY)
    token = auth.sign_session(int(time.time()) + 60)
    monkeypatch.setenv("ACCESS_KEY", KEY + "-rotated")
    assert not auth.verify_session(token)


def test_session_secret_override_keeps_sessions_alive(monkeypatch):
    """显式设置 SESSION_SECRET 后，轮换 ACCESS_KEY 不会作废旧会话。"""
    monkeypatch.setenv("SESSION_SECRET", "fixed-secret-for-tests")
    monkeypatch.setenv("ACCESS_KEY", "key-a")
    token = auth.sign_session(int(time.time()) + 60)
    monkeypatch.setenv("ACCESS_KEY", "key-b")
    assert auth.verify_session(token)


def test_check_key_compares_exactly(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    assert auth.check_key(KEY)
    assert not auth.check_key(KEY + " ")
    assert not auth.check_key("")
    assert not auth.check_key(None)


def test_auth_disabled_when_key_absent(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("DAPENG_ACCESS_KEY", raising=False)
    assert auth.access_key() is None
    assert auth.auth_enabled() is False
    assert not auth.check_key("anything")


def test_ttl_is_clamped(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_HOURS", "0.01")   # 下限 1 小时
    assert auth.ttl_seconds() == 3600
    monkeypatch.setenv("SESSION_TTL_HOURS", "99999")  # 上限 30 天
    assert auth.ttl_seconds() == 720 * 3600
    monkeypatch.setenv("SESSION_TTL_HOURS", "nonsense")
    assert auth.ttl_seconds() == auth.DEFAULT_TTL_HOURS * 3600


# ---------------------------------------------------------------------------
# 中间件：拒绝方式与放行
# ---------------------------------------------------------------------------
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

    return app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth_router._LOGIN_LIMITER._hits.clear()  # 每个用例从干净窗口开始
    auth_router._FAILS.clear()
    auth_router._IP_FAILS.clear()
    return TestClient(_make_app(), follow_redirects=False)


def test_api_without_cookie_returns_401(client):
    r = client.get("/api/protected")
    assert r.status_code == 401
    assert r.json()["ok"] is False
    assert r.headers.get("www-authenticate") == "AccessKey"


def test_page_without_cookie_redirects_to_login(client):
    r = client.get("/dashboard.html")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=")
    assert "dashboard.html" in r.headers["location"]


def test_public_paths_stay_open(client):
    assert client.get("/api/auth/status").status_code == 200
    r = client.get("/login")
    assert r.status_code == 200
    assert "访问验证" in r.text


def test_login_wrong_key_is_401_and_sets_no_cookie(client):
    r = client.post("/api/auth/login", json={"key": "wrong"})
    assert r.status_code == 401
    assert r.json()["message"] == "密钥不正确"
    assert auth.COOKIE_NAME not in r.cookies


def test_login_right_key_sets_httponly_cookie_and_grants_access(client):
    r = client.post("/api/auth/login", json={"key": KEY})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert auth.COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.replace("samesite", "SameSite")

    cookie = r.cookies.get(auth.COOKIE_NAME)
    assert cookie and auth.verify_session(cookie)
    assert client.get("/api/protected", cookies={auth.COOKIE_NAME: cookie}).status_code == 200
    assert client.get("/dashboard.html", cookies={auth.COOKIE_NAME: cookie}).status_code == 200


def test_logout_clears_cookie(client):
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert "Max-Age=0" in r.headers.get("set-cookie", "") or "max-age=0" in r.headers.get("set-cookie", "").lower()


def test_status_reflects_enabled_and_authenticated(client):
    d = client.get("/api/auth/status").json()
    assert d["enabled"] is True and d["authenticated"] is False

    r = client.post("/api/auth/login", json={"key": KEY})
    cookie = r.cookies.get(auth.COOKIE_NAME)
    d2 = client.get("/api/auth/status", cookies={auth.COOKIE_NAME: cookie}).json()
    assert d2["authenticated"] is True


def test_whoami_reports_expiry(client):
    assert client.get("/api/auth/whoami").status_code == 401
    r = client.post("/api/auth/login", json={"key": KEY})
    cookie = r.cookies.get(auth.COOKIE_NAME)
    d = client.get("/api/auth/whoami", cookies={auth.COOKIE_NAME: cookie}).json()
    assert d["ok"] is True and d["expires_at"] > time.time()


def test_login_is_rate_limited(client):
    """连续失败必须被挡：前 5 次 401，之后进入 IP 锁定（429）。

    两层防线叠加：频率限制 10 次/分钟挡快打，单 IP 锁定（5 次/15 分钟）
    挡慢试——6 位口令只有 100 万组合，不限总次数就会被穷举。
    """
    codes = [client.post("/api/auth/login", json={"key": "wrong"}).status_code for _ in range(10)]
    assert codes[:5] == [401] * 5
    assert set(codes[5:]) == {429}
    r = client.post("/api/auth/login", json={"key": "wrong"})
    assert r.status_code == 429
    assert r.headers.get("retry-after")


def test_disabled_auth_lets_everything_through(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("DAPENG_ACCESS_KEY", raising=False)
    app = _make_app()
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/protected").status_code == 200
    assert c.get("/dashboard.html").status_code == 200
    d = c.get("/api/auth/status").json()
    assert d["enabled"] is False and d["authenticated"] is True


def test_login_when_disabled_returns_400(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("DAPENG_ACCESS_KEY", raising=False)
    c = TestClient(_make_app(), follow_redirects=False)
    r = c.post("/api/auth/login", json={"key": "x"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 部署加固：真实 IP / 来源校验 / 强度检查 / 全局失败闸门
# ---------------------------------------------------------------------------
def _req(headers=None, peer="1.2.3.4", method="GET"):
    from starlette.requests import Request

    raw = [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": method, "path": "/", "headers": raw,
                    "client": (peer, 12345), "scheme": "http", "query_string": b""})


def test_client_ip_ignores_forwarded_for_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    req = _req({"X-Forwarded-For": "8.8.8.8"})
    assert auth.client_ip(req) == "1.2.3.4"  # 不可伪造 XFF 绕过限流


def test_client_ip_uses_forwarded_for_behind_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    req = _req({"X-Forwarded-For": "8.8.8.8, 10.0.0.1"})
    assert auth.client_ip(req) == "8.8.8.8"


def test_client_ip_trusted_proxy_peer_list(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,10.0.0.1")
    assert auth.client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, peer="127.0.0.1")) == "9.9.9.9"
    assert auth.client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, peer="5.5.5.5")) == "5.5.5.5"


def test_origin_allowed_rules(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    # 安全方法不校验
    assert auth.origin_allowed(_req(method="GET", headers={"Origin": "https://evil.test"}))
    # 无 Origin（curl / 服务端回调）放行
    assert auth.origin_allowed(_req(method="POST"))
    # 同源放行
    assert auth.origin_allowed(_req(method="POST", headers={"Origin": "http://testserver", "Host": "testserver"}))
    # 跨站写请求拒绝
    assert not auth.origin_allowed(_req(method="POST", headers={"Origin": "https://evil.test", "Host": "testserver"}))
    # 白名单放行
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://dapeng.example.com")
    assert auth.origin_allowed(_req(method="POST", headers={"Origin": "https://dapeng.example.com", "Host": "x"}))


def test_cross_site_post_is_rejected_by_middleware(client):
    r = client.post("/api/auth/login", json={"key": KEY},
                    headers={"Origin": "https://evil.test"})
    assert r.status_code == 403
    assert "来源校验" in r.json()["message"]


def test_key_strength_issue(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "short")
    assert auth.key_strength_issue() and "字符" in auth.key_strength_issue()
    monkeypatch.setenv("ACCESS_KEY", "x" * 32)
    assert auth.key_strength_issue() is None
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    assert auth.key_strength_issue() is None


def test_global_failure_gate_blocks_after_burst(monkeypatch):
    """超过全局限额后，即使换 IP 也暂停登录（分布式爆破兜底）。"""
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth_router._FAILS.clear()
    from server.infra import auth as a
    # 直接灌满失败窗口，验证闸门逻辑（避免 60 次真实请求拖慢用例）
    now = time.time()
    for _ in range(auth_router._GLOBAL_FAIL_LIMIT):
        auth_router._record_failure(now)
    assert auth_router._global_locked(now) > 0
    # 窗口过期后自动解锁
    assert auth_router._global_locked(now + auth_router._GLOBAL_FAIL_WINDOW + 1) == 0
    auth_router._FAILS.clear()


def test_audit_log_never_contains_key(client, monkeypatch, tmp_path):
    """审计日志只记 IP 与结果，绝不能出现密钥明文。"""
    log = tmp_path / "auth.log"
    monkeypatch.setattr(auth_router, "_AUDIT_LOG", str(log))
    client.post("/api/auth/login", json={"key": "super-secret-attempt"})
    client.post("/api/auth/login", json={"key": KEY})
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "super-secret-attempt" not in text
    assert KEY not in text
    assert "login_ok" in text and "login_failed" in text


# ---------------------------------------------------------------------------
# 口令边界：IP 白名单 / 单 IP 渐进锁定 / 弱口令会话缩短
# ---------------------------------------------------------------------------
def test_ip_allowlist_blocks_outsiders(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    monkeypatch.setenv("ACCESS_IP_ALLOWLIST", "127.0.0.1,10.0.0.0/8")
    assert auth.ip_allowed("127.0.0.1") is True
    assert auth.ip_allowed("10.2.3.4") is True
    assert auth.ip_allowed("8.8.8.8") is False
    c = TestClient(_make_app(), follow_redirects=False)  # 对端是 "testclient"
    r = c.get("/api/protected")
    assert r.status_code == 403
    assert "白名单" in r.json()["message"]


def test_ip_allowlist_empty_means_open(monkeypatch):
    monkeypatch.delenv("ACCESS_IP_ALLOWLIST", raising=False)
    assert auth.allowed_ips() == []
    assert auth.ip_allowed("8.8.8.8") is True


def test_weak_key_shortens_session(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "071215")
    monkeypatch.setenv("SESSION_TTL_HOURS", "72")
    assert auth.ttl_seconds() == 8 * 3600


def test_strong_key_keeps_configured_ttl(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "x" * 40)
    monkeypatch.setenv("SESSION_TTL_HOURS", "24")
    assert auth.ttl_seconds() == 24 * 3600


def test_ip_lockout_after_repeated_failures(monkeypatch):
    """15 分钟内错 5 次锁 15 分钟：频率限制挡快打，锁定挡慢试。"""
    monkeypatch.setenv("ACCESS_IP_ALLOWLIST", "")
    auth_router._FAILS.clear()
    auth_router._IP_FAILS.clear()
    auth_router._LOGIN_LIMITER._hits.clear()
    c = TestClient(_make_app(), follow_redirects=False)
    now = time.time()
    for _ in range(auth_router._IP_FAIL_LIMIT):
        auth_router._record_ip_failure("1.2.3.4", now)
    assert auth_router._ip_locked("1.2.3.4", now) > 0
    # 其他 IP 不受影响
    assert auth_router._ip_locked("5.6.7.8", now) == 0
    # 窗口过期自动解锁
    assert auth_router._ip_locked("1.2.3.4", now + auth_router._IP_FAIL_WINDOW + 1) == 0
    auth_router._IP_FAILS.clear()


def test_login_clears_ip_failures_on_success(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", KEY)
    auth_router._IP_FAILS.clear()
    auth_router._LOGIN_LIMITER._hits.clear()
    c = TestClient(_make_app(), follow_redirects=False)
    c.post("/api/auth/login", json={"key": "wrong"})
    assert auth_router._IP_FAILS
    r = c.post("/api/auth/login", json={"key": KEY})
    assert r.status_code == 200
    assert not auth_router._IP_FAILS
