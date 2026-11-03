"""DAPENG Alpha 策略实验室。

设计参考：
- InStock（Apache-2.0）：指标、形态与策略验证的模块化思路；
- HoraceQuant：FastAPI + ECharts 的前后端解耦与策略结果展示；
- KLinePlayground（MIT）：复盘/模拟交易优先，避免直接真实下单。

本模块为原创实现：仅复用本项目已有 K 线数据，提供本地、可解释、无账户依赖的策略评估。
"""
from __future__ import annotations

import asyncio
from math import sqrt
from statistics import mean
from typing import Any, Callable, Dict, List, Tuple

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import CACHE_TTL_KLINE, KLINE_LIMIT, find_stock_name
from server.strategies import compute_atr, compute_macd, compute_rsi
from server.tencent_kline import fetch_tencent_kline

router = APIRouter(prefix="/api/strategy-lab", tags=["strategy-lab"])


def _num(bar: Dict[str, Any], key: str) -> float:
    try:
        return float(bar.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _sma(values: List[float], end: int, length: int) -> float | None:
    if end + 1 < length:
        return None
    window = values[end - length + 1:end + 1]
    return sum(window) / length if window else None


def _max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    peak, drawdown = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _build_metrics(returns: List[float], trades: int, label: str, description: str) -> Dict[str, Any]:
    if not returns:
        return {
            "key": label, "label": label, "description": description,
            "trades": 0, "win_rate": 0.0, "total_return": 0.0,
            "max_drawdown": 0.0, "profit_factor": 0.0, "sharpe": 0.0,
            "grade": "数据不足", "equity": [100.0],
        }

    equity = [100.0]
    for ret in returns:
        equity.append(equity[-1] * (1 + ret))
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    avg = mean(returns)
    variance = mean([(r - avg) ** 2 for r in returns]) if len(returns) > 1 else 0.0
    std = sqrt(variance)
    sharpe = (avg / std * sqrt(252 / max(1, len(returns)))) if std else 0.0
    total_return = equity[-1] / 100.0 - 1
    drawdown = _max_drawdown(equity)
    score = total_return * 100 - drawdown * 60 + (len(wins) / len(returns)) * 20
    grade = "可观察" if score >= 8 else "中性" if score >= -5 else "谨慎"
    return {
        "key": label,
        "label": label,
        "description": description,
        "trades": trades,
        "win_rate": round(len(wins) / len(returns) * 100, 1),
        "total_return": round(total_return * 100, 2),
        "max_drawdown": round(drawdown * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (99.0 if gross_profit else 0.0),
        "sharpe": round(sharpe, 2),
        "grade": grade,
        "equity": [round(x, 2) for x in equity[-80:]],
    }


def _evaluate_trend(closes: List[float]) -> Dict[str, Any]:
    returns, trades = [], 0
    position = False
    entry = 0.0
    for i in range(60, len(closes)):
        ma20, ma60 = _sma(closes, i, 20), _sma(closes, i, 60)
        prev20, prev60 = _sma(closes, i - 1, 20), _sma(closes, i - 1, 60)
        if not all(v is not None for v in (ma20, ma60, prev20, prev60)):
            continue
        cross_up = prev20 <= prev60 and ma20 > ma60
        cross_down = prev20 >= prev60 and ma20 < ma60
        if not position and cross_up:
            position, entry = True, closes[i]
        elif position and cross_down:
            returns.append(closes[i] / entry - 1)
            trades += 1
            position = False
    if position and entry:
        returns.append(closes[-1] / entry - 1)
        trades += 1
    return _build_metrics(returns, trades, "趋势跟随", "MA20/MA60 金叉入场、死叉离场")


def _evaluate_breakout(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    returns, trades = [], 0
    position, entry = False, 0.0
    for i in range(21, len(closes)):
        prior_high = max(closes[i - 20:i])
        avg_volume = mean(volumes[i - 5:i]) if any(volumes[i - 5:i]) else 0.0
        breakout = closes[i] > prior_high and (not avg_volume or volumes[i] >= avg_volume * 1.2)
        exit_signal = position and closes[i] < _sma(closes, i, 10)
        if not position and breakout:
            position, entry = True, closes[i]
        elif exit_signal:
            returns.append(closes[i] / entry - 1)
            trades += 1
            position = False
    if position and entry:
        returns.append(closes[-1] / entry - 1)
        trades += 1
    return _build_metrics(returns, trades, "放量突破", "20 日新高且量能高于 5 日均量 20%")


def _evaluate_reversion(closes: List[float]) -> Dict[str, Any]:
    returns, trades = [], 0
    position, entry = False, 0.0
    for i in range(20, len(closes)):
        ma20 = _sma(closes, i, 20)
        if not ma20:
            continue
        deviation = closes[i] / ma20 - 1
        if not position and deviation <= -0.06:
            position, entry = True, closes[i]
        elif position and (closes[i] >= ma20 or closes[i] / entry - 1 <= -0.08):
            returns.append(closes[i] / entry - 1)
            trades += 1
            position = False
    if position and entry:
        returns.append(closes[-1] / entry - 1)
        trades += 1
    return _build_metrics(returns, trades, "均值回归", "价格偏离 MA20 6% 入场，回归均线或 8% 风控离场")


def _market_regime(closes: List[float]) -> Dict[str, str]:
    if len(closes) < 60:
        return {"label": "数据不足", "tone": "neutral", "detail": "K 线不足 60 根，暂不建议执行策略。"}
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:])
    last = closes[-1]
    if last > ma20 > ma60:
        return {"label": "趋势环境", "tone": "bullish", "detail": "价格位于 MA20/MA60 上方，可优先观察趋势与突破策略。"}
    if last < ma20 < ma60:
        return {"label": "防御环境", "tone": "bearish", "detail": "价格位于 MA20/MA60 下方，建议降低仓位或暂停新增交易。"}
    return {"label": "震荡环境", "tone": "neutral", "detail": "均线未形成一致方向，策略信号应降低置信度。"}


@router.get("/evaluate")
async def evaluate_strategy_lab(code: str = Query(..., min_length=3), period: str = Query("day")):
    """对当前个股的三类规则策略做轻量级历史评估。"""
    safe_period = period if period in {"day", "week"} else "day"
    cache_key = f"strategy_lab:{code}:{safe_period}"

    async def fetch_and_evaluate():
        raw_key = f"raw_kline:{code}:{safe_period}"
        klines = cache.get(raw_key)
        if not klines:
            klines = await fetch_tencent_kline(code, period=safe_period, limit=KLINE_LIMIT)
            if klines:
                cache.set(raw_key, klines, CACHE_TTL_KLINE)
        if not klines or len(klines) < 65:
            return {"code": code, "name": find_stock_name(code), "period": safe_period, "bars": len(klines or []), "strategies": [], "regime": {"label": "数据不足", "tone": "neutral", "detail": "暂未取得足够 K 线数据。"}, "risk": {"atr": 0, "stop_loss": 0, "position_pct": 0}}

        closes = [_num(k, "c") for k in klines]
        volumes = [_num(k, "v") for k in klines]
        valid = [(c, v) for c, v in zip(closes, volumes) if c > 0]
        closes = [c for c, _ in valid]
        volumes = [v for _, v in valid]
        metrics = [_evaluate_trend(closes), _evaluate_breakout(closes, volumes), _evaluate_reversion(closes)]
        atr, stop_loss, position_pct = compute_atr(klines)
        macd = compute_macd(klines)
        rsi = compute_rsi(__import__("numpy").array(closes))
        return {
            "code": code,
            "name": find_stock_name(code),
            "period": safe_period,
            "bars": len(closes),
            "strategies": metrics,
            "regime": _market_regime(closes),
            "risk": {"atr": round(atr, 3), "stop_loss": round(stop_loss, 2), "position_pct": position_pct, "rsi": round(rsi, 1) if rsi is not None else None, "macd_hist": round((macd.get("hist") or [0])[-1], 4)},
            "disclaimer": "历史规则模拟不代表未来收益；结果未计入滑点、涨跌停与流动性约束。",
        }

    data = await cache.get_or_fetch(cache_key, fetch_and_evaluate, 300)
    return {"ok": True, "data": data}
