#!/usr/bin/env python3
"""DAPENG Alpha 一键运维体检。

一条命令看清「本地服务 / 看门狗 / 公网入口 / 登录鉴权 / 日志与磁盘」五件事，
输出人读摘要 + 机器可读 logs/ops_report.json，可直接塞进巡检或定时任务。

用法：
    python scripts/ops/health_report.py              # 全量体检
    python scripts/ops/health_report.py --json       # 只吐 JSON
    python scripts/ops/health_report.py --no-public  # 跳过公网探测（离线环境）

退出码：0 全绿 / 1 有 WARN / 2 有 FAIL。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
REPORT_PATH = LOG_DIR / "ops_report.json"

DEFAULT_PUBLIC_URL = "https://dapeng-alpha.app.workbuddy.host"
DEFAULT_SHARECODE = "4BHGEZEQCQ"

STATUS_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def _load_env() -> None:
    """读 .env（不覆盖已有环境变量），与看门狗保持同一套配置来源。"""
    path = PROJECT_ROOT / ".env"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("'").strip('"')


def _http_get(url: str, timeout: float = 10.0, headers: dict | None = None):
    """返回 (status, body_text, elapsed_ms, error)。"""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "dapeng-ops/1.0"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", "ignore")
            return resp.status, body, int((time.time() - started) * 1000), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(1024).decode("utf-8", "ignore")
        except Exception:
            pass
        return exc.code, body, int((time.time() - started) * 1000), None
    except Exception as exc:
        return None, "", int((time.time() - started) * 1000), f"{type(exc).__name__}: {exc}"


def _port_listening(host: str, port: int, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _port_bound(host: str, port: int) -> bool:
    """判断端口是否已被占用（用 bind 试探，不建立连接）。

    看门狗的 8899 是纯互斥守卫：只 listen 不 accept。用 connect 探测会把
    它的 accept 队列塞满（backlog=1 时第二次探测就会失败），从而误判
    「看门狗未运行」。bind 试探不产生连接，也不影响对端。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return False  # 绑得上 → 没人占用
    except OSError:
        return True
    finally:
        sock.close()


def check_local(port: int) -> dict:
    listening = _port_listening("127.0.0.1", port)
    status, body, ms, err = _http_get(f"http://127.0.0.1:{port}/api/health", timeout=8)
    ok = status == 200 and '"ok":true' in body.replace(" ", "")
    return {
        "name": "本地服务",
        "status": "ok" if ok else ("fail" if not listening else "warn"),
        "detail": f"127.0.0.1:{port} {'监听中' if listening else '未监听'} / "
                  f"{status or 'ERR'} / {ms}ms" + (f" / {err}" if err else ""),
        "port_listening": listening,
        "http_status": status,
        "latency_ms": ms,
    }


def check_watchdog(port: int) -> dict:
    guard = _port_bound("127.0.0.1", 8899)
    return {
        "name": "看门狗",
        "status": "ok" if guard else "fail",
        "detail": "单实例守卫 8899 占用中（看门狗存活）" if guard else "守卫端口 8899 空闲，看门狗未运行",
        "guard_listening": guard,
    }


