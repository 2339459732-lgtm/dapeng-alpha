"""题材雷达 / 板块趋势分析（决策台数据源）
- 数据源 1：本地东财板块快照 data/sector_map.json + sector_periods.json（板块→成分股、5/20/60 日收益）
- 数据源 2：新浪 A 股全市场实时快照（名称/涨跌幅/成交额/换手率），带 180 秒缓存
- 功能：
  * 板块趋势：本地 496 个板块的 5/20/60 日收益 + 今日成分股聚合，计算趋势分与阶段（启动/发酵/加速/分歧/退潮）
  * 字辈炒作：生肖字（羊/马/龙/蛇/兔/牛/虎/猴/鸡/狗/猪/鼠）+ 自主发现高位股中异常聚集的汉字
  * 下钻：板块成分股 / 字辈成分股
"""
from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/themes", tags=["themes"])

PROJECT = Path(__file__).resolve().parents[2]
DATA = PROJECT / "data"

_SINA_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_SINA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}
_ZODIAC = ["羊", "马", "龙", "蛇", "兔", "牛", "虎", "猴", "鸡", "狗", "猪", "鼠"]
_STOP_CHARS = set(
    "中国华科新股份电力银行证券控股集团发展科技实业国际投资资本东方南方北方西部"
    "中大金泰隆盛安信达通海天山水火土木钢铁有色金属医药生物食品农业环保传媒"
)

_cache: Dict[str, Any] = {"universe_ts": 0.0, "universe": [], "snap_ts": 0.0, "snap": None}
_overview_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_OVERVIEW_TTL = 90


# ---------------------------------------------------------------- 快照读取
def _load_board_snapshots() -> Dict[str, Any]:
    now = time.time()
    if _cache["snap"] is not None and now - _cache["snap_ts"] < 300:
        return _cache["snap"]
    snap: Dict[str, Any] = {"sectors": {}, "periods": {}, "returns": {}, "updated_at": None}
    try:
        m = json.loads((DATA / "sector_map.json").read_text(encoding="utf-8"))
        snap["sectors"] = m.get("sectors", {})
        snap["updated_at"] = m.get("updated_at")
    except Exception:
        pass
    try:
        p = json.loads((DATA / "sector_periods.json").read_text(encoding="utf-8"))
        snap["periods"] = p.get("periods", {})
        snap["period_method"] = p.get("period_method")
    except Exception:
        pass
    try:
        r = json.loads((DATA / "sector_returns.json").read_text(encoding="utf-8"))
        snap["returns"] = r.get("series", {})
        snap["dates"] = r.get("dates", [])
    except Exception:
        pass
    _cache["snap"] = snap
    _cache["snap_ts"] = now
    return snap


# ---------------------------------------------------------------- 全市场快照
def _fetch_page(page: int, retries: int = 3) -> List[Dict[str, Any]]:
    for _ in range(retries):
        try:
            r = requests.get(
                _SINA_URL,
                params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "hs_a"},
                headers=_SINA_HEADERS,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
        except Exception:
            time.sleep(0.25)
    return []


def _num(v: Any) -> Optional[float]:
    try:
        if v in (None, "", "-"):
            return None
        return float(v)
    except Exception:
        return None


def _get_universe(ttl: int = 180) -> List[Dict[str, Any]]:
    if _cache["universe"] and time.time() - _cache["universe_ts"] < ttl:
        return _cache["universe"]
    rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for part in ex.map(_fetch_page, range(1, 57)):
            rows.extend(part)
    out: List[Dict[str, Any]] = []
    for r in rows:
        code = str(r.get("symbol") or "")
        name = str(r.get("name") or "").strip()
        if not code or not name or " " in name:
            continue
        chg = _num(r.get("changepercent"))
        price = _num(r.get("trade"))
        if chg is None or price is None:
            continue
        out.append(
            {
                "code": code,
                "name": name,
                "price": price,
                "chg": chg,
                "amount": _num(r.get("amount")) or 0.0,
                "turnover": _num(r.get("turnoverratio")),
                "mktcap": _num(r.get("mktcap")),
                "nmc": _num(r.get("nmc")),
            }
        )
    out.sort(key=lambda x: x["code"])
    _cache["universe"] = out
    _cache["universe_ts"] = time.time()
    return out


