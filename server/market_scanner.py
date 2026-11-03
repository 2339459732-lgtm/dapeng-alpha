"""market-scanner.v1 — 全市场派生分析层：横截面扫描 / 板块轮动 / 资金流向。

定位与边界：
1. 只消费已加载的全市场快照（market_coverage._LAST_ROWS），不新增重量级上游抓取；
2. 资金流主通道（东财 1.push2 行业/个股主力净流入）为尽力而为通道：
   连续失败即进入退避（默认 30 分钟），期间显式降级为「注意力代理」，绝不冒充实测；
3. 板块动量依赖自积累的 sector_history.json（每交易日一条），历史不足时标注可用窗口，
   不用常数填坑；
4. 所有派生字段标注 source / 口径 / proxy 标记。

输出契约（/api/decision/scan 与 MarketPacket.scan 共用）：
{
  "version": "market-scanner.v1",
  "as_of": ..., "generated_at": ...,
  "cross_section": {...},      # 全市场横截面结构
  "rotation": {...},           # 板块轮动
  "money_flow": {...},         # 资金流向（eastmoney 实测 / proxy 代理）
  "chain": {"build_ms": ..., "rows": ..., "history_days": ...}
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import urllib.request
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional
from server.core.numbers import to_float as _finite
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTOR_HISTORY_PATH = os.path.join(ROOT, "data", "sector_history.json")

SCAN_CACHE_KEY = "market_scan:v1"
SCAN_TTL = 120  # 派生层跟随覆盖快照节拍，2 分钟足够

# ---- 东财主力资金流（尽力而为通道） ----------------------------------------
# 实测：push2.eastmoney.com 与多数编号主机在本机被频控（TLS 连接重置），
# 1.push2 间歇可用，因此带失败计数退避；被封期间完全不打上游。
_EM_HOSTS = ("1.push2.eastmoney.com",)
_EM_INDUSTRY_URL = (
    "https://{host}/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2"
    "&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f104,f105"
)
_EM_STOCK_URL = (
    "https://{host}/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fltt=2&invt=2"
    "&fid=f62&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f2,f3,f62,f184"
)
_EM_BLOCK_SECONDS = 1800  # 连续 2 次失败后封锁半小时
_EM_STATE: Dict[str, Any] = {"fails": 0, "blocked_until": 0.0}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 涨幅分布桶（左闭右开，单位 %）
_CHG_BUCKETS = [
    ("gt_5", 5.0, float("inf"), ">+5%"),
    ("p2_5", 2.0, 5.0, "+2~+5%"),
    ("p0_2", 0.0, 2.0, "0~+2%"),
    ("n2_0", -2.0, 0.0, "-2~0%"),
    ("n5_2", -5.0, -2.0, "-5~-2%"),
    ("lt_n5", float("-inf"), -5.0, "<-5%"),
]


# ============================================================================
# 1. 全市场横截面扫描（纯函数）
# ============================================================================

def build_cross_section(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从全市场行快照派生横截面结构：涨幅分布 / 换手结构 / 市值分化 / 估值带。"""
    valid = [r for r in rows if _finite(r.get("chg")) is not None]
    n = len(valid)
    if not n:
        return {"available": False, "reason": "无有效涨跌幅样本", "sample": 0}

    changes = [float(r["chg"]) for r in valid]
    buckets: Dict[str, int] = {key: 0 for key, *_ in _CHG_BUCKETS}
    for chg in changes:
        for key, low, high, _label in _CHG_BUCKETS:
            if low <= chg < high:
                buckets[key] += 1
                break

    # 换手结构
    turnovers = [float(r["turnover"]) for r in valid if _finite(r.get("turnover")) is not None]
    turnover_high = sum(1 for t in turnovers if t > 10.0)
    turnover_active = sum(1 for t in turnovers if 5.0 < t <= 10.0)

    # 市值分化（亿）
    def _tier_avg(lo: float, hi: float) -> Optional[float]:
        vals = [
            float(r["chg"]) for r in valid
            if _finite(r.get("market_cap_yi")) is not None and lo <= float(r["market_cap_yi"]) < hi
        ]
        return round(sum(vals) / len(vals), 2) if vals else None

    cap_big = _tier_avg(1000.0, float("inf"))
    cap_mid = _tier_avg(100.0, 1000.0)
    cap_small = _tier_avg(0.0, 100.0)

    # 估值带（正 PE 才有意义）
    def _pe_band(lo: float, hi: float) -> int:
        return sum(
            1 for r in valid
            if _finite(r.get("pe")) is not None and lo <= float(r["pe"]) < hi
        )

    pe_bands = {
        "lt_15": _pe_band(0.0, 15.0),
        "b15_30": _pe_band(15.0, 30.0),
        "b30_60": _pe_band(30.0, 60.0),
        "gt_60": _pe_band(60.0, float("inf")),
    }

    # 强弱结构综合分（0-100）：上涨家数占比 50% + 中位涨跌 25% + 涨跌停均衡 25%
    up = sum(1 for c in changes if c > 0)
    down = sum(1 for c in changes if c < 0)
    med = median(changes)
    limit_up = sum(1 for c in changes if c >= 9.8)
    limit_down = sum(1 for c in changes if c <= -9.8)
    breadth_part = up / n * 100.0
    median_part = max(0.0, min(100.0, 50.0 + med * 20.0))
    extremes = limit_up + limit_down
    balance_part = 50.0 if extremes == 0 else 50.0 + 50.0 * (limit_up - limit_down) / extremes
    strength = round(breadth_part * 0.5 + median_part * 0.25 + balance_part * 0.25, 1)

    return {
        "available": True,
        "sample": n,
        "distribution": {
            key: {"count": buckets[key], "pct": round(buckets[key] / n * 100, 1), "label": label}
            for key, _lo, _hi, label in _CHG_BUCKETS
        },
        "up": up, "down": down,
        "median_chg": round(med, 2),
        "mean_chg": round(sum(changes) / n, 2),
        "limit_up": limit_up, "limit_down": limit_down,
        "turnover": {
            "high_pct_over_10": turnover_high,
            "active_5_10": turnover_active,
            "median": round(median(turnovers), 2) if turnovers else None,
            "sample": len(turnovers),
        },
        "cap_tiers": {
            "big_over_1000yi": {"avg_chg": cap_big},
            "mid_100_1000yi": {"avg_chg": cap_mid},
            "small_under_100yi": {"avg_chg": cap_small},
        },
        "pe_bands": pe_bands,
        "strength_score": strength,
        "source": "derived:sina_full_market",
    }


