"""DAPENG Alpha v4.0 - 腾讯实时行情路由
GET /api/realtime?codes=sh600519,sz000858
返回腾讯 qt.gtimg.cn 毫秒级实时行情
"""

import asyncio
import logging
from fastapi import APIRouter, Query

from server.realtime import fetch_tencent_quotes

logger = logging.getLogger(__name__)
router = APIRouter()

# 交易时段缓存：盘后不重复请求
_last_fetch_time: float = 0
_cached_results: dict = {}
_cached_codes_key: str = ""
_MIN_FETCH_INTERVAL = 3.0  # 最小请求间隔（秒）


def _is_trading_hours() -> bool:
    """判断是否在A股交易时段（周一至周五 9:30-15:00）"""
    import datetime
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.time()
    morning_start = datetime.time(9, 30)
    morning_end = datetime.time(11, 30)
    afternoon_start = datetime.time(13, 0)
    afternoon_end = datetime.time(15, 0)
    return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)


@router.get("/realtime")
async def get_realtime_quotes(
    codes: str = Query("", description="逗号分隔股票代码，如 sh600519,sz000858"),
):
    """
    腾讯实时行情接口 — 毫秒级返回。

    前端每 3-5 秒轮询一次，盘后自动降低频率。
    """
    if not codes:
        return {"ok": True, "data": {}, "source": "empty"}

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {"ok": True, "data": {}, "source": "empty"}

    # 限制单次查询数量
    if len(code_list) > 50:
        code_list = code_list[:50]

    # 盘后降频：非交易时段缓存30秒
    import time as _time
    global _last_fetch_time, _cached_results, _cached_codes_key
    now = _time.time()
    codes_key = ",".join(sorted(set(code_list)))
    if not _is_trading_hours():
        if now - _last_fetch_time < 30 and _cached_results and _cached_codes_key == codes_key:
            return {"ok": True, "data": _cached_results, "source": "tencent", "cached": True}

    try:
        results = await asyncio.wait_for(
            fetch_tencent_quotes(code_list, timeout=4.0),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        logger.warning("实时行情超时 (%d codes)", len(code_list))
        return {"ok": False, "data": {}, "source": "timeout"}

    # 转成 code → data 映射
    data = {}
    for r in results:
        code = r.get("code", "")
        data[code] = {
            "price": r.get("price", 0),
            "chg": r.get("chg", 0),
            "high": r.get("high", 0),
            "low": r.get("low", 0),
            "open": r.get("open", 0),
            "yclose": r.get("yclose", 0),
            "volume": r.get("volume", 0),
            "amount": r.get("amount", 0),
            "turnover": r.get("turnover", 0),
            "pe": r.get("pe", 0),
            "name": r.get("name", ""),
        }

    # 更新盘后缓存
    if data:
        _cached_results = data
        _cached_codes_key = codes_key
        _last_fetch_time = now

    return {"ok": True, "data": data, "source": "tencent"}
