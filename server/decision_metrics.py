#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决策指标统一层（decision-metrics.v1）。

把「趋势 / 资金流 / 买手率 / 情绪」四类信号收敛成**同一套字段**，供分流规则直接消费：

    {id, label, market, kind, value, unit, score, confidence, source, fields,
     as_of, age_s, ttl_s, stale, coverage, fallback, note}

设计要点
--------
1. **score 统一到 [-1, 1]**：原始值各带量纲（百分点、元、家数），分流规则只认 score，
   阈值才能跨指标比较；原始 `value` 仍保留用于展示与审计。
2. **confidence 独立于 score**：样本覆盖低、口径未核实、数据陈旧 → confidence 降，
   分流层按 weight×confidence 加权并重新归一，缺一项不会把总分拉向 0。
3. **缺失就是缺失**：取不到给 `available=false` + `reason`，不填 0、不给中性分。
4. **买手率是多品种的**：腾讯 `外盘/内盘` 逐笔口径，A 股按成交额取样、ETF 取固定篮子、
   指数无此口径（明确标 unavailable，不拿成交量冒充）。

数据来源与时效（与 data/decision_routing.json 的 adapters 对齐）：
    趋势   dashboard.index_trends（5 分钟线斜率）+ packet.market.indices
    广度   packet.market.breadth（全市场 5668 只）
    资金   dashboard.capital_flow（上游字段未核实，confidence 上限 0.55）+ packet.north（常不披露）
    买手率 腾讯 qt.gtimg.cn（外盘 f7 / 内盘 f8），取样 120 只 + ETF 篮子
    情绪   sentiment.v1
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from server.market_coverage_fetch import _fetch_url, _num

logger = logging.getLogger(__name__)

METRICS_VERSION = "decision-metrics.v1"
BUY_SAMPLE_SIZE = 120
ETF_BASKET = ("sh510300", "sh510500", "sh512100", "sh588000", "sz159915")
_TENCENT = "https://qt.gtimg.cn/q={codes}"
_LINE = re.compile(r'v_([a-z]{2}\d{6})="([^"]*)"')
_CHUNK = 60

#: 各指标的时点/时效（秒）；超过 ttl 记 stale，超过 2×ttl 记 unavailable
TTL = {
    "trend.index": 300, "trend.breadth": 120, "trend.limit": 120,
    "flow.main": 300, "flow.north": 1800,
    "buyratio.market": 180, "buyratio.etf": 180,
    "sentiment.score": 120,
}


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _tanh(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    import math

    return math.tanh(value / scale)


def _metric(metric_id: str, label: str, kind: str, source: str, *, market: str = "a_share",
            value: Any = None, unit: str = "", score: Optional[float] = None,
            confidence: float = 0.0, fields: Optional[List[str]] = None,
            as_of: Optional[str] = None, coverage: Optional[float] = None,
            fallback: Optional[str] = None, note: str = "",
            available: bool = True, reason: Optional[str] = None) -> Dict[str, Any]:
    ttl = TTL.get(metric_id, 300)
    age: Optional[float] = None
    stale = False
    if as_of:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(str(as_of), fmt)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone().replace(tzinfo=None)
                age = round(max(0.0, (datetime.now() - parsed).total_seconds()), 1)
                break
            except ValueError:
                continue
    if age is not None:
        stale = age > ttl
        if age > ttl * 2:
            available = False
            reason = reason or f"数据龄 {age:.0f}s 超过时效上限 {ttl * 2}s"
    return {
        "id": metric_id, "label": label, "market": market, "kind": kind,
        "available": bool(available), "reason": reason,
        "value": value, "unit": unit,
        "score": round(_clamp(score), 3) if (available and score is not None) else None,
        "confidence": round(max(0.0, min(1.0, confidence)), 2) if available else 0.0,
        "source": source, "fields": fields or [],
        "as_of": as_of, "age_s": age, "ttl_s": ttl, "stale": stale,
        "coverage": coverage, "fallback": fallback, "note": note,
    }


# ---------------------------------------------------------------------------
# 买手率：腾讯外盘/内盘（逐笔主动买/卖）
# ---------------------------------------------------------------------------
def _buy_ratio_row(fields: List[str]) -> Optional[Tuple[float, float, float]]:
    """返回 (买手率%, 外盘手, 内盘手)；无有效成交返回 None。"""
    if len(fields) < 38:
        return None
    outer, inner = _num(fields[7]), _num(fields[8])
    if outer is None or inner is None:
        return None
    total = outer + inner
    if total <= 0:
        return None
    return (outer / total * 100.0, outer, inner)


async def _fetch_buy(codes: List[str]) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}
    errors = 0
    sem = asyncio.Semaphore(4)

    async def chunk(batch: List[str]) -> None:
        nonlocal errors
        async with sem:
            try:
                raw = await asyncio.to_thread(_fetch_url, _TENCENT.format(codes=",".join(batch)), 10)
            except Exception as exc:
                logger.info("buy-ratio chunk failed: %s", exc)
                errors += 1
                return
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
        for code, body in _LINE.findall(text):
            parsed = _buy_ratio_row(body.split("~"))
            if parsed:
                rows[code] = {"ratio": round(parsed[0], 2), "outer": parsed[1], "inner": parsed[2]}

    batches = [codes[i:i + _CHUNK] for i in range(0, len(codes), _CHUNK)]
    await asyncio.gather(*[chunk(batch) for batch in batches])
    return {"rows": rows, "errors": errors, "chunks": len(batches)}


