#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""递进式研判链路（decision-pipeline.v1）。

把「数据源 → 广度 → 板块 → 个股 → 研判团/主脑 → 两类产出」串成一条可审计的流程，
每一步都返回**输入样本数、输出数量、口径与降级状态**，不允许静默变更。

为什么要单独一层
----------------
原先各卡片各自取数：大盘面板用 448 只核心样本、扫描用 5,600 只全市场、候选池又是
另一套预筛。用户看到的是三个互相对不上的数字，且没有任何地方说明「为什么少了」。
本模块只做编排，不改各模块的实现，把每一步的 in/out 计数与 reason 显式暴露。

两类产出
--------
* `intraday`：盘中清单。按候选池逐只判断触发条件（高开 0~3%、价格≥昨收、
  量比>1.5、VWAP）。条件缺失时标 `unknown` + reason，不伪装成「已满足」。
* `post_close`：盘后报告。当日筛选漏斗 + 候选实际表现（下一交易日最高/最低/收盘，
  是否触及目标/止损）+ 偏差说明。没有历史快照时明确返回 `pending`。

边界
----
* 只读：不写交易、不改候选池，只做编排与评估。
* VWAP 需要分时数据；当前未接入时返回 `vwap: null` 并在 `missing` 中列出。
* 盘中判断依赖实时行情；休市时所有候选标 `market_closed`，不产生「可买入」。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from server.decision_snapshot import get_market_packet
from server.market_scanner import get_market_scan
from server.quote_hub import quote_hub
from server.strategy_profile import build as build_profile
from server.tencent_kline import fetch_tencent_kline

logger = logging.getLogger(__name__)

VERSION = "decision-pipeline.v1"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _stage(stage_id: str, name: str, *, status: str = "ok",
           scope: Optional[str] = None, sample: Optional[int] = None,
           output: Optional[int] = None, degraded: bool = False,
           reason: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": stage_id, "name": name, "status": status,
        "scope": scope, "sample": sample, "output": output,
        "degraded": degraded, "reason": reason,
        **(extra or {}),
    }


def _sources_stage(packet: Dict[str, Any], scan: Dict[str, Any],
                   profile: Dict[str, Any], panel: Dict[str, Any]) -> Dict[str, Any]:
    """数据源接入：每路带 as_of 与可用状态；任何一路不可用都单独列出。"""
    items = [
        {"name": "实时行情", "source": "tencent", "as_of": (packet.get("inputs_as_of") or {}).get("core_market"),
         "ok": bool(packet.get("market")), "note": "QuoteHub 1.5s TTL"},
        {"name": "全市场覆盖", "source": "market-coverage.v1",
         "as_of": (packet.get("inputs_as_of") or {}).get("full_market"),
         "ok": (packet.get("market") or {}).get("full_market") is not None,
         "note": "沪深京 A 股，5,600+ 样本"},
        {"name": "横截面扫描", "source": "market-scanner.v1", "as_of": scan.get("as_of"),
         "ok": bool((scan.get("cross_section") or {}).get("available")),
         "note": f"样本 {(scan.get('cross_section') or {}).get('sample')}"},
        {"name": "研判团", "source": "brain-panel.v3", "as_of": panel.get("as_of"),
         "ok": bool(panel.get("experts")), "note": f"{len(panel.get('experts') or [])} 位专家"},
        {"name": "候选池", "source": "strategy-profile.v1", "as_of": profile.get("generated_at"),
         "ok": bool(profile.get("ok")), "note": profile.get("reason") or "8 分制"},
    ]
    failed = [i["name"] for i in items if not i["ok"]]
    return _stage("sources", "数据源接入", status="degraded" if failed else "ok",
                  sample=len(items), output=len(items) - len(failed),
                  degraded=bool(failed), reason="不可用：" + "、".join(failed) if failed else None,
                  extra={"items": items})


def _breadth_stage(packet: Dict[str, Any], scan: Dict[str, Any]) -> Dict[str, Any]:
    """市场广度：优先全市场，降级必须带 reason。"""
    market = packet.get("market") or {}
    b = market.get("breadth") or {}
    cross = scan.get("cross_section") or {}
    full = market.get("full_market") is not None
    sample = b.get("sample") or cross.get("sample")
    scope = b.get("scope") or ("沪深京 A 股（全市场）" if full else "核心样本")
    degraded = bool(b.get("degraded")) or not full
    reason = b.get("reason") or (None if full else "全市场快照未就绪，广度退回核心样本")
    return _stage("breadth", "市场广度计算",
                  status="degraded" if degraded else "ok",
                  scope=scope, sample=sample, output=sample,
                  degraded=degraded, reason=reason,
                  extra={"metrics": {
                      "up": b.get("up"), "down": b.get("down"), "flat": b.get("flat"),
                      "up_ratio": b.get("up_ratio"),
                      "limit_up": b.get("limit_up"), "limit_down": b.get("limit_down"),
                      "median_chg": b.get("median_chg"),
                      "scan_limit_up": cross.get("limit_up"),
                      "scan_limit_down": cross.get("limit_down"),
                  }})


