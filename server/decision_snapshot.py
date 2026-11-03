"""决策闭环 · 共享市场快照与情绪 v1。

设计目标：
1. 一次决策周期只冻结一份 MarketPacket，调查 / 情绪 / 专家 / 决策台共享
   ``trace_id / as_of / coverage / quality``；
2. 情绪分 sentiment.v1 按六维加权，缺失维度重归一，不补 0 / 50；
3. 历史序列append-only，为量能分位、历史回放和后续校准提供事实数据。

本模块只读取现有真实数据链路，不新增不可达的行情源。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from server.core.numbers import to_float as _finite
from server.core.timeutil import now_iso as _now_iso
from server.core.numbers import clamp as _clamp

logger = logging.getLogger(__name__)

PACKET_CACHE_KEY = "decision_market_packet:v1"
SENTIMENT_CACHE_KEY = "decision_sentiment:v1"
PACKET_TTL = 55
SENTIMENT_TTL = 45
SENTIMENT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "sentiment_history.json",
)
SENTIMENT_HISTORY_KEEP = 2000
SENTIMENT_SAMPLE_SEC = 240

_history_lock = asyncio.Lock()


def _trace_id(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "mk_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _quality(dash: Dict[str, Any]) -> Dict[str, Any]:
    dq = dash.get("data_quality") or {}
    coverage = dash.get("coverage") or {}
    coverage_pct = _finite(coverage.get("ratio")) or 0.0
    level = str(dq.get("level") or ("good" if coverage_pct >= 95 else "partial" if coverage_pct >= 50 else "poor"))
    critical: List[str] = []
    indices = dash.get("indices") or []
    valid_indices = [
        item for item in indices
        if _finite(item.get("price")) is not None and (_finite(item.get("price")) or 0) > 0
    ]
    breadth = dash.get("breadth") or {}
    if len(valid_indices) < 2:
        critical.append("有效指数少于 2 个")
    if _finite(breadth.get("up")) is None or _finite(breadth.get("down")) is None:
        critical.append("涨跌家数缺失")
    if dash.get("stale"):
        critical.append("当前为历史快照")
    stale = bool(dash.get("stale"))
    level_factor = 1.0 if level == "good" else 0.85 if level == "partial" else 0.60
    if stale:
        level_factor *= 0.70
    score = round((coverage_pct / 100.0) * level_factor, 4)
    return {
        "level": level,
        "coverage_pct": round(coverage_pct, 1),
        "score": score,
        "stale": stale,
        "judgment_available": dq.get("judgment_available"),
        "critical_missing": critical,
        "message": dq.get("message") or "",
        "actionable": coverage_pct >= 70 and not stale and len(critical) == 0,
    }


async def _fetch_dashboard() -> Optional[Dict[str, Any]]:
    try:
        from server.cache import cache
        from server.services.market_dashboard import build_dashboard as _build_dashboard, CACHE_KEY, CACHE_TTL

        return await cache.get_or_fetch(CACHE_KEY, _build_dashboard, CACHE_TTL)
    except Exception as exc:
        logger.warning("decision snapshot: dashboard unavailable: %s", exc)
        return None


async def _fetch_north() -> Dict[str, Any]:
    try:
        from server.ak_market import ak_hub

        result = await ak_hub.get_north_flow()
        return result if isinstance(result, dict) else {"ok": False, "reason": "invalid_response"}
    except Exception as exc:
        return {"ok": False, "reason": f"north_unavailable: {exc}"}


async def _fetch_linkage() -> Dict[str, Any]:
    try:
        from server.global_linkage import get_linkage_with_scorecard

        result = await get_linkage_with_scorecard()
        return result if isinstance(result, dict) else {"ok": False, "reason": "invalid_response"}
    except Exception as exc:
        return {"ok": False, "reason": f"linkage_unavailable: {exc}"}


async def _fetch_intel() -> Optional[Dict[str, Any]]:
    try:
        from server.infra.kv import kv_get

        latest = await kv_get("brain_intel", None)
        return latest if isinstance(latest, dict) else None
    except Exception:
        return None


#: 闭环期间的「快照钉住」：一次决策只能依据同一份事实。
#: 结构：{"packet": dict|None, "until": float, "trace": str|None, "owner": str}
_PIN: Dict[str, Any] = {"packet": None, "until": 0.0, "trace": None, "owner": ""}


def pin_packet(packet: Dict[str, Any], ttl: float = 300.0, owner: str = "cycle") -> None:
    """钉住一份快照：在 ttl 内，`get_market_packet()` 一律返回它（含 force）。

    为什么需要：packet 常规 TTL 55s，而上游慢时单是构建就要 15–26s，一轮闭环很容易
    跨过 TTL 边界；下游任何一次 `force` 都会重建出新一代快照，于是同一条决策链上
    挂着两三个 trace_id。钉住之后，「同一次决策用同一份事实」才是可保证的。
    """
    if not isinstance(packet, dict) or not packet.get("ok"):
        return
    _PIN.update({
        "packet": packet,
        "until": time.time() + max(1.0, float(ttl)),
        "trace": packet.get("trace_id"),
        "owner": owner,
    })


def unpin_packet(owner: str = "") -> None:
    """解钉。`owner` 为空表示强制清空；给了 owner 则只解自己钉的那一份。"""
    if owner and _PIN.get("owner") and _PIN.get("owner") != owner:
        return
    _PIN.update({"packet": None, "until": 0.0, "trace": None, "owner": ""})


def pinned_packet() -> Optional[Dict[str, Any]]:
    if _PIN["packet"] is not None and time.time() < float(_PIN["until"] or 0):
        return _PIN["packet"]  # type: ignore[return-value]
    return None


async def get_market_packet(force: bool = False) -> Dict[str, Any]:
    """冻结一份跨模块共享的市场事实包。

    缺失/异常显式返回 ok:false 或 quality 降级，不生成随机、0 或中性填充。
    闭环钉住期间（`pin_packet`）忽略 force，保证整轮决策读的是同一份事实。
    """
    from server.cache import cache

    pinned = pinned_packet()
    if pinned is not None:
        return pinned

    if not force:
        cached = cache.get(PACKET_CACHE_KEY)
        if cached is not None:
            return cached

    _build_started = time.time()
    dash, north, linkage, intel = await asyncio.gather(
        _fetch_dashboard(), _fetch_north(), _fetch_linkage(), _fetch_intel(),
        return_exceptions=True,
    )
    if isinstance(dash, Exception):
        dash = None
    if isinstance(north, Exception):
        north = {"ok": False, "reason": str(north)}
    if isinstance(linkage, Exception):
        linkage = {"ok": False, "reason": str(linkage)}
    if isinstance(intel, Exception):
        intel = None

    if not isinstance(dash, dict) or not (dash.get("indices") or dash.get("breadth")):
        return {
            "ok": False,
            "version": "market-packet.v1",
            "trace_id": "mk_unavailable_" + str(int(time.time())),
            "as_of": None,
            "generated_at": _now_iso(),
            "message": "大盘快照不可用，禁止生成可执行结论",
        }

    quality = _quality(dash)
    scorecard = (linkage or {}).get("scorecard") if isinstance(linkage, dict) else None

    # 后台覆盖快照足够新时，以全 A 股广度替代核心样本；两次数据时间分别留痕。
    full_market = None
    full_breadth = dash.get("breadth") or {}
    full_turnover = dash.get("turnover") or {}
    try:
        from server.market_coverage import get_cached_coverage

        coverage = get_cached_coverage(max_age=600)
        if coverage and coverage.get("ok"):
            ashare = coverage.get("ashare") or {}
            quotes = ashare.get("quotes") or {}
            if (quotes.get("valid") or 0) >= 3000:
                full_breadth = dict(quotes)
                full_turnover = dict(full_turnover)
                if quotes.get("amount") is not None:
                    full_turnover["amount"] = quotes.get("amount")
                full_market = {
                    "as_of": coverage.get("as_of"),
                    "generated_at": coverage.get("generated_at"),
                    "scope": ashare.get("scope"),
                    "total_universe": ashare.get("total_universe"),
                    "quality": ashare.get("quality"),
                    "fundamentals": ashare.get("fundamentals"),
                    "industries": ashare.get("industries") or [],
                    "source": "market-coverage.v1",
                }
                # 顶层质量改为“全市场报价覆盖”，核心样本覆盖另存，避免 447 只样本
                # 99.8% 被误读为全 A 股覆盖 99.8%。
                full_q = ashare.get("quality") or {}
                quality["core_coverage_pct"] = quality.get("coverage_pct")
                quality["coverage_pct"] = full_q.get("quote_coverage", quality.get("coverage_pct"))
                quality["level"] = full_q.get("level", quality.get("level"))
                quality["full_market"] = True
                quality["full_market_scope"] = ashare.get("scope")
                quality["financial_coverage_pct"] = full_q.get("financial_coverage")
                quality["actionable"] = bool(full_q.get("actionable")) and not dash.get("stale")
                level_factor2 = 1.0 if quality["level"] == "good" else 0.85 if quality["level"] == "partial" else 0.60
                quality["score"] = round((float(quality.get("coverage_pct") or 0) / 100.0) * level_factor2, 4)
    except Exception as exc:
        logger.warning("decision snapshot: full coverage unavailable: %s", exc)

    # 量能维回填：沪/深综指日成交量（真实日 K），供 20 日分位使用。
    try:
        from server.market_coverage import get_volume_backfill

        volume_backfill = await get_volume_backfill(days=25)
        if volume_backfill.get("ok"):
            full_turnover = dict(full_turnover or {})
            full_turnover["volume_backfill"] = volume_backfill
    except Exception as exc:
        logger.warning("decision snapshot: volume backfill unavailable: %s", exc)

    # 派生分析层：横截面扫描 / 板块轮动 / 资金流向（market-scanner.v1）。
    scan: Optional[Dict[str, Any]] = None
    try:
        from server.market_scanner import get_market_scan

        scan = await get_market_scan()
    except Exception as exc:
        logger.warning("decision snapshot: market scan unavailable: %s", exc)

    # 链路时延：数据龄 + 构建耗时，供前端新鲜度指示与流通性监控。
    coverage_age_s: Optional[float] = None
    coverage_generated = (full_market or {}).get("generated_at") if isinstance(full_market, dict) else None
    packet_core = {
        "as_of": dash.get("as_of"),
        "coverage": dash.get("coverage"),
        "quality": quality,
        "indices": dash.get("indices"),
        "breadth": full_breadth,
        "turnover": full_turnover,
        "capital_flow": dash.get("capital_flow"),
        "sectors": dash.get("sectors"),
        "judgment": dash.get("judgment"),
        "risk_tags": dash.get("risk_tags"),
        "source": dash.get("source"),
        "global_composite": (scorecard or {}).get("composite_score"),
        "intel_risk_score": (intel or {}).get("risk_score"),
        "intel_risk_level": (intel or {}).get("risk_level"),
    }
    trace = _trace_id(packet_core)

    evidence = [
        {
            "metric": "sample_coverage",
            "value": quality["coverage_pct"],
            "unit": "%",
            "source": dash.get("source") or "market-dashboard",
            "as_of": dash.get("as_of"),
        },
        {
            "metric": "valid_indices",
            "value": len([
                item for item in (dash.get("indices") or [])
                if (_finite(item.get("price")) or 0) > 0
            ]),
            "unit": "个",
            "source": dash.get("source") or "market-dashboard",
            "as_of": dash.get("as_of"),
        },
    ]
    if full_market:
        evidence.append({
            "metric": "full_market_quote_coverage",
            "value": (full_market.get("quality") or {}).get("quote_coverage"),
            "unit": "%",
            "source": "market-coverage.v1",
            "as_of": full_market.get("as_of"),
            "sample": full_market.get("total_universe"),
        })

    packet = {
        "ok": True,
        "version": "market-packet.v1",
        "trace_id": trace,
        "as_of": dash.get("as_of"),
        "generated_at": _now_iso(),
        "scope": (full_market or {}).get("scope") or dash.get("scope"),
        "quality": quality,
        "source": "market-coverage.v1+sina+tencent" if full_market else dash.get("source"),
        "inputs_as_of": {
            "core_market": dash.get("as_of"),
            "full_market": (full_market or {}).get("as_of"),
            "global": (linkage or {}).get("as_of_date"),
            "intel": (intel or {}).get("ts"),
        },
        "market": {
            "indices": dash.get("indices") or [],
            "breadth": full_breadth,
            "turnover": full_turnover,
            "capital_flow": dash.get("capital_flow") or {},
            "sectors": dash.get("sectors") or [],
            "judgment": dash.get("judgment") or {},
            "risk_tags": dash.get("risk_tags") or [],
            "stale": bool(dash.get("stale")),
            "full_market": full_market,
        },
        "north": north,
        "global": {
            "ok": bool((linkage or {}).get("ok")),
            "as_of_date": (linkage or {}).get("as_of_date"),
            "scorecard": scorecard,
            "reason": None if (linkage or {}).get("ok") else (linkage or {}).get("reason"),
        },
        "intel": intel,
        "evidence": evidence,
        "scan": scan,
        "chain": {
            "build_ms": round((time.time() - _build_started) * 1000, 1),
            "coverage_age_s": (
                round(time.time() - datetime.fromisoformat(str(coverage_generated)).timestamp(), 1)
                if coverage_generated else None
            ),
            "scan_mode": ((scan or {}).get("money_flow") or {}).get("mode"),
            "scan_history_days": ((scan or {}).get("rotation") or {}).get("history_days"),
        },
    }
    # 全市场未就绪时不长缓存 packet：否则重启后 55 秒内所有结论都锁死在核心样本上，
    # 而全市场缓存通常 20 秒内就好。降级态只做 5 秒短缓存，就绪后立刻切回全市场。
    cache.set(PACKET_CACHE_KEY, packet, PACKET_TTL if full_market else 5)
    # 事件触发：快照重建后发布到总线，级联失效 sentiment / scan（闸门 40s）
    try:
        from server.data_bus import bus

        # 记账交给 publish（last_refresh / refresh_count / last_ms / last_trace）
        await bus.publish(
            "packet", packet, trigger="request",
            elapsed_ms=(packet.get("chain") or {}).get("build_ms"),
            trace=trace,
        )
    except Exception as exc:
        logger.debug("data bus publish(packet) skipped: %s", exc)
    return packet


# ---------------------------------------------------------------------------
# 情绪 v1
# ---------------------------------------------------------------------------

def _status_of(score: Optional[float]) -> str:
    if score is None:
        return "数据不足"
    if score >= 80:
        return "过热"
    if score >= 60:
        return "偏热"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏冷"
    return "极冷"


def _load_history() -> Dict[str, Any]:
    empty = {"version": "sentiment.v1", "series": [], "daily": {}}
    try:
        if not os.path.exists(SENTIMENT_HISTORY_PATH):
            return empty
        with open(SENTIMENT_HISTORY_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        if not isinstance(raw, dict):
            return empty
        raw.setdefault("version", "sentiment.v1")
        raw.setdefault("series", [])
        raw.setdefault("daily", {})
        if not isinstance(raw["series"], list):
            raw["series"] = []
        if not isinstance(raw["daily"], dict):
            raw["daily"] = {}
        return raw
    except Exception as exc:
        logger.warning("sentiment history read failed: %s", exc)
        return empty


def _write_history(history: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(SENTIMENT_HISTORY_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".sentiment-", suffix=".tmp", dir=os.path.dirname(SENTIMENT_HISTORY_PATH))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(history, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, SENTIMENT_HISTORY_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as exc:
        logger.warning("sentiment history write failed: %s", exc)


async def _persist_sentiment(snapshot: Dict[str, Any]) -> None:
    """追加情绪快照；同一 trace 去重，最多 4 分钟一条，日成交额按日保留。"""
    async with _history_lock:
        history = _load_history()
        series = history.get("series") or []
        daily = history.get("daily") or {}
        today = datetime.now().strftime("%Y-%m-%d")
        amount = _finite(snapshot.get("inputs", {}).get("amount"))
        if amount is not None:
            daily[today] = max(_finite(daily.get(today)) or 0, amount)
        last = series[-1] if series else None
        should_append = False
        if not last:
            should_append = True
        else:
            try:
                last_ts = datetime.fromisoformat(str(last.get("ts"))).timestamp()
                gap = time.time() - last_ts
            except Exception:
                gap = SENTIMENT_SAMPLE_SEC + 1
            should_append = (
                last.get("trace_id") != snapshot.get("trace_id")
                and gap >= SENTIMENT_SAMPLE_SEC
            )
        if should_append:
            series.append({
                "ts": snapshot.get("as_of") or _now_iso(),
                "trace_id": snapshot.get("trace_id"),
                "score": snapshot.get("score"),
                "status": snapshot.get("status"),
                "coverage": snapshot.get("coverage"),
                "confidence": snapshot.get("confidence"),
                "dimensions": {
                    key: item.get("value")
                    for key, item in (snapshot.get("dimensions") or {}).items()
                },
            })
            history["series"] = series[-SENTIMENT_HISTORY_KEEP:]
        history["daily"] = dict(sorted(daily.items())[-400:])
        _write_history(history)
        snapshot["history_n"] = len(history.get("series") or [])
        snapshot["daily_n"] = len(history.get("daily") or {})


def _percentile_rank(value: float, samples: List[float]) -> Optional[float]:
    if not samples:
        return None
    below = sum(1 for item in samples if item <= value)
    return round(below / len(samples) * 100, 1)


# A 股日内成交量典型 U 型分布（每 30 分钟区间占全天比重，合计 1.0）。
# 仅用于把盘中成交量折算成“全天预估”，属模型折算而非实测，输出必须带 estimated 标记。
_INTRADAY_VOLUME_CURVE = (
    (570, 600, 0.18),   # 09:30-10:00
    (600, 630, 0.14),   # 10:00-10:30
    (630, 660, 0.11),   # 10:30-11:00
    (660, 690, 0.09),   # 11:00-11:30
    (780, 810, 0.09),   # 13:00-13:30
    (810, 840, 0.09),   # 13:30-14:00
    (840, 870, 0.11),   # 14:00-14:30
    (870, 900, 0.19),   # 14:30-15:00
)


def _session_volume_progress(now: Optional[datetime] = None) -> float:
    """已完成的日内成交量占比 (0~1)。收盘后返回 1.0。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return 1.0
    minute = now.hour * 60 + now.minute
    if minute < 570:
        return 0.0
    if minute >= 900:
        return 1.0
    done = 0.0
    for start, end, weight in _INTRADAY_VOLUME_CURVE:
        if minute >= end:
            done += weight
        elif minute > start:
            done += weight * (minute - start) / (end - start)
        else:
            break
    return round(min(done, 1.0), 4)