async def _sample_codes() -> List[Tuple[str, str]]:
    """取样：全市场成交额前 N + ETF 篮子。返回 [(code, label)]。"""
    picked: List[Tuple[str, str]] = []
    try:
        from server.market_coverage import _LAST_ROWS

        rows = [r for r in list(_LAST_ROWS) if r.get("amount") and r.get("code")]
        if not rows:
            # 进程刚启动时全市场行还没装载：主动触发一次（有 90s 缓存，不会打爆上游）
            from server.market_coverage_payload import get_market_coverage

            await get_market_coverage(force=False)
            rows = [r for r in list(_LAST_ROWS) if r.get("amount") and r.get("code")]
        rows.sort(key=lambda r: r.get("amount") or 0, reverse=True)
        for row in rows[:BUY_SAMPLE_SIZE]:
            picked.append((row["code"], row.get("name") or row["code"]))
    except Exception as exc:
        logger.info("buy-ratio sample unavailable: %s", exc)
    for code in ETF_BASKET:
        if not any(code == item[0] for item in picked):
            picked.append((code, code))
    return picked


async def _buy_metrics(force: bool = False) -> List[Dict[str, Any]]:
    sample = await _sample_codes()
    codes = [code for code, _ in sample]
    a_codes = [c for c in codes if c not in ETF_BASKET]
    etf_codes = [c for c in codes if c in ETF_BASKET]
    a_res, etf_res = await asyncio.gather(_fetch_buy(a_codes), _fetch_buy(etf_codes))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def aggregate(result: Dict[str, Any], universe: List[str]) -> Tuple[Optional[float], int]:
        rows = result["rows"]
        outer = sum(v["outer"] for v in rows.values())
        inner = sum(v["inner"] for v in rows.values())
        if outer + inner <= 0:
            return None, 0
        return outer / (outer + inner) * 100.0, len(rows)

    a_ratio, a_n = aggregate(a_res, a_codes)
    etf_ratio, etf_n = aggregate(etf_res, etf_codes)
    a_cov = round(a_n / len(a_codes), 2) if a_codes else 0.0
    etf_cov = round(etf_n / len(etf_codes), 2) if etf_codes else 0.0

    def build(metric_id: str, label: str, market: str, ratio: Optional[float], cov: float,
              n: int, total: int, result: Dict[str, Any]) -> Dict[str, Any]:
        if ratio is None:
            return _metric(metric_id, label, "buyratio", "tencent 外盘/内盘", market=market,
                           available=False, reason="腾讯外盘/内盘为空或全部停牌",
                           fields=["f7 外盘", "f8 内盘"], fallback="降低该维度权重，按其余指标分流")
        return _metric(
            metric_id, label, "buyratio", "tencent 外盘/内盘", market=market,
            value=round(ratio, 2), unit="%",
            score=_clamp((ratio - 50.0) / 15.0),          # 65% 以上视为强买盘
            confidence=0.45 + 0.45 * cov,                  # 覆盖越高越可信
            fields=["f7 外盘(手)", "f8 内盘(手)"],
            as_of=now, coverage=cov,
            fallback="腾讯不可达 → 该维度 unavailable，分流按其余指标加权",
            note=f"主动买手占比，取样 {n}/{total} 只；非 L2 逐笔全量",
        )

    return [
        build("buyratio.market", "全市场买手率（样本）", "a_share", a_ratio, a_cov,
              a_n, len(a_codes), a_res),
        build("buyratio.etf", "ETF 买手率（篮子）", "etf", etf_ratio, etf_cov,
              etf_n, len(etf_codes), etf_res),
    ]


