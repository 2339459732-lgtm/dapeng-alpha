"""DAPENG Alpha 策略选股系统 - GET /api/analyze 实时分析总结接口

基于K线+行情+策略数据，生成结构化分析总结：
- 趋势判断
- 支撑/阻力位
- 风险评估
- 操作建议
- 关键观察点
- v3.0 新增：布林带、KDJ、MACD详细、多时间框架、风险量化、操作建议精细化
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query

from server.cache import cache
from server.config import CACHE_TTL_KLINE, find_stock_name, get_sector_stocks
from server.strategies import (
    compute_atr,
    compute_boll,
    compute_kdj,
    compute_ma,
    compute_ma_cross,
    compute_macd_detail,
    compute_macd,
    compute_max_drawdown,
    compute_multi_timeframe,
    compute_strategy,
    compute_rsi,
    compute_var_95,
    compute_volatility,
    compute_volume_detail,
)
from server.tencent_kline import fetch_tencent_kline
from server.realtime import fetch_tencent_quotes
from server.westock import westock

logger = logging.getLogger(__name__)
router = APIRouter()


def _generate_analysis(code: str, name: str, klines: list, quote: dict) -> dict:
    """基于K线和行情数据生成分析总结"""

    if not klines or len(klines) < 10:
        return {
            "code": code,
            "name": name,
            "trend": "数据不足",
            "summary": "K线数据不足，无法生成分析",
            "support_resistance": [],
            "risk_level": "未知",
            "action": "观望",
            "key_points": ["等待更多数据"],
            "score_detail": {},
        }

    # 计算各项指标
    strategy = compute_strategy(klines)
    ma_cross = compute_ma_cross(klines)
    atr14, stop_loss, position_pct = compute_atr(klines)

    closes = [k["c"] for k in klines]
    last_close = closes[-1]
    volumes = [k["v"] for k in klines]
    highs = [k["h"] for k in klines]
    lows = [k["l"] for k in klines]

    # RSI
    rsi = compute_rsi(closes, 14)

    # 均线
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else last_close
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else last_close
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else last_close

    # ========== v3.0 新增指标 ==========
    # 布林带
    boll = compute_boll(klines)

    # KDJ
    kdj = compute_kdj(klines)

    # MACD详细分析
    macd_detail = compute_macd_detail(klines)
    # 同时获取原始MACD数值用于AI分析
    macd_raw = compute_macd(klines)
    if isinstance(macd_raw, dict):
        dif_vals = macd_raw.get("dif", [])
        dea_vals = macd_raw.get("dea", [])
        hist_vals = macd_raw.get("hist", [])
        macd_detail["dif"] = dif_vals[-1] if dif_vals else 0
        macd_detail["dea"] = dea_vals[-1] if dea_vals else 0
        macd_detail["hist"] = hist_vals[-1] if hist_vals else 0

    # 多时间框架
    multi_timeframe = compute_multi_timeframe(klines)

    # 风险量化
    max_drawdown = compute_max_drawdown(klines)
    volatility = compute_volatility(klines)
    var_95 = compute_var_95(klines)

    # 量价关系多维度
    volume_detail = compute_volume_detail(klines)

    # ========== 1. 趋势判断 ==========
    if ma5 > ma10 > ma20 and last_close > ma5:
        trend = "强势上涨"
        trend_desc = "短中期均线多头排列，价格站上MA5"
    elif ma5 > ma10 and last_close > ma5:
        trend = "震荡偏多"
        trend_desc = "短期均线金叉，但中期均线尚未完全多头排列"
    elif ma5 < ma10 < ma20 and last_close < ma5:
        trend = "弱势下跌"
        trend_desc = "短中期均线空头排列，价格受MA5压制"
    elif ma5 < ma10 and last_close < ma5:
        trend = "震荡偏空"
        trend_desc = "短期均线死叉，价格承压"
    else:
        trend = "横盘整理"
        trend_desc = "均线缠绕，方向不明"

    # MA交叉信号
    if ma_cross == "gold":
        trend_desc += "；MA5/MA10金叉确认"
    elif ma_cross == "dead":
        trend_desc += "；MA5/MA10死叉确认"

    # ========== 2. 支撑/阻力位 ==========
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    sr_levels = []

    # 阻力位
    if last_close < recent_high:
        sr_levels.append({
            "price": round(recent_high, 2),
            "type": "阻力",
            "strength": "强" if recent_high == max(highs[-60:]) else "中",
            "desc": f"20日高点 {recent_high:.2f}",
        })

    # 前高阻力
    for i in range(len(highs) - 2, max(len(highs) - 20, -1), -1):
        if highs[i] > last_close and highs[i] > highs[i + 1]:
            sr_levels.append({
                "price": round(highs[i], 2),
                "type": "阻力",
                "strength": "弱",
                "desc": f"前期高点 {highs[i]:.2f}",
            })
            break

    # 支撑位
    if last_close > recent_low:
        sr_levels.append({
            "price": round(recent_low, 2),
            "type": "支撑",
            "strength": "强" if recent_low == min(lows[-60:]) else "中",
            "desc": f"20日低点 {recent_low:.2f}",
        })

    # MA支撑
    for ma_val, ma_name in [(ma20, "MA20"), (ma10, "MA10"), (ma5, "MA5")]:
        if last_close > ma_val > recent_low:
            sr_levels.append({
                "price": round(ma_val, 2),
                "type": "支撑",
                "strength": "中" if ma_name == "MA20" else "弱",
                "desc": f"{ma_name} 均线支撑",
            })
            break

    # 布林带支撑/阻力
    if boll["lower"] > 0:
        if last_close > boll["lower"] > recent_low:
            sr_levels.append({
                "price": boll["lower"],
                "type": "支撑",
                "strength": "中",
                "desc": "布林下轨支撑",
            })
        if last_close < boll["upper"]:
            sr_levels.append({
                "price": boll["upper"],
                "type": "阻力",
                "strength": "中",
                "desc": "布林上轨阻力",
            })

    # ATR止损位
    if stop_loss > 0:
        sr_levels.append({
            "price": round(stop_loss, 2),
            "type": "止损",
            "strength": "关键",
            "desc": "ATR(14)止损位",
        })

    # ========== 3. 风险评估 ==========
    risk_factors = []
    if rsi and rsi > 70:
        risk_factors.append(f"RSI={rsi:.1f} 超买")
    if rsi and rsi < 30:
        risk_factors.append(f"RSI={rsi:.1f} 超卖")
    if atr14 / last_close > 0.03:
        risk_factors.append(f"波动率偏高(ATR={atr14:.2f})")
    if max_drawdown > 20:
        risk_factors.append(f"最大回撤{max_drawdown:.1f}%")
    if volatility > 40:
        risk_factors.append(f"年化波动率{volatility:.1f}%偏高")

    # 量价背离检测
    price_trend = closes[-1] - closes[-5] if len(closes) >= 5 else 0
    vol_avg = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volumes[-1]
    vol_today = volumes[-1]
    if price_trend > 0 and vol_today < vol_avg * 0.7:
        risk_factors.append("上涨缩量，持续性存疑")
    elif price_trend < 0 and vol_today > vol_avg * 1.5:
        risk_factors.append("下跌放量，卖压较重")

    # KDJ超买超卖
    if kdj["k"] > 80:
        risk_factors.append(f"KDJ-K={kdj['k']:.0f} 超买")
    elif kdj["k"] < 20:
        risk_factors.append(f"KDJ-K={kdj['k']:.0f} 超卖")

    chg = quote.get("chg", 0)
    if abs(chg) > 5:
        risk_factors.append(f"涨跌幅{chg:+.1f}%，波动较大")

    if risk_factors:
        risk_level = "高风险" if len(risk_factors) >= 3 else "中风险" if len(risk_factors) >= 2 else "低风险"
    else:
        risk_level = "低风险"

    # ========== 4. 操作建议（精细化）==========
    total_score = strategy.total_score

    # 计算买入区间
    buy_zone_low = round(last_close * 0.97, 2)  # 支撑位附近3%
    buy_zone_high = round(last_close * 1.01, 2)
    if boll["mid"] > 0:
        buy_zone_low = round(min(boll["mid"], last_close * 0.97), 2)

    # 止盈位
    if boll["upper"] > 0:
        stop_profit_1 = round(boll["upper"], 2)
        stop_profit_2 = round(last_close * 1.10, 2)  # 10%止盈
    else:
        stop_profit_1 = round(last_close * 1.05, 2)
        stop_profit_2 = round(last_close * 1.10, 2)

    # 分批建仓策略
    if total_score >= 70:
        batch_strategy = "首次30%，回调加仓30%，确认突破再加40%"
    elif total_score >= 55:
        batch_strategy = "首次20%，回调加仓30%，确认趋势再加30%，留20%机动"
    else:
        batch_strategy = "观望为主，小仓位试探10-20%"

    if total_score >= 75 and ma_cross == "gold" and (rsi is None or rsi < 75):
        action = "积极买入"
        action_desc = "策略评分高+MA金叉+未超买，多重信号共振"
    elif total_score >= 65 and trend in ["强势上涨", "震荡偏多"]:
        action = "逢低买入"
        action_desc = "趋势偏多，等待回调至支撑位介入"
    elif total_score >= 50 and trend in ["横盘整理", "震荡偏多"]:
        action = "观望为主"
        action_desc = "信号不够明确，等待方向选择"
    elif total_score <= 30 or (ma_cross == "dead" and trend in ["弱势下跌", "震荡偏空"]):
        action = "减仓离场"
        action_desc = "策略评分低或死叉确认，注意风险控制"
    elif total_score <= 40:
        action = "谨慎持有"
        action_desc = "策略评分偏低，设置好止损"
    else:
        action = "持有观察"
        action_desc = "策略评分中性，关注关键位突破"

    # ========== 5. 关键观察点 ==========
    key_points = []
    if ma_cross == "gold":
        key_points.append("MA5/MA10金叉已确认，关注后续量能配合")
    elif ma_cross == "dead":
        key_points.append("MA5/MA10死叉，注意短期调整风险")

    if rsi:
        if rsi > 70:
            key_points.append(f"RSI={rsi:.1f}进入超买区，警惕回调")
        elif rsi < 30:
            key_points.append(f"RSI={rsi:.1f}进入超卖区，关注反弹机会")

    # KDJ观察点
    if kdj["k"] > 80:
        key_points.append(f"KDJ超买(K={kdj['k']:.0f})，短线注意回调")
    elif kdj["k"] < 20:
        key_points.append(f"KDJ超卖(K={kdj['k']:.0f})，关注反弹信号")

    # 布林带观察点
    if boll["position"] > 0.9:
        key_points.append("价格触及布林上轨，关注是否有效突破")
    elif boll["position"] < 0.1:
        key_points.append("价格触及布林下轨，关注是否企稳反弹")

    # 检测K线形态
    last_k = klines[-1]
    body = abs(last_k["c"] - last_k["o"])
    total_range = last_k["h"] - last_k["l"]
    if total_range > 0 and body / total_range < 0.15:
        key_points.append("最新K线十字星，多空分歧，关注方向选择")

    if stop_loss > 0:
        key_points.append(f"止损位 {stop_loss:.2f}，跌破即离场")

    # 多时间框架观察
    if multi_timeframe["overall"] == "多头趋势":
        key_points.append("多时间框架共振偏多，中期趋势向上")
    elif multi_timeframe["overall"] == "空头趋势":
        key_points.append("多时间框架共振偏空，注意控制仓位")

    if len(key_points) == 0:
        key_points.append("当前无明显信号，保持关注")

    # 综合摘要
    summary = f"{name}当前{trend}，策略评分{total_score}分。{trend_desc}。"
    summary += f"建议：{action_desc}。"
    if risk_factors:
        summary += f"风险提示：{'；'.join(risk_factors)}。"
    summary += f"多时间框架：{multi_timeframe['overall']}。"

    return {
        "code": code,
        "name": name,
        "trend": trend,
        "trend_desc": trend_desc,
        "summary": summary,
        "support_resistance": sr_levels,
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "action": action,
        "action_desc": action_desc,
        "key_points": key_points,
        "score_detail": {
            "total": total_score,
            "combined": strategy.combined_score,
            "momentum": next((s.score for s in strategy.signals if s.type == "momentum"), 50),
            "breakout": next((s.score for s in strategy.signals if s.type == "breakout"), 50),
            "volume": next((s.score for s in strategy.signals if s.type == "volume_divergence"), 50),
            "ma_bullish": strategy.ma_bullish_score,
            "pullback": strategy.pullback_score,
            "limit_up": strategy.limit_up_score,
            "bottom_reversal": strategy.bottom_reversal_score,
            "dragon_head": strategy.dragon_head_score,
            "momentum_reversal": strategy.momentum_reversal_score,
            "rsi": round(rsi, 1) if rsi else None,
            "ma_cross": ma_cross,
            "atr14": round(atr14, 4),
            "stop_loss": round(stop_loss, 2),
            "position_pct": position_pct,
        },
        # ========== v3.0 新增字段 ==========
        "boll": boll,
        "kdj": kdj,
        "macd_detail": macd_detail,
        "multi_timeframe": multi_timeframe,
        "risk_quant": {
            "max_drawdown": max_drawdown,
            "volatility": volatility,
            "var_95": var_95,
        },
        "action_detail": {
            "buy_zone": f"{buy_zone_low}-{buy_zone_high}",
            "buy_zone_low": buy_zone_low,
            "buy_zone_high": buy_zone_high,
            "stop_profit_1": stop_profit_1,
            "stop_profit_2": stop_profit_2,
            "batch_strategy": batch_strategy,
        },
        "volume_detail": volume_detail,
        # LLM 增强所需的信号列表（不对外展示）
        "signals_raw": [{"label": s.label, "score": s.score} for s in strategy.signals],
    }


@router.get("/analyze")
async def analyze_stock(
    code: str = Query(..., description="股票代码，如 sh600703"),
):
    """
    实时分析总结：基于K线+行情+策略数据，生成结构化分析。
    """
    name = find_stock_name(code)

    # 获取K线（优先缓存 → CLI → 腾讯HTTP fallback）
    kline_cache_key = f"kline:{code}:day"
    cached_data = cache.get(kline_cache_key)
    if cached_data and isinstance(cached_data, dict) and cached_data.get("klines"):
        klines = cached_data["klines"]
    else:
        raw_klines = cache.get(f"raw_kline:{code}:day")
        if raw_klines:
            klines = raw_klines
        else:
            # 1. 尝试CLI
            klines = await westock.kline(code, period="day")
            if klines:
                cache.set(f"raw_kline:{code}:day", klines, CACHE_TTL_KLINE)
            else:
                # 2. Fallback到腾讯HTTP K线
                from server.config import KLINE_LIMIT
                klines = await fetch_tencent_kline(code, period="day", limit=KLINE_LIMIT)
                if klines:
                    cache.set(f"raw_kline:{code}:day", klines, CACHE_TTL_KLINE)
                    logger.info("analyze kline from Tencent HTTP: %s", code)

    # v5.1 健壮性：过滤非 dict 的异常 K 线条目（腾讯 fqkline 偶发返回 list 段，
    # 曾导致 603501 在 DeepSeek 分支 closes=[k["c"]...] 崩溃，静默丢失 ai_decision）
    if klines:
        klines = [k for k in klines if isinstance(k, dict) and "c" in k and "v" in k]

    if not klines:
        return {
            "ok": True,
            "data": {
                "code": code,
                "name": name,
                "trend": "数据不足",
                "summary": "无法获取K线数据",
                "support_resistance": [],
                "risk_level": "未知",
                "risk_factors": [],
                "action": "观望",
                "action_desc": "等待数据",
                "key_points": ["数据获取失败"],
                "score_detail": {},
                "boll": {"upper": 0, "mid": 0, "lower": 0, "position": 0.5, "signal": "数据不足"},
                "kdj": {"k": 50, "d": 50, "j": 50, "signal": "数据不足"},
                "macd_detail": {"trend": "中性", "zero_axis": "零轴附近", "signal": "无明显信号"},
                "multi_timeframe": {"short": "中性", "mid": "中性", "long": "中性", "overall": "中性"},
                "risk_quant": {"max_drawdown": 0, "volatility": 0, "var_95": 0},
                "action_detail": {"buy_zone": "--", "buy_zone_low": 0, "buy_zone_high": 0, "stop_profit_1": 0, "stop_profit_2": 0, "batch_strategy": "观望"},
                "volume_detail": {"vol_ratio_5": 1, "vol_ratio_10": 1, "streak": "无连续异动", "divergence": "无背离", "signal": "量价正常"},
            },
        }

    # 获取行情（优先CLI → 腾讯HTTP fallback）
    quote_data = await westock.quote(code)
    if not quote_data or quote_data.get("price", 0) == 0:
        # Fallback到腾讯实时行情
        try:
            tencent_results = await fetch_tencent_quotes([code], timeout=4.0)
            if tencent_results:
                tq = tencent_results[0]
                quote_data = {
                    "code": code, "name": tq.get("name", name),
                    "price": tq.get("price", 0), "chg": tq.get("chg", 0),
                    "high": tq.get("high", 0), "low": tq.get("low", 0),
                    "open": tq.get("open", 0), "yclose": tq.get("yclose", 0),
                    "volume": tq.get("volume", 0), "amount": tq.get("amount", 0),
                }
        except Exception:
            pass
    quote = quote_data or {"code": code, "name": name, "price": 0, "chg": 0}

    # 生成分析
    analysis = _generate_analysis(code, name, klines, quote)

    # ========== v4.1 DeepSeek V4 Pro AI 增强决策引擎 ==========
    # DeepSeek 作为最终决策者，综合量化指标做出交易决策
    try:
        from server.llm import get_llm_client

        llm = get_llm_client()
        if llm.api_key:
            score_detail = analysis.get("score_detail", {})
            macd_detail = analysis.get("macd_detail", {})
            kdj = analysis.get("kdj", {"k": 50, "d": 50, "j": 50})
            boll = analysis.get("boll", {})
            mtf = analysis.get("multi_timeframe", {})
            rq = analysis.get("risk_quant", {})
            vd = analysis.get("volume_detail", {})
            closes = [k["c"] for k in klines]
            volumes = [k["v"] for k in klines]
            last_close = closes[-1] if closes else 0.0
            _ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else last_close
            _ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else last_close
            _ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else last_close

            ai_result = await asyncio.wait_for(
                llm.generate_stock_analysis(
                    code=code,
                    name=name,
                    current_price=float(quote.get("price", last_close)),
                    change_pct=float(quote.get("chg", 0)),
                    volume=float(quote.get("volume", volumes[-1] if volumes else 0)),
                    ma5=_ma5,
                    ma10=_ma10,
                    ma20=_ma20,
                    macd_dif=macd_detail.get("dif", 0),
                    macd_dea=macd_detail.get("dea", 0),
                    macd_hist=macd_detail.get("hist", 0),
                    rsi=score_detail.get("rsi") or 50.0,
                    kdj_k=kdj["k"],
                    kdj_d=kdj["d"],
                    kdj_j=kdj["j"],
                    strategy_score=score_detail.get("total", 50),
                    signals=analysis.get("signals_raw", []),
                    # v4.1 新增：布林带、多时间框架、风险量化、量价关系、趋势
                    boll_upper=boll.get("upper", 0),
                    boll_mid=boll.get("mid", 0),
                    boll_lower=boll.get("lower", 0),
                    boll_position=boll.get("position", 0.5),
                    multi_timeframe=mtf.get("overall", "中性"),
                    max_drawdown=rq.get("max_drawdown", 0),
                    volatility=rq.get("volatility", 0),
                    volume_detail=vd.get("signal", "正常") if isinstance(vd, dict) else str(vd),
                    atr14=score_detail.get("atr14", 0),
                    trend_desc=analysis.get("trend_desc", ""),
                    action_suggest=analysis.get("action", "观望"),
                    risk_factors=analysis.get("risk_factors", []),
                ),
                timeout=25.0  # v4.1 增加超时到25秒
            )
            if ai_result:
                analysis["ai_decision"] = ai_result
                # AI 决策覆盖量化决策
                if ai_result.get("decision"):
                    analysis["ai_decision_label"] = ai_result["decision"]
                if ai_result.get("confidence"):
                    analysis["ai_confidence"] = ai_result["confidence"]
                if ai_result.get("key_reason"):
                    analysis["ai_key_reason"] = ai_result["key_reason"]
                if ai_result.get("risk_alert"):
                    analysis["ai_risk_alert"] = ai_result["risk_alert"]
                # 价格字段兜底：AI返回0时用量化数据补
                ad = analysis.get("action_detail", {})
                sd = analysis.get("score_detail", {})
                if ai_result.get("entry_price", {}).get("low", 0) == 0 and ad.get("buy_zone_low", 0) > 0:
                    ai_result["entry_price"] = {"low": ad["buy_zone_low"], "high": ad["buy_zone_high"]}
                if ai_result.get("stop_loss", 0) == 0 and sd.get("stop_loss", 0) > 0:
                    ai_result["stop_loss"] = sd["stop_loss"]
                if ai_result.get("take_profit", {}).get("first", 0) == 0 and ad.get("stop_profit_1", 0) > 0:
                    ai_result["take_profit"] = {"first": ad["stop_profit_1"], "second": ad["stop_profit_2"]}
                if ai_result.get("position_pct", 0) == 0 and sd.get("position_pct", 0) > 0:
                    ai_result["position_pct"] = sd["position_pct"]
                logger.info("DeepSeek AI v4.1 决策完成: %s → %s (置信度 %s%%)", code, ai_result.get("decision"), ai_result.get("confidence"))
    except asyncio.TimeoutError:
        logger.warning("DeepSeek AI 决策超时(25s): %s", code)
    except Exception as e:
        logger.warning("DeepSeek AI 决策失败: %s", str(e))

    return {"ok": True, "data": analysis}
