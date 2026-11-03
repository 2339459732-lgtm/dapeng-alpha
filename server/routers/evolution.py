"""闭环进化 API —— 复盘→迭代→策略库的可视化与人工干预入口（prefix=/api）

端点：
  GET  /api/evolution/status              闭环运行状态（loop_state + 各环计数）
  GET  /api/evolution/iterations?limit    迭代动作记录（晋级/降级/调参/退役）
  GET  /api/evolution/trade-reviews?limit 单笔交易复盘结论（自动归因）
  GET  /api/evolution/library             策略库（被验证有效的策略版本谱系）
  GET  /api/evolution/proposals           调参提案（pending/applied/rejected）
  POST /api/evolution/proposals/{id}/apply   人工立即应用提案（走同一套双轨验证）
  POST /api/evolution/proposals/{id}/reject  人工否决提案
  POST /api/evolution/run                 手动触发一轮闭环日结例程（幂等）
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from server.trading import trading_facade
from server.trading.iterator import (
    ITERATION_LOG_KEY,
    LIBRARY_KEY,
    PROPOSALS_KEY,
    force_apply_proposal,
    reject_proposal,
)
from server.trading.loop_scheduler import LOOP_STATE_KEY
from server.trading.review_engine import TRADE_REVIEWS_KEY
from server.contracts.envelope import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter()


#: 统一信封来自 contracts（contract-envelope.v1）；此处只保留本地别名，调用点不变
_ok = ok
_fail = fail


async def _store():
    await trading_facade.ensure_ready()
    return trading_facade.store


@router.get("/evolution/status")
async def evolution_status() -> Dict[str, Any]:
    """闭环总览：日结循环状态、单笔复盘/迭代/策略库/待处理提案数量，以及闭环口径说明"""
    store = await _store()
    loop_state = await store.kv_get(LOOP_STATE_KEY, {}) or {}
    reviews = await store.kv_get(TRADE_REVIEWS_KEY, []) or []
    iterations = await store.kv_get(ITERATION_LOG_KEY, []) or []
    library = await store.kv_get(LIBRARY_KEY, []) or []
    proposals = await store.kv_get(PROPOSALS_KEY, []) or []
    runtime = trading_facade.loop.runtime_state() if trading_facade.loop else {}
    return _ok({
        "loop": {**loop_state, **{"runtime": runtime}},
        "counts": {
            "trade_reviews": len(reviews),
            "iterations": len(iterations),
            "library_strategies": len(library),
            "validated_strategies": sum(1 for e in library if e.get("status") == "validated"),
            "pending_proposals": sum(1 for p in proposals if p.get("status") == "pending"),
        },
        "explain": "闭环：执行交易 → 收盘日结（权益快照+单笔复盘）→ 迭代决策（晋级/降级/调参）→ 策略库沉淀 → 回到执行，全程无人值守。",
    })


@router.get("/evolution/iterations")
async def evolution_iterations(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """策略迭代日志（晋级/降级/调参记录），按时间倒序分页"""
    store = await _store()
    logs = await store.kv_get(ITERATION_LOG_KEY, []) or []
    return _ok({"total": len(logs), "items": list(reversed(logs[-limit:]))})


@router.get("/evolution/trade-reviews")
async def evolution_trade_reviews(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """单笔交易复盘记录（MFE/MAE/出场效率/归因标签），按时间倒序分页"""
    store = await _store()
    reviews = await store.kv_get(TRADE_REVIEWS_KEY, []) or []
    return _ok({"total": len(reviews), "items": list(reversed(reviews[-limit:]))})


@router.get("/evolution/library")
async def evolution_library() -> Dict[str, Any]:
    """策略库：已验证/观察中的策略与参数沉淀"""
    store = await _store()
    library = await store.kv_get(LIBRARY_KEY, []) or []
    return _ok({"total": len(library), "items": library})


@router.get("/evolution/proposals")
async def evolution_proposals(limit: int = Query(50, ge=1, le=100)) -> Dict[str, Any]:
    """待人工确认的迭代提案列表（含提案依据与建议变更）"""
    store = await _store()
    proposals = await store.kv_get(PROPOSALS_KEY, []) or []
    return _ok({"total": len(proposals), "items": list(reversed(proposals[-limit:]))})


@router.post("/evolution/proposals/{proposal_id}/apply")
async def proposal_apply(proposal_id: str):
    """人工立即应用某条迭代提案，走与自动应用相同的双轨验证后才落库"""
    store = await _store()
    ok, msg = await force_apply_proposal(store, proposal_id)
    return _ok(None, msg) if ok else _fail(msg)


@router.post("/evolution/proposals/{proposal_id}/reject")
async def proposal_reject(proposal_id: str):
    """人工否决某条迭代提案（留痕，不再自动应用）"""
    store = await _store()
    ok, msg = await reject_proposal(store, proposal_id)
    return _ok(None, msg) if ok else _fail(msg)


@router.post("/evolution/run")
async def evolution_run(full: bool = Query(True)):
    """手动触发闭环日结例程（full=True 含调参提案与周报，默认开启便于验证）"""
    await trading_facade.ensure_ready()
    if trading_facade.loop is None:
        return _fail("闭环调度器未就绪", 503)
    try:
        result = await trading_facade.loop.run_daily_routine(trigger="manual", full=full)
    except Exception as e:
        logger.error("evolution_run 失败", exc_info=True)
        return _fail(f"闭环例程执行失败：{type(e).__name__} {e}", 500)
    return _ok(result, "闭环日结例程已执行")
