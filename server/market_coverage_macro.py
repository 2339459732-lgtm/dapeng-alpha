"""宏观源：美债曲线、NY Fed、ECB、世界银行"""

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

from server.market_coverage_fetch import _CACHE, _MACRO_TTL, _fetch_text, _num, logger

_TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
_NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/2.json"
_NYFED_SOFR = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/2.json"
_ECB_DFR = (
    "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
    "?lastNObservations=2&format=csvdata"
)
_WB_CPI = "https://api.worldbank.org/v2/country/{countries}/indicator/FP.CPI.TOTL.ZG?format=json&date=2022:2026&per_page=50"
_WB_GDP = "https://api.worldbank.org/v2/country/{countries}/indicator/NY.GDP.MKTP.KD.ZG?format=json&date=2022:2026&per_page=50"
_WB_UNEMP = "https://api.worldbank.org/v2/country/{countries}/indicator/SL.UEM.TOTL.ZS?format=json&date=2022:2026&per_page=50"
_WB_COUNTRIES = "USA;CHN;JPN;EMU"
_WB_NAMES = {"USA": "美国", "CHN": "中国", "JPN": "日本", "EMU": "欧元区"}

_MACRO_CACHE = _CACHE["macro"]


def _parse_treasury_curve(text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    rows: List[Dict[str, Any]] = []
    for row in reader:
        date_text = (row.get("Date") or "").strip()
        try:
            day = datetime.strptime(date_text, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            continue
        rows.append({
            "date": day,
            "m3": _num(row.get("3 Mo")),
            "y2": _num(row.get("2 Yr")),
            "y10": _num(row.get("10 Yr")),
            "y30": _num(row.get("30 Yr")),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def _parse_nyfed_rate(text: str) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(text)
        rates = payload.get("refRates") or []
    except Exception:
        return []
    rows = []
    for item in rates:
        value = _num(item.get("percentRate"))
        if value is None:
            continue
        rows.append({"date": item.get("effectiveDate"), "value": value,
                     "target_from": _num(item.get("targetRateFrom")),
                     "target_to": _num(item.get("targetRateTo"))})
    rows.sort(key=lambda x: x.get("date") or "")
    return rows


def _parse_ecb_rate(text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        value = _num(row.get("OBS_VALUE"))
        if value is None:
            continue
        rows.append({"date": row.get("TIME_PERIOD"), "value": value})
    rows.sort(key=lambda x: x.get("date") or "")
    return rows


def _parse_worldbank(payload: Any) -> Dict[str, Dict[str, Any]]:
    """World Bank 返回 [metadata, rows]；每国取最新非空年度值。"""
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in payload[1]:
        if not isinstance(row, dict) or row.get("value") is None:
            continue
        key = row.get("countryiso3code") or (row.get("country") or {}).get("id")
        date = str(row.get("date") or "")
        if not key:
            continue
        current = out.get(key)
        if current is None or date > str(current.get("period")):
            out[key] = {"value": _num(row.get("value")), "period": date}
    return out


def _macro_rate_item(key: str, label: str, rows: List[Dict[str, Any]], field: str,
                     unit: str, source: str) -> Dict[str, Any]:
    if not rows:
        return {"key": key, "label": label, "value": None, "unit": unit,
                "period": None, "change": None, "source": source,
                "status": "unavailable", "reason": "source_unavailable"}
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else {}
    value = _num(latest.get(field))
    prev = _num(previous.get(field))
    return {
        "key": key, "label": label, "value": value, "unit": unit,
        "period": latest.get("date"), "previous_period": previous.get("date"),
        "change": round(value - prev, 2) if value is not None and prev is not None else None,
        "source": source, "status": "ok" if value is not None else "missing_value",
        "frequency": "daily",
    }


def _wb_item(indicator: str, iso: str, label: str, values: Dict[str, Dict[str, Any]],
             unit: str) -> Dict[str, Any]:
    item = values.get(iso) or {}
    return {
        "key": f"{indicator}_{iso.lower()}",
        "label": label,
        "value": item.get("value"),
        "unit": unit,
        "period": item.get("period"),
        "change": None,
        "source": "World Bank",
        "status": "ok" if item.get("value") is not None else "unavailable",
        "frequency": "annual",
        "note": "年度值；最新年度可能为估算/初值，以 World Bank 发布日期为准",
    }


async def _wb_series(url_template: str) -> Dict[str, Dict[str, Any]]:
    try:
        text = await _fetch_text(url_template.format(countries=_WB_COUNTRIES), timeout=25)
        return _parse_worldbank(json.loads(text))
    except Exception as exc:
        logger.warning("worldbank unavailable: %s", exc)
        return {}


async def get_macro_snapshot(force: bool = False) -> Dict[str, Any]:
    """全球宏观：美债收益率曲线 + 纽约联储利率 + 欧央行 + World Bank 年度指标 + 中国宏观。"""
    now = time.time()
    cached = _CACHE["macro"]
    if not force and cached["data"] and now - cached["ts"] < _MACRO_TTL:
        return cached["data"]
    from server.ak_market import ak_hub

    t0 = time.time()
    year = datetime.now().year
    treasury_task = _fetch_text(_TREASURY_URL.format(year=year), timeout=25)
    effr_task = _fetch_text(_NYFED_EFFR, timeout=20)
    sofr_task = _fetch_text(_NYFED_SOFR, timeout=20)
    ecb_task = _fetch_text(_ECB_DFR, timeout=20)
    cpi_task = _wb_series(_WB_CPI)
    gdp_task = _wb_series(_WB_GDP)
    unemp_task = _wb_series(_WB_UNEMP)
    china_task = ak_hub.get_macro_data()
    results = await asyncio.gather(
        treasury_task, effr_task, sofr_task, ecb_task,
        cpi_task, gdp_task, unemp_task, china_task,
        return_exceptions=True,
    )
    names = ["treasury", "effr", "sofr", "ecb", "wb_cpi", "wb_gdp", "wb_unemp", "china"]
    values = {name: result for name, result in zip(names, results)}
    treasury = _parse_treasury_curve(values["treasury"]) if isinstance(values["treasury"], str) else []
    effr = _parse_nyfed_rate(values["effr"]) if isinstance(values["effr"], str) else []
    sofr = _parse_nyfed_rate(values["sofr"]) if isinstance(values["sofr"], str) else []
    ecb = _parse_ecb_rate(values["ecb"]) if isinstance(values["ecb"], str) else []
    cpi = values["wb_cpi"] if isinstance(values["wb_cpi"], dict) else {}
    gdp = values["wb_gdp"] if isinstance(values["wb_gdp"], dict) else {}
    unemp = values["wb_unemp"] if isinstance(values["wb_unemp"], dict) else {}
    china = values["china"] if isinstance(values["china"], dict) else {"ok": False, "reason": str(values["china"])}

    items = [
        _macro_rate_item("ust10y", "美国 10 年期国债", treasury, "y10", "%", "US Treasury"),
        _macro_rate_item("ust2y", "美国 2 年期国债", treasury, "y2", "%", "US Treasury"),
        _macro_rate_item("ust3m", "美国 3 个月国债", treasury, "m3", "%", "US Treasury"),
        _macro_rate_item("effr", "美国有效联邦基金利率", effr, "value", "%", "New York Fed"),
        _macro_rate_item("sofr", "美国 SOFR", sofr, "value", "%", "New York Fed"),
        _macro_rate_item("ecb_dfr", "欧央行存款便利利率", ecb, "value", "%", "ECB"),
    ]
    latest10 = next((x for x in items if x["key"] == "ust10y"), {})
    latest2 = next((x for x in items if x["key"] == "ust2y"), {})
    spread = (
        round(latest10["value"] - latest2["value"], 2)
        if latest10.get("value") is not None and latest2.get("value") is not None else None
    )
    items.append({
        "key": "ust_10y_2y", "label": "美债 10Y-2Y 利差", "value": spread, "unit": "%",
        "period": latest10.get("period"), "change": None, "source": "derived:US Treasury",
        "status": "ok" if spread is not None else "missing_inputs", "frequency": "daily",
    })
    for iso, cn_name in _WB_NAMES.items():
        items.append(_wb_item("cpi", iso, f"{cn_name} CPI 同比（年度）", cpi, "%"))
        items.append(_wb_item("gdp", iso, f"{cn_name} GDP 实际增速（年度）", gdp, "%"))
        items.append(_wb_item("unemp", iso, f"{cn_name} 失业率（年度）", unemp, "%"))

    source_status = []
    for key, label, auth in [
        ("treasury", "US Treasury 收益率曲线", "public_no_key"),
        ("effr", "New York Fed EFFR", "public_no_key"),
        ("sofr", "New York Fed SOFR", "public_no_key"),
        ("ecb", "ECB Data Portal 存款便利利率", "public_no_key"),
        ("worldbank", "World Bank 年度宏观", "public_no_key"),
        ("china", "AkShare / 东财中国宏观", "public_no_key"),
    ]:
        records = 0
        if key == "treasury":
            records = len(treasury)
        elif key == "effr":
            records = len(effr)
        elif key == "sofr":
            records = len(sofr)
        elif key == "ecb":
            records = len(ecb)
        elif key == "worldbank":
            records = len(cpi) + len(gdp) + len(unemp)
        elif key == "china":
            records = len(china.get("data") or [])
        source_status.append({
            "key": key, "label": label, "status": "ok" if records else "degraded",
            "auth": auth, "records": records,
        })

    available = len([x for x in items if x.get("value") is not None])
    data = {
        "ok": available > 0,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "global": items,
        "china": china,
        "coverage": {
            "global_available": available,
            "global_total": len(items),
            "rates_available": len([x for x in items if x.get("frequency") == "daily" and x.get("value") is not None]),
            "annual_available": len([x for x in items if x.get("frequency") == "annual" and x.get("value") is not None]),
        },
        "sources": source_status,
        "source": "US Treasury + NY Fed + ECB + World Bank + AkShare",
        "auth": "public_no_key",
        "elapsed_ms": round((time.time() - t0) * 1000),
        "as_of": {
            "rates": (treasury[-1]["date"] if treasury else (ecb[-1].get("date") if ecb else None)),
            "annual": max([str(x.get("period") or "") for x in items if x.get("frequency") == "annual"] or [""]) or None,
        },
        "notes": [
            "日频利率来自官方数据源；World Bank 为年度值，发布有时间滞后，不作为当日交易信号。",
            "缺失序列返回 null + reason，不填充 0 或经验值。",
        ],
    }
    _CACHE["macro"] = {"ts": time.time(), "data": data}
    return data
