#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""北交所（BJ）全标的报价：补上「沪深京」里缺的那一块。

背景
----
全市场标的池一直是 6127 只，但报价只有 5581 只 —— 差额 546 只全部来自北交所：
新浪 `hs_a` 节点只含沪深两市，北交所标的此前只因为「东财季度基本面」里提到过才进了标的池，
于是出现「池子里有、但没有价格/成交额」的空壳行。广度、成交额、涨停家数都因此少算一块。

数据源
------
东财 `clist` 接口，`fs=m:0+t:81+s:2048` 即北交所全部标的（实测 total=363，含 920/8xx/43x 段）。
一次分页可拉完（pz=100 × 4 页），字段与新浪可以对齐：

    f2 最新价 · f3 涨跌幅 · f5 成交量(手) · f6 成交额(元) · f8 换手率
    f9 市盈率 · f12 代码 · f14 名称 · f18 昨收 · f20 总市值 · f21 流通市值

单位对齐（与新浪口径一致，避免下游混淆）：
* 新浪 volume 单位是**股**，东财 f5 是**手** → 乘 100 换算成股；
* 两者 amount 都是**元**，不再换算；
* 市值两家都是**万元** → 除 10000 得亿元，与新浪 `mktcap/10000` 一致。

失败不伪装成功：接口异常/结构变化时返回 `ok:false` 并给出 reason，由上层标注降级，
绝不用 0 或前一日数据顶替。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime
from typing import Any, Dict

from server.market_coverage_fetch import _fetch_json, _num, _prefix_code

logger = logging.getLogger(__name__)

BJ_VERSION = "market-coverage-bj.v1"

#: 东财 clist：北交所全部标的
_BJ_FS = "m:0+t:81+s:2048"
_BJ_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn={page}&pz={size}&po=1&np=1&fltt=2&invt=2&fid=f12&fs=" + _BJ_FS +
    "&fields=f2,f3,f5,f6,f8,f9,f12,f14,f18,f20,f21"
)
_PAGE_SIZE = 100
_MAX_PAGES = 10
_TTL = 300

_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": {}, "meta": {}}


def _row(raw: Dict[str, Any]) -> Dict[str, Any] | None:
    code = _prefix_code(raw.get("f12"))
    if not code:
        return None
    price = _num(raw.get("f2"))
    volume_hand = _num(raw.get("f5"))
    cap = _num(raw.get("f20"))
    float_cap = _num(raw.get("f21"))
    return {
        "code": code,
        "name": str(raw.get("f14") or ""),
        "price": price,
        "chg": _num(raw.get("f3")),
        "change": (round(price - _num(raw.get("f18")), 3)
                   if price is not None and _num(raw.get("f18")) is not None else None),
        "open": None,   # clist 不返回开高低，缺失即 null，不编造
        "high": None,
        "low": None,
        "prev_close": _num(raw.get("f18")),
        "volume": volume_hand * 100 if volume_hand is not None else None,
        "amount": _num(raw.get("f6")),
        "turnover": _num(raw.get("f8")),
        "pe": _num(raw.get("f9")),
        "pb": None,     # clist 无 pb，留空由基本面补齐
        "market_cap_yi": round(cap / 10000, 2) if cap is not None else None,
        "float_cap_yi": round(float_cap / 10000, 2) if float_cap is not None else None,
        "ticktime": None,
        "source": "eastmoney_bj",
        "quote_source": "eastmoney_bj",
    }


async def get_bj_universe(force: bool = False) -> Dict[str, Any]:
    """北交所全标的报价。成功返回 rows（按 bj 前缀键），失败返回 ok:false + reason。"""
    now = time.time()
    cached = _CACHE
    if not force and cached["rows"] and now - cached["ts"] < _TTL:
        return {"ok": True, "rows": cached["rows"], "meta": cached["meta"], "cached": True}

    t0 = time.time()
    first = await _fetch_json(_BJ_URL.format(page=1, size=_PAGE_SIZE))
    if not isinstance(first, dict) or not isinstance(first.get("data"), dict):
        return {"ok": False, "rows": {}, "meta": {"reason": "bj_clist_unexpected_payload"}}
    data = first["data"]
    total = int(_num(data.get("total")) or 0)
    rows: Dict[str, Dict[str, Any]] = {}
    errors = 0

    def absorb(diff: Any) -> None:
        for raw in diff or []:
            if isinstance(raw, dict):
                item = _row(raw)
                if item:
                    rows[item["code"]] = item

    absorb(data.get("diff"))
    pages = min(_MAX_PAGES, max(1, math.ceil(total / _PAGE_SIZE)))
    if pages > 1:
        rest = await asyncio.gather(
            *[_fetch_json(_BJ_URL.format(page=p, size=_PAGE_SIZE)) for p in range(2, pages + 1)],
            return_exceptions=True,
        )
        for batch in rest:
            if isinstance(batch, Exception) or not isinstance(batch, dict):
                errors += 1
                continue
            payload = batch.get("data")
            if not isinstance(payload, dict):
                errors += 1
                continue
            absorb(payload.get("diff"))

    # 只保留北交所代码，防止上游 fs 变化时把别家标的混进 bj 命名空间
    rows = {code: item for code, item in rows.items() if code.startswith("bj")}
    meta = {
        "source": "eastmoney clist m:0+t:81+s:2048",
        "auth": "public_no_key",
        "configured_total": total,
        "rows": len(rows),
        "pages": pages,
        "errors": errors,
        "elapsed_ms": round((time.time() - t0) * 1000),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "北交所涨跌幅限制 30%（ST 5%），下游按 _limit_threshold 的 bj 分支判定",
    }
    if rows:
        _CACHE.update({"ts": time.time(), "rows": rows, "meta": meta})
    return {"ok": bool(rows), "rows": rows, "meta": meta,
            "reason": None if rows else "bj_clist_empty"}


__all__ = ["BJ_VERSION", "get_bj_universe"]
