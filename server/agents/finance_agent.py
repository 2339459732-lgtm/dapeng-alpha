"""DAPENG Alpha v4.0 - 金融分析 Agent

整合宏观数据（GDP/CPI/PMI/利率）+ 板块资金流向，
输出市场情绪评分（0-100）+ 风险等级。
"""

import asyncio
import logging
from datetime import datetime
from statistics import median
from typing import Any, Dict, Optional

from server.agents import AgentStatus, update_agent_status
from server.config import SECTORS
from server.cache import cache
from server.westock import westock
from server.core.numbers import safe_float as _safe_float

logger = logging.getLogger(__name__)

# 情绪评分缓存
_MARKET_SENTIMENT_CACHE_KEY = "market_sentiment"
_CACHE_TTL = 300  # 5分钟缓存


async def get_market_sentiment() -> Dict[str, Any]:
    """
    获取市场情绪评分

    Returns:
        {
            "score": int (0-100),      # 市场情绪评分
            "risk_level": str,           # 风险等级（低/中/高）
            "trend": str,                # 市场趋势（bullish/bearish/neutral）
            "sector_hot": List[str],     # 热门板块
            "summary": str,              # 综合评述
            "updated_at": str,            # 更新时间
        }
    """
    cached = cache.get(_MARKET_SENTIMENT_CACHE_KEY)
    if cached is not None:
        return cached

    update_agent_status("finance_analyzer", AgentStatus.RUNNING)

    try:
        # 1. 获取主要指数行情
        indices = await _fetch_indices()

        # 2. 指数不可用时不伪造中性情绪分。
        score, trend, risk_level = _calc_sentiment_score(indices)

        # 3. 识别热门板块：根据实时样本排序，不使用固定名单。
        sector_hot_details = await _identify_hot_sectors()
        sector_hot = [item["name"] for item in sector_hot_details]

        # 4. 生成综合评述
        summary = _generate_summary(indices, score, trend)

        result = {
            "score": score,
            "risk_level": risk_level,
            "trend": trend,
            "sector_hot": sector_hot,
            "sector_hot_details": sector_hot_details,
            "summary": summary,
            "indices": indices,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_quality": {"available": score is not None, "source": "westock-data" if indices else "unavailable", "index_count": len([item for item in indices.values() if item.get("price", 0) > 0]), "sector_count": len(sector_hot_details), "message": "指数行情已获取；评分只反映主要指数涨跌，不包含样本广度/成交量变化。" if score is not None else "有效指数不足，未生成情绪分。"},
        }

        update_agent_status("finance_analyzer", AgentStatus.IDLE)
        logger.info("finance_analyzer: 市场情绪评分 %s, 趋势 %s", score if score is not None else "N/A", trend)
        if result.get("data_quality", {}).get("available") and score is not None:
            cache.set(_MARKET_SENTIMENT_CACHE_KEY, result, _CACHE_TTL)
        return result

    except Exception as e:
        logger.error("finance_analyzer error: %s", str(e), exc_info=True)
        update_agent_status("finance_analyzer", AgentStatus.ERROR, error_msg=str(e))
        return _default_sentiment()


async def _fetch_indices() -> Dict[str, Any]:
    """获取主要指数行情"""
    indices_codes = ["sh000001", "sz399001", "sz399006"]
    indices = {}

    try:
        quotes_list = await westock.batch_quotes(indices_codes)
        # 忽略上游返回的零价占位行情，避免将缺失误认为平盘。
        for data in quotes_list:
            code = data.get("code", "")
            price = _safe_float(data.get("price"))
            if code and price > 0:
                indices[code] = {
                    "name": data.get("name", code),
                    "price": price,
                    "change_pct": _safe_float(data.get("chg")),
                    "volume": _safe_float(data.get("volume")),
                }
    except Exception as e:
        logger.warning("_fetch_indices error: %s", str(e))

    return indices


async def _identify_hot_sectors(limit: int = 5) -> list:
    """按板块当前样本中位涨跌幅动态排名；数据不足时返回空列表。"""
    from server.realtime import fetch_tencent_quotes

    from server.config import get_all_stock_codes

    codes = [code for code in get_all_stock_codes() if code.startswith(("sh", "sz")) and code not in {stock[0] for stock in SECTORS.get("etf", {}).get("stocks", [])}]
    try:
        quotes = await asyncio.wait_for(fetch_tencent_quotes(codes, timeout=5.0), timeout=6.0)
    except Exception as exc:
        logger.debug("hot-sector batch quote failed: %s", exc)
        return []
    quote_map = {item.get("code"): item for item in (quotes or []) if item.get("code") and _safe_float(item.get("price")) > 0}
    sector_rows = []
    for key, sector in SECTORS.items():
        if key in {"etf", "hk_stocks"}:
            continue
        changes = [_safe_float(quote_map[stock[0]].get("chg")) for stock in sector.get("stocks", []) if stock[0] in quote_map]
        if changes:
            sector_rows.append((sector.get("name", key), median(changes), len(changes)))
    sector_rows.sort(key=lambda row: (row[1], row[2]), reverse=True)
    return [{"name": name, "median_chg": round(change, 2), "sample_count": count} for name, change, count in sector_rows[:limit]]


def _calc_sentiment_score(indices: Dict) -> tuple:
    """根据有效指数涨跌生成方向性参考；有效指数不足时返回无分值。"""
    valid = [item for item in indices.values() if _safe_float(item.get("price")) > 0]
    if len(valid) < 2:
        return None, "unknown", "未知"
    changes = [_safe_float(item.get("change_pct")) for item in valid]
    avg_change = sum(changes) / len(changes)
    score = int(max(0, min(100, 50 + avg_change * 10)))
    trend = "bullish" if avg_change > 1.0 else "bearish" if avg_change < -1.0 else "neutral"
    risk_level = "偏强" if score >= 70 else "中性" if score >= 40 else "偏弱"
    return score, trend, risk_level



def _generate_summary(indices: Dict, score: Optional[int], trend: str) -> str:
    """生成市场综合评述，不将数据缺失转换为中性分。"""
    index_desc = []
    for code, data in indices.items():
        name = data.get("name", code)
        change = data.get("change_pct", 0)
        index_desc.append(f"{name}{change:+.2f}%")

    trend_desc = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡", "unknown": "暂无法判断"}.get(trend, "暂无法判断")
    if score is None:
        return "主要指数有效行情不足，暂不生成市场情绪评分。" + ("已获取：" + ", ".join(index_desc) + "。" if index_desc else "")

    if score >= 70:
        sentiment_desc = "市场情绪乐观"
    elif score >= 50:
        sentiment_desc = "市场情绪中性偏暖"
    elif score >= 30:
        sentiment_desc = "市场情绪偏弱"
    else:
        sentiment_desc = "市场情绪悲观"

    return f"{sentiment_desc}，主要指数：{', '.join(index_desc)}，整体趋势{trend_desc}。"


def _default_sentiment() -> Dict[str, Any]:
    """API失败时返回无结论状态，避免用中性默认分掩盖故障。"""
    return {
        "score": None,
        "risk_level": "未知",
        "trend": "unknown",
        "sector_hot": [],
        "sector_hot_details": [],
        "summary": "市场数据获取失败，暂不输出市场情绪结论。",
        "indices": {},
        "data_quality": {"available": False, "source": "unavailable", "index_count": 0, "sector_count": 0, "message": "数据源不可用；本结果为无结论状态。"},
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
