"""DAPENG Alpha - K线走势与震荡指标自动分析总结

GET /api/analyze/chart-summary?code=sh600519&period=day

数据流：腾讯日K(150根) → strategies.py 指标库(RSI/KDJ/MACD/BOLL/MA)
       → 规则引擎状态诊断（超买/超卖/强势/弱势/震荡/收敛）+ 成因引用
       → DeepSeek 可选润色（无 Key 用规则文本）
60 秒缓存；K线不足时显式降级。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter
from server.core.numbers import safe_float as _finite

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyze", tags=["chart-analysis"])

_CACHE_TTL = 60
_cache: Dict[str, Dict[str, Any]] = {}


def _boll_width_series(closes: np.ndarray, period: int = 20) -> List[float]:
    """BOLL 带宽序列（(上轨-下轨)/中轨 %），用于收敛/扩张判断"""
    out: List[float] = []
    for i in range(len(closes)):
        if i < period - 1:
            out.append(0.0)
            continue
        seg = closes[i - period + 1: i + 1]
        mid = float(np.mean(seg))
        if mid <= 0:
            out.append(0.0)
            continue
        std = float(np.std(seg))
        out.append((4 * std) / mid * 100)
    return out


def _rule_analysis(klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    from server.strategies import (
        compute_boll, compute_kdj, compute_ma, compute_macd, compute_rsi,
    )

    closes = np.array([_finite(k.get("c", k.get("close"))) for k in klines])
    highs = np.array([_finite(k.get("h", k.get("high"))) for k in klines])
    lows = np.array([_finite(k.get("l", k.get("low"))) for k in klines])
    last = float(closes[-1])

    rsi = compute_rsi(closes) or 50.0
    kdj = compute_kdj(klines)
    macd = compute_macd(klines)
    boll = compute_boll(klines)
    ma = compute_ma(klines, [5, 10, 20, 60])
    ma_last = {p: (vals[-1] if vals else None) for p, vals in ma.items()}

    dif = macd["dif"][-1] if macd["dif"] else 0.0
    dea = macd["dea"][-1] if macd["dea"] else 0.0
    hist = macd["hist"][-1] if macd["hist"] else 0.0
    hist_prev = macd["hist"][-2] if len(macd["hist"]) > 1 else hist

    widths = _boll_width_series(closes)
    width_now = widths[-1] if widths else 0.0
    width_prev = widths[-6] if len(widths) > 5 else width_now
    width_ratio = width_now / width_prev if width_prev > 0 else 1.0

    hi20 = float(np.max(highs[-20:]))
    lo20 = float(np.min(lows[-20:]))
    range_pos = (last - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5

    ma5 = ma_last.get(5)
    ma20 = ma_last.get(20)
    ma60 = ma_last.get(60)
    bullish_align = bool(ma5 and ma20 and ma60 and ma5 > ma20 > ma60 and last > ma5)
    bearish_align = bool(ma5 and ma20 and ma60 and ma5 < ma20 < ma60 and last < ma5)

    reasons: List[str] = []
    score = 0  # >0 偏多 <0 偏空

    # RSI
    if rsi >= 70:
        score -= 2
        reasons.append(f"RSI(14)={rsi:.1f}，进入超买区（≥70），短线追涨风险升高")
    elif rsi <= 30:
        score += 2
        reasons.append(f"RSI(14)={rsi:.1f}，进入超卖区（≤30），存在技术性修复需求")
    else:
        reasons.append(f"RSI(14)={rsi:.1f}，处于中性区间")

    # KDJ
    k, d, j = kdj["k"], kdj["d"], kdj["j"]
    if "金叉" in kdj["signal"]:
        score += 2
    elif "死叉" in kdj["signal"]:
        score -= 2
    elif "超买" in kdj["signal"]:
        score -= 1
    elif "超卖" in kdj["signal"]:
        score += 1
    reasons.append(f"KDJ(K={k:.0f}/D={d:.0f}/J={j:.0f})：{kdj['signal']}")

    # MACD
    if dif > dea and hist > 0:
        score += 1
        macd_desc = f"MACD 红柱（DIF {dif:.3f} > DEA {dea:.3f}）"
        if hist > hist_prev:
            macd_desc += "且动能放大"
        else:
            macd_desc += "但动能收窄"
    elif dif < dea and hist < 0:
        score -= 1
        macd_desc = f"MACD 绿柱（DIF {dif:.3f} < DEA {dea:.3f}）"
        if hist < hist_prev:
            macd_desc += "且空方动能放大"
        else:
            macd_desc += "但空方动能衰减"
    else:
        macd_desc = "MACD 处于多空转换临界"
    reasons.append(macd_desc)

    # 均线
    if bullish_align:
        score += 2
        reasons.append(f"MA5>{'' if ma20 is None else ''}MA20>MA60 且价格站上 MA5，标准多头排列")
    elif bearish_align:
        score -= 2
        reasons.append("MA5<MA20<MA60 且价格位于 MA5 下方，空头排列")
    elif ma20:
        side = "上方" if last > ma20 else "下方"
        reasons.append(f"价格位于 MA20（{ma20:.2f}）{side}，均线交织")

    # BOLL 位置与带宽
    pos = _finite(boll.get("position"), 0.5)
    if pos >= 0.9:
        score -= 1
        reasons.append(f"价格贴近布林上轨（位置 {pos * 100:.0f}%），短线承压")
    elif pos <= 0.1:
        score += 1
        reasons.append(f"价格贴近布林下轨（位置 {pos * 100:.0f}%），支撑临近")
    if width_ratio < 0.8:
        reasons.append(f"布林带宽近一周收敛 {abs(1 - width_ratio) * 100:.0f}%，波动率压缩，临近方向选择")
    elif width_ratio > 1.2:
        reasons.append(f"布林带宽扩张 {(width_ratio - 1) * 100:.0f}%，波动率放大，趋势行情概率上升")

    # 20 日区间位置
    reasons.append(f"现价处于 20 日区间 [{lo20:.2f} ~ {hi20:.2f}] 的 {range_pos * 100:.0f}% 位置")

    # 综合状态判定
    if rsi >= 70 and not bullish_align:
        state, level = "超买回落风险", "risk"
    elif rsi <= 30 and not bearish_align:
        state, level = "超卖反弹机会", "opportunity"
    elif score >= 4:
        state, level = "强势上行", "bullish"
    elif score <= -4:
        state, level = "弱势下行", "bearish"
    elif width_ratio < 0.8:
        state, level = "收敛蓄势", "neutral"
    else:
        state, level = "区间震荡", "neutral"

    return {
        "state": state,
        "level": level,
        "score": score,
        "reasons": reasons[:6],
        "indicators": {
            "rsi": round(rsi, 1),
            "kdj": {"k": k, "d": d, "j": j, "signal": kdj["signal"]},
            "macd": {"dif": dif, "dea": dea, "hist": hist, "momentum": "放大" if abs(hist) > abs(hist_prev) else "收窄"},
            "boll": {"position": round(pos, 3), "width_pct": round(width_now, 2), "width_ratio": round(width_ratio, 2)},
            "ma": {"ma5": ma5, "ma20": ma20, "ma60": ma60,
                    "alignment": "多头" if bullish_align else "空头" if bearish_align else "交织"},
            "price": {"last": round(last, 2), "range20_hi": round(hi20, 2), "range20_lo": round(lo20, 2),
                       "range_pos": round(range_pos, 3)},
        },
    }


def _rule_summary(a: Dict[str, Any], code: str) -> str:
    ind = a["indicators"]
    return (
        f"【{a['state']}】{code} 当前综合评分 {a['score']:+d}。"
        f"RSI {ind['rsi']}、KDJ {ind['kdj']['signal']}、MACD 动能{ind['macd']['momentum']}、"
        f"均线{ind['ma']['alignment']}排列。{a['reasons'][-1]}。"
        f"（规则引擎生成，供研究参考，不构成投资建议）"
    )


async def _ai_summary(a: Dict[str, Any], code: str) -> tuple[str, bool]:
    fallback = _rule_summary(a, code)
    try:
        from server.llm import get_llm_client
        client = get_llm_client()
        if not client.api_key:
            return fallback, False
        ind = a["indicators"]
        reasons_text = "；".join(a["reasons"][:5])
        prompt = f"""你是技术分析专家。基于以下{code}的真实指标数据，写一段 100 字以内的行情分析总结，说明当前市场状态（{a['state']}）及其形成原因，语言简明专业，禁止虚构数据。

