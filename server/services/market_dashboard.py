"""大盘纵览聚合服务（market-dashboard.v1）。

职责：聚合真实行情（主要指数 / 样本池 / 板块强弱 / 领涨领跌 / 指数资金流），
不依赖任何路由层，供 API、决策快照、数据调查、因子引擎共用。

分层约定：本模块属于 data 层，只允许依赖 cache/config/eastmoney/realtime 等下层能力。
上游缺失时显式返回降级状态，不生成随机数据。
"""
from __future__ import annotations

import asyncio
import logging
import urllib.parse
from datetime import datetime
from statistics import mean, median
from typing import Any, Dict, List

from server.cache import cache
from server.config import SECTORS, get_all_stock_codes
from server.eastmoney import _fetch_json, fetch_quotes_multi
from server.tencent_kline import fetch_tencent_kline
from server.core.numbers import safe_float as _finite

logger = logging.getLogger(__name__)
CACHE_KEY = "market_dashboard:v3"
CACHE_TTL = 60
_LAST_GOOD_DASHBOARD: Dict[str, Any] | None = None
INDEX_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
INDEX_CODES = [
    "sh000001", "sz399001", "sz399006", "sh000688",
    "sh000300", "sh000905", "sh000852", "hkHSI"
]
INDEX_NAMES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "hkHSI": "恒生指数"
}
INDEX_SHORT_CODE_MAP = {
    "000001": "sh000001", "399001": "sz399001", "399006": "sz399006",
    "000688": "sh000688", "000300": "sh000300", "000905": "sh000905",
    "000852": "sh000852", "HSI": "hkHSI"
}


def _normalize_code(raw_code: str, numeric_map: Dict[str, str]) -> str:
    code = str(raw_code or "")
    if code.startswith(("sh", "sz")):
        return code
    return numeric_map.get(code, code)


def _amount_yuan(quote: Dict[str, Any]) -> float:
    amount = _finite(quote.get("amount"))
    return amount * 10000 if quote.get("amount_unit") == "wan_cny" else amount


def _limit_threshold(code: str, name: str) -> float:
    numeric = code[2:] if code.startswith(("sh", "sz")) else code
    if "ST" in str(name).upper():
        return 4.8
    if numeric.startswith(("300", "301", "688")):
        return 19.5
    return 9.5


def _distribution(changes: List[float]) -> List[Dict[str, Any]]:
    bins = [
        ("跌超5%", lambda x: x <= -5),
        ("-5%~-2%", lambda x: -5 < x <= -2),
        ("-2%~0", lambda x: -2 < x < 0),
        ("平盘", lambda x: x == 0),
        ("0~2%", lambda x: 0 < x < 2),
        ("2%~5%", lambda x: 2 <= x < 5),
        ("涨超5%", lambda x: x >= 5),
    ]
    return [{"label": label, "count": sum(1 for item in changes if predicate(item))} for label, predicate in bins]


CORE_TREND_CODES = ["sh000001", "sz399001", "sz399006", "sh000688"]

async def _fetch_index_trend(code: str) -> Dict[str, Any]:
    """优先 5 分钟分时；停牌、收盘或分钟源无数据时退回近 48 个日线点。"""
    try:
        rows = await fetch_tencent_kline(code, period="5", limit=48, timeout=2.5)
        granularity = "5分钟"
        if not rows or len(rows) < 2:
            rows = await fetch_tencent_kline(code, period="day", limit=48, timeout=2.5)
            granularity = "日线降级"
        return {"rows": rows or [], "granularity": granularity}
    except Exception:
        return {"rows": [], "granularity": "暂无数据"}


