"""通达信本地数据 API（决策台备用 / 离线数据源）"""
from __future__ import annotations

from fastapi import APIRouter, Query

from server import tdx_local

router = APIRouter(prefix="/api/tdx", tags=["tdx"])


@router.get("/status")
async def tdx_status():
    return tdx_local.status()


@router.get("/kline")
async def tdx_kline(
    code: str = Query(..., min_length=3, max_length=16),
    period: str = Query("day", max_length=8),
    limit: int = Query(250, ge=1, le=5000),
):
    return tdx_local.kline(code, period=period, limit=limit)