指标：RSI={ind['rsi']}；KDJ K{ind['kdj']['k']}/D{ind['kdj']['d']}/J{ind['kdj']['j']}（{ind['kdj']['signal']}）；MACD DIF {ind['macd']['dif']}/DEA {ind['macd']['dea']} 动能{ind['macd']['momentum']}；布林位置 {ind['boll']['position'] * 100:.0f}% 带宽变化率 {ind['boll']['width_ratio']}；均线{ind['ma']['alignment']}排列；20日区间位置 {ind['price']['range_pos'] * 100:.0f}%。
成因线索：{reasons_text}

直接输出总结正文，不要标题和列表。"""
        text = await client.chat(prompt, "你是严谨的技术分析师，只基于给定数据发言。", max_tokens=260)
        if text and len(text.strip()) >= 30:
            return text.strip(), True
        return fallback, False
    except Exception as exc:
        logger.debug("chart-summary ai failed: %s", exc)
        return fallback, False


@router.get("/chart-summary")
async def chart_summary(code: str, period: str = "day"):
    """K线 + 震荡指标自动分析总结（60s 缓存）"""
    cache_key = f"{code}:{period}"
    hit = _cache.get(cache_key)
    if hit and time.time() - hit["ts"] < _CACHE_TTL:
        return hit["data"]

    try:
        from server.tencent_kline import fetch_tencent_kline
        rows = await fetch_tencent_kline(code, period=period, limit=150, timeout=4.0)
    except Exception as exc:
        return {"ok": False, "message": f"K线获取失败: {exc}", "data": None}

    if not rows or len(rows) < 30:
        return {"ok": False, "message": "K线数据不足（需≥30根）", "data": None}

    analysis = _rule_analysis(rows)
    summary, ai_powered = await _ai_summary(analysis, code)

    payload = {
        "ok": True,
        "code": code,
        "period": period,
        "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bars": len(rows),
        "state": analysis["state"],
        "level": analysis["level"],
        "summary": summary,
        "reasons": analysis["reasons"],
        "indicators": analysis["indicators"],
        "ai_powered": ai_powered,
    }
    _cache[cache_key] = {"ts": time.time(), "data": payload}
    if len(_cache) > 200:
        # 先回收已过期条目（原实现只看条数，过期数据会一直留到被条数挤掉）
        now = time.time()
        for k in [k for k, v in _cache.items() if now - v["ts"] >= _CACHE_TTL]:
            _cache.pop(k, None)
    if len(_cache) > 200:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1]["ts"])[:50]
        for k, _ in oldest:
            _cache.pop(k, None)
    return payload
