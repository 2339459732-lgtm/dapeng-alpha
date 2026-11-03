"""DAPENG Alpha v4.0 - Agent 管理路由

提供 REST API 控制和查询 Agent 状态：
- GET  /api/agents              → 列出所有 Agent 状态
- POST /api/agents/{name}/start  → 启动 Agent
- POST /api/agents/{name}/stop   → 停止 Agent
- GET  /api/agents/{name}/status → 查询单个 Agent 状态
- GET  /api/market-sentiment      → 市场情绪评分（调用 finance_agent）
- GET  /api/news/{code}          → 个股新闻情绪分析（调用 news_agent）
- GET  /api/alerts               → 最近告警（调用 alert_agent）
- GET  /api/agents/health        → Agent 系统健康检查
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional

from server.agents import (
    AGENT_REGISTRY,
    disable_agent,
    enable_agent,
    get_agent,
    list_agents,
    update_agent_status,
)
from server.agents.orchestrator import health_check, list_all_status

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Agent 状态管理 API
# ============================================================

@router.get("/agents")
async def list_agents_api():
    """列出所有 Agent 状态"""
    return {
        "ok": True,
        "data": list_agents(),
        "total": len(AGENT_REGISTRY),
    }


@router.get("/agents/health")
async def health_check_api():
    """Agent 系统健康检查"""
    result = await health_check()
    return {"ok": result["ok"], "data": result}


@router.get("/agents/{name}/status")
async def get_agent_status_api(name: str):
    """查询单个 Agent 状态"""
    from server.agents.orchestrator import get_agent_status as get_status
    status = get_status(name)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Agent {name} 未注册")
    return {"ok": True, "data": status}


@router.post("/agents/{name}/start")
async def start_agent_api(name: str):
    """启动 Agent"""
    agent = get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {name} 未注册")

    if agent.status.value == "running":
        return {"ok": False, "message": f"Agent {name} 已在运行"}

    # 启动 Agent 任务
    from server.agents.orchestrator import start_agent
    from server.agents import AgentStatus

    # 根据不同 Agent 类型启动对应任务
    if name == "stock_screener":
        from server.agents.stock_screener import start as screener_start
        success = await start_agent(name, screener_start)
    elif name == "stock_analyzer":
        # 分析 Agent 是按需调用，不需要常驻任务
        update_agent_status(name, AgentStatus.IDLE)
        success = True
    elif name == "finance_analyzer":
        update_agent_status(name, AgentStatus.IDLE)
        success = True
    elif name == "news_analyzer":
        update_agent_status(name, AgentStatus.IDLE)
        success = True
    elif name == "alert_agent":
        from server.agents.alert_agent import get_alert_agent
        agent_instance = get_alert_agent()
        loop = asyncio.get_running_loop()
        task = loop.create_task(agent_instance.start())
        from server.agents.orchestrator import _task_map
        _task_map[name] = task
        success = True
    else:
        success = False

    if success:
        enable_agent(name)
        return {"ok": True, "message": f"Agent {name} 已启动"}
    else:
        raise HTTPException(status_code=500, detail=f"启动 Agent {name} 失败")


@router.post("/agents/{name}/stop")
async def stop_agent_api(name: str):
    """停止 Agent"""
    agent = get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {name} 未注册")

    if name == "alert_agent":
        from server.agents.alert_agent import get_alert_agent
        agent_instance = get_alert_agent()
        await agent_instance.stop()
    else:
        from server.agents.orchestrator import stop_agent
        stop_agent(name)

    disable_agent(name)
    return {"ok": True, "message": f"Agent {name} 已停止"}


# ============================================================
# 市场情绪 API（调用 finance_agent）
# ============================================================

@router.get("/market-sentiment")
async def market_sentiment_api():
    """获取市场情绪评分"""
    try:
        from server.agents.finance_agent import get_market_sentiment
        result = await get_market_sentiment()
        return {"ok": True, "data": result}
    except Exception as e:
        logger.error("/market-sentiment error: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 新闻情绪分析 API（调用 news_agent）
# ============================================================

@router.get("/news/{code}")
async def news_sentiment_api(code: str, limit: int = 10):
    """获取个股新闻情绪分析。

    守卫：不再对不存在的标的编造情绪对象 —— 未知代码返回 ok:false + reason；
    "market" 这类市场级代码显式分流到 /api/news/market。
    """
    try:
        c = (code or "").strip()
        if c.lower() in ("", "market", "none", "null", "all"):
            return {"ok": False, "data": None, "source": "unavailable",
                    "reason": "个股情绪接口不处理市场级代码；市场资讯请用 /api/news/market"}
        from server.config import find_stock_name
        if find_stock_name(c) == c:
            return {"ok": False, "data": None, "source": "unavailable",
                    "reason": f"未知标的 {c}：未在股票池/静态配置中找到，不生成情绪对象"}
        from server.agents.news_agent import get_stock_sentiment
        result = await get_stock_sentiment(code)
        return {"ok": True, "data": result}
    except Exception as e:
        logger.error("/news/%s error: %s", code, str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 告警 API（调用 alert_agent）
# ============================================================

@router.get("/alerts")
async def get_alerts_api(limit: int = 20):
    """获取最近告警"""
    try:
        from server.agents.alert_agent import get_alert_agent
        agent = get_alert_agent()
        alerts = agent.get_recent_alerts(limit=limit)
        return {
            "ok": True,
            "data": alerts,
            "total": len(alerts),
        }
    except Exception as e:
        logger.error("/alerts error: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/watch/{code}")
async def add_watch_api(code: str):
    """添加监控股票"""
    try:
        from server.agents.alert_agent import get_alert_agent
        agent = get_alert_agent()
        agent.add_watch_stock(code)
        return {"ok": True, "message": f"已添加监控 {code}"}
    except Exception as e:
        logger.error("/alerts/watch error: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/alerts/watch/{code}")
async def remove_watch_api(code: str):
    """移除监控股票"""
    try:
        from server.agents.alert_agent import get_alert_agent
        agent = get_alert_agent()
        agent.remove_watch_stock(code)
        return {"ok": True, "message": f"已移除监控 {code}"}
    except Exception as e:
        logger.error("/alerts/watch/delete error: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))
