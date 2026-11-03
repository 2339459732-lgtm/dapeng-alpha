"""共振与指标：多周期共振、指标目录与批量计算"""

import asyncio
import copy
import json as _json
import logging
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query
from server import indicators as indicators_mod
from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, find_stock_name
from server.strategies import (
    compute_atr, compute_kdj, compute_ma, compute_ma_cross,
    compute_macd, compute_rsi, compute_strategy,
)
from server.tencent_kline import fetch_tencent_kline
from server.eastmoney import fetch_kline_eastmoney
from server.westock import westock
from server.quote_hub import quote_hub
from server.routers.stream import _bar_ts, _build_quote, _market_open, _norm_period
from server.symbols import MINUTE_PERIODS

from server.routers.kline_common import _ttl_for_period, logger

router = APIRouter()


# ================================================================
#  v5.0 多周期共振分析
# ================================================================
RESONANCE_PERIODS = ["5", "15", "30", "60", "day", "week"]
PERIOD_LABELS = {"5": "5分钟", "15": "15分钟", "30": "30分钟", "60": "60分钟", "day": "日线", "week": "周线"}


def _assess_period(klines: list, period: str) -> dict:
    """对单个周期的 K 线进行四大指标评估，返回多/空/中判断。"""
    import numpy as np

    if not klines or len(klines) < 20:
        return {
            "period": period,
            "label": PERIOD_LABELS.get(period, period),
            "bars": len(klines) if klines else 0,
            "status": "insufficient",
            "trend": "—",
            "ma_trend": "insufficient",
            "macd_signal": "insufficient",
            "kdj_zone": "insufficient",
            "rsi_zone": "insufficient",
            "bullish_count": 0,
            "bearish_count": 0,
            "verdict": "中",
        }

    closes = np.array([float(k.get("c", 0)) for k in klines])
    highs = np.array([float(k.get("h", 0)) for k in klines])
    lows = np.array([float(k.get("l", 0)) for k in klines])

    bullish = 0
    bearish = 0

    # — 1. MA 趋势：MA5 vs MA20 —
    ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else float(closes[-1])
    ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else float(closes[-1])
    if ma5 > ma20:
        ma_trend = "bullish"
        bullish += 1
    elif ma5 < ma20:
        ma_trend = "bearish"
        bearish += 1
    else:
        ma_trend = "neutral"

    # — 2. MACD：DIF vs DEA + 柱状图 —
    macd = compute_macd(klines)
    dif = macd.get("dif", [])
    dea = macd.get("dea", [])
    hist = macd.get("hist", [])
    if len(dif) >= 2 and len(dea) >= 2 and len(hist) >= 2:
        if dif[-1] > dea[-1] and hist[-1] > 0:
            macd_signal = "bullish"
            bullish += 1
        elif dif[-1] < dea[-1] and hist[-1] < 0:
            macd_signal = "bearish"
            bearish += 1
        else:
            macd_signal = "neutral"
    else:
        macd_signal = "insufficient"

    # — 3. KDJ：K 值区间 —
    kdj = compute_kdj(klines)
    k_val = kdj.get("k", 50)
    if k_val < 30:
        kdj_zone = "oversold"
    elif k_val > 70:
        kdj_zone = "overbought"
    elif k_val > 50:
        kdj_zone = "bullish"
        bullish += 1
    else:
        kdj_zone = "bearish"
        bearish += 1

    # — 4. RSI(14) 区间 —
    rsi_val = compute_rsi(closes)
    if rsi_val is not None:
        if rsi_val > 60:
            rsi_zone = "bullish"
            bullish += 1
        elif rsi_val < 40:
            rsi_zone = "bearish"
            bearish += 1
        else:
            rsi_zone = "neutral"
    else:
        rsi_zone = "insufficient"

    # — 综合判定 —
    if bullish >= 3:
        verdict = "多"
        status = "bullish"
    elif bearish >= 3:
        verdict = "空"
        status = "bearish"
    else:
        verdict = "中"
        status = "neutral"

    return {
        "period": period,
        "label": PERIOD_LABELS.get(period, period),
        "bars": len(klines),
        "status": status,
        "trend": {"多": "↑", "空": "↓", "中": "→"}.get(verdict, "→"),
        "ma_trend": ma_trend,
        "macd_signal": macd_signal,
        "kdj_zone": kdj_zone,
        "rsi_zone": rsi_zone,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "verdict": verdict,
    }


