"""数据源注册与健康检查（底层数据平台）
- 统一列出系统可用的数据源、状态、延迟与降级说明
- 供决策台/后台/巡检脚本调用，避免各模块各自猜测数据源可用性
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import requests

from server import tdx_local

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}


def _timed(fn):
    t0 = time.time()
    try:
        ok, note = fn()
        return {"ok": bool(ok), "note": note, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": f"{type(e).__name__}: {str(e)[:80]}", "ms": round((time.time() - t0) * 1000)}


def _check_tencent():
    r = requests.get("http://qt.gtimg.cn/q=sh000001", timeout=6)
    return (r.status_code == 200 and "v_sh000001" in r.text), "腾讯行情 qt.gtimg.cn（指数/个股快照）"


def _check_sina():
    r = requests.get(
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
        params={"page": 1, "num": 3, "sort": "symbol", "asc": 1, "node": "hs_a"},
        headers=_HEADERS,
        timeout=10,
    )
    data = r.json() if r.status_code == 200 else []
    return (isinstance(data, list) and len(data) > 0), "新浪财经 A 股全市场列表（题材雷达）"


def _check_eastmoney_push2():
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={"pn": 1, "pz": 2, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:90+t:3", "fields": "f12,f14"},
        headers=_HEADERS,
        timeout=6,
    )
    return (r.status_code == 200 and "data" in r.text), "东财 push2 板块/资金流（部分网络被阻断）"


def _check_eastmoney_datacenter():
    r = requests.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={"reportName": "RPT_CUSTOM_SYMBOL", "columns": "ALL", "pageNumber": 1, "pageSize": 1},
        headers=_HEADERS,
        timeout=8,
    )
    return (r.status_code == 200), "东财数据中心（龙虎榜/要闻）"


def _check_tdx():
    s = tdx_local.status()
    return bool(s.get("found")), "通达信本地 vipdoc（离线历史行情）" if s.get("found") else "通达信未安装/未设 TDX_HOME"


def _check_tushare():
    token = os.environ.get("TUSHARE_TOKEN") or ""
    return bool(token), "Tushare Pro（需 TUSHARE_TOKEN，基本面/行情可选源）"


def health() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(key: str, label: str, fn, critical: bool = False):
        r = _timed(fn)
        checks.append({
            "key": key,
            "label": label,
            "critical": critical,
            "status": "ok" if r["ok"] else "degraded",
            "latency_ms": r["ms"],
            "note": r["note"],
        })

    add("tencent", "腾讯行情", _check_tencent, critical=True)
    add("sina", "新浪全市场", _check_sina, critical=True)
    add("eastmoney_push2", "东财 push2", _check_eastmoney_push2)
    add("eastmoney_datacenter", "东财数据中心", _check_eastmoney_datacenter)
    add("tdx_local", "通达信本地数据", _check_tdx)
    add("tushare", "Tushare Pro", _check_tushare)

    ok = sum(1 for c in checks if c["status"] == "ok")
    critical_ok = all(c["status"] == "ok" for c in checks if c["critical"])
    return {
        "ok": critical_ok,
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {"total": len(checks), "ok": ok, "degraded": len(checks) - ok, "critical_ok": critical_ok},
        "sources": checks,
        "notes": [
            "critical 源不可用时系统仍有降级/缓存，但实时性下降；",
            "东财 push2 在当前网络常被阻断，东财数据中心与腾讯/新浪可作为替代；",
            "Tushare 需要 TUSHARE_TOKEN（环境变量或 .env），作为基本面与历史行情可选源；",
            "通达信本地数据需安装通达信并下载盘后数据，设置 TDX_HOME。",
        ],
    }