# ---------------------------------------------------------------- 板块趋势
def _stage(today: float, d5: float, d20: float, up_ratio: float) -> str:
    if today >= 4.5 and d5 >= 5 and up_ratio >= 65:
        return "主升/发酵"
    if d5 >= 8 and d20 >= 10:
        return "趋势加速"
    if today >= 2.5 and d5 < 4:
        return "启动"
    if d5 <= -3 or (today <= -2.5 and up_ratio < 40):
        return "退潮"
    if d5 > 0 and today < 0.5 and up_ratio < 55:
        return "分歧"
    return "震荡"


def _build_sector_trends(limit: int = 12) -> List[Dict[str, Any]]:
    snap = _load_board_snapshots()
    uni = {u["code"]: u for u in _get_universe()}
    boards: Dict[str, Any] = {}
    for code, b in (snap.get("sectors") or {}).items():
        cons = [c for c in (b.get("constituents") or []) if c in uni]
        if len(cons) < 3:
            continue
        base_name = str(b.get("name") or "").replace("Ⅱ", "").replace("Ⅲ", "").strip()
        if not base_name:
            continue
        prev = boards.get(base_name)
        if prev and prev.get("count", 0) >= len(cons):
            continue
        chgs = [uni[c]["chg"] for c in cons]
        up = sum(1 for x in chgs if x > 0)
        avg = sum(chgs) / len(chgs)
        period = (snap.get("periods") or {}).get(code) or {}
        d5 = _num(period.get("d5")) or 0.0
        d20 = _num(period.get("d20")) or 0.0
        d60 = _num(period.get("d60")) or 0.0
        up_ratio = up / len(cons) * 100
        trend = d5 * 1.6 + d20 * 0.9 + d60 * 0.2 + avg * 2.0 + (up_ratio - 50) * 0.25
        leaders = sorted(cons, key=lambda c: uni[c]["chg"], reverse=True)[:3]
        boards[base_name] = {
            "code": code,
            "name": base_name,
            "level": b.get("level"),
            "count": len(cons),
            "avg_chg": round(avg, 2),
            "up_ratio": round(up_ratio, 1),
            "amount": round(sum(uni[c]["amount"] for c in cons), 0),
            "d5": round(d5, 2),
            "d20": round(d20, 2),
            "d60": round(d60, 2),
            "trend": round(trend, 1),
            "stage": _stage(avg, d5, d20, up_ratio),
            "leaders": [
                {"code": c, "name": uni[c]["name"], "chg": round(uni[c]["chg"], 2)} for c in leaders
            ],
        }
    rows = sorted(boards.values(), key=lambda x: x["trend"], reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------- 字辈炒作
def _char_groups(limit: int = 8) -> List[Dict[str, Any]]:
    uni = _get_universe()
    total = len(uni) or 1
    groups: List[Dict[str, Any]] = []
    for char in _ZODIAC:
        sub = [u for u in uni if char in u["name"]]
        if not sub:
            continue
        groups.append(_summarize(sub, char))
    return groups


def _summarize(sub: List[Dict[str, Any]], char: str) -> Dict[str, Any]:
    n = len(sub)
    chgs = [u["chg"] for u in sub]
    avg = sum(chgs) / n
    up_ratio = sum(1 for x in chgs if x > 0) / n * 100
    limit_up = sum(1 for x in chgs if x >= 9.7)
    amount = sum(u["amount"] for u in sub)
    heat = min(100, round(n * 2.5 + max(0, avg) * 7 + up_ratio * 0.35 + limit_up * 9))
    if avg >= 4 and limit_up >= 2:
        stage = "发酵"
    elif avg >= 1.5 and up_ratio >= 60:
        stage = "走强"
    elif avg <= -1.5:
        stage = "退潮"
    else:
        stage = "观察"
    leaders = sorted(sub, key=lambda x: x["chg"], reverse=True)[:3]
    return {
        "char": char,
        "count": n,
        "avg_chg": round(avg, 2),
        "up_ratio": round(up_ratio, 1),
        "limit_up": limit_up,
        "amount": round(amount, 0),
        "heat": heat,
        "stage": stage,
        "leaders": [
            {"code": u["code"], "name": u["name"], "chg": round(u["chg"], 2)} for u in leaders
        ],
    }


def _discovered_chars(limit: int = 6) -> List[Dict[str, Any]]:
    uni = _get_universe()
    total = len(uni) or 1
    hot = [u for u in uni if u["chg"] >= 5]
    if len(hot) < 10:
        return []
    from collections import Counter

    hot_rate = Counter()
    for u in hot:
        for c in set(u["name"].replace(" ", "")):
            hot_rate[c] += 1
    all_rate = Counter()
    for u in uni:
        for c in set(u["name"].replace(" ", "")):
            all_rate[c] += 1
    rows = []
    for c, cnt in all_rate.items():
        if c in _STOP_CHARS or c in _ZODIAC or cnt < 3:
            continue
        if hot_rate[c] < 2:
            continue
        lift = (hot_rate[c] / len(hot)) / max(1e-6, cnt / total)
        if lift >= 2.2:
            sub = [u for u in uni if c in u["name"]]
            row = _summarize(sub, c)
            row["lift"] = round(lift, 2)
            rows.append(row)
    rows.sort(key=lambda x: (x["lift"], x["count"]), reverse=True)
    return rows[:limit]


def _build_overview() -> Dict[str, Any]:
    if _overview_cache["data"] is not None and time.time() - _overview_cache["ts"] < _OVERVIEW_TTL:
        return _overview_cache["data"]
    t0 = time.time()
    snap = _load_board_snapshots()
    uni = _get_universe()
    sectors = _build_sector_trends(12)
    zodiac = _char_groups()
    discovered = _discovered_chars()
    data = {
        "ok": True,
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "universe": len(uni),
        "board_snapshot_as_of": snap.get("updated_at"),
        "history_method": snap.get("period_method"),
        "sectors": sectors,
        "zodiac": zodiac,
        "discovered": discovered,
        "elapsed_ms": round((time.time() - t0) * 1000),
        "notes": [
            "板块 5/20/60 日收益来自本地东财板块快照（历史截至 " + str(snap.get("updated_at") or "-") + "），今日涨跌为新浪实时快照聚合；",
            "字辈雷达按名称包含字统计；自主发现 = 高位股（涨幅≥5%）中异常聚集的汉字（lift≥2.2）；",
            "涨停按涨幅≥9.7% 近似统计（创业板/科创板 20cm 同样计入），仅作题材热度参考。",
        ],
    }
    _overview_cache["ts"] = time.time()
    _overview_cache["data"] = data
    return data


def warm_themes_cache() -> None:
    """启动预热：让决策台首次打开时不必等待 5-6 秒的全市场拉取。"""
    try:
        _build_overview()
    except Exception:
        pass


# ---------------------------------------------------------------- 路由
@router.get("/overview")
async def themes_overview():
    data = await asyncio.to_thread(_build_overview)
    return data


@router.get("/board/{code}")
async def theme_board(code: str, limit: int = Query(30, ge=1, le=100)):
    snap = _load_board_snapshots()
    board = (snap.get("sectors") or {}).get(code)
    if not board:
        return {"ok": False, "error": "board_not_found", "code": code}
    uni = {u["code"]: u for u in _get_universe()}
    stocks = [uni[c] for c in (board.get("constituents") or []) if c in uni]
    stocks.sort(key=lambda x: x["chg"], reverse=True)
    period = (snap.get("periods") or {}).get(code) or {}
    return {
        "ok": True,
        "code": code,
        "name": board.get("name"),
        "level": board.get("level"),
        "count": len(stocks),
        "d5": period.get("d5"),
        "d20": period.get("d20"),
        "d60": period.get("d60"),
        "stocks": stocks[:limit],
    }


@router.get("/char/{char}")
async def theme_char(char: str, limit: int = Query(50, ge=1, le=100)):
    uni = _get_universe()
    stocks = [u for u in uni if char in u["name"]]
    stocks.sort(key=lambda x: x["chg"], reverse=True)
    return {"ok": True, "char": char, "count": len(stocks), "stocks": stocks[:limit]}
