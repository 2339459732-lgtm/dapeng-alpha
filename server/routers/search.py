"""BigA Pro 策略选股系统 - GET /api/search 搜索 + GET /api/screen 选股接口"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import (
    CACHE_TTL_KLINE,
    CACHE_TTL_SCREEN,
    SECTORS,
    find_stock_name,
    get_all_stock_codes,
)
from server.strategies import compute_strategy
from server.westock import westock

from server.stock_pool import stock_pool
import requests

logger = logging.getLogger(__name__)
router = APIRouter()


async def _fetch_quotes_batched(codes, batch_size: int = 100, concurrency: int = 8):
    """限并发分批腾讯实时取价（与 /api/quotes、/api/market-dashboard 同一实现）。

    westock CLI 链路已废弃 → 旧实现 quote_map 恒空、价格/涨跌/换手/PE 全 0（评分却为真）。
    改走腾讯批量；取不到不写 0，交由调用方给 null + reason。
    """
    from server.realtime import fetch_tencent_quotes
    if not codes:
        return []
    sem = asyncio.Semaphore(concurrency)
    numeric_map = {str(c)[2:]: c for c in codes}

    async def one(batch):
        async with sem:
            try:
                qs = await fetch_tencent_quotes(batch, timeout=4.0)
            except Exception as exc:
                logger.warning("screen 腾讯取价失败: %s", str(exc))
                return []
            out = []
            for q in qs or []:
                raw = str(q.get("code", ""))
                q["code"] = raw if raw.startswith(("sh", "sz")) else numeric_map.get(raw, raw)
                out.append(q)
            return out

    tasks = [one(codes[i:i + batch_size]) for i in range(0, len(codes), batch_size)]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    merged = []
    for b in batches:
        if not isinstance(b, Exception) and b:
            merged.extend(b)
    return merged


@router.get("/search")
async def search_stock(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(30, ge=1, le=100, description="最大返回数量"),
):
    """
    搜索股票 — 支持全市场 5000+ A 股代码与名称跨板块极速模糊匹配。
    1. 优先在 StockPool 全量 A 股池中精准及模糊匹配
    2. 如果未命中，调用东方财富 Suggest 接口作为公网扩展
    """
    keyword_clean = keyword.strip()
    if not keyword_clean:
        return {"ok": True, "data": {"results": [], "total": 0, "source": "empty"}}

    # 1. 在 StockPool 中搜索
    pool_results = stock_pool.search_stocks(keyword_clean, limit=limit)
    if pool_results:
        return {
            "ok": True,
            "data": {
                "results": pool_results,
                "total": len(pool_results),
                "source": "stock_pool_full_market"
            }
        }

    # 2. 东方财富公网搜索扩展
    try:
        url = "http://searchapi.eastmoney.com/api/suggest/get"
        params = {
            "input": keyword_clean,
            "type": "14",
            "token": "4f1862f4a1c33c93d7733fb7471a8e50",
            "count": str(limit)
        }
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=3.0))
        if resp.status_code == 200:
            items = resp.json().get("QuotationCodeTable", {}).get("Data", [])
            results = []
            for item in items:
                code_raw = str(item.get("Code", ""))
                name_raw = str(item.get("Name", ""))
                market_type = str(item.get("SecurityTypeName", ""))
                if "A股" in market_type or "指数" in market_type or len(code_raw) == 6:
                    full_code = ("sh" if code_raw.startswith("6") or code_raw.startswith("9") else "sz" if code_raw.startswith("0") or code_raw.startswith("3") else "bj" if code_raw.startswith("8") or code_raw.startswith("4") else "") + code_raw
                    results.append({
                        "code": full_code if full_code else code_raw,
                        "symbol": code_raw,
                        "name": name_raw,
                        "sector": "",
                        "sector_name": market_type
                    })
            if results:
                return {
                    "ok": True,
                    "data": {
                        "results": results,
                        "total": len(results),
                        "source": "eastmoney_suggest_api"
                    }
                }
    except Exception as e:
        logger.debug("Eastmoney suggest search exception: %s", e)

    return {"ok": True, "data": {"results": [], "total": 0, "source": "none"}}


@router.get("/screen")
async def screen_stocks(
    min_score: Optional[int] = Query(70, description="最低综合评分"),
    min_chg: Optional[float] = Query(None, description="最低涨跌幅%"),
    min_vol: Optional[float] = Query(None, description="最低量比"),
    min_turnover: Optional[float] = Query(None, description="最低换手率%"),
    max_pe: Optional[float] = Query(None, description="最高市盈率"),
    sector: Optional[str] = Query(None, description="限定板块"),
    limit: Optional[int] = Query(20, ge=1, le=100, description="返回数量上限"),
):
    """
    条件选股 — 按策略评分和涨跌幅筛选全市场股票。

    扫描所有板块（或指定板块），返回满足条件的股票列表。
    """
    cache_key = f"screen:{min_score}:{min_chg}:{min_vol}:{min_turnover}:{max_pe}:{sector}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"ok": True, "data": cached}

    # 确定扫描范围
    if sector:
        sectors_to_scan = {sector: SECTORS.get(sector, SECTORS["semiconductor"])}
    else:
        sectors_to_scan = SECTORS

    # 收集所有待扫描的股票代码
    all_codes = []
    code_sector_map = {}
    for s_key, s_data in sectors_to_scan.items():
        for stock in s_data["stocks"]:
            code, name = stock[0], stock[1]
            if code not in code_sector_map:
                all_codes.append(code)
                code_sector_map[code] = (s_key, s_data["name"], name)

    # 并发获取行情（腾讯实时批量；westock CLI 链路已废弃）
    quotes_data = await _fetch_quotes_batched(all_codes)

    # 构建行情 map
    quote_map = {}
    for q in quotes_data or []:
        code = q.get("code", "")
        if code:
            s_key, s_name, stock_name = code_sector_map.get(code, ("", "", code))
            if not q.get("name") or q["name"] == code:
                q["name"] = stock_name
            quote_map[code] = q

    # 获取 K 线并计算策略评分
    # 先检查缓存，未命中的批量获取
    kline_results = {}
    missing_codes = []
    for code in all_codes:
        kline_cache_key = f"raw_kline:{code}:day"
        cached_kline = cache.get(kline_cache_key)
        if cached_kline is not None:
            kline_results[code] = cached_kline
        else:
            missing_codes.append(code)

    if missing_codes:
        try:
            batch_result = await asyncio.wait_for(
                westock.batch_klines(missing_codes, period="day"),
                timeout=25.0
            )
            for code, klines in batch_result.items():
                kline_results[code] = klines
                if klines:
                    cache.set(f"raw_kline:{code}:day", klines, CACHE_TTL_KLINE)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("screen kline fetch error: %s", str(e))

    # 筛选
    results = []
    for code in all_codes:
        quote = quote_map.get(code, {})
        chg = quote.get("chg")
        s_key, s_name, stock_name = code_sector_map.get(code, ("", "", code))

        # 行情字段过滤：这些条件必须在后端执行，避免前端展示与接口结果不一致
        turnover = quote.get("turnover")
        pe = quote.get("pe")
        if min_chg is not None and (chg is None or chg < min_chg):
            continue
        if min_turnover is not None and (turnover is None or turnover < min_turnover):
            continue
        if max_pe is not None and pe is not None and pe > max_pe:
            continue

        # 量比：使用最新成交量与此前最多5根K线平均成交量计算
        klines = kline_results.get(code)
        vol_ratio = 0.0
        if klines and len(klines) >= 2:
            volumes = [float(k.get("v", 0) or 0) for k in klines]
            baseline = volumes[max(0, len(volumes) - 6):-1]
            if baseline:
                avg_volume = sum(baseline) / len(baseline)
                if avg_volume > 0:
                    vol_ratio = volumes[-1] / avg_volume
        if min_vol is not None and vol_ratio < min_vol:
            continue

        # 计算策略评分
        klines = kline_results.get(code)
        if klines and len(klines) >= 5:
            strategy_result = compute_strategy(klines)
            score = strategy_result.total_score
            signals = [
                {"type": s.type, "label": s.label, "score": s.score, "desc": s.desc}
                for s in strategy_result.signals
            ]
        else:
            score = max(0, min(100, 50 + (chg or 0) * 5))
            signals = []

        # 评分过滤
        if score < min_score:
            continue

        results.append({
            "code": code,
            "name": quote.get("name", stock_name),
            "price": quote.get("price"),
            "chg": chg,
            "volume_ratio": round(vol_ratio, 2),
            "turnover": turnover,
            "pe": pe,
            "score": round(score, 1),
            "signals": signals,
            "sector": s_key,
            "sector_name": s_name,
            "unavailable_reason": quote.get("unavailable_reason") or (
                None if quote.get("price") is not None else "行情暂不可得（腾讯实时源未返回该标的）"),
        })

    # 按评分降序
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    data = {
        "total": len(results),
        "criteria": {
            "min_score": min_score,
            "min_chg": min_chg,
            "min_vol": min_vol,
            "min_turnover": min_turnover,
            "max_pe": max_pe,
            "sector": sector,
        },
        "stocks": results,
    }

    cache.set(cache_key, data, CACHE_TTL_SCREEN)
    return {"ok": True, "data": data}
