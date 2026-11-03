"""BigA Pro 策略选股系统 - GET /api/quotes 行情接口

优化点：
- 使用 westock.batch_klines 批量并发获取K线
- 分离行情和K线缓存（raw_kline vs kline）
- 策略评分始终在请求时计算，保证准确性
- v4.1: 腾讯HTTP fallback — CLI失败时自动切换HTTP数据源
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import (
    CACHE_TTL_KLINE,
    CACHE_TTL_QUOTES,
    DEFAULT_SECTOR,
    KLINE_LIMIT,
    find_stock_name,
    get_sector_name,
    get_sector_stocks,
)
from server.realtime import fetch_tencent_quotes
from server.strategies import compute_strategy
from server.tencent_kline import fetch_tencent_kline
from server.eastmoney import fetch_kline_eastmoney
from server.westock import westock

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/quotes")
async def get_quotes(
    sector: Optional[str] = Query(DEFAULT_SECTOR, description="板块 key"),
    fast: Optional[int] = Query(0, description="快速模式(1): 只返回行情，不等K线策略评分"),
    page: Optional[int] = Query(1, ge=1, description="页码"),
    limit: Optional[int] = Query(None, description="每页数量（不传则返回全量）"),
):
    """
    获取板块行情 + 策略评分（支持全量或分页）。

    v5.5 优化：
    - fast=1：仅返回行情数据（毫秒级），策略评分用缓存或快速估算
    - fast=0（默认）：返回完整行情+K线策略评分
    - page/limit：支持前端分页或一次性加载
    """
    # 获取板块配置
    stocks_config = get_sector_stocks(sector)
    sector_name = get_sector_name(sector)
    codes = [stock[0] for stock in stocks_config]
    name_map = {stock[0]: stock[1] for stock in stocks_config}

    # fast=1 时缩短超时（2秒），优先用缓存
    quotes_timeout = 2.0 if fast else 6.0

    # 检查行情缓存（fast模式优先读缓存，避免网络等待）
    quotes_cache_key = f"quotes_raw:{sector}"
    quotes_data = None
    if fast:
        cached_quotes = cache.get(quotes_cache_key)
        if cached_quotes:
            quotes_data = cached_quotes
            logger.debug("quotes fast=1: using cached quotes for %s", sector)

    # 无缓存时从腾讯HTTP拉取
    if not quotes_data:
        try:
            tencent_quotes = await fetch_tencent_quotes(codes, timeout=quotes_timeout)
            if tencent_quotes:
                quotes_data = []
                tencent_map = {q.get("code", ""): q for q in tencent_quotes}
                for code in codes:
                    tq = tencent_map.get(code, {})
                    # 缺失标的必须落 None 而不是 0：
                    # 0 会被下游当成「有效报价」统计（valid_quotes）、前端也会渲染成 0.00 元，
                    # 用户看到的是「这只股票跌到 0 了」而不是「这只票没取到数」。
                    quotes_data.append({
                        "code": code,
                        "name": tq.get("name", name_map.get(code, code)),
                        "price": tq.get("price"),
                        "chg": tq.get("chg"),
                        "high": tq.get("high"),
                        "low": tq.get("low"),
                        "open": tq.get("open"),
                        "yclose": tq.get("yclose"),
                        "volume": tq.get("volume"),
                        "amount": tq.get("amount"),
                        "turnover": tq.get("turnover"),
                        "pe": tq.get("pe"),
                    })
                # 缓存行情数据（60秒TTL）
                cache.set(quotes_cache_key, quotes_data, CACHE_TTL_QUOTES)
                logger.info("Quotes from Tencent HTTP: %d/%d stocks (timeout=%.1fs)", len(tencent_quotes), len(codes), quotes_timeout)
        except Exception as e:
            logger.warning("Tencent HTTP quotes failed (timeout=%.1fs): %s", quotes_timeout, str(e))
            # fast模式超时后尝试使用旧缓存（即使过期也用）
            if fast:
                # 关键：cache.get() 遇过期条目会**立即删除**，之后再去底层取就永远为空，
                # 因此这里必须用 get_entry()（保留过期条目）才能真正回落到旧数据。
                # 原实现先 cache.get() 再读 cache._store，是一条永远走不到的死代码。
                entry = cache.get_entry(quotes_cache_key)
                if entry and entry.get("value"):
                    quotes_data = entry["value"]
                    logger.info(
                        "Using %s quotes cache for fast mode (age=%.0fs)",
                        "expired" if entry.get("expired") else "fresh",
                        entry.get("age") or 0,
                    )

    if not quotes_data:
        # Fallback: 使用CLI获取
        try:
            cli_quotes = await westock.batch_quotes(codes)
            if cli_quotes:
                quotes_data = cli_quotes
                cache.set(quotes_cache_key, quotes_data, CACHE_TTL_QUOTES)
                logger.info("Quotes from CLI: %d stocks", len(cli_quotes))
        except Exception as e:
            logger.error("CLI quotes fallback also failed: %s", str(e))

    if not quotes_data:
        # 全部失败，使用默认空数据
        quotes_data = [
            {"code": c, "name": name_map.get(c, c), "price": None, "chg": None,
             "high": None, "low": None, "yclose": None, "volume": None, "amount": None,
             "turnover": None, "pe": None,
             "unavailable_reason": "行情暂不可得（腾讯/东财均无返回）"}
            for c in codes
        ]

    # 构建行情 map
    quote_map = {}
    for q in quotes_data:
        code = q.get("code", "")
        if not q.get("name") or q["name"] == code:
            q["name"] = name_map.get(code, code)
        quote_map[code] = q

    # 批量获取 K 线（先检查缓存，未命中的再获取）
    # fast=1 时：只用缓存，不等网络，策略评分使用简单估算
    kline_results = {}
    missing_codes = []

    for code in codes:
        kline_cache_key = f"raw_kline:{code}:day"
        cached_kline = cache.get(kline_cache_key)
        if cached_kline is not None:
            kline_results[code] = cached_kline
        else:
            missing_codes.append(code)

    # 获取缺失的 K 线（fast=1 时跳过网络请求）
    if missing_codes and not fast:
        # v4.5: 优先腾讯HTTP（并发、毫秒级），CLI作为备用
        tencent_failed_codes = []
        # 批量并行获取腾讯HTTP K线（每批10只并发）
        batch_size = 10
        for i in range(0, len(missing_codes), batch_size):
            batch = missing_codes[i:i + batch_size]
            tencent_tasks = [fetch_tencent_kline(code, period="day", limit=KLINE_LIMIT) for code in batch]
            tencent_results = await asyncio.gather(*tencent_tasks, return_exceptions=True)
            for j, code in enumerate(batch):
                result = tencent_results[j]
                if isinstance(result, Exception) or not result:
                    tencent_failed_codes.append(code)
                else:
                    kline_results[code] = result
                    cache.set(f"raw_kline:{code}:day", result, CACHE_TTL_KLINE)

        # 腾讯HTTP失败的，优先尝试东方财富HTTP，其次尝试CLI
        if tencent_failed_codes:
            logger.info("Tencent kline failed for %d codes, trying Eastmoney HTTP fallback", len(tencent_failed_codes))
            em_failed_codes = []
            for i in range(0, len(tencent_failed_codes), 10):
                batch = tencent_failed_codes[i:i + 10]
                em_tasks = [fetch_kline_eastmoney(code, period="day", limit=KLINE_LIMIT) for code in batch]
                em_results = await asyncio.gather(*em_tasks, return_exceptions=True)
                for j, code in enumerate(batch):
                    res = em_results[j]
                    if isinstance(res, Exception) or not res:
                        em_failed_codes.append(code)
                    else:
                        kline_results[code] = res
                        cache.set(f"raw_kline:{code}:day", res, CACHE_TTL_KLINE)

            if em_failed_codes:
                logger.info("Eastmoney kline also failed for %d codes, trying CLI fallback", len(em_failed_codes))
                for i in range(0, len(em_failed_codes), 3):
                    batch = em_failed_codes[i:i + 3]
                    try:
                        cli_result = await asyncio.wait_for(
                            westock.batch_klines(batch, period="day"),
                            timeout=20.0
                        )
                        for code, klines in cli_result.items():
                            if klines:
                                kline_results[code] = klines
                                cache.set(f"raw_kline:{code}:day", klines, CACHE_TTL_KLINE)
                    except Exception as e:
                        logger.warning("CLI kline fallback failed for %s: %s", batch, str(e))
    elif missing_codes and fast:
        logger.debug("fast=1: skipping kline fetch for %d uncached codes", len(missing_codes))

    # 组装结果
    # fast=1：跳过策略评分计算，用简单估算（节省CPU，快速返回）
    # fast=0：完整策略评分
    # 检查是否有整体策略缓存
    strategy_cache_key = f"strategy_scores:{sector}"
    cached_scores = cache.get(strategy_cache_key) if fast else None

    stock_results = []
    for stock in stocks_config:
        code = stock[0]
        name = stock[1]
        quote = quote_map.get(code, {
            "code": code, "name": name, "price": None, "chg": None,
            "high": None, "low": None, "yclose": None, "volume": None, "amount": None,
            "unavailable_reason": "该标的行情暂不可得",
        })
        quote["name"] = name

        klines = kline_results.get(code)

        if fast:
            # fast=1: 优先用缓存的策略评分
            if cached_scores and code in cached_scores:
                score = cached_scores[code]["score"]
                signals = cached_scores[code]["signals"]
            else:
                # 无缓存时简单估算（不计算K线策略）
                chg = quote.get("chg") or 0
                score = max(0, min(100, 50 + chg * 5))
                signals = []
        elif klines and len(klines) >= 5:
            strategy_result = compute_strategy(klines)
            score = strategy_result.total_score
            signals = [
                {"type": s.type, "label": s.label, "score": s.score, "desc": s.desc}
                for s in strategy_result.signals
            ]
        else:
            chg = quote.get("chg") or 0
            score = max(0, min(100, 50 + chg * 5))
            signals = []

        stock_results.append({
            "code": code,
            "name": quote.get("name", name),
            "price": quote.get("price"),
            "chg": quote.get("chg"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "yclose": quote.get("yclose"),
            "volume": quote.get("volume"),
            "amount": quote.get("amount"),
            "score": round(score, 1),
            "signals": signals,
            "turnover": quote.get("turnover"),
            # 价格缺失时补一条明确原因，前端据此显示「—」而不是 0.00
            "unavailable_reason": quote.get("unavailable_reason") or (
                None if isinstance(quote.get("price"), (int, float)) and quote.get("price") > 0
                else "该标的行情暂不可得（价格为空）"
            ),
        })

    # 按评分降序排序
    stock_results.sort(key=lambda x: x["score"], reverse=True)

    # fast=0 时缓存策略评分供 fast=1 复用
    if not fast and stock_results:
        scores_map = {s["code"]: {"score": s["score"], "signals": s["signals"]} for s in stock_results}
        cache.set(strategy_cache_key, scores_map, CACHE_TTL_KLINE)  # 10分钟TTL

    total = len(stock_results)
    if limit is not None:
        start = (page - 1) * limit
        paged_stocks = stock_results[start:start + limit]
    else:
        paged_stocks = stock_results

    # 有效报价 = 价格是正数。原实现用「price is not None」，
    # 而缺失标的曾被填成 0，于是港股通 18 只全部无价却报 valid=18。
    def _has_price(s):
        p = s.get("price")
        return isinstance(p, (int, float)) and p > 0

    valid_count = sum(1 for s in stock_results if _has_price(s))

    data = {
        "sector": sector,
        "sector_name": sector_name,
        "total": total,
        "page": page,
        "limit": limit if limit is not None else total,
        "stocks": paged_stocks,
        "source": "Tencent / Eastmoney Realtime Quotes",
        "valid_quotes": valid_count,
        "degraded": valid_count == 0 if stock_results else True,
        "reason": None if valid_count else "行情暂不可得：腾讯/东财均未返回有效报价（价格字段为 null，非 0）",
    }

    return {"ok": True, "data": data}


@router.get("/quotes/multi")
async def get_quotes_multi(
    codes: str = Query(..., description="逗号分隔的股票代码，如 sh600519,sz000001"),
):
    """批量获取指定股票的实时行情"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"ok": False, "source": "unavailable", "reason": "未提供有效股票代码", "data": []}

    try:
        from server.realtime import fetch_tencent_quotes
        quotes = await fetch_tencent_quotes(code_list, timeout=4.0)
        if quotes:
            return {"ok": True, "source": "Tencent Realtime", "data": quotes}
        raise RuntimeError("Tencent 未返回任何行情")
    except Exception as e:
        logger.warning("get_quotes_multi tencent error: %s", e)
        try:
            from server.eastmoney import fetch_quotes
            quotes = await fetch_quotes(code_list, timeout=4.0)
            if quotes:
                return {"ok": True, "source": "Eastmoney Realtime", "data": quotes}
            return {"ok": False, "source": "unavailable",
                    "reason": "所有行情源均无返回（腾讯+东财）", "data": []}
        except Exception as e2:
            logger.error("get_quotes_multi fallback failed: %s", e2)
            return {"ok": False, "source": "unavailable",
                    "reason": f"行情源不可用: {e2}", "data": []}