def _assess_resonance(periods_data: list) -> dict:
    """根据 6 个周期结果计算共振总评。"""
    verdicts = [p["verdict"] for p in periods_data if p["status"] != "insufficient"]
    n = len(verdicts)
    if n == 0:
        return {
            "type": "insufficient",
            "score": 0,
            "summary": "数据不足，无法判断共振",
            "glow": "neutral",
        }

    bull = verdicts.count("多")
    bear = verdicts.count("空")
    neutral = verdicts.count("中")

    score_raw = bull * 100 / 6

    if bull >= 5:
        return {"type": "strong_bullish", "score": round(score_raw), "summary": "强势多头共振，趋势高度一致", "glow": "bullish"}
    elif bull >= 4:
        return {"type": "bullish", "score": round(score_raw), "summary": "多头共振，趋势确立", "glow": "bullish"}
    elif bear >= 5:
        return {"type": "strong_bearish", "score": round(bear * 100 / 6), "summary": "强势空头共振，风险较高", "glow": "bearish"}
    elif bear >= 4:
        return {"type": "bearish", "score": round(bear * 100 / 6), "summary": "空头共振，注意风险", "glow": "bearish"}
    elif bull > bear and bull >= 3:
        return {"type": "slight_bullish", "score": round(score_raw), "summary": "短线偏多，可关注机会", "glow": "slight_bullish"}
    elif bear > bull and bear >= 3:
        return {"type": "slight_bearish", "score": round(bear * 100 / 6), "summary": "短线偏空，谨慎操作", "glow": "slight_bearish"}
    else:
        return {"type": "divergent", "score": 50, "summary": "周期分歧，观望为主", "glow": "neutral"}


def _build_period_insufficient(period: str) -> dict:
    """构建数据不足的周期结果。"""
    return {
        "period": period,
        "label": PERIOD_LABELS.get(period, period),
        "bars": 0,
        "status": "insufficient",
        "trend": "—",
        "ma_trend": "insufficient",
        "macd_signal": "insufficient",
        "kdj_zone": "insufficient",
        "rsi_zone": "insufficient",
        "bullish_count": 0,
        "bearish_count": 0,
        "verdict": "中",
    }


@router.get("/multi-period-resonance")
async def get_multi_period_resonance(
    code: str = Query(..., description="股票代码，如 sh600703"),
):
    """多周期共振分析 — 同时拉取 6 个周期 K 线并计算共振状态。

    返回 { periods[], resonance{}, ts }
    - periods: 每个周期的指标评估结果
    - resonance: 共振总评（type/score/summary/glow）
    """
    async def _fetch_one(period: str):
        try:
            klines = await fetch_tencent_kline(code, period=period, limit=KLINE_LIMIT)
            return _assess_period(klines, period) if klines else _build_period_insufficient(period)
        except Exception as e:
            logger.warning("Resonance fetch fail %s %s: %s", code, period, str(e))
            return _build_period_insufficient(period)

    # 并行拉取 6 个周期
    results = await asyncio.gather(*[_fetch_one(p) for p in RESONANCE_PERIODS])

    resonance = _assess_resonance(results)

    return {
        "ok": True,
        "data": {
            "code": code,
            "name": find_stock_name(code),
            "periods": results,
            "resonance": resonance,
            "ts": max(
                (p.get("bars", 0) for p in results),
                default=0,
            ),
        },
    }


# ================================================================
#  v6.0 独立技术指标端点 GET /api/kline/indicators
#
#  设计依据：docs/10-architecture/openbb-dsa-integration-design.md §3.4 / §6.2-T03
#
#  为什么不塞进 /api/kline：
#  /api/kline 每次同步计算 MA/MACD/MA_CROSS/ATR/STRATEGY/RSI 已是热点路径，
#  12 组序列全塞进去会让「不看副图」的用户也付这份 CPU。独立端点 = 按需 + 独立缓存键。
#
#  缓存纪律（B4 边界）：
#  - **只读** 现有 `raw_kline:{code}:{period}`（/api/kline 的共享原始缓存），
#    能从它满足就一个上游请求都不打；
#  - 根数不够时才走 `cache.get_or_fetch("ind_kline:...")` 取一次（single-flight），
#    **不写回 raw_kline**（写回会把 /api/kline 的默认根数从 120 变成 250，属行为污染）；
#  - 指标结果按 `ind:{code}:{period}:{names}:{params}:{limit}` 缓存 300s：
#    日 K 收盘后序列不变，但盘中最后一根在动，300s 是「副图不需要秒级」与
#    「盘中不能太旧」的折中；盘中 tick 由前端本地重算最后一根，不重新请求。
# ================================================================
CACHE_TTL_INDICATORS = 300.0        # 指标结果缓存 300s（见上）
INDICATOR_LIMIT_DEFAULT = 250       # 默认根数：够 ADX(14)/Aroon(25)/Ichimoku(52+26) 成形
INDICATOR_LIMIT_MIN = 1             # 下限 1：数据不足要**如实报 ok:false**，不能用 422 代替
INDICATOR_LIMIT_MAX = 800           # 与 /api/kline 上限一致（上游 >800 根不可靠）
INDICATOR_SOURCE = "Tencent K线（本地计算）"