async def _fetch_index_flow(code: str) -> Dict[str, Any]:
    try:
        secid = "1." + code[2:] if code.startswith("sh") else "0." + code[2:]
        params = {
            "lmt": 5, "klt": 1, "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        }
        data = await _fetch_json(INDEX_FLOW_URL + "?" + urllib.parse.urlencode(params), timeout=2.5)
        rows = ((data or {}).get("data") or {}).get("klines") or []
        if not rows:
            return {"code": code, "name": INDEX_NAMES.get(code, code), "available": False}
        parts = str(rows[-1]).split(",")
        if len(parts) < 6:
            return {"code": code, "name": INDEX_NAMES.get(code, code), "available": False}
        return {
            "code": code, "name": INDEX_NAMES.get(code, code), "available": True,
            "time": parts[0], "flow_a": _finite(parts[1]),
            "flow_b": _finite(parts[2]), "flow_c": _finite(parts[3]),
            "flow_d": _finite(parts[4]), "flow_e": _finite(parts[5]),
            "amount_unit": "cny"
        }
    except Exception:
        return {"code": code, "name": INDEX_NAMES.get(code, code), "available": False}


async def _fetch_quotes_batched(codes: List[str], batch_size: int = 100, concurrency: int = 8) -> List[Dict[str, Any]]:
    """限并发分批抓取；腾讯高可用 HTTP 优先（毫秒级），东方财富备用。"""
    from server.realtime import fetch_tencent_quotes
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch_batch(batch: List[str]):
        async with semaphore:
            try:
                quotes = await fetch_tencent_quotes(batch, timeout=2.5)
                if quotes:
                    return quotes
            except Exception:
                pass
            return await fetch_quotes_multi(batch, timeout=2.5, prefer_eastmoney=False)

    code_map = {code[2:]: code for code in codes if code.startswith(("sh", "sz"))}
    tasks = [fetch_batch(codes[index:index + batch_size]) for index in range(0, len(codes), batch_size)]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    results: List[Dict[str, Any]] = []
    for batch in batches:
        if not isinstance(batch, Exception) and batch:
            for quote in batch:
                raw_code = str(quote.get("code", ""))
                quote["code"] = raw_code if raw_code.startswith(("sh", "sz")) else code_map.get(raw_code, raw_code)
                results.append(quote)
    return results


async def build_dashboard() -> Dict[str, Any]:
    from server.realtime import fetch_tencent_quotes
    a_codes = [code for code in get_all_stock_codes() if code.startswith(("sh", "sz")) and code not in {stock[0] for stock in SECTORS.get("etf", {}).get("stocks", [])}]
    stock_numeric_map = {code[2:]: code for code in a_codes}
    index_numeric_map = {code[2:]: code for code in INDEX_CODES}

    quotes_task = _fetch_quotes_batched(a_codes)
    index_quotes_task = fetch_tencent_quotes(INDEX_CODES, timeout=3.0)
    trend_tasks = [_fetch_index_trend(code) for code in CORE_TREND_CODES]
    flow_tasks = [_fetch_index_flow("sh000001"), _fetch_index_flow("sz399001")]

    gathered = await asyncio.gather(
        quotes_task, index_quotes_task, *trend_tasks, *flow_tasks,
        return_exceptions=True,
    )
    raw_quotes = gathered[0] if not isinstance(gathered[0], Exception) else []
    raw_index_quotes = gathered[1] if not isinstance(gathered[1], Exception) else []
    num_trends = len(trend_tasks)
    trend_results = gathered[2:2 + num_trends]
    flow_results = gathered[2 + num_trends:]

    quote_map: Dict[str, Dict[str, Any]] = {}
    sources = set()
    for quote in raw_quotes or []:
        code = _normalize_code(quote.get("code", ""), stock_numeric_map)
        if code in a_codes:
            normalized = dict(quote)
            normalized["code"] = code
            quote_map[code] = normalized
            if normalized.get("source"):
                sources.add(normalized["source"])

    valid_quotes = [q for q in quote_map.values() if _finite(q.get("price")) > 0]
    missing_codes = sorted(set(a_codes) - {q.get("code") for q in valid_quotes})
    coverage_ratio = len(valid_quotes) / len(a_codes) if a_codes else 0.0
    changes = [_finite(q.get("chg")) for q in valid_quotes]
    breadth_buckets = {
        "down_gt5": sum(1 for value in changes if value <= -5),
        "down_2_5": sum(1 for value in changes if -5 < value <= -2),
        "down_0_2": sum(1 for value in changes if -2 < value < 0),
        "flat": sum(1 for value in changes if value == 0),
        "up_0_2": sum(1 for value in changes if 0 < value < 2),
        "up_2_5": sum(1 for value in changes if 2 <= value < 5),
        "up_gt5": sum(1 for value in changes if value >= 5),
    }
    up_count = sum(1 for value in changes if value > 0)
    down_count = sum(1 for value in changes if value < 0)
    flat_count = len(changes) - up_count - down_count
    limit_up = sum(1 for q in valid_quotes if _finite(q.get("chg")) >= _limit_threshold(q.get("code", ""), q.get("name", "")))
    limit_down = sum(1 for q in valid_quotes if _finite(q.get("chg")) <= -_limit_threshold(q.get("code", ""), q.get("name", "")))
    total_amount = sum(_amount_yuan(q) for q in valid_quotes)
    turnover_values = [_finite(q.get("turnover")) for q in valid_quotes if _finite(q.get("turnover")) > 0]
    up_ratio = up_count / len(valid_quotes) * 100 if valid_quotes else 0

    index_map = {}
    for quote in raw_index_quotes or []:
        raw_code = str(quote.get("code", ""))
        code = raw_code if raw_code.startswith(("sh", "sz")) else INDEX_SHORT_CODE_MAP.get(raw_code, index_numeric_map.get(raw_code, raw_code))
        index_map[code] = quote
    indices = []
    for code in INDEX_CODES:
        quote = index_map.get(code, {})
        price = _finite(quote.get("price"))
        has_quote = price > 0
        indices.append({
            "code": code, "name": INDEX_NAMES[code],
            # 缺失指数必须保持 null：0.00 会被下游当成真实点位参与展示与判断。
            "price": round(price, 2) if has_quote else None,
            "chg": round(_finite(quote.get("chg")), 2) if has_quote else None,
            "amount": round(_amount_yuan(quote), 2) if has_quote else None,
            "source": quote.get("source"),
        })

    trends = []
    for code, result in zip(CORE_TREND_CODES, trend_results):
        trend = {"rows": [], "granularity": "暂无数据"} if isinstance(result, Exception) else (result or {})
        rows = trend.get("rows", []) if isinstance(trend, dict) else []
        points = [{"time": row.get("d") or row.get("date"), "close": _finite(row.get("c", row.get("close")))} for row in rows]
        points = [item for item in points if item["time"] and item["close"] > 0]
        trends.append({"code": code, "name": INDEX_NAMES[code], "granularity": trend.get("granularity", "暂无数据"), "points": points})

    sector_keys_by_code: Dict[str, List[str]] = {}
    for key, config in SECTORS.items():
        if key in {"etf", "hk_stocks"}:
            continue
        for stock in config.get("stocks", []):
            if stock and stock[0] in a_codes:
                sector_keys_by_code.setdefault(stock[0], []).append(key)

    sectors = []
    for key, config in SECTORS.items():
        if key in {"etf", "hk_stocks"}:
            continue
        sector_quotes = [quote_map[stock[0]] for stock in config.get("stocks", []) if stock[0] in quote_map]
        if not sector_quotes:
            continue
        sector_changes = [_finite(q.get("chg")) for q in sector_quotes]
        leader = max(sector_quotes, key=lambda item: _finite(item.get("chg")))
        sector_up = sum(1 for value in sector_changes if value > 0)
        sectors.append({
            "key": key, "name": config.get("name", key), "icon": config.get("icon", ""),
            "avg_chg": round(mean(sector_changes), 2),
            "up_ratio": round(sector_up / len(sector_quotes) * 100, 1),
            "amount": round(sum(_amount_yuan(q) for q in sector_quotes), 2),
            "count": len(sector_quotes),
            "leader": {"code": leader.get("code"), "name": leader.get("name"), "chg": round(_finite(leader.get("chg")), 2)},
        })
    sectors.sort(key=lambda item: item["avg_chg"], reverse=True)

    for quote in valid_quotes:
        quote["sector_keys"] = sector_keys_by_code.get(quote.get("code"), [])

    sorted_quotes = sorted(valid_quotes, key=lambda item: _finite(item.get("chg")), reverse=True)
    def compact(quote: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "code": quote.get("code"), "name": quote.get("name"),
            "price": round(_finite(quote.get("price")), 2),
            "chg": round(_finite(quote.get("chg")), 2),
            "turnover": round(_finite(quote.get("turnover")), 2),
            "amount": round(_amount_yuan(quote), 2),
            "sector_keys": quote.get("sector_keys", []),
        }

    index_chgs = [item["chg"] for item in indices if item["price"] is not None and item["chg"] is not None]
    median_chg = median(changes) if changes else 0
    avg_index_chg = mean(index_chgs) if index_chgs else 0
    sentiment_score = round(max(0, min(100, 50 + (up_ratio - 50) * 0.55 + avg_index_chg * 7 + (limit_up - limit_down) * 0.3))) if coverage_ratio >= 0.2 and len(index_chgs) >= 2 else None
    if sentiment_score is None:
        regime = "数据不足"
        judgment = "有效行情覆盖或指数数据不足，暂不输出市场状态判断。"
    elif sentiment_score >= 68:
        regime = "偏强"
        judgment = "市场宽度和指数表现偏强，可关注强势板块中的低位承接机会。"
    elif sentiment_score >= 55:
        regime = "震荡偏多"
        judgment = "指数与个股表现略偏多，适合控制仓位并等待量价确认。"
    elif sentiment_score >= 42:
        regime = "震荡"
        judgment = "多空力量接近平衡，避免追涨，优先采用分批与止损策略。"
    else:
        regime = "偏弱"
        judgment = "市场宽度偏弱，建议降低仓位，优先控制回撤与流动性风险。"

    risk_tags = []
    if coverage_ratio < 0.95:
        risk_tags.append({"level": "high" if coverage_ratio < 0.5 else "medium", "text": "有效行情覆盖不足，指标仅代表当前已返回样本"})
    if up_ratio < 35 and valid_quotes:
        risk_tags.append({"level": "high", "text": "上涨家数占比较低"})
    if valid_quotes and limit_down > limit_up:
        risk_tags.append({"level": "high", "text": "跌停家数多于涨停"})
    if turnover_values and mean(turnover_values) > 8:
        risk_tags.append({"level": "medium", "text": "样本换手偏热"})
    if index_chgs and max(index_chgs) - min(index_chgs) > 1.5:
        risk_tags.append({"level": "medium", "text": "主要指数分化明显"})
    if not risk_tags:
        risk_tags.append({"level": "low", "text": "未发现高优先级系统性风险"})

    flows = [item for item in flow_results if not isinstance(item, Exception)]
    available_flows = [item for item in flows if item.get("available")]
    capital_flow = {
        "available": bool(available_flows),
        "flow_a": round(sum(_finite(item.get("flow_a")) for item in available_flows), 2),
        "flow_b": round(sum(_finite(item.get("flow_b")) for item in available_flows), 2),
        "flow_c": round(sum(_finite(item.get("flow_c")) for item in available_flows), 2),
        "flow_d": round(sum(_finite(item.get("flow_d")) for item in available_flows), 2),
        "flow_e": round(sum(_finite(item.get("flow_e")) for item in available_flows), 2),
        "unit": "元",
        "details": flows,
        "definition": "上证指数与深证成指资金流接口原始分类值合计；因上游字段定义未能核实，不标注主力/大单类别。", 
    }

    return {
        "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
        "refresh_interval_seconds": CACHE_TTL,
        "refresh_mode": "periodic_snapshot",
        "scope": "系统覆盖的沪深A股样本（不含ETF/港股）",
        "source": "+".join(sorted(sources)) or "unavailable",
        "coverage": {"configured": len(a_codes), "valid": len(valid_quotes), "ratio": round(coverage_ratio * 100, 1), "missing_count": len(missing_codes), "missing_codes": missing_codes[:50], "truncated": len(missing_codes) > 50},
        "data_quality": {"level": "good" if coverage_ratio >= 0.95 else "partial" if coverage_ratio >= 0.5 else "poor", "coverage_ratio": round(coverage_ratio * 100, 1), "index_count": len(index_chgs), "judgment_available": sentiment_score is not None, "message": "行情与指数覆盖正常。" if coverage_ratio >= 0.95 and len(index_chgs) >= 2 else "部分行情或指数缺失，情绪结论可能偏差。" if coverage_ratio >= 0.5 else "有效数据不足，不生成市场情绪结论。"},
        "filter_options": {"breadth_buckets": breadth_buckets, "sector_keys": [item["key"] for item in sectors]},
        "indices": indices,
        "index_trends": trends,
        "breadth": {
            "up": up_count, "down": down_count, "flat": flat_count,
            "up_ratio": round(up_ratio, 1), "median_chg": round(median_chg, 2),
            "limit_up": limit_up, "limit_down": limit_down,
            "distribution": _distribution(changes),
            "buckets": breadth_buckets,
        },
        "turnover": {"amount": round(total_amount, 2), "avg_turnover": round(mean(turnover_values), 2) if turnover_values else 0},
        "capital_flow": capital_flow,
        "sectors": sectors,
        "leaders": [compact(item) for item in sorted_quotes[:10]],
        "laggards": [compact(item) for item in list(reversed(sorted_quotes[-10:]))],
        "quotes": [compact(item) for item in sorted(valid_quotes, key=_amount_yuan, reverse=True)],
        "judgment": {"score": sentiment_score, "regime": regime, "text": judgment},
        "risk_tags": risk_tags,
        "opportunities": [item["name"] for item in sectors[:3] if item["avg_chg"] > 0],
        "disclaimer": "市场统计基于系统覆盖的沪深A股样本（不含ETF与港股），不等同于交易所全量股票；数据仅供研究，不构成投资建议。",
    }




async def get_dashboard() -> Dict[str, Any]:
    """带缓存与 last-good 回落的大盘快照，供 HTTP 与内部模块统一调用。"""
    global _LAST_GOOD_DASHBOARD

    async def fetch_data():
        try:
            result = await build_dashboard()
            if result and (result.get("coverage") or {}).get("valid", 0) > 0:
                _LAST_GOOD_DASHBOARD = result
            if result is None or (result.get("coverage") or {}).get("valid", 0) == 0:
                return None
            return result
        except Exception as exc:  # 上游异常不穿透到调用方
            logger.exception("market dashboard build failed: %s", exc)
            return None

    data = await cache.get_or_fetch(CACHE_KEY, fetch_data, CACHE_TTL)
    if data is None:
        if _LAST_GOOD_DASHBOARD:
            stale = dict(_LAST_GOOD_DASHBOARD)
            stale["stale"] = True
            stale["warning"] = "实时行情暂不可用，当前展示上次成功快照。"
            return {"ok": True, "data": stale}
        return {"ok": False, "message": "大盘数据暂不可用，请稍后重试", "data": None}
    data = dict(data)
    data["stale"] = False
    return {"ok": True, "data": data}


def last_good() -> Dict[str, Any] | None:
    """最近一次成功快照（只读，供健康检查/降级判定）。"""
    return _LAST_GOOD_DASHBOARD
