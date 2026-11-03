"""行情与基本面源：新浪全 A 分页、东财季度基本面、全球指数"""

from __future__ import annotations
import asyncio
import csv
import io
import json
import logging
import math
import os
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from server.market_coverage_fetch import (
    _CACHE,
    _EM_FUND_URL,
    _FIN_TTL,
    _GLOBAL_TTL,
    _SINA_COUNT_URL,
    _SINA_PAGE_URL,
    _SINA_TTL,
    _fetch_json,
    _fetch_text,
    _last_trade_date,
    _num,
    _prefix_code,
    logger,
)

async def _sina_page(page: int, sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    async with sem:
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                data = await _fetch_json(_SINA_PAGE_URL.format(page=page), timeout=12)
                return data if isinstance(data, list) else []
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.35 * (attempt + 1))
        logger.warning("sina page %s failed: %s", page, last_exc)
        return []


async def get_sina_universe(force: bool = False) -> Dict[str, Any]:
    """全 A 股（含沪深北）报价 + 估值，100 条/页；失败不伪装成功。"""
    now = time.time()
    cached = _CACHE["sina"]
    if not force and cached["rows"] and now - cached["ts"] < _SINA_TTL:
        return {"ok": True, "rows": cached["rows"], "meta": cached["meta"], "cached": True}
    t0 = time.time()
    try:
        count_text = await _fetch_text(_SINA_COUNT_URL, timeout=10)
        total = int(str(count_text).strip().strip('"') or 0)
    except Exception as exc:
        return {"ok": False, "rows": {}, "meta": {"reason": f"sina_count_failed: {exc}"}}
    if total <= 0:
        return {"ok": False, "rows": {}, "meta": {"reason": "sina_count_empty"}}
    pages = min(80, max(1, math.ceil(total / 100)))
    sem = asyncio.Semaphore(8)
    batches = await asyncio.gather(*[_sina_page(page, sem) for page in range(1, pages + 1)])
    rows: Dict[str, Dict[str, Any]] = {}
    errors = 0
    for batch in batches:
        if not batch:
            errors += 1
            continue
        for raw in batch:
            if not isinstance(raw, dict):
                continue
            code = _prefix_code(raw.get("symbol") or raw.get("code"))
            if not code:
                continue
            price = _num(raw.get("trade"))
            chg = _num(raw.get("changepercent"))
            rows[code] = {
                "code": code,
                "name": raw.get("name") or "",
                "price": price,
                "chg": chg,
                "change": _num(raw.get("pricechange")),
                "open": _num(raw.get("open")),
                "high": _num(raw.get("high")),
                "low": _num(raw.get("low")),
                "prev_close": _num(raw.get("settlement")),
                "volume": _num(raw.get("volume")),
                "amount": _num(raw.get("amount")),
                "turnover": _num(raw.get("turnoverratio")),
                "pe": _num(raw.get("per")),
                "pb": _num(raw.get("pb")),
                "market_cap_yi": round((_num(raw.get("mktcap")) or 0) / 10000, 2) if _num(raw.get("mktcap")) is not None else None,
                "float_cap_yi": round((_num(raw.get("nmc")) or 0) / 10000, 2) if _num(raw.get("nmc")) is not None else None,
                "ticktime": raw.get("ticktime"),
                "source": "sina_market_center",
            }
    meta = {
        "source": "Sina Market_Center hs_a",
        "auth": "public_no_key",
        "configured_total": total,
        "rows": len(rows),
        "errors": errors,
        "elapsed_ms": round((time.time() - t0) * 1000),
        "quote_date_basis": _last_trade_date(),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "上游仅给 ticktime 时分秒；日期按最近交易日推断，非交易所逐笔实时流",
    }
    _CACHE["sina"] = {"ts": time.time(), "rows": rows, "meta": meta}
    return {"ok": len(rows) > 0, "rows": rows, "meta": meta, "cached": False}


