"""后台采集与反补：volume backfill、coverage worker 生命周期"""

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

from server.market_coverage_fetch import (
    _LAST_ROWS,
    _WORKER_STOP,
    _WORKER_TASK,
    _fetch_json,
    _is_trading_session,
    logger,
)
from server.market_coverage_payload import get_market_coverage

VOLUME_BACKFILL_KEY = "market_volume_backfill:v1"
# 沪市综指覆盖全部沪市个股，深证综指覆盖全部深市个股；两者成交量合计≈全市场规模。
VOLUME_INDEX_CODES = ("sh000001", "sz399106")
VOLUME_BACKFILL_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={code},day,,,{days}"


async def _index_daily_volume(code: str, days: int) -> Dict[str, float]:
    """取单个指数的日成交量（手）。返回 {date: volume}。"""
    payload = await _fetch_json(VOLUME_BACKFILL_URL.format(code=code, days=days), timeout=15)
    node = ((payload or {}).get("data") or {}).get(code) or {}
    rows = node.get("day") or node.get("qfqday") or []
    series: Dict[str, float] = {}
    for row in rows:
        if len(row) < 6:
            continue
        try:
            series[str(row[0])] = float(row[5])
        except (TypeError, ValueError):
            continue
    return series


async def get_volume_backfill(days: int = 25, force: bool = False) -> Dict[str, Any]:
    """回填最近 N 个交易日的全市场成交量，用于量能维的 20 日分位。

    只用真实日 K 成交量（单位：手），不推算成交额；
    成交额口径待 sentiment 的 daily 样本累积满 20 个交易日后自动接管。
    """
    from server.cache import cache

    if not force:
        cached = cache.get(VOLUME_BACKFILL_KEY)
        if cached is not None:
            return cached

    results = await asyncio.gather(
        *[_index_daily_volume(code, days) for code in VOLUME_INDEX_CODES],
        return_exceptions=True,
    )
    merged: Dict[str, float] = {}
    failed: List[str] = []
    for code, item in zip(VOLUME_INDEX_CODES, results):
        if isinstance(item, Exception) or not item:
            failed.append(code)
            continue
        for date, vol in item.items():
            merged[date] = merged.get(date, 0.0) + vol

    series = [{"date": d, "vol": round(v, 0)} for d, v in sorted(merged.items()) if v > 0]
    payload = {
        "ok": bool(series),
        "source": "tencent_kline:" + "+".join(VOLUME_INDEX_CODES),
        "unit": "lot",
        "note": "沪市综指 + 深证综指日成交量合计，仅作量能相对水平比较，不等同成交额",
        "series": series,
        "missing_sources": failed,
        "as_of": series[-1]["date"] if series else None,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if series:
        # 日线级数据每日只需刷新一次，缓存 1 小时足够。
        cache.set(VOLUME_BACKFILL_KEY, payload, 3600)
    return payload


def get_universe_rows(limit: int = 50, offset: int = 0, sort: str = "amount") -> Dict[str, Any]:
    rows = list(_LAST_ROWS)
    key = sort if sort in {"amount", "chg", "market_cap_yi", "roe", "pe"} else "amount"
    # 不能把缺该字段的行直接丢掉：北交所/仅有基本面的标的常常没有 amount，
    # 一过滤就把「标的池总数」说小了（实测 5777 被报成 5576）。缺失值排到最后即可。
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))
    total = len(rows)
    present = sum(1 for r in rows if r.get(key) is not None)
    lim = max(1, min(int(limit or 50), 500))
    off = max(0, int(offset or 0))
    return {
        "ok": True,
        "total": total,
        "with_sort_field": present,
        "limit": lim,
        "offset": off,
        "sort": key,
        "data": rows[off:off + lim],
    }


async def coverage_worker() -> None:
    """后台持续更新；交易时段 120s，非交易时段 1800s。"""
    global _WORKER_STOP
    await asyncio.sleep(8)
    while not _WORKER_STOP:
        try:
            payload = await get_market_coverage(force=False)
            # 时间触发 → 事件触发：刷新成功后发布到总线，
            # 由其级联失效 packet（以及 packet 下游的 sentiment / scan / experts）。
            from server.data_bus import bus

            if payload:
                await bus.publish("coverage", payload, trigger="worker")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("coverage worker failed: %s", exc)
        interval = 120 if _is_trading_session() else 1800
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def start_coverage_worker() -> asyncio.Task:
    global _WORKER_TASK, _WORKER_STOP
    _WORKER_STOP = False
    if _WORKER_TASK is None or _WORKER_TASK.done():
        _WORKER_TASK = asyncio.create_task(coverage_worker())
    return _WORKER_TASK


async def stop_coverage_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    _WORKER_STOP = True
    if _WORKER_TASK and not _WORKER_TASK.done():
        _WORKER_TASK.cancel()
        try:
            await _WORKER_TASK
        except asyncio.CancelledError:
            pass
    _WORKER_TASK = None
