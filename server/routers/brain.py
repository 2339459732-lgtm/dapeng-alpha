"""DAPENG Brain — AI 主脑状态与控制接口"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from server.brain import dapeng_brain

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("/status")
async def brain_status():
    """主脑全景状态：调度任务表 / 洞察 / 经验 / LLM 配置"""
    return await dapeng_brain.status()


@router.get("/alignment")
async def brain_alignment(refresh: bool = False, light: bool = True):
    """三层对证：数据配置是否合法 / 能力图 / 各层同一指标是否对得上。

    ``light=True`` 只比事实层与判断层（不触发六专家），适合页面常驻轮询。
    """
    return await dapeng_brain.alignment(refresh=refresh, light=light)


@router.get("/config")
async def brain_config_view(history: int = 8):
    """主脑配置身份与生效值：跑的是哪一版、有没有回落、最近改过什么。

    只读。改配置走 ``python scripts/brain_config_admin.py apply``（先预检再原子写入），
    不在 HTTP 上开写口——配置错误的代价远高于「省一步命令」。
    """
    from server.brain_config import layer_labels, load, meta, history_tail, validate

    effective = load(force=True)
    return {
        "ok": True,
        "version": effective.get("version"),
        "file": meta(),
        "validation": validate(),
        "layers": layer_labels(),
        "history": history_tail(limit=max(1, min(50, int(history)))),
        "effective": effective,
    }


@router.get("/lineage/recent")
async def brain_lineage_recent(limit: int = 10):
    """最近 N 条结论溯源链路（摘要级，不含每节点 facts 明细）。"""
    from server.lineage import recent

    rows = recent(limit)
    return {
        "ok": True,
        "version": "lineage.v1",
        "count": len(rows),
        "data": [
            {
                "trace_id": row.get("trace_id"),
                "built_at": row.get("built_at"),
                "ok": row.get("ok"),
                "gaps": row.get("gaps"),
                "as_of": next((n.get("as_of") for n in row.get("nodes") or []
                               if n.get("key") == "memo"), None),
            }
            for row in rows
        ],
    }


@router.get("/lineage/{trace_id}")
async def brain_lineage(trace_id: str):
    """按 trace_id 回放一条结论的完整依据链（含各节点版本、时点、字段指纹与取值）。

    这是「结论可追溯至上游依据」的可执行形态：拿到仓位结论，一条请求即可看到
    它依据的是哪一版事实、哪些字段、什么时点，以及链路是否完整、时间是否倒挂。
    """
    from server.lineage import get

    row = get(trace_id)
    if row is None:
        return {"ok": False, "message": f"未找到 trace_id={trace_id} 的溯源记录",
                "hint": "闭环只保留最近 60 条；先 POST /api/brain/cycle 生成一条"}
    return {"ok": True, "version": row.get("version"), "data": row}


@router.get("/capabilities")
async def brain_capabilities_view():
    """主脑能力矩阵：计算能力 / 决策能力 / 依赖资源 / 当前可用性 / 提升目标。"""
    return await dapeng_brain.capabilities()


@router.post("/cycle")
async def brain_run_cycle(force: bool = False):
    """按需跑一次完整闭环：情绪追踪 → 市场调查 → 六专家 → 决策备忘录。"""
    return await dapeng_brain.run_cycle(force=force)


@router.get("/cycle/latest")
async def brain_latest_cycle():
    """最近一次闭环结果（进程内 KV 缓存，重启后为空）。"""
    return await dapeng_brain.last_cycle()


@router.get("/insights")
async def brain_insights(limit: int = 20):
    """主脑洞察记录（最近 N 条，按时间顺序返回，含来源任务与生成时间）"""
    status = await dapeng_brain.status()
    insights = (await dapeng_brain._kv_get("brain_insights", []))[-limit:]
    return {"ok": True, "data": insights, "count": len(insights)}


@router.post("/run/{task_id}")
async def brain_run_task(task_id: str):
    """手动触发某个调度任务（market_survey / daily_loop / memory_distill）"""
    return await dapeng_brain.run_task_now(task_id)


@router.post("/survey/refresh")
async def brain_survey_refresh():
    """强制刷新一次动态市场调查（绕过 10 分钟缓存）"""
    from server.market_survey_engine import market_survey_engine

    try:
        payload = await market_survey_engine.get_market_survey(force=True)
        return {"ok": True, "data": payload}
    except Exception as exc:
        logger.exception("survey refresh failed: %s", exc)
        return {"ok": False, "message": str(exc)}


@router.get("/intel")
async def brain_intel():
    """最新情报评估（世界局势/地缘 → 市场影响）"""
    intel = await dapeng_brain._kv_get("brain_intel", None)
    history = await dapeng_brain._kv_get("brain_intel_history", [])
    return {"ok": True, "latest": intel, "history": history[-10:]}


@router.post("/intel/scan")
async def brain_intel_scan():
    """立即执行一轮情报扫描评估"""
    try:
        from server.agents.intel_agent import scan_and_persist
        payload = await scan_and_persist(dapeng_brain._kv_get, dapeng_brain._kv_set)
        return payload
    except Exception as exc:
        logger.exception("intel scan failed: %s", exc)
        return {"ok": False, "message": str(exc)}