def _quarter_candidates(now: Optional[datetime] = None) -> List[str]:
    """最近已结束的四个季度末。当前时点（2026-11）→ 2026-09-30 起。"""
    now = now or datetime.now()
    year, month = now.year, now.month
    if month <= 3:
        q_year, q_month = year - 1, 12
    elif month <= 6:
        q_year, q_month = year, 3
    elif month <= 9:
        q_year, q_month = year, 6
    else:
        q_year, q_month = year, 9
    candidates = []
    for _ in range(4):
        day = 31 if q_month in (3, 12) else 30
        candidates.append(f"{q_year}-{q_month:02d}-{day:02d}")
        q_month -= 3
        if q_month <= 0:
            q_month += 12
            q_year -= 1
    return candidates


async def _em_fund_page(report_date: str, page: int, sem: asyncio.Semaphore) -> Dict[str, Any]:
    async with sem:
        for attempt in range(2):
            try:
                payload = await _fetch_json(_EM_FUND_URL.format(report_date=report_date, page=page), timeout=20)
                result = payload.get("result") if isinstance(payload, dict) else None
                return result if isinstance(result, dict) else {}
            except Exception:
                await asyncio.sleep(0.4 * (attempt + 1))
    return {}


async def get_fundamentals(force: bool = False) -> Dict[str, Any]:
    """最新季度基本面：营收/净利/ROE/EPS/毛利率/行业，按报告期批量拉取。"""
    now = time.time()
    cached = _CACHE["financial"]
    if not force and cached["rows"] and now - cached["ts"] < _FIN_TTL:
        return {"ok": True, "rows": cached["rows"], "meta": cached["meta"], "cached": True}
    t0 = time.time()
    chosen = None
    first = None
    sem = asyncio.Semaphore(6)
    for candidate in _quarter_candidates():
        result = await _em_fund_page(candidate, 1, sem)
        count = int(_num(result.get("count")) or 0)
        if count >= 1000:
            chosen, first = candidate, result
            break
    if not first:
        return {"ok": False, "rows": {}, "meta": {"reason": "no_fundamental_report_available"}}
    pages = min(30, max(1, int(math.ceil((_num(first.get("count")) or 0) / 500))))
    rest = await asyncio.gather(*[_em_fund_page(chosen, page, sem) for page in range(2, pages + 1)])
    results = [first] + [item for item in rest if item]
    rows: Dict[str, Dict[str, Any]] = {}
    errors = 0
    for result in results:
        for raw in result.get("data") or []:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("SECURITY_CODE") or "").strip()
            if not code:
                continue
            prefixed = _prefix_code(code)
            rows[prefixed] = {
                "code": prefixed,
                "name": raw.get("SECURITY_NAME_ABBR") or "",
                "industry": raw.get("BOARD_NAME") or raw.get("PUBLISHNAME") or "",
                "report_date": str(raw.get("REPORTDATE") or "")[:10],
                "eps": _num(raw.get("BASIC_EPS")),
                "bps": _num(raw.get("BPS")),
                "revenue": _num(raw.get("TOTAL_OPERATE_INCOME")),
                "net_profit": _num(raw.get("PARENT_NETPROFIT")),
                "roe": _num(raw.get("WEIGHTAVG_ROE")),
                "revenue_yoy": _num(raw.get("YSTZ")),
                "profit_yoy": _num(raw.get("SJLTZ")),
                "gross_margin": _num(raw.get("XSMLL")),
                "update_date": str(raw.get("UPDATE_DATE") or "")[:10],
                "source": "eastmoney_datacenter",
            }
    if len(results) < pages:
        errors = pages - len(results)
    meta = {
        "source": "EastMoney RPT_LICO_FN_CPD",
        "auth": "public_no_key",
        "report_date": chosen,
        "rows": len(rows),
        "pages": pages,
        "errors": errors,
        "elapsed_ms": round((time.time() - t0) * 1000),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE["financial"] = {"ts": time.time(), "rows": rows, "meta": meta}
    return {"ok": len(rows) > 0, "rows": rows, "meta": meta, "cached": False}


_COUNTRY_PROXIES = [
    ("france", "法国", "usEWQ"), ("germany", "德国", "usEWG"),
    ("japan_proxy", "日本（ETF）", "usEWJ"), ("india", "印度", "usINDA"),
    ("korea", "韩国", "usEWY"), ("brazil", "巴西", "usEWZ"),
    ("australia", "澳大利亚", "usEWA"), ("taiwan", "中国台湾", "usEWT"),
]
_INDUSTRY_PROXIES = [
    ("tech", "信息技术", "usXLK"), ("financials", "金融", "usXLF"),
    ("energy", "能源", "usXLE"), ("health", "医疗", "usXLV"),
    ("consumer_disc", "可选消费", "usXLY"), ("consumer_staples", "必需消费", "usXLP"),
    ("industrials", "工业", "usXLI"), ("materials", "原材料", "usXLB"),
    ("utilities", "公用事业", "usXLU"), ("communication", "通信服务", "usXLC"),
    ("real_estate", "房地产", "usXLRE"),
]


async def get_global_snapshot(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    cached = _CACHE["global"]
    if not force and cached["data"] and now - cached["ts"] < _GLOBAL_TTL:
        return cached["data"]
    from server.global_assets import _fetch_tencent_sync, get_global_assets

    t0 = time.time()
    base, quotes = await asyncio.gather(
        get_global_assets(force=force),
        asyncio.to_thread(_fetch_tencent_sync, [item[2] for item in _COUNTRY_PROXIES + _INDUSTRY_PROXIES], 8.0),
        return_exceptions=True,
    )
    if isinstance(base, Exception):
        base = {"ok": False, "categories": {}, "reason": str(base)}
    if isinstance(quotes, Exception):
        quotes = {}
    indices: List[Dict[str, Any]] = []
    for cat, items in (base.get("categories") or {}).items():
        for item in items or []:
            if item.get("available"):
                indices.append({
                    "key": f"{cat}:{item.get('key')}",
                    "name": item.get("name"),
                    "category": cat,
                    "price": item.get("price"),
                    "chg": item.get("chg"),
                    "unit": item.get("unit"),
                    "source": item.get("source"),
                })
    countries = []
    for key, name, symbol in _COUNTRY_PROXIES:
        q = quotes.get(symbol)
        countries.append({
            "key": key, "name": name, "symbol": symbol,
            "price": q[0] if q else None,
            "chg": q[1] if q else None,
            "available": bool(q and q[0]),
            "source": "tencent_us_etf",
            "proxy_note": "国家/地区 ETF 代理，不是当地官方指数",
            "reason": None if q and q[0] else "no_data_from_sources",
        })
    industries = []
    for key, name, symbol in _INDUSTRY_PROXIES:
        q = quotes.get(symbol)
        industries.append({
            "key": key, "name": name, "symbol": symbol,
            "price": q[0] if q else None,
            "chg": q[1] if q else None,
            "available": bool(q and q[0]),
            "source": "tencent_us_sector_etf",
            "proxy_note": "美股行业 ETF，作为全球行业风险偏好代理",
            "reason": None if q and q[0] else "no_data_from_sources",
        })
    data = {
        "ok": True,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "indices": indices,
        "countries": countries,
        "industries": industries,
        "coverage": {
            "indices": len([x for x in indices if x.get("price") is not None]),
            "countries": len([x for x in countries if x.get("available")]),
            "industries": len([x for x in industries if x.get("available")]),
        },
        "source": "tencent_qt+sina_hq",
        "auth": "public_no_key",
        "elapsed_ms": round((time.time() - t0) * 1000),
        "notes": [
            "国家 ETF 只用于跨市场风险偏好观察，不冒充当地交易所指数。",
            "行业 ETF 为美股 11 个 Select Sector SPDR 代理，不与 A 股行业指数混用。",
        ],
    }
    _CACHE["global"] = {"ts": time.time(), "data": data}
    return data
