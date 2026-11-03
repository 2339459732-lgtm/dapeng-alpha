"""策略总装：compute_strategy 与买入建议生成"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from server.core.numbers import safe_float as _safe_float

from server.strategies_patterns import (
    bottom_reversal,
    dragon_head_strategy,
    limit_up_strategy,
    ma_bullish_arrangement,
    momentum_reversal,
    strong_pullback,
)
from server.strategies_scoring import (
    Signal,
    StrategyResult,
    _extract_closes,
    _extract_highs,
    _extract_lows,
    _extract_volumes,
    breakout_score,
    compute_atr,
    compute_boll,
    compute_ma_cross,
    compute_max_drawdown,
    compute_rsi,
    compute_volatility,
    momentum_score,
    volume_divergence_score,
)

# ============================================================
# 12. 综合评分 + 信号生成（v4.0 扩展6个战法）
# ============================================================
def compute_strategy(klines: List[Dict[str, Any]]) -> StrategyResult:
    """
    计算单只股票的完整策略结果（v4.0 包含6个经典战法）。

    综合评分 = 原策略(60%) + 6大战法(40%)
    """
    result = StrategyResult()

    if not klines or len(klines) < 5:
        result.total_score = 50
        result.combined_score = 50
        result.momentum_desc = "数据不足"
        result.breakout_desc = "数据不足"
        result.volume_desc = "数据不足"
        return result

    # ===== 原有策略 =====
    # 1. 动量策略
    m_score, m_desc = momentum_score(klines)
    result.momentum_score = m_score
    result.momentum_desc = m_desc

    # 2. 突破策略
    b_score, b_desc = breakout_score(klines)
    result.breakout_score = b_score
    result.breakout_desc = b_desc

    # 3. 量价背离策略
    v_score, v_desc = volume_divergence_score(klines)
    result.volume_score = v_score
    result.volume_desc = v_desc

    # 4. MA 交叉检测
    result.ma_cross = compute_ma_cross(klines)

    # 5. ATR 止损 + 仓位建议
    atr14, stop_loss, position_pct = compute_atr(klines)
    result.atr14 = atr14
    result.stop_loss = stop_loss
    result.position_pct = position_pct

    # 6. 原有综合评分
    total = int(m_score * 0.35 + b_score * 0.3 + v_score * 0.35)
    result.total_score = max(0, min(100, total))

    # 7. RSI 值
    closes_arr = _extract_closes(klines)
    result.rsi = compute_rsi(closes_arr, 14)

    # 8. 趋势评分（基于MA排列）
    if len(closes_arr) >= 20:
        ma5 = float(np.mean(closes_arr[-5:]))
        ma10 = float(np.mean(closes_arr[-10:]))
        ma20 = float(np.mean(closes_arr[-20:]))
        current = float(closes_arr[-1])

        if ma5 > ma10 > ma20 and current > ma5:
            result.trend_score = 90
        elif ma5 > ma10 and current > ma5:
            result.trend_score = 70
        elif ma5 < ma10 < ma20 and current < ma5:
            result.trend_score = 15
        elif ma5 < ma10 and current < ma5:
            result.trend_score = 30
        else:
            result.trend_score = 50
    else:
        result.trend_score = 50

    # 9. 风险评分（基于波动率和回撤）
    max_dd = compute_max_drawdown(klines)
    vol = compute_volatility(klines)
    if max_dd > 30:
        result.risk_score = 15
    elif max_dd > 20:
        result.risk_score = 30
    elif max_dd > 10:
        result.risk_score = 50
    elif max_dd > 5:
        result.risk_score = 70
    else:
        result.risk_score = 85

    # ===== v4.0 新增：6 个经典战法 =====
    # 10. 均线多头排列
    ma_bull_score, ma_bull_desc = ma_bullish_arrangement(klines)
    result.ma_bullish_score = ma_bull_score

    # 11. 强势股回调
    pb_score, pb_desc = strong_pullback(klines)
    result.pullback_score = pb_score

    # 12. 涨停板战法
    lu_score, lu_desc = limit_up_strategy(klines)
    result.limit_up_score = lu_score

    # 13. 底部放量反转
    br_score, br_desc = bottom_reversal(klines)
    result.bottom_reversal_score = br_score

    # 14. 龙头战法
    dh_score, dh_desc = dragon_head_strategy(klines)
    result.dragon_head_score = dh_score

    # 15. 动量反转
    mr_score, mr_desc = momentum_reversal(klines)
    result.momentum_reversal_score = mr_score

    # ===== 综合评分（combined_score）=====
    # 原有策略(60%) + 6大战法(40%)
    original_weight = 0.6
    new_tactics_weight = 0.4

    # 6大战法平均分
    tactics_avg = (ma_bull_score + pb_score + lu_score + br_score + dh_score + mr_score) / 6

    combined = int(result.total_score * original_weight + tactics_avg * new_tactics_weight)
    result.combined_score = max(0, min(100, combined))

    # ===== 生成信号列表 =====
    signals = []

    if m_score >= 70:
        signals.append(Signal(
            type="momentum", label="动量", score=m_score, desc=m_desc,
        ))

    if b_score >= 70:
        signals.append(Signal(
            type="breakout", label="突破", score=b_score, desc=b_desc,
        ))

    if v_score >= 65:
        signals.append(Signal(
            type="volume_divergence", label="量价", score=v_score, desc=v_desc,
        ))

    if result.ma_cross == "gold":
        signals.append(Signal(
            type="ma_cross_gold", label="金叉", score=90, desc="MA5上穿MA10",
        ))
    elif result.ma_cross == "dead":
        signals.append(Signal(
            type="ma_cross_dead", label="死叉", score=15, desc="MA5下穿MA10",
        ))

    # 新增战法信号
    if ma_bull_score >= 70:
        signals.append(Signal(
            type="ma_bullish", label="多头排列", score=ma_bull_score, desc=ma_bull_desc,
        ))

    if pb_score >= 70:
        signals.append(Signal(
            type="pullback", label="强势回调", score=pb_score, desc=pb_desc,
        ))

    if lu_score >= 70:
        signals.append(Signal(
            type="limit_up", label="涨停战法", score=lu_score, desc=lu_desc,
        ))

    if br_score >= 70:
        signals.append(Signal(
            type="bottom_reversal", label="底部反转", score=br_score, desc=br_desc,
        ))

    if dh_score >= 70:
        signals.append(Signal(
            type="dragon_head", label="龙头战法", score=dh_score, desc=dh_desc,
        ))

    if mr_score >= 70:
        signals.append(Signal(
            type="momentum_reversal", label="动量反转", score=mr_score, desc=mr_desc,
        ))

    result.signals = signals

    return result


# ============================================================
# 18. 买入推荐生成
# ============================================================
def generate_buy_recommendation(strategy_result: StrategyResult, klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据策略结果和K线数据生成买入建议

    计算内容：
    - 买入区间（基于支撑位、布林带下轨、ATR止损）
    - 止损位
    - 第一/二止盈位
    - 推荐理由文字

    Returns:
        {
            "buy_zone": [low, high],
            "stop_loss": float,
            "stop_profit_1": float,
            "stop_profit_2": float,
            "reason": str,
        }
    """
    closes = _extract_closes(klines)
    highs = _extract_highs(klines)
    lows = _extract_lows(klines)

    # 默认值
    result = {
        "buy_zone": [0.0, 0.0],
        "stop_loss": 0.0,
        "stop_profit_1": 0.0,
        "stop_profit_2": 0.0,
        "reason": "数据不足",
    }

    if len(closes) < 5:
        return result

    current = float(closes[-1])
    if current <= 0:
        return result

    # ========== 1. 计算买入区间 ==========
    # 支撑位附近3%作为买入下限
    buy_zone_low = round(current * 0.97, 2)
    buy_zone_high = round(current * 1.01, 2)

    # 布林带中轨辅助
    boll = compute_boll(klines)
    if boll["mid"] > 0 and boll["mid"] < current:
        buy_zone_low = round(min(boll["mid"], buy_zone_low), 2)

    # 布林带下轨辅助
    if boll["lower"] > 0 and boll["lower"] < current:
        buy_zone_low = round(max(boll["lower"] * 0.98, buy_zone_low), 2)

    # 20日最低价辅助
    if len(lows) >= 20:
        recent_low = float(np.min(lows[-20:]))
        if recent_low < current and recent_low > buy_zone_low * 0.9:
            buy_zone_low = round(max(recent_low * 1.01, buy_zone_low), 2)

    buy_zone = [buy_zone_low, buy_zone_high]

    # ========== 2. 止损位 ==========
    atr14 = strategy_result.atr14
    stop_loss = strategy_result.stop_loss
    if stop_loss <= 0 and atr14 > 0:
        stop_loss = round(current - 2 * atr14, 2)

    # ========== 3. 止盈位 ==========
    stop_profit_1 = 0.0
    stop_profit_2 = 0.0

    if boll["upper"] > 0:
        stop_profit_1 = round(boll["upper"], 2)
    else:
        stop_profit_1 = round(current * 1.05, 2)

    stop_profit_2 = round(current * 1.10, 2)

    # 如果有阻力位，以阻力位作为止盈参考
    if len(highs) >= 20:
        recent_high = float(np.max(highs[-20:]))
        if recent_high > current:
            stop_profit_1 = round(min(recent_high, stop_profit_1), 2)

    # ========== 4. 生成推荐理由 ==========
    total_score = strategy_result.total_score
    signals = strategy_result.signals
    signal_labels = [s.label for s in signals]

    # 基础评分描述
    if total_score >= 80:
        score_desc = f"策略评分{total_score}分，表现优异"
    elif total_score >= 70:
        score_desc = f"策略评分{total_score}分，表现良好"
    else:
        score_desc = f"策略评分{total_score}分，表现尚可"

    # 动量描述
    momentum_desc = ""
    if strategy_result.momentum_score >= 70:
        momentum_desc = "动量强劲"
    elif strategy_result.momentum_score >= 55:
        momentum_desc = "动量回升"

    # 突破描述
    breakout_desc = ""
    if strategy_result.breakout_score >= 70:
        breakout_desc = "突破确认"
    elif strategy_result.breakout_score >= 55:
        breakout_desc = "蓄势待发"

    # 量价描述
    volume_desc = ""
    if strategy_result.volume_score >= 65:
        volume_desc = "量价配合良好"
    elif strategy_result.volume_score >= 50:
        volume_desc = "量能平稳"

    # MA交叉描述
    ma_desc = ""
    if strategy_result.ma_cross == "gold":
        ma_desc = "MA5上穿MA10形成金叉"
    elif strategy_result.ma_cross == "dead":
        ma_desc = "MA5下穿MA10形成死叉"

    # RSI描述
    rsi = strategy_result.rsi
    rsi_desc = ""
    if rsi is not None:
        if rsi < 30:
            rsi_desc = f"RSI从超卖区回升至{rsi:.0f}"
        elif rsi < 45:
            rsi_desc = f"RSI回升至{rsi:.0f}"
        elif rsi > 70:
            rsi_desc = f"RSI达{rsi:.0f}进入超买区"

    # 量比描述
    volumes = _extract_volumes(klines)
    vol_ratio_desc = ""
    if len(volumes) >= 5:
        avg_vol_4 = float(np.mean(volumes[-5:-1]))
        last_vol = float(volumes[-1])
        if avg_vol_4 > 0:
            vol_ratio = last_vol / avg_vol_4
            if vol_ratio > 1.5:
                vol_ratio_desc = f"成交量放大{vol_ratio:.1f}倍"
            elif vol_ratio > 1.2:
                vol_ratio_desc = f"成交量温和放大{vol_ratio:.1f}倍"

    # 组合理由
    reason_parts = [score_desc]
    for desc in [momentum_desc, breakout_desc, volume_desc]:
        if desc:
            reason_parts.append(desc)

    for sig in signals:
        if sig.desc and sig.desc not in reason_parts:
            reason_parts.append(sig.desc)

    if ma_desc:
        reason_parts.append(ma_desc)
    if vol_ratio_desc:
        reason_parts.append(vol_ratio_desc)
    if rsi_desc:
        reason_parts.append(rsi_desc)

    # 买入操作建议
    if total_score >= 80:
        action_desc = f"建议在{buy_zone_low}-{buy_zone_high}区间积极建仓，止损{stop_loss:.2f}。"
    elif total_score >= 70:
        action_desc = f"建议在{buy_zone_low}-{buy_zone_high}区间分批建仓，止损{stop_loss:.2f}。"
    else:
        action_desc = f"建议在{buy_zone_low}-{buy_zone_high}区间轻仓试探，止损{stop_loss:.2f}。"

    reason = "，".join(reason_parts) + "。" + action_desc

    return {
        "buy_zone": buy_zone,
        "stop_loss": round(stop_loss, 2),
        "stop_profit_1": stop_profit_1,
        "stop_profit_2": stop_profit_2,
        "reason": reason,
    }