def _sector_stage(scan: Dict[str, Any]) -> Dict[str, Any]:
    rot = scan.get("rotation") or {}
    industries = rot.get("industries") or []
    top = industries[:5]
    return _stage("sector", "板块筛选",
                  status="ok" if industries else "degraded",
                  scope="申万行业（全市场聚合）", sample=len(industries), output=len(top),
                  degraded=not industries,
                  reason=None if industries else "行业聚合不可用（行情样本不足）",
                  extra={"top": [{"name": i.get("name"), "rank": i.get("rank"),
                                  "avg_chg": i.get("avg_chg"), "count": i.get("count")}
                                 for i in top],
                         "signal": rot.get("signal"), "signal_label": rot.get("signal_label")})


def _stock_stage(profile: Dict[str, Any]) -> Dict[str, Any]:
    if not profile.get("ok"):
        return _stage("stock", "个股筛选", status="degraded", sample=0, output=0,
                      degraded=True, reason=profile.get("reason") or "候选池不可用")
    funnel = profile.get("funnel") or {}
    return _stage("stock", "个股筛选", status="ok",
                  scope="全市场预筛 → 日线技术确认 → 8 分制评分",
                  sample=funnel.get("quoted") or funnel.get("universe"),
                  output=len(profile.get("candidates") or []),
                  extra={"funnel": funnel,
                         "thresholds": (profile.get("runtime") or {}),
                         "score_pass": (profile.get("profile") or "")})


