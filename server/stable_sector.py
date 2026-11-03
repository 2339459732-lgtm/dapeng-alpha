"""板块数据：龙虎榜参数、周期收益、腾讯聚合"""

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests
from server.sector_map import infer_level

from server.stable_cache import logger
from server.stable_em import (
    _BOARD_RETURNS_BUDGET,
    _BOARD_RETURNS_WORKERS,
    _BOARD_RETURN_PERIODS,
    _EM_KLINE_PATH,
    _EM_LHB_COLUMNS,
    _EM_PUSH2HIS_NODES,
    _TENCENT_AGG_NOTE,
    _TENCENT_AGG_SOURCE,
    _TENCENT_QUOTE_CHUNK,
    _em_get_json,
)

def _em_lhb_params(date_str: Optional[str]) -> Dict[str, Any]:
    """构造龙虎榜报表查询参数。

    关键：排序使用**多列** `TRADE_DATE desc, BILLBOARD_NET_AMT desc`。
    这样即使没有（或拿不到）日期过滤，也只会返回"最近交易日"的榜单，
    绝不会退化成"全历史按净买额排序"（曾导致榜首被 2015 年救市日占据）。
    给定 date_str 时额外按交易日过滤，锁定单日。
    """
    params: Dict[str, Any] = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILS",
        "columns": _EM_LHB_COLUMNS,
        "pageNumber": "1",
        "pageSize": "25",
        "sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT",
        "sortTypes": "-1,-1",
        "source": "WEB",
        "client": "WEB",
    }
    if date_str:
        params["filter"] = f"(TRADE_DATE='{date_str}')"
    return params


# 龙虎榜陈旧阈值（自然日）：超过则判定为历史快照，触发降级护栏
_EM_LHB_STALE_DAYS = 10


def _is_stale_date(date_str: Optional[str]) -> bool:
    """判断 YYYY-MM-DD 相对今天是否陈旧（距今 > _EM_LHB_STALE_DAYS 天）。

    无法解析时按"不陈旧"处理（保守：避免误伤），真实陈旧判定由调用方以
    返回数据中的最大交易日驱动。
    """
    if not date_str:
        return False
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (datetime.now().date() - d).days > _EM_LHB_STALE_DAYS


def _calc_period_returns(closes: List[float]) -> Dict[str, Optional[float]]:
    """由收盘价序列计算多周期收益（含当日）：(close[-1]/close[-1-n]-1)*100。"""
    out: Dict[str, Optional[float]] = {}
    for n in _BOARD_RETURN_PERIODS:
        if len(closes) > n and closes[-1 - n]:
            out[str(n)] = round((closes[-1] / closes[-1 - n] - 1.0) * 100.0, 2)
        else:
            out[str(n)] = None
    return out


def _board_period_returns(codes: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """并发拉取板块日K并计算真实多周期收益。

    板块日K走 push2his，secid 形如 `90.BKxxxx`。返回
    {code: {"5":x, "20":x, "60":x}}；在总时间预算内未拿到的板块不会出现在
    结果里（调用方据此置 None + reason），**绝不回退到 chg*系数 推算**。
    """
    import concurrent.futures as _cf

    result: Dict[str, Dict[str, Optional[float]]] = {}
    deadline = time.time() + _BOARD_RETURNS_BUDGET

    def _one(code: str):
        if time.time() > deadline:
            return code, None
        payload = _em_get_json(
            _EM_KLINE_PATH,
            {
                "secid": f"90.{code}",
                "klt": "101", "fqt": "1", "lmt": "70",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f53",
                "end": "20500101",
            },
            _EM_PUSH2HIS_NODES,
        )
        if not isinstance(payload, dict):
            return code, None
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        closes: List[float] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) >= 2:
                try:
                    closes.append(float(parts[1]))
                except (ValueError, TypeError):
                    pass
        if len(closes) <= max(_BOARD_RETURN_PERIODS):
            return code, None
        return code, _calc_period_returns(closes)

    targets = [c for c in codes if c]
    if not targets:
        return result
    with _cf.ThreadPoolExecutor(max_workers=_BOARD_RETURNS_WORKERS) as ex:
        futures = [ex.submit(_one, c) for c in targets]
        for fut in _cf.as_completed(futures):
            try:
                code, vals = fut.result()
            except Exception:  # noqa: BLE001 - 单板块失败不影响整体
                continue
            if vals:
                result[code] = vals
    return result