# ============================================================================
# 2. 板块轮动（依赖自积累历史）
# ============================================================================

def _aggregate_industries(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        name = (row.get("industry") or "").strip()
        if not name:
            continue
        buckets.setdefault(name, []).append(row)
    total_amount = sum(_finite(r.get("amount")) or 0 for r in rows)
    result = []
    for name, items in buckets.items():
        changes = [float(r["chg"]) for r in items if _finite(r.get("chg")) is not None]
        if not changes:
            continue
        amount = sum(_finite(r.get("amount")) or 0 for r in items)
        up = sum(1 for c in changes if c > 0)
        result.append({
            "name": name,
            "count": len(items),
            "avg_chg": round(sum(changes) / len(changes), 2),
            "up_ratio": round(up / len(changes) * 100, 1),
            "amount_yi": round(amount, 2),
            "amount_share": round(amount / total_amount * 100, 3) if total_amount > 0 else 0.0,
        })
    result.sort(key=lambda x: x["avg_chg"], reverse=True)
    return result


def _load_sector_history() -> Dict[str, Any]:
    try:
        with open(SECTOR_HISTORY_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "days": {}}


def _write_sector_history(history: Dict[str, Any]) -> None:
    days = history.get("days") or {}
    # 只保留最近 40 个交易日
    for key in sorted(days)[:-40]:
        days.pop(key, None)
    try:
        os.makedirs(os.path.dirname(SECTOR_HISTORY_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".sector-hist-", suffix=".tmp", dir=os.path.dirname(SECTOR_HISTORY_PATH))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(history, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, SECTOR_HISTORY_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as exc:
        logger.warning("sector history persist failed: %s", exc)


def build_rotation(rows: List[Dict[str, Any]], history: Dict[str, Any], today: str) -> Dict[str, Any]:
    """板块轮动：今日行业排名 + 与昨日对比的排名变化 + 动量（历史够用时）。"""
    industries = _aggregate_industries(rows)
    if not industries:
        return {"available": False, "reason": "无行业映射样本（依赖东财 BOARD_NAME）"}

    days = history.get("days") or {}
    history_days = sorted([d for d in days if d < today])
    yesterday = history_days[-1] if history_days else None

    today_ranks = {item["name"]: i + 1 for i, item in enumerate(industries)}
    yesterday_sectors = ((days.get(yesterday) or {}).get("sectors") or {}) if yesterday else {}
    yesterday_ranks = {
        name: i + 1 for i, name in enumerate(
            sorted(yesterday_sectors, key=lambda k: (yesterday_sectors[k].get("avg_chg") or 0), reverse=True)
        )
    } if yesterday_sectors else {}

    for item in industries:
        name = item["name"]
        prev_rank = yesterday_ranks.get(name)
        item["rank"] = today_ranks[name]
        item["rank_change"] = (prev_rank - item["rank"]) if prev_rank else None  # 正数=排名上升

    # 动量：近 N 日 avg_chg 累计（含今日），历史不足时为 null
    for item in industries:
        name = item["name"]
        for window, key in ((5, "mom5"), (20, "mom20")):
            dates = history_days[-(window - 1):]
            if len(dates) < window - 1:
                item[key] = None
                continue
            values = []
            for date in dates:
                sectors = (days.get(date) or {}).get("sectors") or {}
                value = _finite((sectors.get(name) or {}).get("avg_chg"))
                if value is not None:
                    values.append(value)
            if len(values) < window - 1:
                item[key] = None
            else:
                item[key] = round(sum(values) + item["avg_chg"], 2)

    top3_today = [x["name"] for x in industries[:3]]
    top3_yesterday = sorted(
        yesterday_sectors, key=lambda k: (yesterday_sectors[k].get("avg_chg") or 0), reverse=True
    )[:3] if yesterday_sectors else []
    retained = [x for x in top3_today if x in top3_yesterday]
    new_entrants = [x for x in top3_today if x not in top3_yesterday]

    if not yesterday_sectors:
        signal, label = "baseline", "首日基线"
    elif len(retained) >= 2:
        signal, label = "continuity", "主线延续"
    elif len(retained) == 1:
        signal, label = "rotation", "温和轮动"
    else:
        signal, label = "churn", "快速轮动"

    return {
        "available": True,
        "industries": industries,
        "top3_today": top3_today,
        "top3_yesterday": top3_yesterday,
        "retained_leaders": retained,
        "new_entrants": new_entrants,
        "signal": signal,
        "signal_label": label,
        "history_days": len(history_days) + 1,
        "momentum_note": (
            f"动量窗口 {len(history_days) + 1}/20 个交易日，mom20 需积累满 20 日后可用"
            if len(history_days) + 1 < 20 else "动量窗口已满 20 日"
        ),
        "source": "derived:board_name_aggregation",
    }


def record_sector_day(rows: List[Dict[str, Any]], history: Dict[str, Any], today: str) -> bool:
    """把今日行业聚合写入历史（幂等：同日覆盖）。仅收盘后或样本充足时记录。"""
    industries = _aggregate_industries(rows)
    if len(industries) < 10:
        return False
    sectors = {
        item["name"]: {
            "avg_chg": item["avg_chg"],
            "amount_yi": item["amount_yi"],
            "amount_share": item["amount_share"],
            "count": item["count"],
        }
        for item in industries
    }
    days = history.setdefault("days", {})
    changed = days.get(today, {}).get("sectors") != sectors
    days[today] = {"sectors": sectors, "written_at": _now_iso()}
    if changed:
        _write_sector_history(history)
    return changed


# ============================================================================
# 3. 资金流向（东财实测通道 + 注意力代理保底）
# ============================================================================

async def _em_fetch(url: str) -> Optional[Dict[str, Any]]:
    if time.time() < _EM_STATE["blocked_until"]:
        return None
    for host in _EM_HOSTS:
        try:
            req = urllib.request.Request(url.format(host=host), headers={
                "User-Agent": _UA, "Referer": "https://quote.eastmoney.com/",
            })
            def _load():
                with _OPENER.open(req, timeout=8) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            payload = await asyncio.to_thread(_load)
            _EM_STATE["fails"] = 0
            return payload
        except Exception as exc:
            logger.info("eastmoney push2 %s failed: %s", host, exc)
    _EM_STATE["fails"] += 1
    if _EM_STATE["fails"] >= 2:
        _EM_STATE["blocked_until"] = time.time() + _EM_BLOCK_SECONDS
        logger.warning("eastmoney push2 blocked for %ss after %s fails", _EM_BLOCK_SECONDS, _EM_STATE["fails"])
    return None


async def _fetch_em_money_flow() -> Optional[Dict[str, Any]]:
    industry_raw = await _em_fetch(_EM_INDUSTRY_URL)
    if not industry_raw:
        return None
    diff = ((industry_raw.get("data") or {}).get("diff")) or []
    if not diff:
        return None
    industries = []
    for item in diff:
        net = _finite(item.get("f62"))
        industries.append({
            "code": item.get("f12"), "name": item.get("f14"),
            "chg": _finite(item.get("f3")),
            "main_net_yi": round(net / 1e8, 2) if net is not None else None,  # 元 → 亿
            "main_net_pct": _finite(item.get("f184")),
            "up_count": item.get("f104"), "down_count": item.get("f105"),
        })
    industries = [x for x in industries if x["main_net_yi"] is not None]
    industries.sort(key=lambda x: x["main_net_yi"], reverse=True)
    inflow_total = round(sum(x["main_net_yi"] for x in industries if x["main_net_yi"] > 0), 2)
    outflow_total = round(sum(x["main_net_yi"] for x in industries if x["main_net_yi"] < 0), 2)

    stocks = []
    stock_raw = await _em_fetch(_EM_STOCK_URL)
    if stock_raw:
        for item in ((stock_raw.get("data") or {}).get("diff")) or []:
            net = _finite(item.get("f62"))
            stocks.append({
                "code": item.get("f12"), "name": item.get("f14"),
                "price": _finite(item.get("f2")), "chg": _finite(item.get("f3")),
                "main_net_yi": round(net / 1e8, 2) if net is not None else None,
                "main_net_pct": _finite(item.get("f184")),
            })
    stocks = [x for x in stocks if x["main_net_yi"] is not None]

    return {
        "mode": "eastmoney",
        "as_of": _now_iso(),
        "industries": industries,
        "inflow_total_yi": inflow_total,
        "outflow_total_yi": outflow_total,
        "net_total_yi": round(inflow_total + outflow_total, 2),
        "top_inflow": industries[:5],
        "top_outflow": industries[-5:][::-1],
        "top_stocks": stocks[:10],
        "source": "eastmoney_push2:1.push2",
        "proxy": False,
        "note": "主力净流入 = 超大单 + 大单净额（东财口径），非全口径资金",
    }


def build_money_flow_proxy(rows: List[Dict[str, Any]], history: Dict[str, Any], today: str) -> Dict[str, Any]:
    """注意力代理：行业成交额占比变化 × 方向。仅在东财通道不可用时使用，显式标注 proxy。"""
    industries = _aggregate_industries(rows)
    days = history.get("days") or {}
    history_days = sorted([d for d in days if d < today])
    yesterday = history_days[-1] if history_days else None
    yesterday_sectors = ((days.get(yesterday) or {}).get("sectors") or {}) if yesterday else {}

    shifts = []
    for item in industries:
        prev_share = _finite((yesterday_sectors.get(item["name"]) or {}).get("amount_share"))
        if prev_share is None:
            continue
        delta = round(item["amount_share"] - prev_share, 3)
        shifts.append({
            "name": item["name"],
            "chg": item["avg_chg"],
            "amount_share": item["amount_share"],
            "share_delta": delta,
            "attention_yi": round(delta * (item["amount_yi"] / item["amount_share"]) if item["amount_share"] else 0, 2),
        })
    shifts.sort(key=lambda x: x["share_delta"], reverse=True)
    net_attention = round(sum(s["share_delta"] * (1 if s["chg"] > 0 else -1) for s in shifts), 3) if shifts else None

    # 首日没有昨日基线时，退化为「当前成交份额榜」——仍是可核实的真实数据，
    # 只是衡量的是存量关注度而非迁移方向。
    current_share_top = [
        {"name": x["name"], "amount_share": x["amount_share"], "chg": x["avg_chg"],
         "amount_yi": x["amount_yi"]}
        for x in sorted(industries, key=lambda i: i["amount_share"], reverse=True)[:6]
    ]
    return {
        "mode": "proxy",
        "as_of": _now_iso(),
        "attention_shifts": shifts[:8],
        "current_share_top": current_share_top,
        "net_attention": net_attention,
        "proxy": True,
        "baseline_days": len(history_days),
        "source": "derived:amount_share_shift",
        "note": (
            "东财主力资金通道被频控，当前展示「资金注意力代理」：行业成交额占比变化 × 涨跌方向，"
            "衡量资金关注度迁移，非主力净流入实测"
        ),
    }


# ============================================================================
# 4. 统一入口
# ============================================================================

async def get_market_scan(force: bool = False) -> Dict[str, Any]:
    from server.cache import cache
    from server.market_coverage import get_cached_coverage

    if not force:
        cached = cache.get(SCAN_CACHE_KEY)
        if cached is not None:
            return cached

    started = time.perf_counter()
    coverage = get_cached_coverage(max_age=1800)
    if coverage is None:
        payload = {
            "ok": False, "version": "market-scanner.v1", "reason": "全市场覆盖快照未就绪",
            "as_of": None, "generated_at": _now_iso(),
        }
        return payload

    from server.market_coverage import _LAST_ROWS  # 只读最近一次成功加载的全市场行
    rows = [r for r in _LAST_ROWS if r.get("chg") is not None or r.get("industry")]
    if not rows:
        payload = {
            "ok": False, "version": "market-scanner.v1", "reason": "全市场行快照为空",
            "as_of": coverage.get("as_of"), "generated_at": _now_iso(),
        }
        return payload

    today = (coverage.get("as_of") or datetime.now().strftime("%Y-%m-%d"))[:10]
    history = _load_sector_history()

    # 每个交易日记录一次行业聚合（幂等），为轮动/动量积累窗口
    try:
        record_sector_day(rows, history, today)
    except Exception as exc:
        logger.warning("sector day record failed: %s", exc)

    cross = build_cross_section(rows)
    rotation = build_rotation(rows, history, today)

    money_flow: Dict[str, Any]
    em = None
    try:
        em = await _fetch_em_money_flow()
    except Exception as exc:
        logger.warning("eastmoney money flow failed: %s", exc)
    money_flow = em if em else build_money_flow_proxy(rows, history, today)

    payload = {
        "ok": True,
        "version": "market-scanner.v1",
        "as_of": coverage.get("as_of"),
        "generated_at": _now_iso(),
        "cross_section": cross,
        "rotation": rotation,
        "money_flow": money_flow,
        "chain": {
            "build_ms": round((time.perf_counter() - started) * 1000, 1),
            "rows": len(rows),
            "history_days": rotation.get("history_days"),
            "money_flow_mode": money_flow.get("mode"),
        },
    }
    cache.set(SCAN_CACHE_KEY, payload, SCAN_TTL)
    try:
        from server.data_bus import bus

        await bus.publish(
            "scan", payload, trigger="request",
            elapsed_ms=(payload.get("chain") or {}).get("build_ms"),
            trace=payload.get("as_of"),
        )
    except Exception as exc:
        logger.debug("data bus publish(scan) skipped: %s", exc)
    return payload