def _brain_stage(panel: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    experts = panel.get("experts") or []
    consensus = panel.get("consensus") or {}
    judgment = (packet.get("market") or {}).get("judgment") or {}
    return _stage("brain", "研判团与主脑",
                  status="ok" if experts else "degraded",
                  sample=len(experts), output=len(experts),
                  degraded=not experts,
                  reason=None if experts else "专家面板不可用",
                  extra={"experts": [{"id": e.get("expert_id"), "name": e.get("name"),
                                      "stance": e.get("stance"), "confidence": e.get("confidence"),
                                      "thesis": (e.get("thesis") or "")[:120]} for e in experts],
                         "consensus": consensus,
                         "judgment": judgment})


async def _intraday_list(candidates: List[Dict[str, Any]],
                         market_open: bool) -> Dict[str, Any]:
    """盘中清单：逐只对候选判断触发条件，缺失项显式列出。"""
    if not candidates:
        return {"available": True, "market_open": market_open, "items": [],
                "note": "候选池为空：今日无 8 分以上标的，空仓不做"}

    codes = [c.get("code") for c in candidates if c.get("code")]
    quotes = await quote_hub.snapshot(codes)
    items = []
    for c in candidates:
        code = c.get("code")
        q = quotes.get(code) or {}
        prev_close = _num(q.get("prev_close")) or _num(c.get("price"))
        price = _num(q.get("price"))
        open_px = _num(q.get("open"))
        vol_ratio = _num(c.get("volume_ratio"))
        missing: List[str] = []
        reasons: List[str] = []

        open_pct = None
        if open_px and prev_close:
            open_pct = (open_px / prev_close - 1) * 100
        else:
            missing.append("开盘价")

        vwap = None  # 分时均价暂未接入
        missing.append("VWAP（分时均线未接入）")

        if not market_open:
            action, note = "market_closed", "休市：仅生成待确认清单，开盘后重新评估"
        elif open_pct is None or price is None:
            action, note = "unknown", "缺少开盘价/现价，无法判定"
        elif open_pct > 3:
            action, note = "blocked", f"高开 {open_pct:.1f}% > 3%，不追"
        elif price < prev_close:
            action, note = "blocked", "现价低于昨收，取消"
        elif vol_ratio is not None and vol_ratio <= 1.5:
            action, note = "watch", f"量比 {vol_ratio:.2f} ≤ 1.5，等待放量"
        elif vwap is None:
            action, note = "watch", "价格与量比满足，但缺 VWAP 无法完成最后确认"
        elif price < vwap:
            action, note = "watch", "价格低于 VWAP，等待站上"
        else:
            action, note = "ready", "全部条件满足，可买入"

        if open_pct is not None and not (0 <= open_pct <= 3):
            reasons.append(f"开盘涨幅 {open_pct:.2f}% 不在 0~3%")
        if vol_ratio is not None and vol_ratio <= 1.5:
            reasons.append(f"量比 {vol_ratio:.2f} ≤ 1.5")

        items.append({
            "code": code, "name": c.get("name"),
            "action": action, "note": note,
            "score": c.get("score"), "score_detail": c.get("score_detail"),
            "sector": c.get("sector"), "sector_rank": c.get("sector_rank"),
            "prev_close": prev_close, "price": price, "open_pct": open_pct,
            "volume_ratio": vol_ratio, "vwap": vwap,
            "trigger": c.get("trigger"), "stop": c.get("stop"), "target": c.get("target"),
            "shares": c.get("shares"), "amount": c.get("first_entry_amount"),
            "reasons": reasons, "missing": missing,
        })
    return {"available": True, "market_open": market_open, "items": items,
            "note": "休市期间为待确认清单；开盘后由同一接口重新判定" if not market_open else None}


def _post_close(profile: Dict[str, Any], review: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not profile.get("ok"):
        return {"available": False, "reason": profile.get("reason") or "候选池不可用"}
    market = profile.get("market") or {}
    funnel = profile.get("funnel") or {}
    candidates = profile.get("candidates") or []
    deviations: List[str] = []
    if not candidates:
        deviations.append(
            f"当日 0 只入选：市场分 {market.get('score')}/2（指数 MA20 "
            f"{'上方' if market.get('above_ma20') else '下方'}，涨停 {market.get('limit_up')} / "
            f"跌停 {market.get('limit_down')}），按规则空仓。")
    if funnel.get("scored") and funnel.get("passed") == 0:
        deviations.append(f"预筛 {funnel.get('prefiltered')} 只、评分 {funnel.get('scored')} 只，"
                          "全部未达 8 分或存在风险项。")
    return {
        "available": True,
        "date": profile.get("date"),
        "screening": {"market": market, "funnel": funnel, "thresholds": profile.get("runtime")},
        "candidates": [{"code": c.get("code"), "name": c.get("name"), "score": c.get("score"),
                        "sector": c.get("sector"), "stop": c.get("stop"), "target": c.get("target"),
                        "reason": c.get("score_detail")} for c in candidates],
        "performance": review or {"status": "pending", "reason": "尚无已完成的下一交易日数据"},
        "deviations": deviations,
        "notes": profile.get("caveats") or [],
    }


async def run(force: bool = False) -> Dict[str, Any]:
    """跑完整链路。各阶段并行取数，任一段失败只降级该段，不影响整体返回。"""
    started = time.perf_counter()
    packet_task = get_market_packet(force=force)
    scan_task = get_market_scan(force=force)
    profile_task = build_profile(force=force)

    packet, scan, profile = await asyncio.gather(packet_task, scan_task, profile_task,
                                                 return_exceptions=True)
    packet = packet if isinstance(packet, dict) else {}
    scan = scan if isinstance(scan, dict) else {}
    profile = profile if isinstance(profile, dict) else {"ok": False, "reason": "候选池异常"}

    try:
        from server.decision_experts import run_expert_panel

        panel = await run_expert_panel(force=force)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pipeline: expert panel unavailable: %s", exc)
        panel = {}

    market_open = bool(quotes_market_open())
    intraday = await _intraday_list(profile.get("candidates") or [], market_open)

    review = None
    try:
        from server.strategy_review import latest_review

        review = await latest_review()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pipeline: strategy review unavailable: %s", exc)
        review = {"status": "unavailable", "reason": f"复盘模块异常：{exc}"}

    stages = [
        _sources_stage(packet, scan, profile, panel),
        _breadth_stage(packet, scan),
        _sector_stage(scan),
        _stock_stage(profile),
        _brain_stage(panel, packet),
    ]
    degraded = [s for s in stages if s.get("degraded")]
    return {
        "ok": True,
        "version": VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "status": "degraded" if degraded else "ok",
        "degraded_stages": [s["id"] for s in degraded],
        "stages": stages,
        "intraday": intraday,
        "post_close": _post_close(profile, review),
        "trace_id": packet.get("trace_id"),
    }


def quotes_market_open() -> bool:
    """交易时段判断（09:30–11:30 / 13:00–15:00，工作日）。"""
    from datetime import datetime

    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)


__all__ = ["VERSION", "run", "quotes_market_open"]
