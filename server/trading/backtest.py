"""自动交易核心包 - 轻量回测器（纯 numpy，禁止 pandas）

架构蓝图 §7.4：
- 逐日事件循环：strategy.evaluate(code, klines[:i+1], ctx) → 次日开盘价成交（T+1）
- 维护现金/持仓/净值曲线；输出 胜率/年化/最大回撤/总交易次数/盈亏比/净值曲线
- 策略的 evaluate 在实盘与回测中是同一份代码，杜绝"回测一套实盘一套"
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .models import BUY_SIGNAL_TYPES, SELL_SIGNAL_TYPES, DEFAULT_INITIAL_CASH, Position
from .strategies.base import Strategy, StrategyContext

logger = logging.getLogger(__name__)

# 初始本金统一取自 models.DEFAULT_INITIAL_CASH（单一真相源，禁止在本文件再写死 1_000_000）
_INITIAL_CASH = DEFAULT_INITIAL_CASH
_TRADING_DAYS_PER_YEAR = 252


def _strategy_warmup(strategy: Strategy, params: Dict[str, Any]) -> int:
    """预热长度：策略自带 warmup() 或按默认参数估算"""
    warmup_fn = getattr(strategy, "warmup", None)
    if callable(warmup_fn):
        try:
            return max(int(warmup_fn(params)), 2)
        except Exception:
            pass
    return 30


def run_backtest(
    strategy: Strategy,
    code: str,
    klines: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """给定策略 + 历史日K，输出回测指标（不依赖 pandas/quant_core）

    返回：win_rate / annual_return / max_drawdown / total_trades /
          profit_factor / equity_curve([[date, equity], ...])
    """
    params = dict(params or strategy.default_params)
    if not klines or len(klines) < 40:
        raise ValueError(f"K线数据不足（{len(klines or [])} 根），无法回测")

    warmup = _strategy_warmup(strategy, params)
    if warmup >= len(klines) - 2:
        warmup = max(len(klines) // 3, 10)

    cash = _INITIAL_CASH
    shares = 0
    entry_price = 0.0
    entry_date = ""
    trades: List[Dict[str, Any]] = []       # 已完成交易（含盈亏）
    equity_curve: List[List[Any]] = []

    for i in range(warmup, len(klines)):
        window = klines[: i + 1]
        bar = klines[i]
        bar_date = str(bar.get("d", ""))
        try:
            bar_dt = datetime.strptime(bar_date, "%Y-%m-%d")
        except ValueError:
            bar_dt = datetime.now()

        position: Optional[Position] = None
        if shares > 0:
            position = Position(code=code, name=code, qty=shares, avg_cost=entry_price)

        ctx = StrategyContext(quote=None, position=position, params=params, now=bar_dt)
        try:
            sig = strategy.evaluate(code, window, ctx)
        except Exception:
            logger.warning("backtest: evaluate 异常 %s @%s", code, bar_date, exc_info=True)
            sig = None

        # 次日开盘价成交（T+1，贴近 A 股现实）
        if sig is not None and i + 1 < len(klines):
            next_open = float(klines[i + 1].get("o", 0) or 0)
            next_date = str(klines[i + 1].get("d", ""))
            if next_open > 0:
                if sig.signal_type in BUY_SIGNAL_TYPES and shares == 0:
                    qty = math.floor(cash / next_open / 100) * 100
                    if qty > 0:
                        cash -= qty * next_open
                        shares, entry_price, entry_date = qty, next_open, next_date
                elif sig.signal_type in SELL_SIGNAL_TYPES and shares > 0:
                    cash += shares * next_open
                    pnl_pct = (next_open - entry_price) / entry_price if entry_price > 0 else 0.0
                    trades.append({
                        "entry_date": entry_date, "exit_date": next_date,
                        "entry_price": round(entry_price, 3), "exit_price": round(next_open, 3),
                        "qty": shares, "pnl_pct": round(pnl_pct, 4),
                        "exit_reason": sig.signal_type,
                    })
                    shares, entry_price, entry_date = 0, 0.0, ""

        close = float(bar.get("c", 0) or 0)
        equity = cash + shares * close
        equity_curve.append([bar_date, round(equity / _INITIAL_CASH, 4)])

    return compute_metrics(equity_curve, trades)


def compute_metrics(
    equity_curve: List[List[Any]], trades: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """由净值曲线与成交记录计算五项指标（纯 numpy）"""
    if not equity_curve:
        return {
            "win_rate": 0.0, "annual_return": 0.0, "max_drawdown": 0.0,
            "total_trades": 0, "profit_factor": 0.0, "equity_curve": [],
        }

    equities = np.array([float(p[1]) for p in equity_curve])
    n_days = len(equities)

    # 胜率
    wins = [t for t in trades if t["pnl_pct"] > 0]
    win_rate = round(len(wins) / len(trades), 4) if trades else 0.0

    # 年化收益（按 252 个交易日）
    final = float(equities[-1])
    if n_days > 1 and final > 0:
        annual_return = round(float(final ** (_TRADING_DAYS_PER_YEAR / n_days) - 1), 4)
    else:
        annual_return = 0.0

    # 最大回撤（负值表示）
    cummax = np.maximum.accumulate(equities)
    drawdowns = np.where(cummax > 0, (equities - cummax) / cummax, 0.0)
    max_drawdown = round(float(np.min(drawdowns)), 4)

    # 盈亏比：总盈利 / 总亏损（无亏损时记盈利总额）
    gross_win = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = -sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0)
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 2)
    elif gross_win > 0:
        profit_factor = round(gross_win, 2)
    else:
        profit_factor = 0.0

    return {
        "win_rate": win_rate,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "total_trades": len(trades),
        "profit_factor": profit_factor,
        "equity_curve": equity_curve,
    }
