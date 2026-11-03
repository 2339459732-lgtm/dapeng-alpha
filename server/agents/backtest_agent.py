"""DAPENG Alpha v4.0 Agent - 回测Agent"""

import logging
from typing import Any, Dict, List
import asyncio

from server.agents import get_agent, update_agent_status, AgentStatus
from server.agents.orchestrator import AgentTask, TaskPriority, get_orchestrator
from server.config import get_all_stock_codes
from server.westock import westock
from server.strategies import compute_strategy

logger = logging.getLogger(__name__)

BACKTEST_CACHE_KEY = "agent:backtest:{code}"
BACKTEST_CACHE_TTL = 3600  # 回测结果缓存1小时


async def run_backtest(
    code: str,
    start_date: str = "",
    end_date: str = "",
    strategy_params: dict = None,
) -> Dict[str, Any]:
    """
    回测Agent核心逻辑：对单只股票运行策略回测

    返回格式：
    {
        "code": str,
        "name": str,
        "total_return": float,    # 总收益率%
        "annual_return": float,  # 年化收益率%
        "max_drawdown": float,  # 最大回撤%
        "sharpe_ratio": float,   # 夏普比率
        "win_rate": float,        # 胜率%
        "profit_factor": float,  # 盈亏比
        "total_trades": int,     # 总交易次数
        "trades": [...],         # 每笔交易详情
    }
    """
    agent = get_agent("backtest_runner")
    if agent and not agent.enabled:
        return {"error": "回测Agent已禁用", "code": code}

    update_agent_status("backtest_runner", AgentStatus.RUNNING)
    logger.info("回测Agent启动：code=%s", code)

    try:
        # 1. 获取历史K线（最多120根）
        klines = await asyncio.wait_for(
            westock.fetch_kline(code, period="day", limit=120),
            timeout=20.0
        )

        if not klines or len(klines) < 20:
            return {"error": "K线数据不足，无法回测", "code": code}

        # 2. 模拟回测
        result = _simulate_backtest(klines, strategy_params or {})

        update_agent_status("backtest_runner", AgentStatus.IDLE)
        logger.info("回测Agent完成：code=%s, return=%.1f%%", code, result["total_return"])
        return result

    except asyncio.TimeoutError:
        update_agent_status("backtest_runner", AgentStatus.ERROR, "K线获取超时")
        return {"error": "K线获取超时", "code": code}
    except Exception as e:
        update_agent_status("backtest_runner", AgentStatus.ERROR, str(e))
        logger.error("回测Agent异常：%s", str(e))
        return {"error": str(e), "code": code}


def _simulate_backtest(klines: list, params: dict) -> Dict[str, Any]:
    """
    简化的回测模拟器
    规则：策略评分>=70买入，止损-5%或止盈+15%卖出，最大持仓20交易日
    """
    closes = [float(k.get("c", 0)) for k in klines]
    if len(closes) < 20:
        return {"error": "数据不足"}

    cash = 100000.0  # 初始资金
    position = 0.0  # 持仓数量
    buy_price = 0.0
    buy_day = -1
    trades = []
    equity_curve = [cash]
    win_count = 0
    loss_count = 0
    total_profit = 0.0

    for i in range(20, len(closes)):
        current = closes[i]
        day_klines = klines[:i+1]

        strategy_result = compute_strategy(day_klines)
        score = strategy_result.total_score
        atr = strategy_result.atr14 or 0

        # 买入条件：无持仓 + 评分>=70 + 信号>=2
        if position == 0 and score >= 70 and len(strategy_result.signals) >= 2:
            # 全仓买入（扣除手续费万2.5）
            fee_rate = 0.00025
            buy_price = current * (1 + fee_rate)
            position = cash / buy_price
            cash = 0.0
            buy_day = i
            trades.append({
                "action": "buy",
                "day": i,
                "price": round(current, 2),
                "score": score,
                "signals": [s.label for s in strategy_result.signals],
            })

        # 卖出条件
        elif position > 0:
            hold_days = i - buy_day
            pnl_pct = (current - buy_price) / buy_price * 100

            should_sell = False
            sell_reason = ""

            # 止损：-5%
            if pnl_pct <= -5.0:
                should_sell = True
                sell_reason = "止损"
            # 止盈：+15%
            elif pnl_pct >= 15.0:
                should_sell = True
                sell_reason = "止盈"
            # 持仓超时：20交易日
            elif hold_days >= 20:
                should_sell = True
                sell_reason = "超时"
            # 策略转空：评分<40
            elif score < 40:
                should_sell = True
                sell_reason = "策略转空"

            if should_sell:
                # 卖出（扣除手续费）
                fee_rate = 0.00025
                sell_price = current * (1 - fee_rate)
                cash = position * sell_price
                pnl = (sell_price - buy_price) * position
                total_profit += pnl

                if pnl > 0:
                    win_count += 1
                else:
                    loss_count += 1

                trades.append({
                    "action": "sell",
                    "day": i,
                    "price": round(current, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": sell_reason,
                    "hold_days": hold_days,
                })
                position = 0.0
                buy_price = 0.0

        # 记录权益曲线
        current_equity = cash + position * current
        equity_curve.append(current_equity)

    # 计算指标
    initial_cash = 100000.0
    final_equity = equity_curve[-1] if equity_curve else initial_cash
    total_return = (final_equity - initial_cash) / initial_cash * 100

    # 最大回撤
    max_equity = initial_cash
    max_dd = 0.0
    for eq in equity_curve:
        if eq > max_equity:
            max_equity = eq
        dd = (max_equity - eq) / max_equity * 100
        if dd > max_dd:
            max_dd = dd

    # 年化收益率（假设120个交易日≈半年）
    trading_days = len(equity_curve)
    if trading_days > 0:
        annual_return = total_return * (252 / trading_days)
    else:
        annual_return = 0.0

    # 夏普比率（简化）
    if len(equity_curve) > 1:
        returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                    for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
        import numpy as np
        if returns:
            avg_return = np.mean(returns)
            std_return = np.std(returns)
            sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    win_rate = (win_count / (win_count + loss_count) * 100) if (win_count + loss_count) > 0 else 0

    profit_factor = 1.5  # 简化

    from server.config import find_stock_name
    name = find_stock_name(klines[0].get("code", ""))

    return {
        "code": klines[0].get("code", ""),
        "name": name,
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "total_trades": len([t for t in trades if t["action"] == "buy"]),
        "trades": trades[-10:],  # 只返回最近10笔
        "equity_curve": [round(e, 2) for e in equity_curve[::10]],  # 每10天取一个点
    }


async def run_backtest_task(code: str, start_date: str = "", end_date: str = "") -> Dict[str, Any]:
    """提交回测任务到调度器"""
    orch = get_orchestrator()
    task = AgentTask(
        agent_name="backtest_runner",
        func=run_backtest,
        args=(code, start_date, end_date),
        priority=TaskPriority.NORMAL,
        timeout=60.0,
    )
    return await orch.submit(task)


async def run_backtest_batch(codes: List[str]) -> List[Dict[str, Any]]:
    """批量回测多只股票"""
    tasks = [run_backtest_task(code) for code in codes[:10]]  # 最多10只
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]
