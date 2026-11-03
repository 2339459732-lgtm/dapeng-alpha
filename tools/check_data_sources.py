#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""逐项检查 DAPENG Alpha 的数据源与连接（只读，不修改任何文件）。

用法：
    python tools/check_data_sources.py [--base http://localhost:8900]

检查项：
 1. static/docs-graph.json 存在、可解析、节点/关系数与预期一致；
 2. 三个消费端页面可访问（project-docs.html / plate-graph.html / dashboard.html / admin.html）；
 3. 图谱数据被消费端正确加载（页面内联检查不做，改查静态资源可达）；
 4. 自动化中心关键接口返回 200 且 ok=true；
 5. 复盘接口 /api/review/equity 返回 200。

退出码：全部 PASS → 0，否则 1。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def http_ok(url: str, timeout: float = 6.0) -> tuple[bool, int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(2_000_000)
            return (200 <= r.status < 400), r.status, body.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return False, e.code, str(e)
    except Exception as e:  # noqa: BLE001
        return False, 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8900")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    results: list[tuple[str, bool, str]] = []

    # 1) 图谱数据
    gp = STATIC / "docs-graph.json"
    if not gp.exists():
        results.append(("docs-graph.json 存在", False, f"缺失：{gp}"))
    else:
        try:
            data = json.loads(gp.read_text(encoding="utf-8"))
            nodes = data.get("nodes") or []
            links = data.get("links") or []
            directed = sum(1 for l in links if l.get("type") == "link")
            ok = len(nodes) >= 32 and len(links) >= 80
            results.append((
                "docs-graph.json 解析",
                ok,
                f"nodes={len(nodes)} links={len(links)}（引用 {directed} / 登记 {len(links)-directed}）",
            ))
        except Exception as e:  # noqa: BLE001
            results.append(("docs-graph.json 解析", False, repr(e)))

    # 2) 页面可达
    for page in ["project-docs.html", "plate-graph.html", "dashboard.html", "admin.html"]:
        ok, code, _ = http_ok(f"{base}/{page}")
        results.append((f"页面 {page}", ok, f"HTTP {code}"))

    # 3) 静态增强资源
    for asset in ["js/docs-graph-3d.js", "css/docs-graph-3d.css", "js/panel-consistency.js",
                  "css/panel-consistency.css", "js/admin-theme.js", "css/admin-theme.css",
                  "js/automation-cockpit.js", "css/automation-cockpit.css"]:
        ok, code, _ = http_ok(f"{base}/{asset}")
        results.append((f"资源 {asset}", ok, f"HTTP {code}"))

    # 4) 自动化中心关键接口
    for ep in [
        "/api/automation/config",
        "/api/automation/gate",
        "/api/automation/positions",
        "/api/automation/orders?limit=5",
        "/api/automation/signals?limit=5",
        "/api/automation/review/metrics?days=30",
        "/api/review/equity",
        "/api/automation/risk/health",
        "/api/automation/market-sync",
    ]:
        ok, code, body = http_ok(f"{base}{ep}")
        ok = ok and ('"ok":true' in body.replace(" ", "") or '"ok": true' in body)
        results.append((f"接口 {ep}", ok, f"HTTP {code}"))

    # 5) 后台接口
    for ep in ["/api/admin/stats", "/api/admin/users?limit=1"]:
        ok, code, body = http_ok(f"{base}{ep}")
        results.append((f"接口 {ep}", ok, f"HTTP {code}"))

    # 6) 护城河：存活 / 就绪 / 状态 / 完整性
    for name, ep in [
        ("healthz", "/healthz"),
        ("readyz", "/readyz"),
        ("system status", "/api/system/status"),
        ("system integrity", "/api/system/integrity"),
    ]:
        ok, code, body = http_ok(f"{base}{ep}")
        ok = ok and ('"ok":true' in body.replace(" ", "") or '"ok": true' in body)
        results.append((f"护城河 {name}", ok, f"HTTP {code}"))

    # 7) 决策台数据链路：数据源健康 / 题材雷达 / 通达信本地 / K线 / 手机页
    ok, code, body = http_ok(f"{base}/api/data-sources/health")
    results.append(("接口 /api/data-sources/health", ok and '"critical_ok":true' in body.replace(" ", ""), f"HTTP {code}"))
    try:
        ds = json.loads(body)
        keys = {x.get("key"): x.get("status") for x in ds.get("sources", [])}
        results.append(("数据源核心 tencent/sina", keys.get("tencent") == "ok" and keys.get("sina") == "ok", str(keys)))
    except Exception as e:  # noqa: BLE001
        results.append(("数据源核心 tencent/sina", False, repr(e)))
    ok, code, body = http_ok(f"{base}/api/themes/overview", timeout=60)
    try:
        th = json.loads(body)
        results.append(("接口 /api/themes/overview", ok and th.get("ok") and th.get("universe", 0) > 1000,
                        f"HTTP {code} universe={th.get('universe')}"))
    except Exception as e:  # noqa: BLE001
        results.append(("接口 /api/themes/overview", False, repr(e)))
    ok, code, body = http_ok(f"{base}/api/tdx/status")
    try:
        tdx = json.loads(body)
        results.append(("接口 /api/tdx/status", ok and tdx.get("ok") is True, f"HTTP {code} found={tdx.get('found')}"))
    except Exception as e:  # noqa: BLE001
        results.append(("接口 /api/tdx/status", False, repr(e)))
    ok, code, body = http_ok(f"{base}/api/kline?code=sz000001&period=day&limit=30", timeout=30)
    try:
        kl = json.loads(body)
        results.append(("K线接口日线可用", ok and kl.get("data", {}).get("bars", 0) > 0,
                        f"HTTP {code} bars={kl.get('data', {}).get('bars')}"))
    except Exception as e:  # noqa: BLE001
        results.append(("K线接口日线可用", False, repr(e)))
    ok, code, _ = http_ok(f"{base}/mobile.html")
    results.append(("手机决策页 /mobile.html", ok, f"HTTP {code}"))

    # 8) 安全响应头
    try:
        req = urllib.request.Request(f"{base}/api/health")
        with urllib.request.urlopen(req, timeout=6) as r:
            hdrs = {k.lower(): v for k, v in r.headers.items()}
        for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "x-request-id"):
            results.append((f"安全头 {h}", h in hdrs, hdrs.get(h, "缺失")[:48]))
    except Exception as e:  # noqa: BLE001
        results.append(("安全响应头", False, repr(e)))

    # 8) 本地化 vendor 资源（去 CDN 依赖）
    for asset in ["vendor/tailwind-cdn.js", "vendor/vanilla-tilt.min.js",
                  "vendor/fontawesome-all.min.css", "vendor/webfonts/fa-solid-900.woff2"]:
        ok, code, _ = http_ok(f"{base}/{asset}")
        results.append((f"vendor {asset}", ok, f"HTTP {code}"))

    # 9) admin.html 不再引用外部 CDN
    admin = STATIC / "admin.html"
    if admin.is_file():
        text = admin.read_text(encoding="utf-8", errors="ignore")
        ok = ("cdn.tailwindcss.com" not in text) and ("cdnjs.cloudflare.com" not in text)
        results.append(("admin.html 无外部 CDN", ok, "已本地化" if ok else "仍引用 CDN"))
    else:
        results.append(("admin.html 无外部 CDN", False, "文件缺失"))

    # 10) 备份与自动恢复资产
    latest = ROOT / "backups" / "latest.json"
    if latest.is_file():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            ok = bool(payload.get("ok")) and bool(payload.get("zip"))
            results.append(("备份 latest.json", ok, payload.get("zip", "")))
        except Exception as e:  # noqa: BLE001
            results.append(("备份 latest.json", False, repr(e)))
    else:
        results.append(("备份 latest.json", False, "尚未执行 server.infra.backup"))
    for rel in ("tools/watchdog.ps1", "tools/install-moat.ps1"):
        results.append((f"运维脚本 {rel}", (ROOT / rel).is_file(), "存在" if (ROOT / rel).is_file() else "缺失"))

    width = max(len(name) for name, _, _ in results)
    passed = 0
    print("DAPENG Alpha · 数据源与连接检查")
    print("=" * (width + 14))
    for name, ok, note in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {note}")
        passed += 1 if ok else 0
    print("=" * (width + 14))
    print(f"{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