# ---------------------------------------------------------------------------
# 趋势 / 资金 / 情绪
# ---------------------------------------------------------------------------
def _index_trend_metric(dashboard: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    trends = dashboard.get("index_trends") or []
    slopes: List[float] = []
    for item in trends[:4]:
        points = item.get("points") or []
        closes = [_num(p.get("close")) for p in points if _num(p.get("close")) is not None]
        if len(closes) >= 6:
            recent = closes[-6:]
            base = recent[0]
            if base:
                slopes.append((recent[-1] - base) / base * 100.0)
    if slopes:
        mean_slope = sum(slopes) / len(slopes)
        return _metric(
            "trend.index", "主要指数 30 分钟趋势", "trend", "market-dashboard.index_trends",
            value=round(mean_slope, 2), unit="%",
            score=_tanh(mean_slope, 0.6), confidence=0.75,
            fields=["index_trends[].points[].close"],
            as_of=dashboard.get("as_of"), coverage=round(len(slopes) / max(1, len(trends)), 2),
            fallback="5 分钟线不可用 → 退化为指数当日涨跌幅",
            note=f"{len(slopes)} 个指数近 6 根 5 分钟线",
        )
    indices = (packet.get("market") or {}).get("indices") or []
    chgs = [_num(i.get("chg")) for i in indices if _num(i.get("chg")) is not None]
    if chgs:
        mean_chg = sum(chgs) / len(chgs)
        return _metric(
            "trend.index", "主要指数当日涨跌（降级）", "trend", "market-packet.indices",
            value=round(mean_chg, 2), unit="%", score=_tanh(mean_chg, 1.2), confidence=0.5,
            fields=["market.indices[].chg"], as_of=packet.get("as_of"),
            coverage=round(len(chgs) / max(1, len(indices)), 2),
            fallback="无更细粒度数据", note="5 分钟线缺失，使用当日涨跌幅",
        )
    return _metric("trend.index", "主要指数趋势", "trend", "n/a", available=False,
                   reason="指数与分时均不可用", fallback="该维度不参与分流")


def _breadth_metrics(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    breadth = (packet.get("market") or {}).get("breadth") or {}
    up_ratio = _num(breadth.get("up_ratio"))
    limit_up, limit_down = _num(breadth.get("limit_up")), _num(breadth.get("limit_down"))
    total, valid = _num(breadth.get("total")), _num(breadth.get("valid"))
    coverage = round((valid or 0) / total, 3) if total else None
    as_of = packet.get("as_of")
    out: List[Dict[str, Any]] = []
    if up_ratio is None:
        out.append(_metric("trend.breadth", "全市场上涨占比", "trend", "market-packet.breadth",
                           available=False, reason="广度字段缺失", fallback="该维度不参与分流"))
    else:
        out.append(_metric(
            "trend.breadth", "全市场上涨占比", "trend", "market-packet.breadth",
            value=round(up_ratio, 1), unit="%", score=_clamp((up_ratio - 50.0) / 30.0),
            confidence=0.55 + 0.4 * (coverage or 0), fields=["breadth.up_ratio"],
            as_of=as_of, coverage=coverage,
            fallback="全市场快照缺失 → 退化为核心样本广度（口径不同，置信降半）",
            note=f"{int(valid or 0)}/{int(total or 0)} 只有报价",
        ))
    if limit_up is None or limit_down is None:
        out.append(_metric("trend.limit", "涨跌停强度", "trend", "market-packet.breadth",
                           available=False, reason="涨跌停家数缺失", fallback="该维度不参与分流"))
    else:
        strength = (limit_up - limit_down) / max(5.0, limit_up + limit_down + 5.0)
        out.append(_metric(
            "trend.limit", "涨跌停强度", "trend", "market-packet.breadth",
            value=round(limit_up - limit_down, 0), unit="家",
            score=_clamp(strength * 2.2), confidence=0.7,
            fields=["breadth.limit_up", "breadth.limit_down"], as_of=as_of, coverage=coverage,
            fallback="涨跌停不可用 → 该维度不参与分流",
            note=f"涨停 {int(limit_up)} / 跌停 {int(limit_down)}",
        ))
    return out


def _flow_metrics(dashboard: Dict[str, Any], packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    flow = dashboard.get("capital_flow") or {}
    components = [flow.get(k) for k in ("flow_a", "flow_b", "flow_c", "flow_d", "flow_e")]
    components = [c for c in components if isinstance(c, (int, float))]
    if flow.get("available") and components:
        # 口径未核实：只取 flow_a（多数行情软件里是主力净额），并把置信压到 0.55 以下
        main = _num(flow.get("flow_a"))
        if main is not None:
            out.append(_metric(
                "flow.main", "主力资金净额（口径待核实）", "flow", "market-dashboard.capital_flow",
                value=round(main / 1e8, 2), unit="亿元",
                score=_tanh(main / 1e8, 40.0), confidence=0.5,
                fields=["capital_flow.flow_a"], as_of=dashboard.get("as_of"), coverage=1.0,
                fallback="资金流接口不可用 → 该维度不参与分流",
                note="上游未标注主力/大单类别，仅作方向参考，权重已折半",
            ))
    else:
        out.append(_metric("flow.main", "主力资金净额", "flow", "market-dashboard.capital_flow",
                           available=False, reason="资金流接口不可用或字段为空",
                           fallback="该维度不参与分流"))
    north = packet.get("north") or {}
    data = north.get("data") or {}
    total = _num(data.get("total"))
    disclosed = bool(data.get("disclosed")) and total is not None
    if disclosed:
        out.append(_metric(
            "flow.north", "北向资金净流入", "flow", "akshare 沪深港通",
            value=round(total / 1e8, 2), unit="亿元", score=_tanh(total / 1e8, 50.0),
            confidence=0.8, fields=["north.data.total"], as_of=north.get("update_time"),
            coverage=1.0, fallback="未披露 → 该维度不参与分流",
        ))
    else:
        out.append(_metric(
            "flow.north", "北向资金净流入", "flow", "akshare 沪深港通",
            available=False, reason="交易所未披露当日北向净额（disclosed=false）",
            fallback="北向不披露是常态，该维度权重自动分配给其余资金指标",
            note="不是故障：沪深港通已改为按季披露",
        ))
    return out


def _sentiment_metric(sentiment: Dict[str, Any]) -> Dict[str, Any]:
    score = _num(sentiment.get("score"))
    coverage = _num(sentiment.get("coverage"))
    if score is None:
        return _metric("sentiment.score", "情绪总分", "sentiment", "sentiment.v1",
                       available=False, reason=sentiment.get("message") or "情绪分不可用",
                       fallback="该维度不参与分流")
    return _metric(
        "sentiment.score", "情绪总分", "sentiment", "sentiment.v1",
        value=round(score, 1), unit="分", score=_clamp((score - 50.0) / 30.0),
        confidence=0.4 + 0.5 * (coverage or 0), fields=["score", "coverage"],
        as_of=sentiment.get("as_of"), coverage=coverage,
        fallback="六维覆盖不足 → 置信按覆盖比例衰减",
        note=f"覆盖 {round((coverage or 0) * 100)}%",
    )


async def collect(force: bool = False) -> Dict[str, Any]:
    """汇总一次全部指标（缓存由上游各自负责，这里不重复建缓存）。"""
    from server.decision_snapshot import get_market_packet, get_sentiment_snapshot
    from server.services.market_dashboard import build_dashboard

    started = time.perf_counter()
    packet, sentiment, dashboard = await asyncio.gather(
        get_market_packet(force=force), get_sentiment_snapshot(force=force),
        build_dashboard(), return_exceptions=True,
    )
    packet = packet if isinstance(packet, dict) else {}
    sentiment = sentiment if isinstance(sentiment, dict) else {}
    dashboard = dashboard if isinstance(dashboard, dict) else {}

    buy = await _buy_metrics(force=force)
    metrics = [
        _index_trend_metric(dashboard, packet),
        *_breadth_metrics(packet),
        *_flow_metrics(dashboard, packet),
        *buy,
        _sentiment_metric(sentiment),
    ]
    available = [m for m in metrics if m["available"]]
    stale = [m["id"] for m in metrics if m["stale"]]
    return {
        "ok": True,
        "version": METRICS_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "as_of": packet.get("as_of") or dashboard.get("as_of"),
        "trace_id": packet.get("trace_id"),
        "metrics": metrics,
        "summary": {
            "total": len(metrics),
            "available": len(available),
            "unavailable": [m["id"] for m in metrics if not m["available"]],
            "stale": stale,
            "coverage": round(len(available) / len(metrics), 2) if metrics else 0.0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    }


__all__ = ["METRICS_VERSION", "TTL", "BUY_SAMPLE_SIZE", "ETF_BASKET", "collect"]
