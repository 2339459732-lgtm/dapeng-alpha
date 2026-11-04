"""运维体检与看门狗心跳的单元测试。

覆盖两处易错逻辑：
1. 守卫端口占用判定必须用 bind 试探——用 connect 会塞满 listen(1) 队列，
   第二次探测就误判「看门狗已死」（真实踩过的坑）；
2. 心跳探测只认 HTTP 200 + JSON ok=true，异常页 / 5xx 一律算失败。
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# 看门狗导入时会读 .env 写 os.environ，测试前后做快照还原，避免污染其他用例
_env_snapshot = dict(os.environ)
wd = _load("dapeng_watchdog_under_test", "scripts/server_watchdog.py")
os.environ.clear()
os.environ.update(_env_snapshot)

ops = _load("dapeng_ops_health", "scripts/ops/health_report.py")


# ---------------------------------------------------------------------------
# 端口占用判定
# ---------------------------------------------------------------------------
def test_port_bound_true_when_listening():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert ops._port_bound("127.0.0.1", port) is True
    finally:
        srv.close()


def test_port_bound_false_when_free():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert ops._port_bound("127.0.0.1", port) is False


def test_port_bound_survives_repeat_probes():
    """重复探测不能像 connect 那样把 accept 队列塞满后自相矛盾。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        results = [ops._port_bound("127.0.0.1", port) for _ in range(5)]
        assert all(results)
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# 心跳探测
# ---------------------------------------------------------------------------
class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"ok": true, "version": "4.0"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


class _HtmlHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html>gateway error</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Server:
    def __init__(self, handler):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_probe_health_ok(monkeypatch):
    with _Server(_OkHandler) as srv:
        monkeypatch.setattr(wd, "PORT", srv.port)
        ok, detail = wd._probe_health(timeout=3)
    assert ok is True
    assert detail == "ok"


def test_probe_health_rejects_html_page(monkeypatch):
    """命中的是异常页（HTML）时必须判失败，不能因为 200 就放行。"""
    with _Server(_HtmlHandler) as srv:
        monkeypatch.setattr(wd, "PORT", srv.port)
        ok, detail = wd._probe_health(timeout=3)
    assert ok is False
    assert "非 JSON" in detail


def test_probe_health_connection_refused(monkeypatch):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    monkeypatch.setattr(wd, "PORT", port)
    ok, _ = wd._probe_health(timeout=2)
    assert ok is False


# ---------------------------------------------------------------------------
# 体检报告结构
# ---------------------------------------------------------------------------
def test_check_logs_reports_size():
    result = ops.check_logs()
    assert result["name"] == "日志"
    assert result["status"] in ("ok", "warn")
    assert isinstance(result["total_mb"], float)


def test_check_disk_fields():
    result = ops.check_disk()
    assert result["status"] in ("ok", "warn", "fail")
    assert result["free_gb"] > 0


def test_run_overall_takes_worst_status(monkeypatch):
    monkeypatch.setattr(ops, "check_local", lambda port: {"name": "本地服务", "status": "ok", "detail": ""})
    monkeypatch.setattr(ops, "check_watchdog", lambda port: {"name": "看门狗", "status": "warn", "detail": ""})
    monkeypatch.setattr(ops, "check_auth", lambda port: {"name": "鉴权闸门", "status": "fail", "detail": ""})
    monkeypatch.setattr(ops, "check_logs", lambda: {"name": "日志", "status": "ok", "detail": ""})
    monkeypatch.setattr(ops, "check_disk", lambda: {"name": "磁盘", "status": "ok", "detail": ""})
    monkeypatch.setattr(ops, "check_tasks", lambda: {"name": "计划任务", "status": "ok", "detail": ""})
    report = ops.run(8900, include_public=False, public_url="http://x", sharecode="y")
    assert report["overall"] == "fail"
    assert len(report["checks"]) == 6
