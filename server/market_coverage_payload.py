"""口径汇总：宽度、基本面摘要、行业摘要、coverage 载荷"""

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
    LATEST_PATH,
    _CACHE,
    _COVERAGE_TTL,
    _LAST_ROWS,
    _last_trade_date,
    _limit_threshold,
    _num,
    _prefix_code,
    _write_json_atomic,
    logger,
)
from server.market_coverage_macro import get_macro_snapshot
from server.market_coverage_sources import get_fundamentals, get_global_snapshot, get_sina_universe
from server.market_coverage_bj import get_bj_universe
from server.market_coverage_gapfill import get_gapfill_quotes

def _exchange_of(code: str) -> str:
    if code.startswith("bj"):
        return "bj"
    if code.startswith(("sh", "sz")):
        return code[:2]
    return "other"


def _exchange_breakdown(codes: List[str], quote_rows: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """分市场完整性：沪 / 深 / 京 各自的标的数与**有报价**数。

    只看整体覆盖率会把「北交所整块缺失」稀释成 8 个百分点（546/6127），
    分市场摆开才能一眼看出缺口在哪个交易所。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        ex = _exchange_of(code)
        slot = out.setdefault(ex, {"total": 0, "quoted": 0})
        slot["total"] += 1
        row = quote_rows.get(code) or {}
        if row.get("price") is not None:
            slot["quoted"] += 1
    for slot in out.values():
        slot["coverage"] = round(slot["quoted"] / slot["total"] * 100, 1) if slot["total"] else 0.0
    return out


def _market_breadth(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [r for r in rows if r.get("price") is not None and r.get("chg") is not None]
    up = sum(1 for r in valid if r["chg"] > 0)
    down = sum(1 for r in valid if r["chg"] < 0)
    flat = len(valid) - up - down
    limit_up = sum(1 for r in valid if r["chg"] >= _limit_threshold(r["code"], r.get("name") or ""))
    limit_down = sum(1 for r in valid if r["chg"] <= -_limit_threshold(r["code"], r.get("name") or ""))
    amounts = [r["amount"] for r in valid if r.get("amount") is not None and r["amount"] > 0]
    changes = [r["chg"] for r in valid]
    return {
        "total": len(rows), "valid": len(valid),
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up / len(valid) * 100, 1) if valid else None,
        "limit_up": limit_up, "limit_down": limit_down,
        "amount": round(sum(amounts), 2) if amounts else None,
        "median_chg": round(median(changes), 2) if changes else None,
    }


def _fundamental_summary(rows: List[Dict[str, Any]], financial: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    valid_pe = [r["pe"] for r in rows if r.get("pe") is not None and 0 < r["pe"] < 500]
    valid_pb = [r["pb"] for r in rows if r.get("pb") is not None and 0 < r["pb"] < 100]
    roes = [f.get("roe") for f in financial.values() if f.get("roe") is not None]
    rev = [f.get("revenue_yoy") for f in financial.values() if f.get("revenue_yoy") is not None]
    profit = [f.get("profit_yoy") for f in financial.values() if f.get("profit_yoy") is not None]
    caps = [r.get("market_cap_yi") for r in rows if r.get("market_cap_yi") is not None and r["market_cap_yi"] > 0]
    return {
        "pe_median": round(median(valid_pe), 2) if valid_pe else None,
        "pb_median": round(median(valid_pb), 2) if valid_pb else None,
        "roe_median": round(median(roes), 2) if roes else None,
        "revenue_yoy_median": round(median(rev), 2) if rev else None,
        "profit_yoy_median": round(median(profit), 2) if profit else None,
        "market_cap_yi": round(sum(caps), 2) if caps else None,
        "valuation_coverage": len(valid_pe), "financial_coverage": len(financial),
        "roe_coverage": len(roes),
    }


def _industry_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        name = row.get("industry")
        if not name:
            continue
        buckets.setdefault(name, []).append(row)
    result = []
    for name, items in buckets.items():
        changes = [x["chg"] for x in items if x.get("chg") is not None]
        roes = [x.get("roe") for x in items if x.get("roe") is not None]
        amount = sum((x.get("amount") or 0) for x in items)
        result.append({
            "name": name, "count": len(items),
            "avg_chg": round(sum(changes) / len(changes), 2) if changes else None,
            "median_roe": round(median(roes), 2) if roes else None,
            "amount": round(amount, 2) if amount else None,
        })
    result.sort(key=lambda x: (x["amount"] or 0), reverse=True)
    return result[:20]


def build_coverage_payload(
    sina: Dict[str, Any],
    financial: Dict[str, Any],
    global_snapshot: Dict[str, Any],
    macro_snapshot: Dict[str, Any],
    core_spot: Dict[str, Any],
    dashboard: Optional[Dict[str, Any]] = None,
    bj: Optional[Dict[str, Any]] = None,
    gapfill: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    quote_rows = dict(sina.get("rows") or {})
    # 北交所单独一路抓。冲突时以新浪为准，只补缺不覆盖。
    for code, row in ((bj or {}).get("rows") or {}).items():
        quote_rows.setdefault(code, row)
    # 第三路兜底：主源限流/分页失败漏掉的代码用腾讯批量补价格。
    # 只补「没有价格」的，不覆盖已有报价（避免源间混用导致口径漂移）。
    gap_filled = 0
    for code, row in ((gapfill or {}).get("rows") or {}).items():
        current = quote_rows.get(code)
        if current is None or current.get("price") is None:
            if current:
                merged_row = dict(current)
                merged_row.update({k: v for k, v in row.items() if v is not None})
                quote_rows[code] = merged_row
            else:
                quote_rows[code] = row
            gap_filled += 1
    fin_rows = dict(financial.get("rows") or {})
    # 核心指数/核心样本以 60 秒级行情覆盖新浪 5 分钟快照（仅覆盖其 code 范围）
    spot_rows = core_spot.get("data") if isinstance(core_spot, dict) else []
    spot_map = {}
    for item in spot_rows or []:
        code = _prefix_code(item.get("code"))
        if code:
            spot_map[code] = item
    for code, item in spot_map.items():
        row = quote_rows.setdefault(code, {"code": code, "name": item.get("name") or "", "source": "market-dashboard"})
        row.update({
            "price": _num(item.get("price")),
            "chg": _num(item.get("chg")),
            "amount": _num(item.get("amount")),
            "turnover": _num(item.get("turnover")),
            "quote_source": "market-dashboard/tencent",
        })
    merged: List[Dict[str, Any]] = []
    all_codes = sorted(set(quote_rows) | set(fin_rows))
    for code in all_codes:
        q = quote_rows.get(code, {})
        f = fin_rows.get(code, {})
        row = {
            "code": code,
            "name": q.get("name") or f.get("name") or code,
            "price": q.get("price"),
            "chg": q.get("chg"),
            "amount": q.get("amount"),
            "turnover": q.get("turnover"),
            "pe": q.get("pe"),
            "pb": q.get("pb"),
            "market_cap_yi": q.get("market_cap_yi"),
            "float_cap_yi": q.get("float_cap_yi"),
            "industry": f.get("industry"),
            "report_date": f.get("report_date"),
            "eps": f.get("eps"),
            "bps": f.get("bps"),
            "revenue": f.get("revenue"),
            "net_profit": f.get("net_profit"),
            "roe": f.get("roe"),
            "revenue_yoy": f.get("revenue_yoy"),
            "profit_yoy": f.get("profit_yoy"),
            "gross_margin": f.get("gross_margin"),
            "quote_source": q.get("quote_source") or q.get("source"),
            "fundamental_source": "eastmoney_datacenter" if f else None,
        }
        merged.append(row)
    breadth = _market_breadth(merged)
    fundamentals = _fundamental_summary(merged, fin_rows)
    total_universe = len(merged)
    quote_coverage = breadth["valid"] / total_universe if total_universe else 0
    financial_coverage = len(fin_rows) / total_universe if total_universe else 0
    valuation_coverage = (fundamentals["valuation_coverage"] or 0) / total_universe if total_universe else 0
    total_available = bool(total_universe >= 3000 and quote_coverage >= 0.80)
    exchanges = _exchange_breakdown(all_codes, quote_rows)
    quality = {
        "level": "good" if quote_coverage >= 0.95 and financial_coverage >= 0.80 else
                 "partial" if quote_coverage >= 0.60 and financial_coverage >= 0.40 else "poor",
        "quote_coverage": round(quote_coverage * 100, 1),
        "financial_coverage": round(financial_coverage * 100, 1),
        "valuation_coverage": round(valuation_coverage * 100, 1),
        # 分市场完整性：整体覆盖率会把「北交所整块缺」稀释成一个百分点，
        # 分市场看才知道缺口在哪一块。
        "exchanges": exchanges,
        "gapfilled": gap_filled,
        "actionable": total_available,
    }
    sources = [
        {"key": "sina_market", "label": "新浪全市场行情与估值（沪深）", "status": "ok" if sina.get("ok") else "error",
         "auth": "public_no_key", "records": len(quote_rows), "as_of": sina.get("meta", {}).get("fetched_at"),
         "latency_ms": sina.get("meta", {}).get("elapsed_ms")},
        {"key": "tencent_gapfill", "label": "腾讯批量补缺（主源漏抓兜底）",
         "status": "ok" if (gapfill or {}).get("ok") else "error",
         "auth": "public_no_key",
         "records": len((gapfill or {}).get("rows") or {}),
         "requested": ((gapfill or {}).get("meta") or {}).get("requested"),
         "unresolved": ((gapfill or {}).get("meta") or {}).get("unresolved_count"),
         "latency_ms": ((gapfill or {}).get("meta") or {}).get("elapsed_ms"),
         "as_of": ((gapfill or {}).get("meta") or {}).get("fetched_at")},
        {"key": "eastmoney_bj", "label": "东财北交所行情（京）", "status": "ok" if (bj or {}).get("ok") else "error",
         "auth": "public_no_key", "records": len((bj or {}).get("rows") or {}),
         "as_of": (bj or {}).get("meta", {}).get("fetched_at"),
         "latency_ms": (bj or {}).get("meta", {}).get("elapsed_ms"),
         "reason": None if (bj or {}).get("ok") else (bj or {}).get("reason")},
        {"key": "eastmoney_fund", "label": "东财最新季度基本面", "status": "ok" if financial.get("ok") else "error",
         "auth": "public_no_key", "records": len(fin_rows), "as_of": financial.get("meta", {}).get("report_date"),
         "latency_ms": financial.get("meta", {}).get("elapsed_ms")},
        {"key": "market_dashboard", "label": "腾讯核心行情（60s）", "status": "ok" if core_spot.get("ok") else "error",
         "auth": "public_no_key", "records": len(spot_rows or []), "as_of": core_spot.get("update_time")},
        {"key": "tencent_global", "label": "全球指数/国家与行业 ETF", "status": "ok" if global_snapshot.get("ok") else "error",
         "auth": "public_no_key", "records": (global_snapshot.get("coverage") or {}).get("indices", 0) +
                    (global_snapshot.get("coverage") or {}).get("countries", 0) +
                    (global_snapshot.get("coverage") or {}).get("industries", 0),
         "as_of": global_snapshot.get("update_time")},
    ]
    macro_sources = macro_snapshot.get("sources") if isinstance(macro_snapshot.get("sources"), list) else []
    for item in macro_sources:
        sources.append({
            "key": "macro_" + str(item.get("key")),
            "label": item.get("label"),
            "status": item.get("status"),
            "auth": item.get("auth"),
            "records": item.get("records"),
            "as_of": (macro_snapshot.get("as_of") or {}).get("rates"),
        })
    return {
        "ok": total_universe > 0,
        "version": "market-coverage.v1",
        "as_of": dashboard.get("as_of") if dashboard else datetime.now().astimezone().isoformat(timespec="seconds"),
        "quote_date_basis": _last_trade_date(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ashare": {
            "scope": "沪深北 A 股（含 ST，不含 ETF/基金）",
            "total_universe": total_universe,
            "quotes": breadth,
            "fundamentals": fundamentals,
            "quality": quality,
            "industries": _industry_summary(merged),
            "leaders": [dict(r, rank=i + 1) for i, r in enumerate(sorted(
                [r for r in merged if r.get("chg") is not None], key=lambda x: x["chg"], reverse=True)[:10])],
            "laggards": [dict(r, rank=i + 1) for i, r in enumerate(sorted(
                [r for r in merged if r.get("chg") is not None], key=lambda x: x["chg"])[:10])],
        },
        "global": global_snapshot,
        "macro": macro_snapshot,
        "sources": sources,
        "notes": [
            "A 股全市场行情来自新浪 100/页分页快照，核心指数由腾讯 60 秒快照覆盖；基本面来自东财最新季度报告。",
            "PE/PB 仅在有有效估值时计入中位数；负 PE / 缺失值不填充。",
            "行业 ETF 作为全球行业风险偏好代理，不冒充当地交易所行业指数。",
            "宏观指标来自 US Treasury / NY Fed / ECB / World Bank 与中国宏观；年度值存在发布滞后，以 period 字段为准。",
        ],
    }


async def get_market_coverage(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    cached = _CACHE["coverage"]
    if not force and cached["data"] and now - cached["ts"] < _COVERAGE_TTL:
        return {**cached["data"], "cached": True}
    from server.ak_market import ak_hub
    from server.services.market_dashboard import build_dashboard as _build_dashboard

    sina, financial, bj, global_snapshot, macro_snapshot, core_spot, dashboard = await asyncio.gather(
        get_sina_universe(force=force),
        get_fundamentals(force=force),
        get_bj_universe(force=force),
        get_global_snapshot(force=force),
        get_macro_snapshot(force=force),
        ak_hub.get_market_spot(),
        _build_dashboard(),
        return_exceptions=True,
    )
    safe = lambda value: value if isinstance(value, dict) else {"ok": False, "reason": str(value)}
    sina = safe(sina)
    financial = safe(financial)
    bj = safe(bj)
    global_snapshot = safe(global_snapshot)
    macro_snapshot = safe(macro_snapshot)
    core_spot = safe(core_spot)
    dashboard = dashboard if isinstance(dashboard, dict) else None
    # 缺口 = 池子里有、但主源/北交所源都没给价格的代码
    priced = {**dict(sina.get("rows") or {}), **dict(bj.get("rows") or {})}
    universe_codes = set(priced) | set(financial.get("rows") or {})
    missing = [code for code in universe_codes
               if (priced.get(code) or {}).get("price") is None]
    if missing:
        gapfill = await get_gapfill_quotes(missing, force=force)
        if not gapfill.get("ok"):
            logger.warning("gapfill failed: %s", gapfill)
    else:
        gapfill = {"ok": True, "rows": {}, "meta": {"requested": 0, "resolved": 0}}

    payload = build_coverage_payload(sina, financial, global_snapshot, macro_snapshot,
                                     core_spot, dashboard, bj, gapfill)
    # 就地更新而不是重新绑定：_LAST_ROWS 被 market_coverage / market_coverage_worker /
    # market_scanner 三处 from-import 引用，重新赋值等于把新数据丢进另一个对象，
    # /api/decision/universe 因此长期返回空。
    _LAST_ROWS.clear()
    # build rows for paginated universe endpoint
    # (identical merge logic, kept here to avoid exposing private summary rows)
    sina_rows = dict(sina.get("rows") or {})
    fin_rows = dict(financial.get("rows") or {})
    bj_rows = dict(bj.get("rows") or {})
    gap_rows = dict((gapfill or {}).get("rows") or {})
    for code in sorted(set(sina_rows) | set(fin_rows) | set(bj_rows) | set(gap_rows)):
        q = sina_rows.get(code) or bj_rows.get(code) or gap_rows.get(code) or {}
        f = fin_rows.get(code, {})
        _LAST_ROWS.append({
            "code": code, "name": q.get("name") or f.get("name") or code,
            "price": q.get("price"), "chg": q.get("chg"), "amount": q.get("amount"),
            "turnover": q.get("turnover"), "pe": q.get("pe"), "pb": q.get("pb"),
            "market_cap_yi": q.get("market_cap_yi"), "industry": f.get("industry"),
            "roe": f.get("roe"), "revenue_yoy": f.get("revenue_yoy"),
            "profit_yoy": f.get("profit_yoy"), "report_date": f.get("report_date"),
            "exchange": "bj" if code.startswith("bj") else code[:2],
            "quote_source": q.get("quote_source") or q.get("source"),
        })
    payload["cached"] = False
    _CACHE["coverage"] = {"ts": time.time(), "data": payload}
    try:
        _write_json_atomic(LATEST_PATH, {
            "version": payload["version"], "as_of": payload["as_of"],
            "generated_at": payload["generated_at"], "quality": payload["ashare"]["quality"],
            "ashare": {
                "total_universe": payload["ashare"]["total_universe"],
                "quotes": payload["ashare"]["quotes"],
                "fundamentals": payload["ashare"]["fundamentals"],
            },
            "global": {k: payload["global"].get(k) for k in ("coverage", "update_time")},
            "macro": {k: payload["macro"].get(k) for k in ("coverage", "update_time")},
            "sources": payload["sources"],
        })
    except Exception as exc:
        logger.warning("coverage snapshot persist failed: %s", exc)
    return payload


def get_cached_coverage(max_age: int = 900) -> Optional[Dict[str, Any]]:
    """只读最近一次成功覆盖，供 survey/快照在不等上游的情况下复用。"""
    cached = _CACHE.get("coverage") or {}
    data = cached.get("data")
    if not data:
        return None
    if max_age > 0 and time.time() - (cached.get("ts") or 0) > max_age:
        return None
    return data