def check_public(base_url: str, sharecode: str, timeout: float = 90.0) -> dict:
    """公网体检：先带 sharecode 进站拿平台 Cookie，再探健康端点。

    托管平台会校验分享码，首次必须带 ?sharecode=；后续请求靠 Cookie。
    因此这里用同一个 opener（带 CookieJar）走两步，模拟真实用户访问。
    """
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    base = base_url.rstrip("/")

    started = time.time()
    try:
        with opener.open(f"{base}/?sharecode={sharecode}", timeout=timeout) as resp:
            entry_status = resp.status
    except urllib.error.HTTPError as exc:
        return {"name": "公网入口", "status": "fail",
                "detail": f"入口 HTTP {exc.code}", "entry_status": exc.code}
    except Exception as exc:
        return {"name": "公网入口", "status": "fail",
                "detail": f"入口不可达：{type(exc).__name__}: {exc}"}
    entry_ms = int((time.time() - started) * 1000)

    try:
        with opener.open(f"{base}/api/health", timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", "ignore")
            health_status = resp.status
    except urllib.error.HTTPError as exc:
        return {"name": "公网入口", "status": "warn" if exc.code in (401, 403) else "fail",
                "detail": f"入口 {entry_status}/{entry_ms}ms，健康端点 HTTP {exc.code}",
                "entry_status": entry_status, "entry_ms": entry_ms}
    except Exception as exc:
        return {"name": "公网入口", "status": "fail",
                "detail": f"健康端点不可达：{type(exc).__name__}: {exc}"}

    ok = health_status == 200 and '"ok":true' in body.replace(" ", "")
    return {
        "name": "公网入口",
        "status": "ok" if ok else "warn",
        "detail": f"入口 {entry_status}/{entry_ms}ms，健康 {health_status}（冷启动可能 30-60s）",
        "entry_status": entry_status,
        "entry_ms": entry_ms,
        "health_status": health_status,
    }


def check_auth(port: int, public_base: str | None = None) -> dict:
    """确认鉴权闸门是「开」的——这是系统最重要的一条安全不变量。"""
    target = f"http://127.0.0.1:{port}/api/auth/status"
    status, body, _, err = _http_get(target, timeout=6)
    if status != 200:
        return {"name": "鉴权闸门", "status": "warn", "detail": f"状态端点 {status or err}"}
    try:
        data = json.loads(body)
    except ValueError:
        return {"name": "鉴权闸门", "status": "warn", "detail": "状态返回非 JSON"}

    required = data.get("required")
    authed = data.get("authenticated")
    if required is False:
        return {"name": "鉴权闸门", "status": "fail",
                "detail": "REQUIRE_ACCESS_KEY 未开启——服务处于免密状态", "data": data}
    return {
        "name": "鉴权闸门",
        "status": "ok",
        "detail": f"已开启（当前会话{'已登录' if authed else '未登录'}）",
        "data": {k: data.get(k) for k in ("required", "authenticated", "remaining", "idle_left")},
    }


def check_logs() -> dict:
    """日志体量与轮转情况：单文件 > 20MB 说明轮转没生效。"""
    items = []
    biggest = 0
    for path in sorted(LOG_DIR.glob("*.log*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        biggest = max(biggest, size)
        items.append({"file": path.name, "mb": round(size / 1024 / 1024, 2)})
    total_mb = round(sum(i["mb"] for i in items), 1)
    status = "ok" if biggest < 20 * 1024 * 1024 else "warn"
    return {
        "name": "日志",
        "status": status,
        "detail": f"共 {len(items)} 个文件 / {total_mb}MB，最大 "
                  f"{round(biggest / 1024 / 1024, 2)}MB" + ("（超过 20MB 需检查轮转）" if status == "warn" else ""),
        "files": items,
        "total_mb": total_mb,
    }


def check_disk() -> dict:
    usage = shutil.disk_usage(PROJECT_ROOT)
    free_gb = round(usage.free / 1024 ** 3, 1)
    used_pct = round(usage.used / usage.total * 100, 1)
    status = "ok" if free_gb >= 5 else ("warn" if free_gb >= 1 else "fail")
    return {
        "name": "磁盘",
        "status": status,
        "detail": f"剩余 {free_gb}GB（已用 {used_pct}%）",
        "free_gb": free_gb,
        "used_pct": used_pct,
    }


def _task_state_powershell(name: str) -> str | None:
    """用 PowerShell cmdlet 兜底查任务状态（schtasks 可能被安全策略拦截）。"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-ScheduledTask -TaskName '{name}').State"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def check_tasks() -> dict:
    """Windows 计划任务：服务自启与每日备份。非 Windows / 被策略拦截时降级为 info。"""
    if os.name != "nt":
        return {"name": "计划任务", "status": "ok", "detail": "非 Windows，跳过"}
    results = {}
    blocked = False
    for name in ("DAPENG-Alpha-Service", "DAPENG-Alpha-Backup"):
        state = None
        try:
            proc = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                state = "已注册"
            elif "拒绝访问" in (proc.stderr or "") or "Access is denied" in (proc.stderr or ""):
                blocked = True
        except Exception:
            blocked = True
        if state is None:
            ps = _task_state_powershell(name)
            if ps:
                state = f"已注册({ps})"
            elif blocked:
                results[name] = "查询受限"
                continue
            else:
                results[name] = "缺失"
                continue
        results[name] = state
    missing = [k for k, v in results.items() if v == "缺失"]
    limited = [k for k, v in results.items() if v == "查询受限"]
    if missing:
        status = "warn"
    elif limited:
        status = "ok"
    else:
        status = "ok"
    detail = "，".join(f"{k}:{v}" for k, v in results.items())
    if limited:
        detail += "（安全策略拦截查询，不计为故障）"
    return {
        "name": "计划任务",
        "status": status,
        "detail": detail,
        "tasks": results,
    }


def run(port: int, include_public: bool, public_url: str, sharecode: str) -> dict:
    checks = [
        check_local(port),
        check_watchdog(port),
        check_auth(port),
        check_logs(),
        check_disk(),
        check_tasks(),
    ]
    if include_public:
        checks.insert(2, check_public(public_url, sharecode))

    worst = max((STATUS_ORDER[c["status"]] for c in checks), default=0)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "version": "1.0",
        "overall": {0: "ok", 1: "warn", 2: "fail"}[worst],
        "checks": checks,
    }


def _emit(text: str) -> None:
    """安全输出：pythonw.exe 下 sys.stdout 为 None，计划任务里不能因此崩掉。"""
    if sys.stdout is None:
        return
    try:
        print(text)
    except Exception:
        pass


def _print_human(report: dict) -> None:
    icons = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    _emit(f"\nDAPENG Alpha 运维体检  {report['generated_at']}  总体：{report['overall'].upper()}\n")
    _emit("-" * 72)
    for item in report["checks"]:
        _emit(f"{icons[item['status']]} {item['name']:<6} {item['detail']}")
    _emit("-" * 72)
    _emit(f"报告已写入 {REPORT_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="DAPENG Alpha 一键运维体检")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--no-public", action="store_true", help="跳过公网探测")
    parser.add_argument("--public-url", default=os.getenv("PUBLIC_URL", DEFAULT_PUBLIC_URL))
    parser.add_argument("--sharecode", default=os.getenv("PUBLIC_SHARECODE", DEFAULT_SHARECODE))
    args = parser.parse_args()

    _load_env()
    port = int((os.getenv("PORT") or "8900").strip() or 8900)

    report = run(port, not args.no_public, args.public_url, args.sharecode)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        _emit(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return STATUS_ORDER[report["overall"]]


if __name__ == "__main__":
    sys.exit(main())
