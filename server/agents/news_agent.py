"""DAPENG Alpha v4.0 - 新闻分析 Agent

获取个股新闻/公告，进行情绪分析（正面/中性/负面），
输出 sentiment_score (-1~1)，提取关键事件标签。
"""

import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

from server.agents import AgentStatus, update_agent_status
from server.westock import westock

logger = logging.getLogger(__name__)


async def fetch_stock_news(code: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    获取个股新闻/公告（通过 westock CLI）

    Args:
        code: 股票代码 (sh600519)
        limit: 返回条数

    Returns:
        [{"title": ..., "content": ..., "time": ..., "source": ...}, ...]
    """
    update_agent_status("news_analyzer", AgentStatus.RUNNING)

    try:
        # 调用 westock 获取新闻公告
        # westock 目前可能不支持直接获取新闻，这里提供接口框架
        result = await westock.get_news(code, limit=limit)

        if result:
            logger.info("news_analyzer: %s 获取 %d 条新闻", code, len(result))
            update_agent_status("news_analyzer", AgentStatus.IDLE)
            return result
        else:
            # 如果 westock 不支持，返回空列表
            logger.warning("news_analyzer: %s westock 未返回新闻数据", code)
            update_agent_status("news_analyzer", AgentStatus.IDLE)
            return []

    except Exception as e:
        logger.error("news_analyzer error for %s: %s", code, str(e))
        update_agent_status("news_analyzer", AgentStatus.ERROR, error_msg=str(e))
        return []


async def analyze_sentiment(news_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    对新闻列表进行情绪分析

    Returns:
        {
            "sentiment_score": float (-1.0 ~ 1.0),
            "sentiment_label": str ("正面"/"中性"/"负面"),
            "keywords": List[str],
            "events": List[str],
        }
    """
    if not news_list:
        return {
            "sentiment_score": 0.0,
            "sentiment_label": "中性",
            "keywords": [],
            "events": [],
        }

    # 简化版情绪分析（基于关键词）
    positive_keywords = [
        "增长", "上升", "利好", "盈利", "分红", "重组", "并购",
        "创新", "突破", "扩产", "订单", "增持", "回购",
    ]
    negative_keywords = [
        "下跌", "亏损", "诉讼", "处罚", "减持", "质押", "退市",
        "暴雷", "造假", "违规", "冻结", "仲裁", "下滑",
    ]

    total_score = 0.0
    matched_keywords = set()
    events = []

    for news in news_list[:10]:  # 只看最新10条
        title = news.get("title", "")
        content = news.get("content", "")
        text = title + " " + content

        pos_count = sum(1 for kw in positive_keywords if kw in text)
        neg_count = sum(1 for kw in negative_keywords if kw in text)

        # 提取关键词
        for kw in positive_keywords + negative_keywords:
            if kw in text:
                matched_keywords.add(kw)

        # 简单评分
        if pos_count > neg_count:
            total_score += 0.3
        elif neg_count > pos_count:
            total_score -= 0.3

        # 提取事件标签
        if "增持" in text or "回购" in text:
            events.append("股东增持")
        if "减持" in text:
            events.append("股东减持")
        if "重组" in text or "并购" in text:
            events.append("并购重组")
        if "分红" in text:
            events.append("分红派息")
        if "诉讼" in text or "仲裁" in text:
            events.append("法律诉讼")
        if "订单" in text or "中标" in text:
            events.append("重大订单")

    # 归一化到 [-1, 1]
    sentiment_score = max(-1.0, min(1.0, total_score))

    if sentiment_score > 0.3:
        label = "正面"
    elif sentiment_score < -0.3:
        label = "负面"
    else:
        label = "中性"

    return {
        "sentiment_score": round(sentiment_score, 2),
        "sentiment_label": label,
        "keywords": list(matched_keywords)[:5],
        "events": list(set(events)),
    }


async def get_stock_sentiment(code: str) -> Dict[str, Any]:
    """
    获取单只股票的情绪分析结果（一键调用）

    Returns:
        {
            "code": str,
            "name": str,
            "sentiment_score": float,
            "sentiment_label": str,
            "keywords": List[str],
            "events": List[str],
            "news_count": int,
            "updated_at": str,
        }
    """
    from server.config import find_stock_name

    news_list = await fetch_stock_news(code, limit=10)
    sentiment = await analyze_sentiment(news_list)

    name = find_stock_name(code)

    return {
        "code": code,
        "name": name,
        **sentiment,
        "news_count": len(news_list),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
