"""DAPENG Alpha v4.0 Agent - 选股Agent"""

import logging

from server.core.timeutil import now_iso
from typing import Any, Dict, List
import asyncio

from server.agents import get_agent, update_agent_status, AgentStatus
from server.agents.orchestrator import AgentTask, TaskPriority, get_orchestrator
from server.config import get_all_stock_codes
from server.westock import westock
from server.strategies import compute_strategy, generate_buy_recommendation
from server.cache import cache

logger = logging.getLogger(__name__)

SCAN_CACHE_KEY = "agent:scan_result"
SCAN_CACHE_TTL = 120  # 扫描结果缓存 120 秒


async def run_screen(
    min_score: int = 60,
    min_signals: int = 1,
    sectors: List[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    选股Agent核心逻辑：扫描全市场，返回评分>=min_score的优质标的

    返回格式：
    {
        "total_scanned": int,
        "qualified": int,
        "results": [...],
        "timestamp": "..."
    }
    """
    agent = get_agent("stock_screener")
    if agent and not agent.enabled:
        return {"error": "选股Agent已禁用", "results": []}

    update_agent_status("stock_screener", AgentStatus.RUNNING)
    logger.info("选股Agent启动：min_score=%d, sectors=%s", min_score, sectors)

    try:
        # 获取股票列表
        if sectors:
            from server.config import SECTORS, get_sector_stocks
            codes = []
            for s in sectors:
                stocks = get_sector_stocks(s)
                codes.extend([stock[0] for stock in stocks])
        else:
            codes = get_all_stock_codes()  # 返回 List[str]

        logger.info("选股Agent：扫描 %d 只股票", len(codes))

        # 分批获取K线（每批8只，并发控制）
        BATCH = 8
        results = []
        total = 0

        for i in range(0, len(codes), BATCH):
            batch = codes[i:i + BATCH]
            try:
                kline_map = await asyncio.wait_for(
                    westock.batch_klines(batch, period="day"),
                    timeout=25.0
                )
                for code, klines in kline_map.items():
                    total += 1
                    if not klines or len(klines) < 5:
                        continue

                    strategy_result = compute_strategy(klines)
                    if strategy_result.total_score >= min_score and len(strategy_result.signals) >= min_signals:
                        name = _find_name(code)
                        rec = generate_buy_recommendation(strategy_result, klines)
                        results.append({
                            "code": code,
                            "name": name,
                            "score": strategy_result.total_score,
                            "signals": [s.label for s in strategy_result.signals],
                            "buy_zone": rec["buy_zone"],
                            "stop_loss": rec["stop_loss"],
                            "reason": rec["reason"][:80],
                        })

            except asyncio.TimeoutError:
                logger.warning("选股Agent批次超时：batch=%s", batch)
            except Exception as e:
                logger.warning("选股Agent批次错误：%s", str(e))

        # 按评分排序
        results.sort(key=lambda x: x["score"], reverse=True)
        if limit:
            results = results[:limit]

        result = {
            "total_scanned": total,
            "qualified": len(results),
            "results": results,
            "timestamp": now_iso(timespec="microseconds", tz=False),
        }

        # 写入缓存
        cache.set(SCAN_CACHE_KEY, result, SCAN_CACHE_TTL)
        update_agent_status("stock_screener", AgentStatus.IDLE)
        logger.info("选股Agent完成：扫描%d只，命中%d只", total, len(results))
        return result

    except Exception as e:
        update_agent_status("stock_screener", AgentStatus.ERROR, str(e))
        logger.error("选股Agent异常：%s", str(e))
        return {"error": str(e), "results": []}


async def run_screen_task(min_score: int = 60, sectors: List[str] = None) -> Dict[str, Any]:
    """提交选股任务到调度器"""
    orch = get_orchestrator()
    task = AgentTask(
        agent_name="stock_screener",
        func=run_screen,
        args=(min_score, 1, sectors, 20),
        priority=TaskPriority.HIGH,
        timeout=120.0,
    )
    return await orch.submit(task)


def get_cached_result() -> Dict[str, Any]:
    """获取上次扫描结果（不重新扫描）"""
    return cache.get(SCAN_CACHE_KEY, {})


def _find_name(code: str) -> str:
    from server.config import find_stock_name
    return find_stock_name(code)


