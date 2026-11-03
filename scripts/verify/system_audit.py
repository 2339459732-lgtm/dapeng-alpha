#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""系统体检（system-audit.v1）：服务 / 配置 / 上游连接 / 缓存 / 安全姿态。

为什么要有这个脚本
------------------
「都正常」不该靠印象。这些检查项分布在进程、文件、上游、HTTP 响应头里，
人工逐项核对一次要十几分钟且容易漏；脚本化之后可以随时复跑，也能当发布前闸门。

判定分三级：
    PASS 明确通过
    WARN 可用但有代价（降级、代理、未配置项），不阻断
    FAIL 应当立即处理

用法
----
    python scripts/verify/system_audit.py              # 报告
    python scripts/verify/system_audit.py --gate       # 出现 FAIL 则退出码 1
    python scripts/verify/system_audit.py --json r.json
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from _auth import auth_headers, load_key

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:8900"
_UA = {"User-Agent": "DapengAudit/1.0", "Accept": "application/json"}

RESULTS: List[Dict[str, Any]] = []


#: 登录态 Cookie：开启 ACCESS_KEY 的实例上，审计接口同样需要鉴权
_AUTH: Dict[str, str] = {}


def record(area: str, item: str, status: str, detail: str) -> None:
    RESULTS.append({"area": area, "item": item, "status": status, "detail": detail})


