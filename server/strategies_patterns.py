"""形态与战法（均线 / MACD / 多周期 / 量价 / 涨停 / 龙头 / 反转）"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from server.core.numbers import safe_float as _safe_float

from server.strategies_scoring import (
    _extract_closes,
    _extract_highs,
    _extract_lows,
    _extract_opens,
    _extract_volumes,
    compute_rsi,
)

# ============================================================
# 13. MA 计算（供 K 线接口使用）
# ============================================================
def compute_ma(klines: List[Dict[str, Any]], periods: List[int] = None) -> Dict[str, List[float]]:
    """
    计算 MA 均线。

    返回 {"ma5": [...], "ma10": [...], "ma20": [...]}
    """
    if periods is None:
        periods = [5, 10, 20]

    closes = _extract_closes(klines)
    result = {}

    for period in periods:
        key = f"ma{period}"
        ma_values = []
        for i in range(len(closes)):
            if i < period - 1:
                ma_values.append(0.0)
            else:
                ma_values.append(round(float(np.mean(closes[i - period + 1:i + 1])), 2))
        result[key] = ma_values

    return result


# ============================================================
# 14. MACD 计算（供 K 线接口使用）
# ============================================================
def compute_macd(klines: List[Dict[str, Any]],
                 fast: int = 12,
                 slow: int = 26,
                 signal: int = 9) -> Dict[str, List[float]]:
    """
    计算 MACD 指标。

    返回 {"dif": [...], "dea": [...], "hist": [...]}
    """
    closes = _extract_closes(klines)
    if len(closes) < slow:
        return {"dif": [], "dea": [], "hist": []}

    # EMA 计算
    def ema(data: np.ndarray, n: int) -> np.ndarray:
        """计算 EMA"""
        k = 2.0 / (n + 1)
        result = np.zeros_like(data)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = data[i] * k + result[i - 1] * (1 - k)
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    hist = (dif - dea) * 2

    return {
        "dif": [round(float(v), 4) for v in dif],
        "dea": [round(float(v), 4) for v in dea],
        "hist": [round(float(v), 4) for v in hist],
    }


# ============================================================
# 15. 多时间框架趋势分析
# ============================================================
def compute_multi_timeframe(klines: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    计算5日/20日/60日趋势对比，给出短中长期趋势判断。

    返回 {short, mid, long, overall}
    """
    closes = _extract_closes(klines)
    result = {
        "short": "中性",
        "mid": "中性",
        "long": "中性",
        "overall": "中性",
    }

    if len(closes) < 5:
        return result

    # 短期趋势（5日）
    chg_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 and closes[-6] != 0 else 0
    if chg_5 > 3:
        result["short"] = "强势上涨"
    elif chg_5 > 0:
        result["short"] = "偏多震荡"
    elif chg_5 > -3:
        result["short"] = "偏弱震荡"
    else:
        result["short"] = "弱势下跌"

    # 中期趋势（20日）
    if len(closes) >= 21 and closes[-21] != 0:
        chg_20 = (closes[-1] - closes[-21]) / closes[-21] * 100
        if chg_20 > 8:
            result["mid"] = "中期上涨"
        elif chg_20 > 0:
            result["mid"] = "中期偏多"
        elif chg_20 > -8:
            result["mid"] = "中期偏空"
        else:
            result["mid"] = "中期下跌"
    else:
        result["mid"] = "数据不足"

    # 长期趋势（60日）
    if len(closes) >= 61 and closes[-61] != 0:
        chg_60 = (closes[-1] - closes[-61]) / closes[-61] * 100
        if chg_60 > 15:
            result["long"] = "长期牛市"
        elif chg_60 > 0:
            result["long"] = "长期偏多"
        elif chg_60 > -15:
            result["long"] = "长期偏空"
        else:
            result["long"] = "长期熊市"
    else:
        result["long"] = "数据不足"

    # 综合趋势判断
    bullish_count = sum(1 for v in [result["short"], result["mid"], result["long"]] if "涨" in v or "多" in v or "牛" in v)
    bearish_count = sum(1 for v in [result["short"], result["mid"], result["long"]] if "跌" in v or "空" in v or "熊" in v)

    if bullish_count >= 2:
        result["overall"] = "多头趋势"
    elif bearish_count >= 2:
        result["overall"] = "空头趋势"
    else:
        result["overall"] = "震荡趋势"

    return result


