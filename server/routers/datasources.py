"""数据源健康接口"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from server import datasources

router = APIRouter(prefix="/api/data-sources", tags=["datasources"])


@router.get("/health")
async def data_sources_health():
    return await asyncio.to_thread(datasources.health)
