#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全市场报价补缺（market-coverage-gapfill.v1）。

为什么需要补缺
--------------
事实层的主报价源是新浪 `hs_a`，它按页返回、偶发限流，单次抓取会漏掉一两百只；
北交所又只能靠东财单独一路。漏掉的那些标的不会报错，只会让「广度/成交额/涨停家数」
整体偏小——而且每次漏的还不一样，前后两次快照对不上。

补缺策略：**用第二个源把「池子里有、但没有价格」的代码补齐**，而不是假装它们不存在。
补不到（退市/长期停牌）就保持 null，并在 `gapfill.unresolved` 里列出来——
「没有报价」本身是事实，不该被平均掉。

数据源：腾讯 `qt.gtimg.cn`，一次可查多只，支持 sh/sz/bj 全部代码段。
字段位置（实测 88 段，~ 分隔）：
    1 名称 · 3 最新价 · 4 昨收 · 5 开盘 · 6 成交量(手) · 31 涨跌额 · 32 涨跌幅%
    33 最高 · 34 最低 · 37 成交额(万元) · 38 换手率% · 39 市盈率
    44 流通市值(亿) · 45 总市值(亿) · 46 市净率
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from server.market_coverage_fetch import _fetch_url, _num

logger = logging.getLogger(__name__)

GAPFILL_VERSION = "market-coverage-gapfill.v1"
_URL = "https://qt.gtimg.cn/q={codes}"
_CHUNK = 60
_CONCURRENCY = 4
_TTL = 120

_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "payload": None}
_LINE = re.compile(r'v_([a-z]{2}\d{6})="([^"]*)"')


def _prefixed(code: str) -> Optional[str]:
    text = str(code or "").strip().lower()
    if len(text) == 8 and text[:2] in ("sh", "sz", "bj") and text[2:].isdigit():
        return text
    return None


def _row(code: str, fields: List[str]) -> Optional[Dict[str, Any]]:
    if len(fields) < 47:
        return None
    price = _num(fields[3])
    if price is None or price <= 0:
        return None   # 停牌/退市/无成交：保持无报价，不编造
    volume_hand = _num(fields[6])
    amount_wan = _num(fields[37])
    total_cap = _num(fields[45])
    float_cap = _num(fields[44])
    return {
        "code": code,
        "name": fields[1] or "",
        "price": price,
        "chg": _num(fields[32]),
        "change": _num(fields[31]),
        "open": _num(fields[5]),
        "high": _num(fields[33]),
        "low": _num(fields[34]),
        "prev_close": _num(fields[4]),
        "volume": volume_hand * 100 if volume_hand is not None else None,
        "amount": amount_wan * 10000 if amount_wan is not None else None,
        "turnover": _num(fields[38]),
        "pe": _num(fields[39]),
        "pb": _num(fields[46]),
        "market_cap_yi": total_cap,
        "float_cap_yi": float_cap,
        "ticktime": (fields[30] or "")[8:14] or None,
        "source": "tencent_gapfill",
        "quote_source": "tencent_gapfill",
    }


async def _chunk(codes: List[str], sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    async with sem:
        try:
            # 腾讯返回 GBK：用 _fetch_text（按 utf-8 解码）会把中文名解成乱码，
            # 所以这里取原始字节自行按 gbk 解码（失败再退 utf-8）。
            raw = await asyncio.to_thread(_fetch_url, _URL.format(codes=",".join(codes)), 10)
        except Exception as exc:
            logger.info("gapfill chunk failed (%d codes): %s", len(codes), exc)
            return []
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    rows: List[Dict[str, Any]] = []
    for code, body in _LINE.findall(text):
        item = _row(code, body.split("~"))
        if item:
            rows.append(item)
    return rows


async def get_gapfill_quotes(codes: Iterable[str], force: bool = False) -> Dict[str, Any]:
    """给一批「缺报价」的代码补行情。成功返回 rows（按带前缀代码键）。"""
    wanted = sorted({c for c in (_prefixed(x) for x in codes) if c})
    if not wanted:
        return {"ok": True, "rows": {}, "meta": {"requested": 0, "resolved": 0, "chunks": 0}}

    key = hashlib.sha1("|".join(wanted).encode()).hexdigest()[:16]
    if not force and _CACHE["key"] == key and time.time() - _CACHE["ts"] < _TTL:
        return _CACHE["payload"]

    t0 = time.time()
    sem = asyncio.Semaphore(_CONCURRENCY)
    chunks = [wanted[i:i + _CHUNK] for i in range(0, len(wanted), _CHUNK)]
    batches = await asyncio.gather(*[_chunk(batch, sem) for batch in chunks])
    rows: Dict[str, Dict[str, Any]] = {}
    for batch in batches:
        for item in batch:
            rows[item["code"]] = item

    unresolved = [code for code in wanted if code not in rows]
    payload = {
        # 一个都没补到时 ok=False：让上层 sources 里如实显示 error，
        # 而不是「补缺成功但 0 条」这种看起来正常的假象。
        "ok": bool(rows) or not wanted,
        "rows": rows,
        "meta": {
            "version": GAPFILL_VERSION,
            "source": "tencent qt.gtimg.cn",
            "requested": len(wanted),
            "resolved": len(rows),
            "unresolved": unresolved[:80],
            "unresolved_count": len(unresolved),
            "chunks": len(chunks),
            "elapsed_ms": round((time.time() - t0) * 1000),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": "补不到的代码保持无报价（退市/停牌/代码无效），不参与广度计算",
        },
    }
    _CACHE.update({"key": key, "ts": time.time(), "payload": payload})
    return payload


__all__ = ["GAPFILL_VERSION", "get_gapfill_quotes"]
