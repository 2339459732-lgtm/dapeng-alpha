"""信号结构体、三维评分与技术指标原语（RSI / BOLL / KDJ / ATR / 回撤 / 波动 / VaR）"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from server.core.numbers import safe_float as _safe_float

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """策略信号"""
    type: str       # "momentum" | "breakout" | "volume_divergence" | "ma_cross_gold" | "ma_cross_dead"
    label: str      # 中文标签
    score: int      # 0-100
    desc: str       # 描述


@dataclass
class StrategyResult:
    """单只股票的策略计算结果"""
    momentum_score: int = 0
    momentum_desc: str = "数据不足"
    breakout_score: int = 0
    breakout_desc: str = "数据不足"
    volume_score: int = 0
    volume_desc: str = "数据不足"
    ma_cross: Optional[str] = None  # "gold" | "dead" | None
    atr14: float = 0.0
    stop_loss: float = 0.0
    position_pct: int = 0
    rsi: Optional[float] = None  # RSI(14) 值
    total_score: int = 0
    signals: List[Signal] = field(default_factory=list)
    # v3.0 新增
    trend_score: int = 0       # 趋势评分
    risk_score: int = 0        # 风险评分
    # v4.0 新增：6个经典战法评分
    ma_bullish_score: int = 0    # 均线多头排列
    pullback_score: int = 0      # 强势股回调
    limit_up_score: int = 0      # 涨停板战法
    bottom_reversal_score: int = 0 # 底部放量反转
    dragon_head_score: int = 0    # 龙头战法
    momentum_reversal_score: int = 0  # 动量反转
    combined_score: int = 0      # 综合评分（所有战法）



def _extract_closes(klines: List[Dict[str, Any]]) -> np.ndarray:
    """从 K 线数据提取收盘价数组"""
    return np.array([_safe_float(k.get("c", 0)) for k in klines])


def _extract_highs(klines: List[Dict[str, Any]]) -> np.ndarray:
    """从 K 线数据提取最高价数组"""
    return np.array([_safe_float(k.get("h", 0)) for k in klines])


def _extract_lows(klines: List[Dict[str, Any]]) -> np.ndarray:
    """从 K 线数据提取最低价数组"""
    return np.array([_safe_float(k.get("l", 0)) for k in klines])


def _extract_volumes(klines: List[Dict[str, Any]]) -> np.ndarray:
    """从 K 线数据提取成交量数组"""
    return np.array([_safe_float(k.get("v", 0)) for k in klines])


def _extract_opens(klines: List[Dict[str, Any]]) -> np.ndarray:
    """从 K 线数据提取开盘价数组"""
    return np.array([_safe_float(k.get("o", 0)) for k in klines])


# ============================================================
# 1. 动量策略
# ============================================================
def momentum_score(klines: List[Dict[str, Any]]) -> tuple:
    """
    计算动量策略评分。

    基于 5 日、10 日、20 日涨幅和 RSI(14) 辅助判断。
    返回 (score, desc)
    """
    closes = _extract_closes(klines)
    # 下方用到 closes[-21]（20 日涨幅），故至少需要 21 根；原先判 < 20 会越界
    if len(closes) < 21:
        return 50, "数据不足"

    current = closes[-1]

    # 计算涨幅
    chg_5 = (current - closes[-6]) / closes[-6] * 100 if closes[-6] != 0 else 0
    chg_10 = (current - closes[-11]) / closes[-11] * 100 if closes[-11] != 0 else 0
    chg_20 = (current - closes[-21]) / closes[-21] * 100 if closes[-21] != 0 else 0

    # 基础评分：以 5 日涨幅为主
    if chg_5 > 5:
        base_score = 85
    elif chg_5 > 2:
        base_score = 70
    elif chg_5 > 0:
        base_score = 55
    elif chg_5 > -2:
        base_score = 40
    elif chg_5 > -5:
        base_score = 25
    else:
        base_score = 10

    # 10 日和 20 日涨幅调整
    if chg_10 > 3:
        base_score = min(95, base_score + 5)
    elif chg_10 < -3:
        base_score = max(5, base_score - 5)

    if chg_20 > 5:
        base_score = min(95, base_score + 3)
    elif chg_20 < -5:
        base_score = max(5, base_score - 3)

    # RSI(14) 计算
    rsi = compute_rsi(closes, 14)
    if rsi is not None:
        if rsi > 70:
            base_score = max(5, base_score - 10)
        elif rsi < 30:
            base_score = min(95, base_score + 10)

    base_score = max(0, min(100, base_score))

    # 描述
    if chg_5 > 5:
        desc = f"强势上涨 5日+{chg_5:.1f}%"
    elif chg_5 > 2:
        desc = f"上行动量 5日+{chg_5:.1f}%"
    elif chg_5 > 0:
        desc = f"温和上涨 5日+{chg_5:.1f}%"
    elif chg_5 > -2:
        desc = f"小幅回调 5日{chg_5:.1f}%"
    elif chg_5 > -5:
        desc = f"弱势调整 5日{chg_5:.1f}%"
    else:
        desc = f"下行趋势 5日{chg_5:.1f}%"

    if rsi is not None:
        if rsi > 70:
            desc += " RSI超买"
        elif rsi < 30:
            desc += " RSI超卖"

    return base_score, desc


# ============================================================
# 2. 突破策略
# ============================================================
def breakout_score(klines: List[Dict[str, Any]]) -> tuple:
    """
    计算突破策略评分。

    基于当前价在 20 日高低区间的位置和布林带位置。
    返回 (score, desc)
    """
    if len(klines) < 20:
        return 50, "数据不足"

    closes = _extract_closes(klines)
    highs = _extract_highs(klines)
    lows = _extract_lows(klines)

    current = closes[-1]
    recent_20_high = np.max(highs[-20:])
    recent_20_low = np.min(lows[-20:])

    # 当前价在 20 日高低区间位置
    price_range = recent_20_high - recent_20_low
    if price_range > 0:
        position = (current - recent_20_low) / price_range
    else:
        position = 0.5

    # 基础评分
    if current > recent_20_high * 0.98:
        base_score = 85
        desc = "逼近高点突破"
    elif position > 0.8:
        base_score = 75
        desc = "高位蓄势"
    elif position > 0.5:
        base_score = 60
        desc = "中上区间整理"
    elif position > 0.3:
        base_score = 45
        desc = "中下区间震荡"
    else:
        base_score = 25
        desc = "低位弱势"

    # 布林带位置辅助判断
    if len(closes) >= 20:
        ma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:], ddof=0)
        upper_band = ma20 + 2 * std20
        lower_band = ma20 - 2 * std20

        if current > upper_band:
            base_score = min(95, base_score + 10)
            desc = "突破布林上轨"
        elif current > ma20:
            base_score = min(90, base_score + 3)
        elif current < lower_band:
            base_score = max(5, base_score - 10)
            desc = "跌破布林下轨"

    base_score = max(0, min(100, base_score))
    return base_score, desc


# ============================================================
# 3. 量价背离策略
# ============================================================
def volume_divergence_score(klines: List[Dict[str, Any]]) -> tuple:
    """
    计算量价背离策略评分。

    基于最近 5 日量比和量价方向是否一致。
    返回 (score, desc)
    """
    if len(klines) < 5:
        return 50, "数据不足"

    closes = _extract_closes(klines)
    volumes = _extract_volumes(klines)

    # 最近 5 日量比（当日成交量 / 前4日均量）
    recent_vols = volumes[-5:]
    recent_closes = closes[-5:]

    avg_vol_prev4 = np.mean(recent_vols[:4])
    last_vol = recent_vols[-1]

    if avg_vol_prev4 > 0:
        vol_ratio = last_vol / avg_vol_prev4
    else:
        vol_ratio = 1.0

    # 量价方向
    price_chg = recent_closes[-1] - recent_closes[-2]
    price_up = price_chg > 0
    vol_up = vol_ratio > 1.2

    # 评分逻辑
    if vol_ratio > 2.0 and price_up:
        base_score = 85
        desc = f"放量上涨 量比{vol_ratio:.1f}"
    elif vol_ratio > 1.5 and price_up:
        base_score = 72
        desc = f"温和放量上涨 量比{vol_ratio:.1f}"
    elif vol_ratio > 1.0 and price_up:
        base_score = 58
        desc = f"量能平稳上涨 量比{vol_ratio:.1f}"
    elif vol_ratio < 0.5 and not price_up:
        base_score = 35
        desc = f"缩量下跌 量比{vol_ratio:.1f}"
    elif vol_ratio > 2.0 and not price_up:
        base_score = 15
        desc = f"放量下跌 量比{vol_ratio:.1f}"
    elif not price_up:
        base_score = 38
        desc = f"缩量调整 量比{vol_ratio:.1f}"
    else:
        base_score = 50
        desc = f"量能一般 量比{vol_ratio:.1f}"

    # 量价背离检测
    if vol_up and not price_up:
        base_score = max(5, base_score - 5)
        desc += " 量价背离"
    elif not vol_up and price_up:
        desc += " 量能不足"

    base_score = max(0, min(100, base_score))
    return base_score, desc


# ============================================================
# 4. MA 交叉检测
# ============================================================
def compute_ma_cross(klines: List[Dict[str, Any]]) -> Optional[str]:
    """
    检测 MA5/MA10 金叉/死叉。
    最近 5 个交易日内出现交叉即标记。

    返回 "gold" | "dead" | None
    """
    closes = _extract_closes(klines)
    if len(closes) < 15:
        return None

    ma5 = np.convolve(closes, np.ones(5) / 5, mode="valid")
    ma10 = np.convolve(closes, np.ones(10) / 10, mode="valid")

    # ma10 比 ma5 少 5 个元素，对齐
    ma5_aligned = ma5[5:]  # 跳过前5个以对齐
    min_len = min(len(ma5_aligned), len(ma10))
    ma5_aligned = ma5_aligned[:min_len]
    ma10_aligned = ma10[:min_len]

    # 检查最近 5 天是否有交叉
    check_days = min(5, min_len - 1)
    if check_days < 1:
        return None

    for i in range(-check_days, 0):
        if i - 1 < -min_len:
            continue
        prev_diff = ma5_aligned[i - 1] - ma10_aligned[i - 1]
        curr_diff = ma5_aligned[i] - ma10_aligned[i]

        if prev_diff <= 0 and curr_diff > 0:
            return "gold"
        if prev_diff >= 0 and curr_diff < 0:
            return "dead"

    return None


# ============================================================
# 5. ATR 止损 + 仓位建议
# ============================================================
def compute_atr(klines: List[Dict[str, Any]], period: int = 14) -> tuple:
    """
    计算 ATR(14) 止损位和仓位建议。

    返回 (atr14, stop_loss, position_pct)
    """
    if len(klines) < period + 1:
        return 0.0, 0.0, 0

    closes = _extract_closes(klines)
    highs = _extract_highs(klines)
    lows = _extract_lows(klines)

    # 计算真实波幅 TR
    tr_list = []
    for i in range(1, len(klines)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0.0, 0.0, 0

    # ATR = TR 的 period 日简单移动平均
    tr_array = np.array(tr_list)
    atr14 = float(np.mean(tr_array[-period:]))

    current = closes[-1]
    # 止损位 = 当前价 - 2 * ATR(14)
    stop_loss = current - 2 * atr14

    # 仓位建议
    if current > 0 and atr14 > 0:
        risk_ratio = 2 * atr14 / current
        risk_budget = 0.05
        position = min(0.8, max(0.1, (risk_budget / risk_ratio) * 1.0))
        position_pct = int(position * 100)
    else:
        position_pct = 10

    return atr14, stop_loss, position_pct


# ============================================================
# 6. RSI 计算
# ============================================================
def compute_rsi(closes: np.ndarray, period: int = 14) -> Optional[float]:
    """
    计算 RSI 指标。

    返回 RSI 值，数据不足返回 None
    """
    if len(closes) < period + 1:
        return None

    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


# ============================================================
# 7. 布林带计算 BOLL(20,2)
# ============================================================
def compute_boll(klines: List[Dict[str, Any]], period: int = 20, nbdev: int = 2) -> Dict[str, Any]:
    """
    计算布林带 BOLL(20,2)。

    返回 {upper, mid, lower, position, signal}
    - position: 当前价在布林带中的位置 (0~1)
    - signal: 信号描述
    """
    closes = _extract_closes(klines)
    if len(closes) < period:
        return {
            "upper": 0.0, "mid": 0.0, "lower": 0.0,
            "position": 0.5, "signal": "数据不足",
        }

    mid = float(np.mean(closes[-period:]))
    std = float(np.std(closes[-period:], ddof=0))
    upper = mid + nbdev * std
    lower = mid - nbdev * std
    current = float(closes[-1])

    # 计算位置
    band_width = upper - lower
    if band_width > 0:
        position = (current - lower) / band_width
    else:
        position = 0.5

    position = max(0.0, min(1.0, position))

    # 信号判断
    if current > upper:
        signal = "突破上轨，强势或超买"
    elif current > mid:
        signal = "中轨上方，偏多运行"
    elif current > lower:
        signal = "中轨下方，偏弱运行"
    else:
        signal = "跌破下轨，超卖或加速下跌"

    return {
        "upper": round(upper, 2),
        "mid": round(mid, 2),
        "lower": round(lower, 2),
        "position": round(position, 3),
        "signal": signal,
    }


# ============================================================
# 8. KDJ 指标计算
# ============================================================
def compute_kdj(klines: List[Dict[str, Any]], n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, Any]:
    """
    计算 KDJ 指标。

    返回 {k, d, j, signal}
    """
    if len(klines) < n:
        return {"k": 50.0, "d": 50.0, "j": 50.0, "signal": "数据不足"}

    highs = _extract_highs(klines)
    lows = _extract_lows(klines)
    closes = _extract_closes(klines)

    # 计算 RSV 序列
    k_val = 50.0
    d_val = 50.0
    prev_k = 50.0  # 跟踪上一轮 K 值用于金叉/死叉判断

    for i in range(n - 1, len(closes)):
        high_n = np.max(highs[i - n + 1:i + 1])
        low_n = np.min(lows[i - n + 1:i + 1])

        if high_n != low_n:
            rsv = (closes[i] - low_n) / (high_n - low_n) * 100
        else:
            rsv = 50.0

        prev_k = k_val
        k_val = float((2 / m1) * k_val + (1 / m1) * rsv)
        d_val = float((2 / m2) * d_val + (1 / m2) * k_val)

    j_val = float(3 * k_val - 2 * d_val)

    # 信号判断
    signal = "中性"
    if k_val > 80 and d_val > 80:
        signal = "超买区域，注意回调风险"
    elif k_val < 20 and d_val < 20:
        signal = "超卖区域，关注反弹机会"
    elif k_val > d_val and k_val < 80:
        # 检测金叉：前一日 K < D，当日 K > D
        if prev_k < d_val:
            signal = "KDJ金叉，看多信号"
        else:
            signal = "K线在D线上方，偏多"
    elif k_val < d_val and k_val > 20:
        # 检测死叉：前一日 K > D，当日 K < D
        if prev_k > d_val:
            signal = "KDJ死叉，看空信号"
        else:
            signal = "K线在D线下方，偏空"

    return {
        "k": round(k_val, 2),
        "d": round(d_val, 2),
        "j": round(j_val, 2),
        "signal": signal,
    }


# ============================================================
# 9. 最大回撤计算
# ============================================================
def compute_max_drawdown(klines: List[Dict[str, Any]]) -> float:
    """
    计算近期最大回撤百分比（从近期高点回撤的最大百分比）。

    返回回撤百分比（正值表示回撤幅度）
    """
    closes = _extract_closes(klines)
    if len(closes) < 2:
        return 0.0

    # 计算累计最高点
    cummax = np.maximum.accumulate(closes)
    # 回撤 = (当前价 - 历史最高) / 历史最高
    drawdowns = (closes - cummax) / cummax
    max_dd = float(np.min(drawdowns)) * 100  # 转为百分比，负值

    return round(abs(max_dd), 2)


# ============================================================
# 10. 年化波动率计算
# ============================================================
def compute_volatility(klines: List[Dict[str, Any]], period: int = 20) -> float:
    """
    计算年化波动率（20日收益率标准差 * √252）。

    返回年化波动率百分比
    """
    closes = _extract_closes(klines)
    if len(closes) < period + 1:
        return 0.0

    # 计算日收益率
    recent_closes = closes[-(period + 1):]
    returns = np.diff(recent_closes) / recent_closes[:-1]

    # 过滤无效值
    returns = returns[np.isfinite(returns)]
    if len(returns) < 5:
        return 0.0

    daily_std = float(np.std(returns, ddof=1))
    annual_vol = float(daily_std * np.sqrt(252) * 100)  # 年化并转百分比

    return round(annual_vol, 2)


# ============================================================
# 11. VaR (95% 置信度) 计算
# ============================================================
def compute_var_95(klines: List[Dict[str, Any]], period: int = 20) -> float:
    """
    计算 VaR(95%)，基于历史收益率分布的第5百分位数。

    返回 VaR 百分比（正值表示可能的最大亏损）
    """
    closes = _extract_closes(klines)
    if len(closes) < period + 1:
        return 0.0

    recent_closes = closes[-(period + 1):]
    returns = np.diff(recent_closes) / recent_closes[:-1]
    returns = returns[np.isfinite(returns)]

    if len(returns) < 5:
        return 0.0

    # 95% VaR = 第5百分位数的绝对值
    var_95 = float(abs(float(np.percentile(returns, 5))) * 100)

    return round(var_95, 2)