# ============================================================
# 16. MACD 详细分析
# ============================================================
def compute_macd_detail(klines: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    MACD 详细分析：金叉/死叉趋势、红绿柱变化趋势、零轴位置。

    返回 {trend, zero_axis, signal}
    """
    macd_data = compute_macd(klines)
    dif = macd_data.get("dif", [])
    dea = macd_data.get("dea", [])
    hist = macd_data.get("hist", [])

    result = {
        "trend": "中性",
        "zero_axis": "零轴附近",
        "signal": "无明显信号",
    }

    if len(dif) < 3 or len(hist) < 3:
        return result

    # 1. 金叉/死叉趋势
    last_dif = dif[-1]
    last_dea = dea[-1]
    prev_dif = dif[-2]
    prev_dea = dea[-2]

    if prev_dif <= prev_dea and last_dif > last_dea:
        result["trend"] = "MACD金叉"
    elif prev_dif >= prev_dea and last_dif < last_dea:
        result["trend"] = "MACD死叉"
    elif last_dif > last_dea:
        result["trend"] = "DIF在DEA上方"
    else:
        result["trend"] = "DIF在DEA下方"

    # 2. 零轴位置
    if last_dif > 0 and last_dea > 0:
        result["zero_axis"] = "零轴上方，多头市场"
    elif last_dif < 0 and last_dea < 0:
        result["zero_axis"] = "零轴下方，空头市场"
    elif last_dif > 0 and last_dea < 0:
        result["zero_axis"] = "DIF上穿零轴中"
    else:
        result["zero_axis"] = "DIF下穿零轴中"

    # 3. 红绿柱变化趋势
    recent_hist = hist[-5:]
    if len(recent_hist) >= 3:
        # 红柱放大/缩小
        if all(h >= 0 for h in recent_hist[-3:]) and recent_hist[-1] > recent_hist[-2]:
            result["signal"] = "红柱放大，多头增强"
        elif all(h >= 0 for h in recent_hist[-3:]) and recent_hist[-1] < recent_hist[-2]:
            result["signal"] = "红柱缩小，多头减弱"
        elif all(h <= 0 for h in recent_hist[-3:]) and recent_hist[-1] < recent_hist[-2]:
            result["signal"] = "绿柱放大，空头增强"
        elif all(h <= 0 for h in recent_hist[-3:]) and recent_hist[-1] > recent_hist[-2]:
            result["signal"] = "绿柱缩小，空头减弱"
        elif recent_hist[-2] <= 0 and recent_hist[-1] > 0:
            result["signal"] = "绿转红，趋势转多"
        elif recent_hist[-2] >= 0 and recent_hist[-1] < 0:
            result["signal"] = "红转绿，趋势转空"

    return result


# ============================================================
# 17. 量价关系多维度分析
# ============================================================
def compute_volume_detail(klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    5日/10日量比、连续缩量/放量检测、量价背离强化。

    返回 {vol_ratio_5, vol_ratio_10, streak, divergence, signal}
    """
    volumes = _extract_volumes(klines)
    closes = _extract_closes(klines)

    result = {
        "vol_ratio_5": 1.0,
        "vol_ratio_10": 1.0,
        "streak": "无连续异动",
        "divergence": "无背离",
        "signal": "量价正常",
    }

    if len(volumes) < 11:
        return result

    # 5日量比
    avg_vol_5 = float(np.mean(volumes[-6:-1]))
    if avg_vol_5 > 0:
        result["vol_ratio_5"] = round(float(volumes[-1]) / avg_vol_5, 2)

    # 10日量比
    avg_vol_10 = float(np.mean(volumes[-11:-1]))
    if avg_vol_10 > 0:
        result["vol_ratio_10"] = round(float(volumes[-1]) / avg_vol_10, 2)

    # 连续缩量/放量检测
    recent_vols = volumes[-5:]
    vol_changes = np.diff(recent_vols)

    consecutive_up = 0
    consecutive_down = 0
    for vc in reversed(vol_changes):
        if vc > 0:
            consecutive_up += 1
        else:
            break
    for vc in reversed(vol_changes):
        if vc < 0:
            consecutive_down += 1
        else:
            break

    if consecutive_up >= 3:
        result["streak"] = f"连续{consecutive_up + 1}日放量"
    elif consecutive_down >= 3:
        result["streak"] = f"连续{consecutive_down + 1}日缩量"

    # 量价背离强化
    price_chg_5 = float(closes[-1] - closes[-6])
    vol_chg_5 = float(np.sum(volumes[-5:]) - np.sum(volumes[-10:-5]))

    if price_chg_5 > 0 and vol_chg_5 < 0:
        result["divergence"] = "价涨量缩，顶背离风险"
        result["signal"] = "量价顶背离"
    elif price_chg_5 < 0 and vol_chg_5 > 0:
        result["divergence"] = "价跌量增，底背离可能"
        result["signal"] = "量价底背离"
    elif price_chg_5 > 0 and vol_chg_5 > 0:
        result["divergence"] = "量价齐升，趋势健康"
        result["signal"] = "量价配合良好"
    elif price_chg_5 < 0 and vol_chg_5 < 0:
        result["divergence"] = "量缩价跌，下跌动能减弱"
        result["signal"] = "缩量下跌"

    return result


# ============================================================
# 19. 均线多头排列策略
# ============================================================
def ma_bullish_arrangement(klines: List[Dict[str, Any]]) -> tuple:
    """
    均线多头排列：5日>10日>20日>60日均线，均线向上发散
    
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    if len(closes) < 60:
        return 50, "数据不足（需要60日数据）"
    
    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:]))
    
    # 检查多头排列
    is_bullish = ma5 > ma10 > ma20 > ma60
    
    # 检查均线方向（短期均线是否向上）
    ma5_prev = float(np.mean(closes[-6:-1])) if len(closes) >= 6 else ma5
    ma10_prev = float(np.mean(closes[-11:-1])) if len(closes) >= 11 else ma10
    
    ma5_up = ma5 > ma5_prev
    ma10_up = ma10 > ma10_prev
    
    if is_bullish:
        score = 90
        if ma5_up and ma10_up:
            score = 95
            desc = "完美多头排列，均线向上发散"
        else:
            desc = "多头排列，但短期均线方向待确认"
    elif ma5 > ma10 > ma20:
        score = 75
        desc = "中短期多头排列"
    elif ma5 > ma10:
        score = 60
        desc = "短期均线多头"
    else:
        score = 30
        desc = "均线空头排列"
    
    return score, desc


# ============================================================
# 20. 强势股回调策略
# ============================================================
def strong_pullback(klines: List[Dict[str, Any]]) -> tuple:
    """
    强势股回调：近10日涨幅>15%，回调至10日均线附近（±3%），成交量萎缩
    
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    volumes = _extract_volumes(klines)
    
    if len(closes) < 10:
        return 50, "数据不足"
    
    # 近10日涨幅
    chg_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 and closes[-11] != 0 else 0
    
    # 当前价与10日均线距离
    ma10 = float(np.mean(closes[-10:]))
    price_to_ma10 = (closes[-1] - ma10) / ma10 * 100 if ma10 != 0 else 0
    
    # 成交量萎缩检测
    avg_vol_5 = float(np.mean(volumes[-6:-1])) if len(volumes) >= 6 else volumes[-1]
    last_vol = float(volumes[-1])
    vol_contraction = last_vol < avg_vol_5 * 0.8 if avg_vol_5 > 0 else False
    
    # 评分逻辑
    if chg_10 > 15 and abs(price_to_ma10) < 3 and vol_contraction:
        score = 85
        desc = f"强势股回调至MA10附近（偏离{price_to_ma10:.1f}%），成交量萎缩"
    elif chg_10 > 10 and abs(price_to_ma10) < 5:
        score = 70
        desc = f"股票强势，回调至MA10附近（偏离{price_to_ma10:.1f}%）"
    elif chg_10 > 5:
        score = 55
        desc = "近期上涨，但未达强势标准"
    else:
        score = 35
        desc = "近期涨幅不足，非强势股"
    
    return score, desc


# ============================================================
# 21. 涨停板战法
# ============================================================
def limit_up_strategy(klines: List[Dict[str, Any]]) -> tuple:
    """
    涨停板战法：昨日涨停，今日高开低走或低开高走，判断接力意愿
    
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    opens = _extract_opens(klines)
    
    if len(closes) < 2:
        return 50, "数据不足"
    
    # 昨日涨幅（近似涨停=+10%左右，创业板/科创板+20%）
    prev_chg = (closes[-2] - closes[-3]) / closes[-3] * 100 if len(closes) >= 3 and closes[-3] != 0 else 0
    
    # 今日开盘跳空
    today_gap = (opens[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] != 0 else 0
    
    # 今日走势
    today_chg = (closes[-1] - opens[-1]) / opens[-1] * 100 if opens[-1] != 0 else 0
    
    # 判断是否涨停
    is_limit_up = abs(prev_chg - 10) < 1 or abs(prev_chg - 20) < 1  # 主板10%，创业板20%
    
    if not is_limit_up:
        return 50, "昨日未涨停"
    
    # 今日走势分析
    if today_gap > 3 and today_chg > 0:
        score = 90
        desc = f"涨停板后高开高走（+{today_gap:.1f}%），接力意愿强"
    elif today_gap > 0 and today_chg > 0:
        score = 75
        desc = f"涨停板后平开高走，有接力迹象"
    elif today_gap < 0 and today_chg > 0:
        score = 60
        desc = f"涨停板后低开高走，消化获利盘"
    elif today_gap > 5 and today_chg < 0:
        score = 25
        desc = f"涨停板后高开低走，抛压较重"
    else:
        score = 45
        desc = f"涨停板后走势震荡，方向不明"
    
    return score, desc


# ============================================================
# 22. 底部放量反转策略
# ============================================================
def bottom_reversal(klines: List[Dict[str, Any]]) -> tuple:
    """
    底部放量反转：股价在60日低位，成交量突破60日均量2倍，MACD金叉
    
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    volumes = _extract_volumes(klines)
    highs = _extract_highs(klines)
    lows = _extract_lows(klines)
    
    if len(closes) < 60:
        return 50, "数据不足（需要60日数据）"
    
    # 是否在60日低位（当前价在60日最低价的5%以内）
    low_60 = float(np.min(lows[-60:])) if len(lows) >= 60 else lows[-1]
    is_low = (closes[-1] - low_60) / low_60 < 0.05 if low_60 > 0 else False
    
    # 成交量突破60日均量
    avg_vol_60 = float(np.mean(volumes[-61:-1])) if len(volumes) >= 61 else volumes[-1]
    vol_breakout = volumes[-1] > avg_vol_60 * 2 if avg_vol_60 > 0 else False
    
    # MACD金叉检测
    macd_data = compute_macd(klines)
    dif = macd_data.get("dif", [])
    dea = macd_data.get("dea", [])
    
    golden_cross = False
    if len(dif) >= 2 and len(dea) >= 2:
        golden_cross = dif[-2] <= dea[-2] and dif[-1] > dea[-1]
    
    # 评分逻辑
    if is_low and vol_breakout and golden_cross:
        score = 95
        desc = "底部放量+MACD金叉，反转信号强烈"
    elif is_low and vol_breakout:
        score = 80
        desc = "底部放量，等待MACD确认"
    elif is_low and golden_cross:
        score = 70
        desc = "底部MACD金叉，成交待放大"
    elif is_low:
        score = 55
        desc = "在60日低位，但未放量或金叉"
    else:
        score = 35
        desc = "不在底部区域"
    
    return score, desc


# ============================================================
# 23. 龙头战法
# ============================================================
def dragon_head_strategy(klines: List[Dict[str, Any]]) -> tuple:
    """
    龙头战法：板块内涨幅第一，换手率>5%，资金净流入>1亿
    
    注：因无法实时获取板块数据和资金流向，此处仅根据个股表现判断
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    volumes = _extract_volumes(klines)
    
    if len(closes) < 10:
        return 50, "数据不足"
    
    # 近5日涨幅（近似龙头判断）
    chg_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 and closes[-6] != 0 else 0
    
    # 换手率估算（成交量/流通股本，此处用成交量近似）
    avg_vol = float(np.mean(volumes[-6:-1])) if len(volumes) >= 6 else volumes[-1]
    turnover_est = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
    
    # 资金净流入估算（此处用价格变动和成交量近似）
    price_up = closes[-1] > closes[-2] if len(closes) >= 2 else False
    big_vol = volumes[-1] > avg_vol * 1.5
    
    # 评分逻辑
    if chg_5 > 8 and turnover_est > 1.5 and price_up and big_vol:
        score = 90
        desc = f"龙头特征：5日涨幅{chg_5:.1f}%，换手活跃，资金流入"
    elif chg_5 > 5 and turnover_est > 1.2:
        score = 75
        desc = f"领涨特征：5日涨幅{chg_5:.1f}%，换手积极"
    elif chg_5 > 3:
        score = 60
        desc = f"涨幅居前：5日涨幅{chg_5:.1f}%，但换手不足"
    else:
        score = 40
        desc = "涨幅不突出，非龙头特征"
    
    return score, desc


# ============================================================
# 24. 动量反转策略
# ============================================================
def momentum_reversal(klines: List[Dict[str, Any]]) -> tuple:
    """
    动量反转：RSI(6)<30超卖，K线出现锤子线/启明星，成交量放大
    
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    volumes = _extract_volumes(klines)
    lows = _extract_lows(klines)
    opens = _extract_opens(klines)
    
    if len(closes) < 10:
        return 50, "数据不足"
    
    # RSI(6)超卖
    rsi6 = compute_rsi(closes, 6) if len(closes) >= 7 else None
    is_oversold = rsi6 is not None and rsi6 < 30
    
    # K线形态检测（锤子线：实体小，下影线长）
    last_candle = klines[-1]
    last_open = float(last_candle.get("o", 0))
    last_close = float(last_candle.get("c", 0))
    last_high = float(last_candle.get("h", 0))
    last_low = float(last_candle.get("l", 0))
    
    body = abs(last_close - last_open)
    total_range = last_high - last_low if last_high > last_low else 1
    lower_shadow = min(last_open, last_close) - last_low
    
    is_hammer = body / total_range < 0.3 and lower_shadow / total_range > 0.5
    
    # 启明星（简化：昨日下跌，今日上涨，开盘低于昨日收盘）
    is_morning_star = False
    if len(klines) >= 2:
        prev_close = float(klines[-2].get("c", 0))
        prev_open = float(klines[-2].get("o", 0))
        if prev_close < prev_open:  # 昨日阴线
            if last_close > last_open and last_open < prev_close:  # 今日阳线，开盘低于昨日收盘
                is_morning_star = True
    
    # 成交量放大
    avg_vol_5 = float(np.mean(volumes[-6:-1])) if len(volumes) >= 6 else volumes[-1]
    vol_expansion = volumes[-1] > avg_vol_5 * 1.2 if avg_vol_5 > 0 else False
    
    # 评分逻辑
    if is_oversold and (is_hammer or is_morning_star) and vol_expansion:
        score = 90
        desc = "动量反转：RSI超卖+K线反转形态+放量"
    elif is_oversold and (is_hammer or is_morning_star):
        score = 78
        desc = "动量反转：RSI超卖+K线反转形态"
    elif is_oversold and vol_expansion:
        score = 68
        desc = "动量反转：RSI超卖+放量"
    elif is_oversold:
        score = 58
        desc = f"RSI(6)={rsi6:.1f}超卖，等待反转信号"
    elif is_hammer or is_morning_star:
        score = 55
        desc = "K线出现反转形态，但RSI未超卖"
    else:
        score = 40
        desc = "无明显反转信号"
    
    return score, desc