def _get(path: str, timeout: float = 20.0, headers: Optional[Dict[str, str]] = None) -> Tuple[int, bytes, Dict[str, str]]:
    req = urllib.request.Request(BASE + path, headers={**_UA, **_AUTH, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b"", dict(exc.headers or {})
    except Exception as exc:  # 连接被拒/超时
        return 0, str(exc).encode(), {}


def _json(path: str, timeout: float = 20.0) -> Tuple[int, Any]:
    code, body, _ = _get(path, timeout)
    try:
        return code, json.loads(body or b"{}")
    except Exception:
        return code, None


# ---------------------------------------------------------------------------
# 1. 服务与端口
# ---------------------------------------------------------------------------
def check_services() -> None:
    listeners: Dict[int, List[Tuple[str, int]]] = {}
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status == "LISTEN" and conn.laddr and conn.laddr.port in (8900, 8899):
                listeners.setdefault(conn.laddr.port, []).append((conn.laddr.ip, conn.pid))
    except Exception as exc:
        record("服务", "端口探测", "WARN", f"psutil 不可用：{exc}")
    for port, label in ((8900, "主服务"), (8899, "看门狗")):
        items = listeners.get(port) or []
        if not items:
            record("服务", f"{label} :{port}", "FAIL", "未监听")
            continue
        addrs = {ip for ip, _ in items}
        exposed = addrs - {"127.0.0.1", "::1"}
        if exposed:
            # 非回环监听本身不是缺陷：对外开放是部署选择。判级取决于**是否已加锁**：
            # 有访问密钥 + IP 白名单 → WARN（可接受）；无密钥 → FAIL（谁都能进）。
            # 这些值通常只在 .env 里，未注入进程环境；用 load_key + 读取 .env 兜底
            key = (load_key() or "").strip()
            allow = (os.getenv("ACCESS_IP_ALLOWLIST") or "").strip()
            if not allow:
                try:
                    from pathlib import Path as _Path
                    for _line in (_Path(__file__).resolve().parents[2] / ".env").read_text(
                            encoding="utf-8", errors="ignore").splitlines():
                        if _line.strip().startswith("ACCESS_IP_ALLOWLIST="):
                            allow = _line.split("=", 1)[1].strip()
                            break
                except OSError:
                    pass
            if key and allow:
                record("服务", f"{label} :{port}", "WARN",
                       f"对外开放 {sorted(exposed)}，已有私钥 + IP 白名单 {allow}")
            elif key:
                record("服务", f"{label} :{port}", "WARN",
                       f"对外开放 {sorted(exposed)}，有私钥但未设 IP 白名单")
            else:
                record("服务", f"{label} :{port}", "FAIL",
                       f"对外开放 {sorted(exposed)} 且未配置访问密钥")
        else:
            record("服务", f"{label} :{port}", "PASS", f"监听 127.0.0.1，pid={items[0][1]}")

    started = time.perf_counter()
    code, _ = _json("/api/health", timeout=10)
    cost = round((time.perf_counter() - started) * 1000, 1)
    record("服务", "健康检查", "PASS" if code == 200 else "FAIL",
           f"HTTP {code} · {cost}ms")


# ---------------------------------------------------------------------------
# 2. 配置文件
# ---------------------------------------------------------------------------
def check_configs() -> None:
    required = {
        "data/brain_config.json": ("version",),
        "data/decision_config.json": (),
    }
    for rel, keys in required.items():
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            record("配置", rel, "FAIL", "文件缺失")
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            record("配置", rel, "FAIL", f"JSON 解析失败：{exc}")
            continue
        missing = [k for k in keys if k not in payload]
        record("配置", rel, "PASS" if not missing else "FAIL",
               f"可解析 · {os.path.getsize(path)} bytes" + (f" · 缺 {missing}" if missing else ""))

    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        keys = {}
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
        llm = keys.get("DEEPSEEK_API_KEY", "")
        record("配置", ".env LLM Key", "PASS" if llm else "WARN",
               "已配置（不回显）" if llm else "未配置：AI 研判将走规则降级")
        admin = keys.get("ADMIN_TOKEN", "")
        record("配置", ".env ADMIN_TOKEN", "PASS" if admin else "WARN",
               "已配置，管理接口需令牌" if admin else "未配置：管理接口仅限本机回环访问")
    else:
        record("配置", ".env", "WARN", "文件不存在，依赖环境变量")

    code, payload = _json("/api/brain/config")
    if code == 200 and isinstance(payload, dict):
        val = payload.get("validation") or {}
        record("配置", "brain-config 运行期校验", "PASS" if val.get("ok") else "WARN",
               f"issues={len(val.get('issues') or [])} warnings={len(val.get('warnings') or [])} "
               f"sha={((payload.get('file') or {}).get('sha256') or '-')}")
    else:
        record("配置", "brain-config 运行期校验", "FAIL", f"HTTP {code}")


# ---------------------------------------------------------------------------
# 3. 上游连接
# ---------------------------------------------------------------------------
def _probe(name: str, url: str, timeout: float = 10.0, expect: str = "") -> None:
    started = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(4000).decode("utf-8", "replace")
            code = resp.status
    except Exception as exc:
        record("上游", name, "WARN", f"不可达：{type(exc).__name__}")
        return
    cost = round((time.perf_counter() - started) * 1000, 1)
    ok = code == 200 and (expect in body if expect else True)
    record("上游", name, "PASS" if ok else "WARN",
           f"HTTP {code} · {cost}ms" + ("" if ok else " · 返回内容不符合预期"))


def check_upstreams() -> None:
    _probe("新浪全市场计数",
           "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodeStockCount?node=hs_a", expect='"')
    _probe("东财北交所列表",
           "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&fltt=2&invt=2"
           "&fid=f12&fs=m:0+t:81+s:2048&fields=f12,f14,f2", expect="data")
    _probe("腾讯行情", "https://qt.gtimg.cn/q=sh600519", expect="v_sh600519")
    def _tcp(name: str, host: str, port: int = 443) -> None:
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=6):
                cost = round((time.perf_counter() - started) * 1000, 1)
            record("上游", name, "PASS", f"TCP {host}:{port} · {cost}ms")
        except Exception as exc:
            record("上游", name, "WARN", f"不可达：{type(exc).__name__}")

    _tcp("LLM 端点", "api.deepseek.com")
    _probe("东财基本面",
           "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
           "&columns=SECURITY_CODE&pageNumber=1&pageSize=2&source=WEB&client=WEB", expect="result")


# ---------------------------------------------------------------------------
# 4. 缓存
# ---------------------------------------------------------------------------
def check_cache() -> None:
    code, payload = _json("/api/decision/cache")
    if code != 200 or not isinstance(payload, dict):
        record("缓存", "统计接口", "FAIL", f"HTTP {code}")
        return
    data = payload.get("cache") or {}
    counted = data.get("bytes")
    recomputed = data.get("recomputed_bytes")
    consistent = counted is not None and recomputed is not None and counted == recomputed
    record("缓存", "账实一致", "PASS" if consistent else "FAIL",
           f"累计 {counted} B / 实算 {recomputed} B")
    record("缓存", "容量水位", "PASS" if (data.get("bytes_utilization") or 0) < 0.85 else "WARN",
           f"{data.get('size')}/{data.get('max_size')} 条 · "
           f"{round((data.get('bytes_utilization') or 0) * 100, 1)}%")
    # 少量过期条目在「上一次读取」到「下一次后台回收」之间是正常现象（回收周期 30s），
    # 只有堆积明显（>5% 且 >2 条）才值得告警，否则这条检查会天天喊狼来了。
    expired_now = int(data.get("expired_now") or 0)
    size = int(data.get("size") or 0)
    tolerance = max(2, int(size * 0.05))
    record("缓存", "过期堆积", "PASS" if expired_now <= tolerance else "WARN",
           f"expired_now={expired_now}（容忍 {tolerance}） · 后台回收运行="
           f"{data.get('cleanup_task_running')}")
    hit = data.get("hit_rate")
    record("缓存", "命中率", "PASS" if (hit is None or hit >= 0.3) else "WARN",
           f"{(round(hit * 100, 1) if hit is not None else '—')}%")

    code, bus = _json("/api/decision/bus")
    if code == 200 and isinstance(bus, dict):
        chain = (bus.get("data") or bus).get("chain") or []
        stale = [c["key"] for c in chain if not c.get("fresh")]
        record("缓存", "链路新鲜度", "PASS" if not stale else "WARN",
               f"{len(chain) - len(stale)}/{len(chain)} fresh" + (f" · 陈旧 {stale}" if stale else ""))
        no_ms = [c["key"] for c in chain if c.get("last_ms") is None]
        record("缓存", "耗时可观测", "PASS" if not no_ms else "WARN",
               "全部有耗时" if not no_ms else f"未上报耗时 {no_ms}")


# ---------------------------------------------------------------------------
# 4.5 存储与一致性
# ---------------------------------------------------------------------------
def check_storage() -> None:
    import sqlite3
    import tempfile

    probe = os.path.join(ROOT, "data", ".audit_probe")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        record("存储", "data 目录可写", "PASS", "写入/删除探测通过")
    except Exception as exc:
        record("存储", "data 目录可写", "FAIL", f"{type(exc).__name__}: {exc}")

    for name in ("api_calls.db", "trading.db", "market.db", "users.db"):
        path = os.path.join(ROOT, "data", name)
        if not os.path.exists(path):
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                status = conn.execute("PRAGMA quick_check").fetchone()
            finally:
                conn.close()
            ok = bool(status) and str(status[0]).lower() == "ok"
            record("存储", f"SQLite {name}", "PASS" if ok else "FAIL",
                   f"quick_check={status[0] if status else '无返回'} · {os.path.getsize(path)} bytes")
        except Exception as exc:
            record("存储", f"SQLite {name}", "FAIL", f"{type(exc).__name__}: {exc}")

    # 关键落盘文件：存在性与最近修改时间（能判断后台任务是否还在写）
    for rel, label in (("data/market_coverage_latest.json", "全市场快照"),
                       ("data/lineage.json", "结论溯源环"),
                       ("logs/watchdog.log", "看门狗日志")):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            record("存储", label, "WARN", "文件不存在")
            continue
        age_min = round((time.time() - os.path.getmtime(path)) / 60, 1)
        record("存储", label, "PASS" if age_min < 60 else "WARN",
               f"{age_min} 分钟前更新 · {os.path.getsize(path)} bytes")


def check_consistency() -> None:
    """响应一致性：抽查各层端点，信封字段是否统一。

    「响应一致」不是风格问题：前端与脚本都按 ok/version 判断，
    少一个字段就会变成「接口成功但读不到数据」的静默故障。
    """
    endpoints = [
        ("/api/market-dashboard", ("ok",)),
        ("/api/decision/snapshot", ("ok", "version", "trace_id", "as_of")),
        ("/api/decision/sentiment", ("ok", "version", "trace_id")),
        ("/api/decision/sentiment/flow", ("ok", "version", "trace_id")),
        ("/api/intelligent/market/survey", ("ok", "version", "trace_id", "as_of")),
        ("/api/decision/experts", ("ok", "version", "trace_id")),
        ("/api/brain/alignment?light=true", ("ok", "version")),
        ("/api/brain/config", ("ok", "version")),
        # 龙虎榜复盘：尚未生成时也返回 ok:false + version + message，前端据此提示而非白屏
        ("/api/decision/lhb-review", ("ok", "version")),
    ]
    missing_all = []
    for path, keys in endpoints:
        code, payload = _json(path, timeout=60)
        if not isinstance(payload, dict):
            missing_all.append(f"{path}(HTTP {code})")
            continue
        body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        missing = [k for k in keys if k not in body and k not in payload]
        if missing:
            missing_all.append(f"{path} 缺 {missing}")
    record("一致性", "信封字段", "PASS" if not missing_all else "WARN",
           f"{len(endpoints)} 个端点信封齐备" if not missing_all else "；".join(missing_all[:3]))


# ---------------------------------------------------------------------------
# 4.6 决策分流
# ---------------------------------------------------------------------------
def check_routing() -> None:
    code, payload = _json("/api/decision/routing", timeout=90)
    if code != 200 or not isinstance(payload, dict):
        record("分流", "分流接口", "FAIL", f"HTTP {code}")
        return
    cfg = payload.get("config") or {}
    record("分流", "规则配置", "PASS" if cfg.get("ok") else "FAIL",
           f"routes={cfg.get('routes')} issues={len(cfg.get('issues') or [])}")
    coverage = payload.get("coverage")
    degraded = bool(payload.get("degraded"))
    record("分流", "指标覆盖", "PASS" if (coverage or 0) >= 0.5 else "WARN",
           f"{round((coverage or 0) * 100, 1)}% · 不可用 {payload.get('unavailable')}")
    record("分流", "当前路由", "PASS" if payload.get("route_id") else "FAIL",
           f"{payload.get('route_id')}「{payload.get('label')}」cap="
           f"{(payload.get('action') or {}).get('cap_max')}"
           + ("（降级）" if degraded else ""))
    if degraded:
        record("分流", "降级原因", "WARN", "；".join(payload.get("degraded_reasons") or []))

    code_m, metrics = _json("/api/decision/metrics", timeout=90)
    if code_m == 200 and isinstance(metrics, dict):
        bought = [m for m in metrics.get("metrics") or [] if m.get("kind") == "buyratio"]
        ok_buy = any(m.get("available") for m in bought)
        record("分流", "买手率可用", "PASS" if ok_buy else "WARN",
               "；".join(f"{m['id']}={m.get('value')}%" for m in bought if m.get("available"))
               or "买手率不可用（腾讯接口异常）")
        summary = metrics.get("summary") or {}
        record("分流", "指标采集耗时", "PASS" if (summary.get("elapsed_ms") or 0) < 8000 else "WARN",
               f"{summary.get('elapsed_ms')}ms · 可用 {summary.get('available')}/{summary.get('total')}")
    else:
        record("分流", "指标接口", "FAIL", f"HTTP {code_m}")


# ---------------------------------------------------------------------------
# 5. 安全姿态
# ---------------------------------------------------------------------------
def check_security() -> None:
    code, _, headers = _get("/api/health")
    lowered = {k.lower(): v for k, v in headers.items()}
    for name, key in (("X-Content-Type-Options", "x-content-type-options"),
                      ("X-Frame-Options", "x-frame-options"),
                      ("Referrer-Policy", "referrer-policy")):
        record("安全", f"响应头 {name}", "PASS" if lowered.get(key) else "FAIL",
               lowered.get(key) or "缺失")

    # DNS 重绑定：伪造 Host 头必须被拒
    code_evil, _, _ = _get("/api/health", headers={"Host": "evil.example.com"})
    record("安全", "Host 头校验", "PASS" if code_evil == 400 else "FAIL",
           f"伪造 Host 返回 {code_evil}（期望 400）")

    # CORS：非白名单来源不得拿到 ACAO
    code_cors, _, hdrs = _get("/api/health", headers={"Origin": "https://evil.example.com"})
    acao = {k.lower(): v for k, v in hdrs.items()}.get("access-control-allow-origin")
    record("安全", "CORS 白名单", "PASS" if acao in (None, "") else "FAIL",
           f"ACAO={acao or '未返回'}")

    # 历史备份文件不得可通过 Web 访问
    code_bak, _, _ = _get("/dashboard.html.bak-v85")
    record("安全", "备份文件不可达", "PASS" if code_bak == 404 else "FAIL",
           f"HTTP {code_bak}")

    # 管理接口：无令牌时仅回环可用（本机请求应 200，但不泄露敏感字段）
    code_admin, admin = _json("/api/admin/users?page=1&size=1")
    if code_admin == 200 and isinstance(admin, dict):
        items = ((admin.get("data") or {}).get("items") or [])
        phone = (items[0] or {}).get("guardian_phone") if items else ""
        masked = (not phone) or ("*" in str(phone))
        record("安全", "用户信息脱敏", "PASS" if masked else "FAIL",
               "手机号已脱敏" if masked else "列表返回了完整手机号")
    else:
        record("安全", "用户信息脱敏", "PASS", f"管理接口未开放（HTTP {code_admin}），无法经 HTTP 取到用户信息")

    # 接口文档暴露面（本机工具可接受，但要显式记录）
    code_docs, _, _ = _get("/openapi.json")
    record("安全", "OpenAPI 暴露", "WARN" if code_docs == 200 else "PASS",
           "本机可达（本地工具可接受，公网部署需关闭）")


def render(json_path: Optional[str]) -> int:
    icons = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}
    by_area: Dict[str, List[Dict[str, Any]]] = {}
    for row in RESULTS:
        by_area.setdefault(row["area"], []).append(row)
    for area, rows in by_area.items():
        print(f"\n== {area} ==")
        for row in rows:
            print(f"  [{icons[row['status']]:<4}] {row['item']:<24} {row['detail']}")
    total = len(RESULTS)
    counts = {k: sum(1 for r in RESULTS if r["status"] == k) for k in ("PASS", "WARN", "FAIL")}
    print(f"\n合计 {total} 项：PASS {counts['PASS']} · WARN {counts['WARN']} · FAIL {counts['FAIL']}")
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"version": "system-audit.v1",
                       "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "counts": counts, "results": RESULTS}, fh, ensure_ascii=False, indent=2)
        print(f"明细写入 {json_path}")
    return counts["FAIL"]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="系统体检")
    parser.add_argument("--gate", action="store_true", help="有 FAIL 则退出码 1")
    parser.add_argument("--json", help="把明细写入文件")
    args = parser.parse_args(argv)

    # 先登录（未配置密钥时返回空，行为不变）
    _AUTH.update(auth_headers(BASE))

    check_services()
    check_configs()
    check_upstreams()
    check_cache()
    check_routing()
    check_storage()
    check_consistency()
    check_security()
    fails = render(args.json)
    if fails and args.gate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