def _split_names(raw: Optional[str]) -> List[str]:
    """把 ``names=adx,mfi`` 解析成列表；空 -> ``["all"]``。"""
    if not raw:
        return ["all"]
    names = [s.strip().lower() for s in str(raw).replace(";", ",").split(",")]
    names = [s for s in names if s]
    return names or ["all"]


def _expand_names(names: List[str]) -> List[str]:
    """展开 ``all`` 与别名，去重保序，返回注册名列表（未知名原样保留供报错）。"""
    expanded: List[str] = []
    for raw in names:
        if raw in ("all", "*"):
            for name in indicators_mod.INDICATOR_NAMES:
                if name not in expanded:
                    expanded.append(name)
            continue
        resolved = indicators_mod.resolve_name(raw)
        key = resolved if resolved else raw
        if key not in expanded:
            expanded.append(key)
    return expanded


async def _load_indicator_klines(code: str, period: str, limit: int) -> Optional[list]:
    """取用于计算指标的 K 线：先复用共享原始缓存，再按需取一次上游。

    数据源顺序与 /api/kline 完全一致（腾讯 HTTP → 东财 HTTP → westock CLI），
    **不新增源**，只是把结果存在独立键下，避免污染 /api/kline 的根数语义。
    """
    per = _norm_period(period)
    shared = cache.get(f"raw_kline:{code}:{per}")
    if shared and len(shared) >= limit:
        logger.debug("indicators reuse raw_kline cache: %s %s (%d bars)", code, per, len(shared))
        return list(shared[-limit:])

    ttl = _ttl_for_period(per)
    ind_key = f"ind_kline:{code}:{per}:{limit}"

    async def _fetch() -> Optional[list]:
        bars: Optional[list] = None
        try:
            bars = await fetch_tencent_kline(code, period=per, limit=limit, timeout=4.0)
        except Exception as exc:  # noqa: BLE001 —— 单源失败不阻断后续源
            logger.debug("indicators tencent kline fail %s: %s", code, exc)
        if not bars:
            try:
                bars = await fetch_kline_eastmoney(code, period=per, limit=limit, timeout=3.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("indicators eastmoney kline fail %s: %s", code, exc)
        if not bars:
            try:
                bars = await westock.kline(code, period=per)
            except Exception as exc:  # noqa: BLE001
                logger.debug("indicators westock kline fail %s: %s", code, exc)
        return bars or None

    bars = await cache.get_or_fetch(ind_key, _fetch, ttl)
    if not bars:
        # 兜底：即使根数不足共享缓存，也用它算（同标的同周期的真实数据，不造假）
        if shared:
            return list(shared[-limit:])
        return None
    return list(bars[-limit:]) if len(bars) > limit else list(bars)


def _klines_as_of(klines: List[Dict[str, Any]]) -> tuple:
    """取最后一根 K 线的时间戳 -> ``(as_of_iso, age_seconds)``。

    日/周/月线只有日期（``20261019``），按 A 股收市时点 ``15:00`` 标注
    （日线数据只有收盘后才定稿；标 00:00 会把陈旧度虚报 15 小时）。
    取不到时返回 ``(None, None)``（不猜时间，避免角标显示假「刚刚更新」）。
    """
    if not klines:
        return None, None
    last = klines[-1] if isinstance(klines[-1], dict) else {}
    raw = last.get("d") or last.get("t") or last.get("date") or last.get("time")
    if raw is None:
        return None, None
    s = str(raw).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    try:
        if len(digits) >= 14:                       # 20261019150000
            dt = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        elif len(digits) == 12:                     # 202610191130
            dt = datetime.strptime(digits[:12], "%Y%m%d%H%M")
        elif len(digits) == 8:                      # 20261019 -> 当日 15:00 收盘
            day = datetime.strptime(digits[:8], "%Y%m%d")
            dt = day.replace(hour=15)
        else:
            return s or None, None
    except ValueError:
        return s or None, None
    age = max(0.0, datetime.now().timestamp() - dt.timestamp())
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00"), round(age, 1)


def _indicator_payload(
    code: str,
    period: str,
    limit: int,
    names: List[str],
    params_raw: Any,
    klines: Optional[list],
) -> Dict[str, Any]:
    """纯计算：把「K 线 + 指标名」装配成完整响应体（无 I/O，便于单测与计时）。"""
    positional = indicators_mod.parse_positional(params_raw)
    bars = len(klines) if klines else 0
    series: Dict[str, Any] = {}
    failed: List[str] = []
    ignored_params: List[str] = []
    unknown: List[str] = []

    for name in names:
        if indicators_mod.resolve_name(name) is None:
            unknown.append(name)
            series[name] = {
                "values": [],
                "overlay": False,
                "params": {},
                "params_schema": {},
                "panes": {},
                "label": "",
                "value_range": [None, None],
                "min_bars": 0,
                "reason": f"未知指标名 {name!r}（可用：{','.join(indicators_mod.INDICATOR_NAMES)}）",
            }
            failed.append(name)
            continue

        # 位置参数只在「参数个数吻合」时套用，否则用默认值（并如实记录被忽略）
        spec = indicators_mod.INDICATOR_REGISTRY[indicators_mod.resolve_name(name)]
        use_pos = positional
        if positional is not None and len(positional) != len(spec.params):
            if len(names) > 1 or len(positional) != len(spec.params):
                use_pos = None
                if name not in ignored_params:
                    ignored_params.append(name)

        item = indicators_mod.compute(name, klines, positional=use_pos)
        series[item["name"]] = {
            "values": item["values"],
            "overlay": item["overlay"],
            "params": item["params"],
            "params_schema": item["params_schema"],
            "panes": item["series"],
            "label": item["label"],
            "value_range": item["value_range"],
            "min_bars": item["min_bars"],
            "reason": item["reason"],
        }
        if item["reason"]:
            failed.append(item["name"])

    ok = not failed
    if ok:
        message = "ok"
    elif klines is None:
        message = "K 线数据不可得，无法计算指标（不返回空数组冒充成功）"
    elif unknown:
        message = (
            f"{len(failed)}/{len(names)} 个指标不可用："
            + "、".join(failed)
            + ("；未知指标：" + "、".join(unknown) if unknown else "")
        )
    else:
        message = f"{len(failed)}/{len(names)} 个指标数据不足或无效：" + "、".join(failed)

    as_of, age = _klines_as_of(klines or [])
    return {
        "ok": ok,
        "message": message,
        "source": INDICATOR_SOURCE if bars else None,
        "as_of": as_of,
        "age": age,
        "degraded": bool(failed),
        "reason": None if ok else message,
        "data": {
            "code": code,
            "period": _norm_period(period),
            "bars": bars,
            "limit": limit,
            "names": [n for n in names if n in series],
            "series": series,
            "failed": failed,
            "params_ignored": ignored_params,
        },
    }


@router.get("/kline/indicators")
async def get_kline_indicators(
    code: str = Query(..., description="股票代码，如 sh600519"),
    period: Optional[str] = Query("day", description="周期: day/week/month/60/30/15/5/1"),
    limit: Optional[int] = Query(
        INDICATOR_LIMIT_DEFAULT,
        ge=INDICATOR_LIMIT_MIN,
        le=INDICATOR_LIMIT_MAX,
        description="计算用 K 线根数（1-800）。根数不足会如实返回 ok:false + reason，而不是 422。",
    ),
    names: Optional[str] = Query(
        None,
        description="指标名，逗号分隔，如 adx,mfi,supertrend；all=全部 12 类；不传等价 all",
    ),
    indicator: Optional[str] = Query(
        None,
        description="兼容设计文档 §3.4 的单指标入参（等价 names=<indicator>）",
    ),
    params: Optional[str] = Query(
        None,
        description='参数覆盖，位置语义，如 "10,3.0"；参数个数与指标不符时该指标用默认值',
    ),
    fresh: Optional[bool] = Query(False, description="true=绕过指标缓存重算"),
):
    """**独立技术指标端点**（12 类 + 3 项补充）。

    与 ``/api/kline`` 的关系：**完全新增，不改既有端点**。K 线来源复用现有链路与缓存
    （``raw_kline:*`` / 腾讯→东财→westock），指标为本地纯计算。

    入参：
      * ``names``（或 ``indicator``）：``adx`` / ``aroon`` / ``obv`` / ``mfi`` / ``atr`` /
        ``stoch`` / ``cci`` / ``supertrend`` / ``ichimoku`` / ``donchian`` / ``keltner``(kc) /
        ``vwap``，另支持 §3.4 的 ``choppiness`` / ``williams_r`` / ``di``；``all`` 取 12 类主指标。
      * ``params``：位置语义参数覆盖（``"14"`` / ``"10,3.0"``）。
      * ``limit``：1–800，默认 250。

    响应：每个指标返回 ``values``（主 pane 序列，与根数等长）、``overlay``、``params``、
    ``params_schema``（前端参数弹层用）、``panes``（多线指标的全部序列）。
    前置样本不足 / 未知指标 / K 线不可得时该指标 ``values: []`` + ``reason``，
    整体 ``ok:false`` + ``message``；**任何情况下都不补 0 占位**。
    """
    t0 = time.perf_counter()
    c = (code or "").strip().lower()
    if not c or len(c) < 6:
        return {"ok": False, "data": None, "message": "股票代码格式应为 sh600519 / sz000858"}

    eff_limit = INDICATOR_LIMIT_DEFAULT if limit is None else int(limit)
    per = _norm_period(period)
    raw_names = _split_names(indicator if (indicator and not names) else names)
    names_resolved = _expand_names(raw_names)
    params_key = (params or "").strip()

    # 缓存键：names 用解析后的注册名（别名与真名共享缓存），params 原样参与
    cache_key = (
        f"ind:{c}:{per}:{','.join(names_resolved)}:{params_key}:{eff_limit}"
    )
    if fresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("data") is not None:
        payload = copy.deepcopy(cached["data"])
        payload.setdefault("meta", {})["cache_hit"] = True
        payload["meta"]["kline_ms"] = 0.0
        payload["meta"]["compute_ms"] = 0.0
        payload["meta"]["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return payload

    t_kline = time.perf_counter()
    klines = await _load_indicator_klines(c, per, eff_limit)
    kline_ms = round((time.perf_counter() - t_kline) * 1000.0, 2)
    if klines is None:
        payload = {
            "ok": False,
            "message": "K 线数据不可得（腾讯/东财/westock 均失败），无法计算指标",
            "source": None,
            "as_of": None,
            "age": None,
            "degraded": True,
            "reason": "上游 K 线全部不可用；未返回空数组冒充成功",
            "data": {
                "code": c,
                "period": per,
                "bars": 0,
                "limit": eff_limit,
                "names": names_resolved,
                "series": {},
                "failed": names_resolved,
                "params_ignored": [],
            },
            "meta": {
                "cache_hit": False,
                "kline_ms": kline_ms,
                "compute_ms": 0.0,
                "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "source_chain": "tencent->eastmoney->westock",
            },
        }
        return payload

    t_comp = time.perf_counter()
    payload = _indicator_payload(c, per, eff_limit, names_resolved, params, klines)
    compute_ms = round((time.perf_counter() - t_comp) * 1000.0, 2)
    payload["data"]["name"] = find_stock_name(c)
    payload["meta"] = {
        "cache_hit": False,
        "kline_ms": kline_ms,
        "compute_ms": compute_ms,
        "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        "ttl": CACHE_TTL_INDICATORS,
        "source_chain": "raw_kline cache / tencent->eastmoney->westock",
        "overlay_names": [
            k for k, v in payload["data"]["series"].items() if v.get("overlay")
        ],
    }
    if not payload["data"]["failed"]:
        # 只有全部成功才缓存：失败态缓存会把「根数不够」固化 5 分钟，用户改不了
        cache.set(cache_key, {"data": copy.deepcopy(payload), "ts": time.time()},
                  CACHE_TTL_INDICATORS)
    return payload


@router.get("/kline/indicators/catalog")
async def get_indicators_catalog():
    """指标目录：名字、overlay、panes、参数 schema（含 type/min/max/default/unit/desc）。

    前端副图下拉与参数弹层**只读这个接口**，不在 JS 里硬编码 12 份参数。
    """
    items = indicators_mod.list_registry()
    return {
        "ok": True,
        "data": {
            "count": len(items),
            "overlay_count": sum(1 for i in items if i["overlay"]),
            "indicators": items,
        },
        "message": "ok",
    }