async def _tencent_sector_aggregate() -> Optional[List[Dict[str, Any]]]:
    """③ 级降级：用腾讯成分股实时行情聚合出**真实**板块指标。

    复用 /api/quotes 背后的同一取价链路（server.realtime.fetch_tencent_quotes），
    不新写 HTTP；成分股按 `_TENCENT_QUOTE_CHUNK` 分片并发批量取价。

    聚合口径（逐板块）：
      - chg：成分股涨跌幅按**成交额加权**平均（权重 = 成分股成交额）
      - amount：成分股成交额求和（腾讯单位为万元 → 本函数换算为元）
      - up/down/flat_count：涨跌家数；count：有效成分股数
      - lead_stock：板块内涨幅最高的成分股名
      - turnover：成分股换手率均值
      - net_flow：腾讯无资金流字段 → None + reason（**绝不推算**）
      - 5/20/60日%：按需聚合成分股日K，但单只 K 线实测 ~5-7s，448 只不可行 → None + reason
    返回 None 表示腾讯也不可用（由调用方继续降级到硬编码样本）。
    """
    from server.config import SECTORS
    from server.realtime import fetch_tencent_quotes

    boards = [(k, v) for k, v in SECTORS.items() if k not in ("etf", "hk_stocks")]
    if not boards:
        return None

    # 去重收集全部成分股代码
    codes: List[str] = []
    seen = set()
    for _k, v in boards:
        for s in v.get("stocks", []):
            c = s[0] if s else None
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    if not codes:
        return None

    chunks = [
        codes[i:i + _TENCENT_QUOTE_CHUNK]
        for i in range(0, len(codes), _TENCENT_QUOTE_CHUNK)
    ]
    results = await asyncio.gather(
        *[fetch_tencent_quotes(ch, timeout=6.0) for ch in chunks],
        return_exceptions=True,
    )
    quote_map: Dict[str, Dict[str, Any]] = {}
    for res in results:
        if isinstance(res, Exception) or not res:
            continue
        for q in res:
            code = q.get("code")
            if code:
                quote_map[code] = q
    if not quote_map:
        return None

    # 补齐：首轮缺失的代码再取一次（应对单分片瞬时失败），避免静默的部分覆盖
    missing = [c for c in codes if c not in quote_map]
    if missing:
        retry_chunks = [
            missing[i:i + _TENCENT_QUOTE_CHUNK]
            for i in range(0, len(missing), _TENCENT_QUOTE_CHUNK)
        ]
        retry_results = await asyncio.gather(
            *[fetch_tencent_quotes(ch, timeout=6.0) for ch in retry_chunks],
            return_exceptions=True,
        )
        for res in retry_results:
            if isinstance(res, Exception) or not res:
                continue
            for q in res:
                code = q.get("code")
                if code and code not in quote_map:
                    quote_map[code] = q
        logger.info(
            "sector_ranking: ③ retry fetched %d missing codes, now %d/%d covered",
            len(missing), len(quote_map), len(codes),
        )

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sectors: List[Dict[str, Any]] = []
    for key, cfg in boards:
        qs = [quote_map[s[0]] for s in cfg.get("stocks", [])
              if s and s[0] in quote_map]
        if not qs:
            continue

        amt_wan = [float(q.get("amount") or 0) for q in qs]
        chgs = [float(q.get("chg") or 0) for q in qs]
        total_amt_wan = sum(amt_wan)
        if total_amt_wan > 0:
            chg = sum(c * a for c, a in zip(chgs, amt_wan)) / total_amt_wan
        else:
            chg = sum(chgs) / len(chgs)
        chg = round(chg, 4)

        up = sum(1 for c in chgs if c > 0)
        dn = sum(1 for c in chgs if c < 0)
        flat = len(qs) - up - dn
        lead = (max(qs, key=lambda q: float(q.get("chg") or 0)).get("name") or "--")
        turnover = round(sum(float(q.get("turnover") or 0) for q in qs) / len(qs), 4)

        sectors.append({
            "rank": 0,
            "code": key,
            "name": cfg.get("name", key),
            "level": infer_level(cfg.get("name", key)),
            "chg": chg,
            "change_pct": chg,
            "chg_pct": chg,
            "amount": total_amt_wan * 1e4,   # 万元 → 元
            "turnover": turnover,
            "lead_stock": lead,
            "leading_stock": lead,
            "up_count": up,
            "down_count": dn,
            "flat_count": flat,
            "count": len(qs),
            "net_flow": None,
            "net_flow_source": "unavailable",
            "net_flow_reason": "tencent_quotes_has_no_fund_flow",
            "5日%": None,
            "20日%": None,
            "60日%": None,
            "period_returns_source": "unavailable",
            "period_returns_reason": "tencent_kline_too_slow_for_constituent_aggregation",
            "aggregated": True,
            "aggregation_note": _TENCENT_AGG_NOTE,
            "field_units": {
                "chg": "percent",
                "amount": "cny",
                "turnover": "percent",
                "net_flow": "unavailable",
                "period_returns": "percent",
            },
            "source": _TENCENT_AGG_SOURCE,
            "synthetic": False,
            "updated_at": ts,
        })

    if not sectors:
        return None

    sectors.sort(key=lambda r: r["chg"], reverse=True)
    for i, r in enumerate(sectors):
        r["rank"] = i + 1
    logger.info(
        "sector_ranking: ③ aggregated %d boards from tencent quotes (%d/%d codes)",
        len(sectors), len(quote_map), len(codes),
    )
    return sectors
