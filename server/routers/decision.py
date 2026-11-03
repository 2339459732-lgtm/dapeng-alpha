"""决策闭环 API：共享快照 / 情绪 v1 / 六专家 / 全市场与全球覆盖。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from server.decision_experts import run_expert_panel
from server.decision_snapshot import get_market_packet, get_sentiment_history, get_sentiment_snapshot
from server.market_coverage import get_market_coverage, get_universe_rows
from server.market_scanner import get_market_scan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/decision", tags=["decision-closed-loop"])


@router.get("/snapshot")
async def decision_snapshot(refresh: bool = Query(False, description="强制刷新共享快照")):
    """一次决策周期的统一 MarketPacket（trace_id / as_of / coverage / quality）。"""
    return await get_market_packet(force=refresh)


@router.get("/sentiment")
async def decision_sentiment(refresh: bool = Query(False)):
    """sentiment.v1 六维情绪分；缺失维度重归一，覆盖不足不输出总分。"""
    return await get_sentiment_snapshot(force=refresh)


@router.get("/sentiment/history")
async def decision_sentiment_history(limit: int = Query(120, ge=1, le=1000)):
    return get_sentiment_history(limit=limit)


@router.get("/sentiment/flow")
async def decision_sentiment_flow(refresh: bool = Query(False)):
    """sentiment-flow.v1 情绪追踪：变化 / 分位 / 拐点 / 持续性 / 驱动归因。

    与 /sentiment 的区别：那个是「此刻多少分」，这个是「正在往哪走、谁在推」。
    """
    from server.sentiment_tracker import get_sentiment_flow

    return await get_sentiment_flow(force=refresh)


@router.get("/sentiment/flow/history")
async def decision_sentiment_flow_history(limit: int = Query(120, ge=1, le=240)):
    from server.sentiment_tracker import get_flow_history

    return get_flow_history(limit=limit)


@router.get("/experts")
async def decision_experts(refresh: bool = Query(False)):
    """六专家独立意见和主脑聚合结果；相同 trace 在缓存窗内复用。"""
    return await run_expert_panel(force=refresh)


@router.get("/coverage")
async def decision_coverage(refresh: bool = Query(False)):
    """全 A 股行情/估值/基本面 + 全球指数/行业代理 + 宏观覆盖。"""
    return await get_market_coverage(force=refresh)


@router.get("/universe")
async def decision_universe(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("amount", pattern="^(amount|chg|market_cap_yi|roe|pe)$"),
):
    """全市场分页查询；仅返回已加载快照，不在请求线程里打上游。"""
    return get_universe_rows(limit=limit, offset=offset, sort=sort)


@router.get("/metrics")
async def decision_metrics(refresh: bool = Query(False, description="强制重算指标（绕过上游缓存）")):
    """统一指标层：趋势 / 资金流 / 买手率 / 情绪，全部归一化到 score∈[-1,1]。

    每项都带 source / fields / as_of / age_s / ttl_s / stale / coverage / confidence，
    以及不可用时的 reason 与降级路径——分流规则只消费这里，不自己去猜数据。
    """
    from server.decision_metrics import collect

    return await collect(force=refresh)


@router.get("/routing")
async def decision_routing_view(
    refresh: bool = Query(False, description="强制重算分流"),
    with_metrics: bool = Query(False, description="附带指标明细"),
):
    """决策分流：按 data/decision_routing.json 的规则给出路由、仓位上限与证据。

    只收紧不放大：分流结论作为备忘录的仓位闸门，不产生交易方向。
    """
    from server.decision_routing import current

    return await current(force=refresh, with_metrics=with_metrics)


@router.get("/scan")
async def decision_scan(refresh: bool = Query(False)):
    """market-scanner.v1：横截面扫描 / 板块轮动 / 资金流向（实测或显式代理）。"""
    return await get_market_scan(force=refresh)


@router.get("/lhb-review")
async def lhb_review_latest(
    refresh: bool = Query(False, description="强制重跑（会重新打上游，约 5-10 秒）"),
):
    """龙虎榜复盘：最近一份已生成的报告（收盘后由主脑自动触发）。

    默认**只读已落盘结果**，不在请求线程里打上游——东财营业部明细要拉十几页，
    放在 GET 里会让页面首屏卡住。需要最新数据时显式传 refresh=true。
    """
    from server import lhb_review

    if refresh:
        return await lhb_review.build()
    cached = lhb_review.load()
    if cached:
        return cached
    return {"ok": False, "version": lhb_review.VERSION,
            "message": "暂无龙虎榜复盘（收盘后自动生成，或传 refresh=true 立即生成）"}


@router.get("/lhb-review/recent")
async def lhb_review_recent(limit: int = Query(10, ge=1, le=60)):
    """近期复盘索引：仅日期 + 记录数 + 主因分布，用于切换查看历史。"""
    from server import lhb_review

    return {"ok": True, "version": lhb_review.VERSION, "items": lhb_review.recent(limit=limit)}


@router.get("/lhb-review/{date}")
async def lhb_review_by_date(
    date: str,
    refresh: bool = Query(False, description="强制重跑该日复盘"),
):
    """指定交易日的复盘。date 形如 2026-11-02。"""
    import re as _re

    from server import lhb_review

    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return {"ok": False, "version": lhb_review.VERSION,
                "message": "date 需为 YYYY-MM-DD"}
    if refresh:
        return await lhb_review.build(date=date)
    cached = lhb_review.load(date)
    if cached:
        return cached
    return {"ok": False, "version": lhb_review.VERSION, "date": date,
            "message": "该日无已生成的复盘，传 refresh=true 生成"}


@router.get("/bus")
async def decision_bus():
    """全局数据总线：资源图、依赖、刷新节拍、链路年龄、最近事件、订阅者数量。"""
    from server.data_bus import bus

    return bus.snapshot()


@router.get("/cache")
async def decision_cache():
    """缓存治理状态：条目数/字节占用/命中率/淘汰与拒写计数/最大条目。"""
    from server.cache import cache

    stats = cache.stats()
    return {
        "ok": True,
        "version": "cache-governance.v1",
        "cache": stats,
        "policy": {
            "max_size": stats.get("max_size"),
            "max_bytes": stats.get("max_bytes"),
            "cleanup_interval_s": stats.get("cleanup_interval"),
            "eviction": "先回收过期 → 再按 LRU 淘汰 → 单条超预算直接拒写",
        },
    }


@router.get("/system")
async def decision_system():
    """决策系统总览：链路（bus）+ 缓存（cache）联合视图，供运维与页面角标使用。"""
    from server.cache import cache
    from server.data_bus import bus

    snapshot = bus.snapshot()
    return {
        "ok": True,
        "version": "decision-system.v1",
        "generated_at": snapshot.get("generated_at"),
        "chain": snapshot.get("chain"),
        "graph": snapshot.get("graph"),
        "subscribers": snapshot.get("subscribers"),
        "events": (snapshot.get("events") or [])[:10],
        "cache": cache.stats(),
    }


@router.get("/coverage/stream")
async def decision_coverage_stream(request: Request):
    """SSE：持续推送同一份覆盖快照。前端只读缓存，不触发额外上游抓取。"""

    async def event_generator():
        seq = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await get_market_coverage(force=False)
                    seq += 1
                    data = json.dumps({
                        "seq": seq,
                        "trace_time": payload.get("generated_at"),
                        "ashare": payload.get("ashare", {}).get("quotes"),
                        "quality": payload.get("ashare", {}).get("quality"),
                        "sources": [
                            {"key": s.get("key"), "status": s.get("status"), "as_of": s.get("as_of")}
                            for s in (payload.get("sources") or [])
                        ],
                    }, ensure_ascii=False)
                    yield "event: coverage.snapshot\n" + f"data: {data}\n\n"
                except Exception as exc:
                    yield "event: coverage.error\n" + f"data: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/stream")
async def decision_stream(request: Request, interval: int = Query(30, ge=10, le=300)):
    """统一 SSE（事件驱动）：数据总线刷新即推送，无需等待轮询周期。

    协议：
    - 首帧立即推送 sentiment / experts / scan 三份当前值；
    - 之后由 data-bus 事件驱动：refresh(sentiment|experts|scan) 时推送新载荷，
      invalidate 时推送失效通知（前端可据此标记重建中）；
    - 无事件时每 ``interval`` 秒发一次注释心跳，保持连接与代理存活。
    """
    from server.data_bus import bus

    async def event_generator():
        queue = bus.subscribe()
        seq = 0

        async def push(key: str) -> str:
            nonlocal seq
            seq += 1
            if key == "sentiment":
                payload = await get_sentiment_snapshot(force=False)
            elif key == "experts":
                payload = await run_expert_panel(force=False)
            else:
                payload = await get_market_scan(force=False)
            return "event: " + key + "\ndata: " + json.dumps(
                {"seq": seq, "key": key, "payload": payload},
                ensure_ascii=False, default=str,
            ) + "\n\n"

        try:
            for key in ("sentiment", "experts", "scan"):
                try:
                    yield await push(key)
                except Exception as exc:
                    yield "event: " + key + ".error\ndata: " + json.dumps(
                        {"message": str(exc)}, ensure_ascii=False) + "\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                etype = event.get("type")
                key = event.get("key")
                if etype == "refresh" and key in ("sentiment", "experts", "scan"):
                    try:
                        yield await push(key)
                    except Exception as exc:
                        yield "event: " + str(key) + ".error\ndata: " + json.dumps(
                            {"message": str(exc)}, ensure_ascii=False) + "\n\n"
                elif etype == "invalidate":
                    yield "event: invalidate\ndata: " + json.dumps(
                        {"keys": event.get("keys"), "cause": event.get("cause")},
                        ensure_ascii=False) + "\n\n"
        except asyncio.CancelledError:
            return
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
