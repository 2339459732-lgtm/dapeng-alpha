"""DAPENG Alpha v4.0 - 分析 Agent

调用 LLMClient (DeepSeek V4 Pro) 对个股进行深度分析。
输入：股票代码 + K线数据 + 策略评分
输出：趋势判断、操作建议、目标价位、止损价位
"""

import logging
from typing import Any, Dict, List, Optional

from server.agents import AgentStatus, update_agent_status
from server.llm import get_llm_client

logger = logging.getLogger(__name__)

# 单次分析超时（秒）
ANALYSIS_TIMEOUT = 30


async def analyze_stock(
    code: str,
    name: str,
    klines: List[Dict[str, Any]],
    strategy_score: int,
    signals: List[Any],
) -> Optional[Dict[str, Any]]:
    """
    对单只股票进行 AI 深度分析

    Args:
        code: 股票代码 (sh600519)
        name: 股票名称
        klines: K线数据（来自 westock）
        strategy_score: 策略综合评分 (0-100)
        signals: 触发信号列表

    Returns:
        {
            "summary": str,
            "trend": str,
            "suggestion": str,
            "risk": str,
            "target_price": {"low": float, "high": float},
            "stop_loss": float,
        }
    """
    update_agent_status("stock_analyzer", AgentStatus.RUNNING)

    if not klines or len(klines) < 20:
        logger.warning("analyze_stock: K线数据不足 %s", code)
        update_agent_status("stock_analyzer", AgentStatus.IDLE)
        return None

    try:
        # 提取最新行情数据
        last = klines[-1]
        prev = klines[-2] if len(klines) >= 2 else last

        current_price = float(last.get("c", 0))
        prev_close = float(prev.get("c", current_price))
        change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0.0
        volume = float(last.get("v", 0))

        # 计算 MA
        closes = [float(k.get("c", 0)) for k in klines[-20:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20

        # 计算标准 EMA 递推 MACD（默认 12/26/9）
        dif, dea, hist = _calc_macd(klines)
        rsi = _calc_rsi(klines)
        k, d, j = _calc_kdj(klines)

        # 格式化信号
        signal_labels = []
        for s in signals:
            if isinstance(s, dict):
                signal_labels.append(s.get("label", ""))
            elif hasattr(s, "label"):
                signal_labels.append(s.label)
        signals_desc = "；".join(signal_labels) if signal_labels else "无"

        # 调用 LLM
        llm_client = get_llm_client()
        result = await llm_client.generate_stock_analysis(
            code=code,
            name=name,
            current_price=current_price,
            change_pct=change_pct,
            volume=volume,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            macd_dif=dif,
            macd_dea=dea,
            macd_hist=hist,
            rsi=rsi,
            kdj_k=k,
            kdj_d=d,
            kdj_j=j,
            strategy_score=strategy_score,
            signals=signals,
        )

        if result:
            logger.info("stock_analyzer: %s 分析完成", code)
            update_agent_status("stock_analyzer", AgentStatus.IDLE)
            return result
        else:
            logger.warning("stock_analyzer: %s LLM返回为空", code)
            update_agent_status("stock_analyzer", AgentStatus.IDLE)
            return None

    except Exception as e:
        logger.error("stock_analyzer error for %s: %s", code, str(e), exc_info=True)
        update_agent_status("stock_analyzer", AgentStatus.ERROR, error_msg=str(e))
        return None


def _calc_macd(klines: List[Dict], fast=12, slow=26, signal=9) -> tuple:
    """使用完整K线历史按EMA递推计算标准MACD的最新 DIF、DEA 与柱值。"""
    try:
        closes = [float(k.get("c", 0)) for k in klines if float(k.get("c", 0)) > 0]
        if len(closes) < slow + signal:
            return 0.0, 0.0, 0.0
        fast_line = _ema_series(closes, fast)
        slow_line = _ema_series(closes, slow)
        dif_line = [a - b for a, b in zip(fast_line, slow_line)]
        dea_line = _ema_series(dif_line, signal)
        dif, dea = dif_line[-1], dea_line[-1]
        return round(dif, 4), round(dea, 4), round((dif - dea) * 2, 4)
    except (TypeError, ValueError, OverflowError):
        return 0.0, 0.0, 0.0


def _ema_series(values: List[float], period: int) -> List[float]:
    """返回完整EMA序列，首值使用首个样本并逐项递推。"""
    if not values or period <= 0:
        return []
    alpha = 2 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * alpha + output[-1] * (1 - alpha))
    return output


def _ema(prices: List[float], period: int) -> float:
    """兼容旧调用的最新EMA值。"""
    values = _ema_series(prices, period)
    return values[-1] if values else 0.0


def _calc_rsi(klines: List[Dict], period=14) -> float:
    """RSI 计算"""
    try:
        closes = [float(k.get("c", 0)) for k in klines[-period - 1:]]
        if len(closes) < period + 1:
            return 50.0

        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)
    except Exception:
        return 50.0


def _calc_kdj(klines: List[Dict], period=9) -> tuple:
    """按常用 9,3,3 平滑公式计算最新 K/D/J。"""
    try:
        if period <= 0 or len(klines) < period:
            return 50.0, 50.0, 50.0
        k_value = d_value = 50.0
        for index in range(period - 1, len(klines)):
            window = klines[index - period + 1:index + 1]
            highs = [float(item.get("h", 0)) for item in window]
            lows = [float(item.get("l", 0)) for item in window]
            close = float(klines[index].get("c", 0))
            high, low = max(highs), min(lows)
            rsv = 50.0 if high <= low else (close - low) / (high - low) * 100
            k_value = (2 * k_value + rsv) / 3
            d_value = (2 * d_value + k_value) / 3
        j_value = 3 * k_value - 2 * d_value
        return round(k_value, 1), round(d_value, 1), round(j_value, 1)
    except (TypeError, ValueError, OverflowError):
        return 50.0, 50.0, 50.0
