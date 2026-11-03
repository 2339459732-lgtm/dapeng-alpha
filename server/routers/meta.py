# -*- coding: utf-8 -*-
"""统一能力元信息 —— 前端「实时/缓存/收盘/不可用」角标的唯一事实源（T01）。

契约见 `docs/10-architecture/openbb-dsa-integration-design.md` §3.8：

    GET /api/meta/capability?keys=finance,positions,kline,flow,sector

    {
      "ok": true,
      "data": {
        "finance": {"state": "closed", "label": "收盘 2026-06-30",
                    "as_of": "...", "age": 1036800, "degraded": false,
                    "reason": null, "source": "AkShare/新浪"}
      },
      ...§3.1 信封元字段...
    }

硬性约束：
1. **只读**：只读 L1（QuoteHub 计数）与 L2（TTLCache 条目），**绝不触发上游**。
   实现上不调用任何 ``get_or_fetch``，也不碰 ``quote_hub.snapshot()``。
2. 无数据时返回 ``state="unavailable"`` + ``reason``，HTTP 仍为 200，**不 500**。
3. ``state`` 的推导规则集中在 :func:`_classify` 一处，前端做镜像校验。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import (
    CAPABILITY_CLOSED_TOLERANCE,
    CAPABILITY_REALTIME_AGE,
    CAPABILITY_STALE_FACTOR,
)
from server.quote_hub import quote_hub
from server.routers.stream import _market_open, hub as stream_hub
from server.core.timeutil import now_iso as _now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meta", tags=["meta"])

# ----------------------------------------------------------------------
#  能力键注册表：key → 数据来源（L1 实时 / L2 缓存前缀）
#  新增数据域时只改这里（T02/T03/T04 各自补充自己的前缀即可）
# ----------------------------------------------------------------------
CAPABILITY_DEFS: Dict[str, Dict[str, Any]] = {
    # L2 缓存域（非价格数据）
    "finance": {
        # akfund:v1:abstract 为 T02 新链路（providers/akshare_fundamental.py）的缓存前缀
        "prefixes": ("finance:", "akfund:v1:abstract:"),
        "source": "AkShare/新浪·财务摘要",
        "daily": True,
    },
    "valuation": {
        "prefixes": ("valuation:", "valuation_pct:", "akfund:v1:valuation:"),
        "source": "AkShare/百度·估值分位",
        "daily": True,
    },
    "dividend": {
        "prefixes": ("dividend:", "akfund:v1:dividend:"),
        "source": "巨潮/东财·分红送配",
        "daily": True,
    },
    "kline": {
        "prefixes": ("kline:", "raw_kline:", "kline_closes:"),
        "source": "腾讯/东财 K线",
        "daily": False,
    },
    "timeshare": {
        "prefixes": ("timeshare:",),
        "source": "腾讯分时",
        "daily": False,
    },
    "flow": {
        "prefixes": ("fund_flow_v2:", "sector_flow_v2:", "fund_flow:"),
        "source": "东财 push2·资金流",
        "daily": False,
    },
    "sector": {
        "prefixes": ("sector_ranking_v2", "sector:"),
        "source": "东财 push2·板块",
        "daily": False,
    },
    "overview": {
        "prefixes": ("market_overview_v3", "market_dashboard:"),
        "source": "大盘快照",
        "daily": False,
    },
    "news": {
        "prefixes": ("news:", "market_news_ak:"),
        "source": "AkShare·财经要闻",
        "daily": False,
    },
    "north": {
        "prefixes": ("north:",),
        "source": "沪深股通",
        "daily": False,
    },
    "profile": {
        "prefixes": ("profile:",),
        "source": "个股简况",
        "daily": True,
    },
    "research": {
        "prefixes": ("report:",),
        "source": "研报/一致预期",
        "daily": True,
    },
    # ICT 模型（本地计算：输入腾讯K线，输出 scan / history / structure-stats 三份结果）
    # 前缀说明：
    #   ict:                  → 结果缓存（ict:scan / ict:history / ict:structure，最后写入者优先）
    #   ict_scan_kline:       → /api/ict/scan 专用 K 线（独立键，不写 raw_kline）
    #   ict_hist_kline:       → /api/ict/history 与 structure-stats 专用 K 线
    # daily=True：模型基于日线序列，结果在收盘后不再变化，角标应允许「收盘」态。
    "ict": {
        "prefixes": ("ict:", "ict_scan_kline:", "ict_hist_kline:"),
        "source": "ICT 模型（本地计算，输入腾讯K线）",
        "daily": True,
    },
    # L1 实时域（价格只能来自 QuoteHub，见 §3.9 规则 1）
    "quote": {"quote_hub": True, "source": "腾讯实时/QuoteHub"},
    "positions": {"quote_hub": True, "source": "腾讯实时/QuoteHub（持仓浮盈）"},
}


def _classify(
    *,
    has_value: bool,
    age: Optional[float],
    ttl: Optional[float],
    market_open: bool,
    degraded: bool = False,
    stale_max: Optional[float] = None,
    closed_tolerance: Optional[float] = None,
    allow_realtime: bool = True,
) -> str:
    """由 (has_value, age, ttl, market_open, stale_max) 推导四态 + 陈旧态。

    判定顺序（**市场状态优先**，唯一实现，前端镜像）：
    1. 无值 / 无年龄 → ``unavailable``（从未成功取数）
    2. 休市 且 age ≤ 休市容忍窗口（默认 24h）→ ``closed``
       —— 收盘后数据本就不刷新，**不能**把「休市」报成「行情源故障」；
    3. 盘中 age ≤ 实时阈值（3s）→ ``realtime``（仅当 ``allow_realtime=True``）
    4. 盘中 age ≤ ttl → ``cached``
    5. 盘中 ttl < age ≤ stale_max → ``stale``（有值但偏旧，降级使用）
    6. 盘中 age > stale_max（或休市且超容忍窗口且超 stale_max）→ ``unavailable``（真故障）

    Args:
        allow_realtime: 是否允许判 ``realtime``。日频域（``CAPABILITY_DEFS`` 里
            ``daily: True``，如财报/估值/分红/ICT）**按需计算、收盘后不再变化**，
            刚算完 1 秒就标「实时 2s」会让用户以为它每秒在更新，属误导。
            传 ``False`` 时第 3 步跳过，直接落到 ``cached``（label 为「缓存 Ns」）。
            **只影响 realtime 这一档**：``unavailable`` / ``closed`` / ``stale``
            的判定顺序与阈值完全不变。
    """
    if not has_value or age is None:
        return "unavailable"

    tol = CAPABILITY_CLOSED_TOLERANCE if closed_tolerance is None else float(closed_tolerance)
    if not market_open and age <= tol:
        return "closed"

    if market_open:
        if allow_realtime and age <= CAPABILITY_REALTIME_AGE:
            return "realtime"
        if ttl is None or age <= ttl * CAPABILITY_STALE_FACTOR:
            return "cached"
        if stale_max is None or age <= stale_max:
            return "stale"
        return "unavailable"

    # 休市但已超过容忍窗口：只有还在 stale_max 内才算「陈旧」，否则按故障处理
    if stale_max is not None and age <= stale_max:
        return "stale"
    return "unavailable"


def _label(state: str, age: Optional[float], as_of: Optional[str]) -> str:
    """四态 → 角标文案（中文，直接展示给用户）。"""
    if state == "realtime":
        return "实时 3s" if age is None else f"实时 {max(1, int(round(age)))}s"
    if state == "cached":
        return "缓存" if age is None else f"缓存 {max(1, int(round(age)))}s"
    if state == "closed":
        hhmm = ""
        if as_of:
            if "T" in as_of:
                hhmm = as_of.split("T", 1)[1][:5]
            elif len(as_of) >= 16:
                hhmm = as_of[11:16]
        if hhmm:
            return f"收盘 · {hhmm}"
        return f"收盘 · {as_of[:10]}" if as_of else "收盘"
    if state == "stale":
        if age is None:
            return "陈旧"
        return f"陈旧 {max(1, int(round(age / 60)))} 分钟"
    return "不可用"


def _empty(key: str, reason: str,
           realtime_allowed: Optional[bool] = None) -> Dict[str, Any]:
    """不可用态的标准形状（字段齐全、无 0 冒充）。

    ``realtime_allowed`` 与可用态保持同名字段，使「allow_realtime 是否生效」在
    **无数据时也能被验证**（否则 profile/research 这类暂时无缓存的域只能靠猜）。
    """
    return {
        "key": key,
        "state": "unavailable",
        "label": "不可用",
        "as_of": None,
        "age": None,
        "degraded": True,
        "reason": reason,
        "source": None,
        "realtime_allowed": realtime_allowed,
    }


def _resolve_cache_domain(key: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """从 L2 缓存读取某数据域最新条目的元信息（不触发上游）。"""
    allow_realtime = not bool(spec.get("daily"))
    entry = cache.find_newest(spec.get("prefixes") or ())
    if entry is None:
        return _empty(key, "尚无缓存（该数据域本次进程内还未成功取过数）",
                      realtime_allowed=allow_realtime)

    value = entry.get("value")
    upstream_degraded = bool(value.get("degraded")) if isinstance(value, dict) else False
    ttl = entry.get("ttl")
    # 缓存域没有独立 stale_max：按 ttl 的 4 倍作为「陈旧上限」的保守默认
    stale_max = (float(ttl) * 4.0) if ttl else None
    state = _classify(
        has_value=True,
        age=entry.get("age"),
        ttl=ttl,
        market_open=_market_open(),
        degraded=upstream_degraded,
        stale_max=stale_max,
        # 日频域（财报/估值/分红/ICT…）不判 realtime：它们按需计算、收盘后不再变化，
        # 标「实时 Ns」会误导用户以为在持续刷新。此处的 daily 字段此前只声明未被读取。
        allow_realtime=allow_realtime,
    )
    as_of = None
    if entry.get("stored_at"):
        as_of = datetime.fromtimestamp(entry["stored_at"]).astimezone().isoformat(timespec="seconds")

    reason: Optional[str] = None
    if state == "stale":
        reason = (f"缓存已超过 TTL（age={entry.get('age')}s > ttl={ttl}s），降级使用"
                  if ttl else "缓存偏旧，降级使用")
    elif state == "unavailable":
        reason = f"缓存已超出陈旧上限（age={entry.get('age')}s）"
    elif upstream_degraded and state != "closed":
        reason = "上游本次降级，展示的是上一份有效数据"

    return {
        "key": key,
        "cache_key": entry.get("key"),
        "state": state,
        "label": _label(state, entry.get("age"), as_of),
        "as_of": as_of,
        "age": entry.get("age"),
        "degraded": state == "stale" or (upstream_degraded and state != "closed"),
        "reason": reason,
        "source": spec.get("source"),
        # 自证字段：本次判定是否允许「实时」档。
        # 日频域（daily: True）恒为 False —— 即使当前无数据（unavailable）也能据此确认
        # 「allow_realtime 已生效」，不必依赖 age 是否刚好落在 3s 窗口内去碰运气。
        "realtime_allowed": not bool(spec.get("daily")),
    }


def _resolve_quote_hub(key: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """从 L1 QuoteHub 计数器推导实时域状态（不触发上游取数）。

    注意顺序：**先判休市**（休市数据不刷新是正常现象 → ``closed``），
    再看是否真的超过陈旧上限（那才是故障 → ``unavailable``）。
    休市时**不透出** QuoteHub 的「N 秒无更新」类 reason —— 那是噪音。
    """
    h = quote_hub.stats_snapshot()
    ttl = float(h.get("ttl_seconds") or 1.5)
    stale_max = float(h.get("stale_max_seconds") or 30.0)
    cached = int(h.get("codes_cached") or 0)
    last_ok_at = h.get("last_ok_at")

    if cached <= 0 or not last_ok_at:
        return _empty(key, "尚无实时报价快照（QuoteHub 为空，本次进程内还未成功取过价）")

    try:
        ok_dt = datetime.strptime(last_ok_at, "%Y-%m-%d %H:%M:%S").astimezone()
        age = max(0.0, (datetime.now().astimezone() - ok_dt).total_seconds())
    except Exception:  # pragma: no cover - 时间格式异常按未知处理
        return _empty(key, "QuoteHub 成功时间无法解析")

    market_open = _market_open()
    tolerance = max(CAPABILITY_CLOSED_TOLERANCE, stale_max * 4.0)
    state = _classify(
        has_value=True,
        age=age,
        ttl=ttl,
        market_open=market_open,
        stale_max=stale_max,
        closed_tolerance=tolerance,
    )
    as_of = ok_dt.isoformat(timespec="seconds")

    reason: Optional[str] = None
    label_override: Optional[str] = None
    if state == "stale":
        reason = f"实时快照偏旧（{int(age)}s > TTL {ttl:g}s），仍在陈旧上限内"
    elif state == "unavailable":
        # QuoteHub 是惰性取数：没有 SSE 订阅者、也没有 REST 请求时它本就不刷新，
        # 缓存因此自然变老。盘中表现为 age > stale_max，但根因是「未激活」而非「源故障」。
        # 若一律报 unavailable，值班会把「没人看」误判成行情挂了，反而掩盖真实故障。
        try:
            idle = bool(market_open) and stream_hub.active_clients <= 0
        except Exception:  # pragma: no cover - 防御式
            idle = False
        if idle:
            state = "closed"
            reason = "当前无 SSE 订阅者，行情未在后台刷新（惰性取数）；打开看板或订阅后自动恢复"
            hhmm = as_of[11:16] if len(as_of) >= 16 else "--:--"
            label_override = f"未订阅 · {hhmm}"
        else:
            reason = f"行情源 {int(age)} 秒无更新（超过 {int(stale_max)} 秒上限）"

    return {
        "key": key,
        "state": state,
        "label": label_override or _label(state, age, as_of),
        "as_of": as_of,
        "age": round(age, 3),
        "degraded": state == "stale",
        "reason": reason,
        "source": spec.get("source"),
        "codes_cached": cached,
    }


def _resolve(key: str) -> Dict[str, Any]:
    """解析单个能力键（未注册 → unavailable + reason，不抛错）。"""
    spec = CAPABILITY_DEFS.get(key)
    if spec is None:
        return _empty(key, f"未注册的能力键：{key}")
    if spec.get("quote_hub"):
        return _resolve_quote_hub(key, spec)
    return _resolve_cache_domain(key, spec)


def _parse_keys(raw: str) -> List[str]:
    """解析 ?keys=a,b,c（去重保序）；为空 → 全部已注册键。"""
    out: List[str] = []
    for part in str(raw or "").split(","):
        k = part.strip().lower()
        if k and k not in out:
            out.append(k)
    return out or list(CAPABILITY_DEFS.keys())


@router.get("/capability")
async def get_capability(
    keys: str = Query("", description="逗号分隔的能力键，如 finance,positions,kline；空=全部"),
) -> Dict[str, Any]:
    """统一能力元信息（**只读缓存，不触发上游**）。

    返回每个 key 的 ``state``（realtime/cached/closed/stale/unavailable）+
    ``as_of`` / ``age`` / ``reason`` / ``source``，并附带 §3.1 信封元字段。
    """
    wanted = _parse_keys(keys)
    data: Dict[str, Dict[str, Any]] = {}
    for key in wanted:
        try:
            data[key] = _resolve(key)
        except Exception as exc:  # pragma: no cover - 单个键异常不影响整体
            logger.warning("capability resolve failed for %s: %s", key, exc)
            data[key] = _empty(key, f"能力解析异常：{type(exc).__name__}")

    # 信封级聚合：degraded = 任一不可用/降级；as_of 取最新；age 取最大
    states = [v.get("state") for v in data.values()]
    ages = [v.get("age") for v in data.values() if isinstance(v.get("age"), (int, float))]
    as_ofs = [v.get("as_of") for v in data.values() if v.get("as_of")]
    degraded = any(s in ("unavailable", "stale") for s in states) or any(
        bool(v.get("degraded")) for v in data.values()
    )
    reason = next((v.get("reason") for v in data.values() if v.get("reason")), None)

    return {
        "ok": True,
        "data": data,
        "message": "ok",
        "source": "cache+quote_hub",
        "as_of": max(as_ofs) if as_ofs else _now_iso(),
        "age": max(ages) if ages else None,
        "degraded": bool(degraded),
        "reason": reason,
        "market_open": _market_open(),
    }
