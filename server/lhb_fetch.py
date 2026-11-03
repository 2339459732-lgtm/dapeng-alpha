#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""龙虎榜数据抓取层（lhb-fetch.v1）。

从 `server/lhb_review.py` 拆出：只负责「把东财的数据原样取回来」，不做任何归因判断。
拆分的理由有两个，都不是为了好看：
1. 归因逻辑要能脱网单测（`attribute()` 是纯函数），抓取混在里面会让单测被迫打网络；
2. 抓取会变（接口改字段、加备用源），归因规则也会变（阈值调整），但两者变化的节奏不同，
   放一个文件里改一边就要重读另一边。

数据来源
--------
* 营业部买卖明细：`RPT_BILLBOARD_DAILYDETAILSBUY` / `...SELL`（营业部级，含 3 日成功率）
* 个股级龙虎榜：`RPT_DAILYBILLBOARD_DETAILS`（上榜原因、成交额、流通市值、换手率）
* 公告：`np-anotice-stock.eastmoney.com`（消息面归因用）
* 涨停/炸板/跌停池：akshare 东财同口径，**按复盘日**取（不用 T+1 盘中广度）
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EM = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_ANN = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        import urllib.request

        _OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _OPENER


def _get_json(url: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    import json as _json
    import urllib.request

    try:
        req = urllib.request.Request(url, headers=_UA)
        with _opener().open(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("lhb fetch failed: %s", exc)
        return None
def _prefix_code(code: str) -> str:
    """6 位代码补市场前缀；已带前缀的原样返回。

    北交所必须先判：`920xxx` 是北交所新号段（老号段 43/83/87/88），
    如果只按「9 开头 → 沪市」处理，920 开头会被错标成 sh，后续行情与涨跌停阈值全跟着错。
    """
    text = str(code or "").strip().lower()
    if len(text) == 8 and text[:2] in ("sh", "sz", "bj"):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) != 6:
        return text
    if digits.startswith(("43", "83", "87", "88", "920")):
        return "bj" + digits
    if digits.startswith(("6", "9")):
        return "sh" + digits
    if digits.startswith(("0", "2", "3")):
        return "sz" + digits
    return "bj" + digits


# ---------------------------------------------------------------------------
# 数据抓取：营业部买卖明细
# ---------------------------------------------------------------------------
async def _fetch_seat_page(report: str, date: str, page: int, size: int = 500) -> Dict[str, Any]:
    url = (f"{_EM}?sortColumns=SECURITY_CODE&sortTypes=1&pageSize={size}&pageNumber={page}"
           f"&reportName={report}&columns=ALL&source=WEB&client=WEB"
           f"&filter=(TRADE_DATE%3D%27{date}%27)")
    data = await asyncio.to_thread(_get_json, url)
    if not isinstance(data, dict):
        return {"rows": [], "pages": 0, "error": "请求失败"}
    result = data.get("result") or {}
    return {"rows": result.get("data") or [], "pages": int(result.get("pages") or 0), "error": None}


async def fetch_seats(date: str) -> Dict[str, Any]:
    """拉取某日买入/卖出营业部明细（全部页）。"""
    first = await asyncio.gather(
        _fetch_seat_page("RPT_BILLBOARD_DAILYDETAILSBUY", date, 1),
        _fetch_seat_page("RPT_BILLBOARD_DAILYDETAILSSELL", date, 1),
    )
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for report, page1 in zip(("RPT_BILLBOARD_DAILYDETAILSBUY", "RPT_BILLBOARD_DAILYDETAILSSELL"), first):
        if page1["error"]:
            errors.append(f"{report}: {page1['error']}")
        rows.extend(page1["rows"])
        pages = page1["pages"]
        if pages > 1:
            rest = await asyncio.gather(*[
                _fetch_seat_page(report, date, p) for p in range(2, min(pages, 30) + 1)
            ])
            for batch in rest:
                if batch["error"]:
                    errors.append(f"{report}: {batch['error']}")
                rows.extend(batch["rows"])
    return {"ok": bool(rows), "rows": rows, "errors": errors,
            "meta": {"date": date, "rows": len(rows)}}


async def fetch_stock_list(date: str) -> Dict[str, Any]:
    """个股级龙虎榜（含上榜原因、成交额、流通市值），用于过滤与上下文。"""
    url = (f"{_EM}?sortColumns=SECURITY_CODE&sortTypes=1&pageSize=500&pageNumber=1"
           f"&reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL&source=WEB&client=WEB"
           f"&filter=(TRADE_DATE%3D%27{date}%27)")
    data = await asyncio.to_thread(_get_json, url)
    result = (data or {}).get("result") or {}
    rows = result.get("data") or []
    return {"ok": bool(rows), "rows": rows, "pages": int(result.get("pages") or 0)}


def _announcements(code: str, days: int = 5, as_of: Optional[str] = None) -> Dict[str, Any]:
    """近 N 日公告标题（东财）。失败返回 available:false，不猜。

    时间窗口必须锚定**复盘日**，不能用 `datetime.now()`：复盘 11-02 的榜在 11-05 才跑，
    用 now 会把 11-03~11-05 的公告算进 11-02 的动因里，属于典型的未来函数。
    """
    url = (f"{_ANN}?sr=-1&page_size=10&page_index=1&ann_type=A&client_source=web"
           f"&stock_list={code[2:]}&f_node=0&s_node=0")
    data = _get_json(url, timeout=8)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return {"available": False, "reason": "公告接口不可用", "items": [], "as_of": as_of}
    items = data["data"].get("list") or []
    anchor = as_of or datetime.now().strftime("%Y-%m-%d")
    try:
        base = datetime.strptime(anchor[:10], "%Y-%m-%d")
    except ValueError:
        base = datetime.now()
    cutoff = (base - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for item in items[:10]:
        notice = str(item.get("notice_date") or "")[:10]
        if notice and (notice < cutoff or notice > anchor[:10]):
            continue
        columns = item.get("columns") or []
        out.append({
            "date": notice,
            "title": str(item.get("title") or "")[:80],
            "type": (columns[0].get("column_name") if columns else "") or "",
        })
    return {"available": True, "items": out, "as_of": anchor[:10]}


# ---------------------------------------------------------------------------
# 市场上下文（板块、广度、情绪）
# ---------------------------------------------------------------------------
def _limit_threshold(code: str, name: str) -> float:
    digits = code[2:] if code.startswith(("sh", "sz", "bj")) else code
    if code.startswith("bj"):
        return 29.5
    if digits.startswith(("300", "301", "688")):
        return 19.5
    if "ST" in str(name).upper():
        return 4.8
    return 9.5


async def fetch_limit_pools(date: str) -> Dict[str, Any]:
    """复盘日当天的涨停/炸板/跌停池（akshare 东财口径）。

    为什么不用「当前广度」：龙虎榜是 T 日盘后数据，用 T+1 盘中的涨停家数去解释 T 日的行为，
    时间轴是错的。涨停池按日期取，天然对齐。
    """

    def _load() -> Dict[str, Any]:
        import akshare as ak

        ymd = date.replace("-", "")
        out: Dict[str, Any] = {"zt": {}, "zb": {}, "dt": {}, "available": False}
        try:
            zt = ak.stock_zt_pool_em(date=ymd)
            for row in zt.to_dict("records"):
                code = _prefix_code(str(row.get("代码") or ""))
                if not code:
                    continue
                out["zt"][code] = {
                    "name": row.get("名称"),
                    "chg": row.get("涨跌幅"),
                    "price": row.get("最新价"),
                    "amount": row.get("成交额"),
                    "turnover": row.get("换手率"),
                    "first_seal": str(row.get("首次封板时间") or ""),
                    "last_seal": str(row.get("最后封板时间") or ""),
                    "open_times": row.get("炸板次数"),
                    "board_count": row.get("连板数"),
                    "industry": str(row.get("所属行业") or ""),
                    "seal_stat": row.get("涨停统计"),
                }
            out["available"] = bool(out["zt"])
        except Exception as exc:
            out["reason"] = f"涨停池不可用：{type(exc).__name__}"
        for key, fn in (("zb", "stock_zt_pool_zbgc_em"), ("dt", "stock_zt_pool_dtgc_em")):
            try:
                df = getattr(ak, fn)(date=ymd)
                out[key] = {_prefix_code(str(r.get("代码") or "")): r.get("名称")
                            for r in df.to_dict("records")}
            except Exception:
                out[key] = {}
        return out

    return await asyncio.to_thread(_load)


async def market_context(date: str) -> Dict[str, Any]:
    """复盘日市场情绪与题材热度：全部来自当日涨停/炸板/跌停池。"""
    pools = await fetch_limit_pools(date)
    zt, zb, dt = pools.get("zt") or {}, pools.get("zb") or {}, pools.get("dt") or {}
    boards = [int(v.get("board_count") or 1) for v in zt.values()] or [0]
    industries: Dict[str, int] = {}
    for info in zt.values():
        name = info.get("industry") or "未分类"
        industries[name] = industries.get(name, 0) + 1
    total_try = len(zt) + len(zb)
    return {
        "date": date,
        "available": bool(pools.get("available")),
        "reason": pools.get("reason"),
        "zt_count": len(zt),
        "zb_count": len(zb),
        "dt_count": len(dt),
        "seal_rate": round(len(zt) / total_try * 100, 1) if total_try else None,
        "max_board": max(boards),
        "board_distribution": {b: sum(1 for x in boards if x == b) for b in sorted(set(boards))},
        "industries": industries,
        "zt": zt,
        "zb": zb,
    }


