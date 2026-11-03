"""大盘纵览 HTTP 入口（薄路由）。

业务逻辑已下沉到 server/services/market_dashboard.py；
本文件只负责路由声明与响应封装，保持原有路径与返回结构不变。
"""
from __future__ import annotations

from fastapi import APIRouter

from server.services.market_dashboard import (  # noqa: F401  （CACHE_* 为兼容旧引用保留）
    CACHE_KEY,
    CACHE_TTL,
    _LAST_GOOD_DASHBOARD,
    build_dashboard,
    get_dashboard,
)

# 兼容旧调用方：历史代码以 _build_dashboard 引用该函数
_build_dashboard = build_dashboard

router = APIRouter(prefix="/api/market-dashboard", tags=["market-dashboard"])


@router.get("")
async def get_market_dashboard():
    """大盘纵览总接口：指数快照、涨跌家数、情绪评分与 regime、板块与资金、风险标签、机会提示。"""
    return await get_dashboard()