def build_sentiment_snapshot(packet: Dict[str, Any], history: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """纯函数：由 MarketPacket 计算 sentiment.v1，便于独立复算与测试。"""
    market = packet.get("market") or {}
    breadth = market.get("breadth") or {}
    indices = market.get("indices") or []
    turnover = market.get("turnover") or {}
    global_data = packet.get("global") or {}
    intel = packet.get("intel") or {}
    quality = packet.get("quality") or {}
    hist = history if history is not None else _load_history()

    up = _finite(breadth.get("up"))
    down = _finite(breadth.get("down"))
    limit_up = _finite(breadth.get("limit_up"))
    limit_down = _finite(breadth.get("limit_down"))
    amount = _finite(turnover.get("amount"))
    avg_turnover = _finite(turnover.get("avg_turnover"))

    valid_changes = [
        _finite(item.get("chg")) for item in indices
        if _finite(item.get("price")) is not None and (_finite(item.get("price")) or 0) > 0
    ]
    valid_changes = [item for item in valid_changes if item is not None]
    avg_chg = sum(valid_changes) / len(valid_changes) if valid_changes else None
    composite = _finite((global_data.get("scorecard") or {}).get("composite_score"))
    risk_score = _finite(intel.get("risk_score"))

    dimensions: Dict[str, Dict[str, Any]] = {}

    def add(key: str, label: str, value: Optional[float], weight: float, method: str, note: str = "") -> None:
        dimensions[key] = {
            "key": key, "label": label,
            "value": None if value is None else round(_clamp(value), 1),
            "weight": weight, "available": value is not None,
            "method": method, "note": note,
        }

    breadth_value = None
    if up is not None and down is not None and (up + down) > 0:
        breadth_value = 100.0 * up / (up + down)
        note = f"上涨 {int(up)} / 下跌 {int(down)}"
    else:
        note = "涨跌家数缺失"
    add("breadth", "宽度", breadth_value, 0.30, "100×up/(up+down)", note)

    momentum_value = None
    momentum_source = None
    if avg_chg is not None:
        momentum_value = 50.0 + 25.0 * avg_chg
        momentum_source = "index_avg"
        note = f"{len(valid_changes)} 个有效指数平均 {avg_chg:+.2f}%；MA20 尚未接入"
    else:
        # 指数行情偶发缺 chg 时，用全市场个股涨跌中位数兜底：
        # 否则单维缺失会把覆盖率压到 0.65（< 0.70），整体情绪分直接消失。
        median_chg = _finite(breadth.get("median_chg"))
        if median_chg is not None:
            momentum_value = 50.0 + 25.0 * median_chg
            momentum_source = "breadth_median"
            note = f"指数涨跌幅缺失，改用全市场个股涨跌中位数 {median_chg:+.2f}%（口径不同，仅作替代）"
        else:
            note = "有效指数不足，且无全市场涨跌中位数可替代"
    add("momentum", "动量", momentum_value, 0.20,
        "50+25×avg_chg（缺指数时用全市场涨跌中位数）", note)

    limit_value = None
    if limit_up is not None and limit_down is not None:
        limit_value = 50.0 + 40.0 * (limit_up - limit_down) / (limit_up + limit_down + 5.0)
        note = f"涨停 {int(limit_up)} / 跌停 {int(limit_down)}"
    else:
        note = "涨跌停家数缺失"
    add("limit", "涨跌停", limit_value, 0.15, "50+40×(lu-ld)/(lu+ld+5)", note)

    volume_value = None
    volume_estimated = False
    volume_inputs: Dict[str, Any] = {}
    daily = hist.get("daily") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    prior_amounts = [
        _finite(value) for key, value in daily.items()
        if key != today and _finite(value) is not None and (_finite(value) or 0) > 0
    ]
    prior_amounts = sorted([float(x) for x in prior_amounts if x is not None])
    volume_method = "20日成交额百分位"
    if amount is not None and len(prior_amounts) >= 20:
        volume_value = _percentile_rank(amount, prior_amounts[-20:])
        note = f"相对前 20 个交易日成交额分位 {volume_value:.1f}%"
        volume_inputs = {"amount": amount, "window": 20, "basis": "amount"}
    else:
        # 成交额样本不足 20 日时，用沪/深综指日成交量回填（真实日 K，非推算）。
        backfill = turnover.get("volume_backfill") or {}
        rows = [r for r in (backfill.get("series") or []) if (_finite(r.get("vol")) or 0) > 0]
        today_vol = None
        prior_vols: List[float] = []
        if rows:
            if rows[-1].get("date") == today:
                today_vol = float(rows[-1]["vol"])
                prior_vols = [float(r["vol"]) for r in rows[:-1]][-20:]
            else:
                prior_vols = [float(r["vol"]) for r in rows][-20:]
        progress = _session_volume_progress()
        if today_vol is not None and len(prior_vols) >= 20 and progress > 0.05:
            projected = today_vol if progress >= 0.98 else today_vol / progress
            volume_estimated = progress < 0.98
            volume_value = _percentile_rank(projected, prior_vols)
            volume_method = "20日成交量分位（U 型时段折算全天预估）"
            if volume_estimated:
                note = (
                    f"盘中成交量 {today_vol / 1e8:.2f} 亿手，按时段折算全天约 "
                    f"{projected / 1e8:.2f} 亿手，分位 {volume_value:.1f}%（预估，非实测）"
                )
            else:
                note = f"成交量 {today_vol / 1e8:.2f} 亿手，分位 {volume_value:.1f}%"
            volume_inputs = {
                "volume_today": today_vol,
                "volume_projected": round(projected, 0),
                "session_progress": progress,
                "window": len(prior_vols),
                "basis": "volume",
                "unit": "lot",
                "source": backfill.get("source"),
            }
        else:
            note = f"历史样本 成交额 {len(prior_amounts)}/20、成交量 {len(prior_vols)}/20，暂不计算"
            volume_inputs = {"amount_samples": len(prior_amounts), "volume_samples": len(prior_vols)}
    add("volume", "量能", volume_value, 0.15, volume_method, note)
    dimensions["volume"]["estimated"] = volume_estimated
    dimensions["volume"]["inputs"] = volume_inputs

    global_value = None
    if composite is not None:
        global_value = 50.0 + 12.0 * composite
        note = f"全球联动 composite {composite:+.3f}"
    else:
        note = "全球联动不可用"
    add("global", "全球", global_value, 0.10, "50+12×composite", note)

    event_value = None
    if risk_score is not None:
        # v1 只使用情报风险分；新闻正负面分类未上线，标记 heuristic，不冒充实测。
        event_value = 50.0 - 0.5 * risk_score
        note = f"情报风险分 {risk_score:.0f}（仅情报代理，非新闻情绪实测）"
    else:
        note = "情报风险评估缺失"
    add("event", "事件", event_value, 0.10, "50-0.5×intel_risk", note)

    available = [item for item in dimensions.values() if item["available"]]
    coverage = round(sum(item["weight"] for item in available), 4)
    weight_sum = sum(item["weight"] for item in available)
    score = None
    if weight_sum > 0:
        score = round(sum((item["value"] or 0) * item["weight"] for item in available) / weight_sum, 1)
    if coverage < 0.70:
        score = None

    quality_level = str(quality.get("level") or "poor")
    quality_factor = 1.0 if quality_level == "good" else 0.85 if quality_level == "partial" else 0.60
    stale_factor = 0.70 if market.get("stale") else 1.0
    confidence = round(coverage * quality_factor * stale_factor, 2)
    confidence_level = "高" if confidence >= 0.85 else "中" if confidence >= 0.70 else "低"

    flags: List[Dict[str, str]] = []
    if avg_chg is not None and up is not None and down is not None:
        active = up + down
        if avg_chg > 0 and active > 0 and up / active < 0.40:
            flags.append({"code": "price_breadth_divergence", "level": "high", "text": "指数上涨但上涨家数占比 < 40%，出现价格与宽度背离"})
        if avg_chg < 0 and active > 0 and up / active > 0.60:
            flags.append({"code": "price_breadth_divergence_bear", "level": "medium", "text": "指数下跌但上涨家数占比 > 60%，存在结构性抵抗"})
    if limit_up is not None and limit_down is not None and limit_up >= 20 and limit_down >= 20:
        lo, hi = min(limit_up, limit_down), max(limit_up, limit_down)
        balanced = hi > 0 and lo / hi >= 0.5
        flags.append({
            "code": "limit_extreme_split",
            "level": "high" if balanced else "medium",
            "text": (
                f"涨停 {int(limit_up)} / 跌停 {int(limit_down)}，两端同时高企且接近均衡，结构撕裂"
                if balanced else
                f"涨停 {int(limit_up)} / 跌停 {int(limit_down)}，两端同时活跃但一侧主导，分化加剧"
            ),
        })
    if risk_score is not None and risk_score >= 60:
        flags.append({"code": "event_risk", "level": "high" if risk_score >= 75 else "medium", "text": f"情报风险 {risk_score:.0f} 分，情绪分可信度与仓位上限应下调"})
    sectors = market.get("sectors") or []
    amounts = sorted([_finite(item.get("amount")) or 0 for item in sectors], reverse=True)
    total_amount = sum(amounts)
    if total_amount > 0 and len(amounts) >= 3 and sum(amounts[:3]) / total_amount >= 0.60:
        flags.append({"code": "sector_concentration", "level": "medium", "text": "前 3 板块成交额占比 ≥ 60%，主线偏拥挤"})

    # 数据口径必须自描述：全市场缓存未就绪时会回退核心样本，
    # 若不标注，消费方无法区分「430 只样本的 48%」与「全市场 48%」。
    full = market.get("full_market") or {}
    valid_count = _finite(breadth.get("valid"))
    if not valid_count:
        valid_count = _finite(breadth.get("total"))
    if not valid_count and up is not None and down is not None:
        valid_count = up + down + (_finite(breadth.get("flat")) or 0.0)
    valid_count = valid_count or 0.0
    if full and valid_count >= 1000:
        data_scope = "full_a_share"
        scope_label = (
            f"全 A 股：有效报价 {int(valid_count)} / 全库 "
            f"{int(_finite(full.get('total_universe')) or _finite(breadth.get('total')) or 0)}"
        )
    else:
        data_scope = "core_sample"
        scope_label = f"核心样本 {int(valid_count)} 只（非全市场口径）"
        flags.append({
            "code": "scope_degraded",
            "level": "medium",
            "text": "宽度与涨跌停基于核心样本而非全市场（全市场缓存未就绪），情绪分代表性下降",
        })

    status = _status_of(score)
    return {
        "ok": score is not None,
        "version": "sentiment.v1",
        "trace_id": packet.get("trace_id"),
        "as_of": packet.get("as_of"),
        "generated_at": _now_iso(),
        "score": score,
        "status": status,
        "confidence": confidence,
        "confidence_level": confidence_level,
        "coverage": coverage,
        "quality": {"level": quality_level, "stale": bool(market.get("stale"))},
        "data_scope": data_scope,
        "scope_label": scope_label,
        "dimensions": dimensions,
        "risk_flags": flags,
        "inputs": {
            "up": up, "down": down, "flat": _finite(breadth.get("flat")),
            "limit_up": limit_up, "limit_down": limit_down,
            "amount": amount, "avg_turnover": avg_turnover,
            "avg_index_chg": round(avg_chg, 4) if avg_chg is not None else None,
            "median_chg": _finite(breadth.get("median_chg")),
            "momentum_source": momentum_source,
            "global_composite": composite, "intel_risk_score": risk_score,
        },
        "formula": "S = 0.30B + 0.20M + 0.15L + 0.15V + 0.10G + 0.10E；缺失维度按可用权重归一",
        "history_n": len(hist.get("series") or []),
        "daily_n": len(daily),
        "message": None if score is not None else "有效维度覆盖不足 70%，不输出情绪总分",
    }


async def get_sentiment_snapshot(force: bool = False) -> Dict[str, Any]:
    from server.cache import cache

    packet = await get_market_packet(force=force)
    if not packet.get("ok"):
        return {
            "ok": False,
            "version": "sentiment.v1",
            "trace_id": packet.get("trace_id"),
            "as_of": packet.get("as_of"),
            "generated_at": _now_iso(),
            "score": None,
            "status": "数据不足",
            "message": packet.get("message") or "市场快照不可用",
        }
    cached = cache.get(SENTIMENT_CACHE_KEY)
    if not force and cached and cached.get("trace_id") == packet.get("trace_id"):
        return cached
    snapshot = build_sentiment_snapshot(packet)
    try:
        await _persist_sentiment(snapshot)
    except Exception as exc:
        logger.warning("sentiment persist failed: %s", exc)
    cache.set(SENTIMENT_CACHE_KEY, snapshot, SENTIMENT_TTL)
    try:
        from server.data_bus import bus

        await bus.publish("sentiment", snapshot, trigger="request")
    except Exception as exc:
        logger.debug("data bus publish(sentiment) skipped: %s", exc)
    return snapshot


def get_sentiment_history(limit: int = 120) -> Dict[str, Any]:
    history = _load_history()
    series = history.get("series") or []
    count = max(1, min(int(limit or 120), 1000))
    return {
        "ok": True,
        "version": "sentiment.v1",
        "count": len(series),
        "daily_count": len(history.get("daily") or {}),
        "data": series[-count:],
    }
