"""DAPENG Alpha v4.5 - 智能选股扫描 v3 (腾讯行情 + DeepSeek AI)

二阶段引擎：
  Phase 1: 腾讯实时行情全量扫描 + 智能打分（≤5秒，356只）
  Phase 2: DeepSeek V4 Pro 批量AI研判（Top 20, ~8秒）

优点：快（~15s完成）| 可靠（无CLI依赖）| 智能（AI深度分析）
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter

from server.cache import cache
from server.config import SECTORS, find_stock_sector, get_all_stock_codes
from server.core.numbers import clamp as _clamp

logger = logging.getLogger(__name__)
router = APIRouter()

SCAN_CACHE_PREFIX = "scan_v4_explainable_result"
SCAN_STATUS_PREFIX = "scan_v4_explainable_status"
SCAN_CACHE_TTL = 180
SCAN_BATCH_TENCENT = 40
SCAN_AI_TOP_N = 15   # AI批量分析Top N
SCAN_RESULT_MIN = 50  # 最低推荐评分
SCAN_DEFAULT_PRICE_MIN = 0
SCAN_DEFAULT_PRICE_MAX = 200


def _make_scan_key(price_min: float, price_max: float, sectors: List[str]) -> str:
    sector_key = ",".join(sorted(set(sectors))) if sectors else "all"
    return f"{price_min:g}:{price_max:g}:{sector_key}"


def _update_scan_status(status_key: str, **kwargs):
    s = cache.get(status_key) or {"status": "running"}
    s.update(kwargs)
    s["updated_at"] = datetime.now().isoformat()
    cache.set(status_key, s, 300)


def _format_amount(amount: float) -> str:
    if amount >= 100000000:
        return f"{amount / 100000000:.1f}亿元"
    if amount >= 10000:
        return f"{amount / 10000:.0f}万元"
    return f"{amount:.0f}元"


def _enrich_explainability(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """为扫描候选补充可解释因子、入场逻辑和非校准概率估算。"""
    score = float(candidate.get("total_score", 0) or 0)
    chg = float(candidate.get("chg", 0) or 0)
    turnover = float(candidate.get("turnover", 0) or 0)
    amplitude = float(candidate.get("amplitude", 0) or 0)
    pe = float(candidate.get("pe", 0) or 0)
    amount = float(candidate.get("amount", 0) or 0)
    day_position = float(candidate.get("day_position", 0.5) or 0.5)
    price = float(candidate.get("price", 0) or 0)

    drivers: List[str] = []
    cautions: List[str] = []
    evidence: List[Dict[str, Any]] = []

    if chg >= 5:
        drivers.append(f"当日上涨{chg:.2f}%，短线动量强")
        evidence.append({"label": "价格动量", "value": f"+{chg:.2f}%", "impact": "strong"})
    elif chg >= 1:
        drivers.append(f"当日上涨{chg:.2f}%，价格动量偏多")
        evidence.append({"label": "价格动量", "value": f"+{chg:.2f}%", "impact": "positive"})
    elif chg >= 0:
        drivers.append("价格保持红盘，短线承接尚可")
        evidence.append({"label": "价格动量", "value": f"{chg:+.2f}%", "impact": "neutral"})
    else:
        cautions.append(f"当日仍下跌{abs(chg):.2f}%，反转尚未确认")
        evidence.append({"label": "价格动量", "value": f"{chg:.2f}%", "impact": "negative"})

    if 3 <= turnover <= 10:
        drivers.append(f"换手率{turnover:.1f}%，资金参与活跃且未明显过热")
        evidence.append({"label": "换手活跃度", "value": f"{turnover:.1f}%", "impact": "strong"})
    elif 2 <= turnover <= 15:
        drivers.append(f"换手率{turnover:.1f}%，流动性较好")
        evidence.append({"label": "换手活跃度", "value": f"{turnover:.1f}%", "impact": "positive"})
    elif turnover > 15:
        cautions.append(f"换手率{turnover:.1f}%偏高，需防冲高回落")
        evidence.append({"label": "换手活跃度", "value": f"{turnover:.1f}%", "impact": "warning"})
    else:
        cautions.append(f"换手率仅{turnover:.1f}%，持续性仍需成交确认")
        evidence.append({"label": "换手活跃度", "value": f"{turnover:.1f}%", "impact": "neutral"})

    if amount >= 500000000:
        drivers.append(f"成交额{_format_amount(amount)}，资金关注度较高")
        evidence.append({"label": "成交额", "value": _format_amount(amount), "impact": "strong"})
    elif amount >= 100000000:
        drivers.append(f"成交额{_format_amount(amount)}，具备一定资金关注")
        evidence.append({"label": "成交额", "value": _format_amount(amount), "impact": "positive"})
    else:
        cautions.append(f"成交额{_format_amount(amount)}，需关注流动性")
        evidence.append({"label": "成交额", "value": _format_amount(amount), "impact": "neutral"})

    if 10 <= pe <= 30:
        drivers.append(f"PE {pe:.1f}倍，处于模型偏好估值区间")
        evidence.append({"label": "估值", "value": f"PE {pe:.1f}", "impact": "positive"})
    elif pe > 50:
        cautions.append(f"PE {pe:.1f}倍偏高，估值回撤风险较大")
        evidence.append({"label": "估值", "value": f"PE {pe:.1f}", "impact": "warning"})
    elif pe <= 0:
        cautions.append("PE为负或不可用，基本面估值信号较弱")
        evidence.append({"label": "估值", "value": "PE不可用", "impact": "warning"})
    else:
        evidence.append({"label": "估值", "value": f"PE {pe:.1f}", "impact": "neutral"})

    if 3 <= amplitude <= 6:
        drivers.append(f"振幅{amplitude:.1f}%，波动与交易机会相对均衡")
        evidence.append({"label": "日内振幅", "value": f"{amplitude:.1f}%", "impact": "positive"})
    elif amplitude > 8:
        cautions.append(f"振幅{amplitude:.1f}%偏大，短线风险上升")
        evidence.append({"label": "日内振幅", "value": f"{amplitude:.1f}%", "impact": "warning"})
    else:
        evidence.append({"label": "日内振幅", "value": f"{amplitude:.1f}%", "impact": "neutral"})

    if 0.3 <= day_position <= 0.7:
        drivers.append("价格位于日内中段，尚未处于极端追高位置")
    elif day_position > 0.85:
        cautions.append("价格接近日内高位，建议等待回踩确认")
    elif day_position < 0.2:
        cautions.append("价格靠近日内低位，承接强度仍需确认")

    # 概率为启发式展示：由综合评分映射，并对过热/估值/流动性风险做惩罚。
    probability = 48 + (score - 50) * 0.6
    if chg > 7:
        probability -= 4
    if turnover > 15:
        probability -= 3
    if amplitude > 8:
        probability -= 3
    if pe > 50 or pe <= 0:
        probability -= 2
    if amount < 30000000:
        probability -= 2
    probability = round(_clamp(probability, 35, 82), 1)

    if probability >= 68:
        probability_level = "较高"
    elif probability >= 56:
        probability_level = "中等"
    else:
        probability_level = "偏低"

    buy_low = round(price * 0.97, 2)
    buy_high = round(price * 1.01, 2)
    stop_loss = round(price * (0.94 if amplitude <= 6 else 0.92), 2)
    target_1 = round(price * 1.08, 2)
    target_2 = round(price * 1.15, 2)
    position_pct = 30 if score >= 80 else 20 if score >= 70 else 10
    if turnover > 15 or amplitude > 8:
        position_pct = min(position_pct, 15)

    if score >= 80:
        action = "强烈关注"
    elif score >= 70:
        action = "逢低关注"
    elif score >= 60:
        action = "观察候选"
    else:
        action = "谨慎观察"

    core_driver = drivers[0] if drivers else f"综合评分{score:.0f}分"
    buy_logic = (
        f"{core_driver}；仅在{buy_low:.2f}-{buy_high:.2f}区间出现承接时分批介入，"
        f"跌破{stop_loss:.2f}止损，首仓不超过{position_pct}%"
    )
    entry_conditions = [
        f"价格进入{buy_low:.2f}-{buy_high:.2f}并企稳",
        "成交额与换手率不出现明显衰减",
        f"跌破{stop_loss:.2f}则取消或退出计划",
    ]

    candidate.update({
        "action": candidate.get("action") or action,
        "risk_level": candidate.get("risk_level") or ("高" if len(cautions) >= 3 else "中" if cautions else "低"),
        "position_pct": candidate.get("position_pct") or position_pct,
        "buy_zone": candidate.get("buy_zone") or [buy_low, buy_high],
        "stop_loss": candidate.get("stop_loss") or stop_loss,
        "stop_profit_1": candidate.get("stop_profit_1") or target_1,
        "stop_profit_2": candidate.get("stop_profit_2") or target_2,
        "signals": candidate.get("signals") or [item["label"] for item in evidence if item["impact"] in {"strong", "positive"}],
        "reason": candidate.get("reason") or "；".join(drivers[:2]) or "综合因子暂未形成强共振",
        "rise_drivers": drivers[:4],
        "risk_factors": cautions[:4],
        "factor_evidence": evidence,
        "buy_logic": buy_logic,
        "entry_conditions": entry_conditions,
        "rise_probability": probability,
        "probability_level": probability_level,
        "probability_horizon": "未来1-5个交易日",
        "probability_basis": f"综合评分{score:.0f}分，正向因子{len(drivers)}项、风险因子{len(cautions)}项；由快照因子启发式映射，未经历史概率校准",
        "probability_disclaimer": "概率为模型估算，不代表确定收益或投资承诺",
    })
    return candidate


# ============================================================
#  Phase 1: 腾讯行情全量扫描 + 多维度智能打分
# ============================================================
async def _phase1_tencent_screen(codes: List[str], price_min: float, price_max: float, status_key: str) -> List[Dict]:
    """腾讯行情批量获取 + 多维度智能评分"""
    from server.realtime import fetch_tencent_quotes

    _update_scan_status(status_key, phase="P1", progress=3, total=len(codes))

    all_results = []
    failed_batches = 0
    for i in range(0, len(codes), SCAN_BATCH_TENCENT):
        batch = codes[i:i + SCAN_BATCH_TENCENT]
        try:
            batch_results = await asyncio.wait_for(
                fetch_tencent_quotes(batch, timeout=4.0),
                timeout=6.0
            )
            all_results.extend(batch_results or [])
        except Exception as e:
            failed_batches += 1
            logger.warning("Scan P1 batch %d error: %s", i, str(e)[:60])

        pct = min(30, 5 + int((i + len(batch)) / max(len(codes), 1) * 25))
        _update_scan_status(status_key, progress=pct, fetched=len(all_results), requested=i + len(batch), failed_batches=failed_batches)

    unique_quotes = {}
    for quote in all_results:
        code = quote.get("code")
        if code and float(quote.get("price", 0) or 0) > 0:
            unique_quotes[code] = quote
    all_results = list(unique_quotes.values())
    coverage_ratio = len(all_results) / max(len(codes), 1)
    _update_scan_status(status_key, fetched=len(all_results), coverage=round(coverage_ratio * 100, 1), failed_batches=failed_batches, phase="P1", progress=30)

    if not all_results:
        return []

    # 多维度评分模型
    scored = []
    for r in all_results:
        price = float(r.get("price", 0) or 0)
        chg = float(r.get("chg", 0) or 0)
        turnover = float(r.get("turnover", 0) or 0)
        volume = float(r.get("volume", 0) or 0)
        pe = float(r.get("pe", 0) or 0)
        high = float(r.get("high", 0) or 0)
        low = float(r.get("low", 0) or 0)
        yclose = float(r.get("yclose", 0) or 0)
        amount = float(r.get("amount", 0) or 0)
        code = r.get("code", "")
        name = r.get("name", "")

        if price <= 0:
            continue

        # 价格区间过滤：使用本次请求的参数，不能依赖全局变量
        if price < price_min or price > price_max:
            continue

        # 评分拆解：每一项都保留，供前端解释“为什么入选”。
        if chg >= 5:      momentum_score = 20
        elif chg >= 3:    momentum_score = 15
        elif chg >= 1:    momentum_score = 10
        elif chg >= 0:    momentum_score = 5
        elif chg >= -1:   momentum_score = 0
        elif chg >= -3:   momentum_score = -10
        else:             momentum_score = -20

        if 3 <= turnover <= 10:   liquidity_score = 12
        elif 2 <= turnover <= 15: liquidity_score = 8
        elif turnover > 15:       liquidity_score = 3
        else:                     liquidity_score = 2

        amplitude = ((high - low) / yclose * 100) if yclose > 0 else 0
        if 3 <= amplitude <= 6:   volatility_score = 10
        elif amplitude <= 8:      volatility_score = 5
        else:                     volatility_score = 0

        if 10 <= pe <= 30:        valuation_score = 10
        elif 0 < pe <= 50:        valuation_score = 5
        elif pe <= 0:             valuation_score = 2
        else:                     valuation_score = 0

        if amount >= 500000000:   capital_score = 10
        elif amount >= 100000000: capital_score = 6
        elif amount >= 30000000:  capital_score = 3
        else:                     capital_score = 0

        day_pos = (price - low) / (high - low) if high > low else 0.5
        position_score = 5 if 0.3 <= day_pos <= 0.7 else 0

        score_breakdown = {
            "base": 50,
            "momentum": momentum_score,
            "liquidity": liquidity_score,
            "volatility": volatility_score,
            "valuation": valuation_score,
            "capital_attention": capital_score,
            "day_position": position_score,
        }
        score = max(0, min(100, sum(score_breakdown.values())))

        sector_key = find_stock_sector(code)
        sector_name = SECTORS.get(sector_key, {}).get("name", sector_key)

        scored.append(_enrich_explainability({
            "code": code, "name": name, "data_source": r.get("source", "tencent"), "price": round(price, 2),
            "chg": round(chg, 2), "turnover": round(turnover, 1),
            "volume": int(volume), "pe": round(pe, 1), "amount": amount,
            "high": high, "low": low, "yclose": yclose,
            "amplitude": round(amplitude, 1),
            "day_position": round(day_pos, 2),
            "sector": sector_key, "sector_name": sector_name,
            "total_score": score,
            "score_breakdown": score_breakdown,
        }))

    scored.sort(key=lambda x: x["total_score"], reverse=True)
    for item in scored:
        item["scan_coverage_pct"] = round(coverage_ratio * 100, 1)
        item["scan_configured_count"] = len(codes)
        item["scan_valid_count"] = len(all_results)
    return scored


# ============================================================
#  Phase 2: DeepSeek V4 Pro 批量AI研判
# ============================================================
async def _phase2_ai_analyze(candidates: List[Dict], status_key: str) -> List[Dict]:
    """批量 DeepSeek AI 分析 - 一次调用分析多只股票"""
    from server.llm import get_llm_client

    # 确保 AI 不可用、超时或解析失败时，每只股票仍有完整确定性解释。
    for candidate in candidates:
        _enrich_explainability(candidate)

    llm = get_llm_client()
    if not llm.api_key:
        for c in candidates:
            c["ai_reason"] = ""
        return candidates

    top = candidates[:SCAN_AI_TOP_N]
    if not top:
        return candidates

    _update_scan_status(status_key, phase="P2-AI", progress=65)

    # 构建批量分析prompt
    stock_list = []
    for i, c in enumerate(top):
        stock_list.append(
            f"{i+1}. {c['name']}({c['code']}) 价格{c['price']:.1f} 涨跌{c['chg']:+.1f}% "
            f"换手{c['turnover']:.1f}% PE{c['pe']:.0f} 振幅{c['amplitude']:.1f}% "
            f"评分{c['total_score']}"
        )
    stocks_text = "\n".join(stock_list)

    prompt = f"""你是资深A股量化分析师。以下是通过多维度筛选的候选股票，请对每只给出：

1) 操作建议（强烈推荐/推荐买入/值得关注/观望 之一）
2) 风险等级（低/中/高 之一）
3) 推荐仓位%（10-40）
4) 一句话核心理由（15字内）

候选股票：
{stocks_text}

请严格按JSON数组输出，不要任何其他文字：
[
  {{"idx":1,"action":"推荐买入","risk":"低","position":30,"reason":"放量突破平台，短线看多"}},
  ...
]"""

    try:
        response = await asyncio.wait_for(
            llm.chat(prompt, max_tokens=800),
            timeout=25.0
        )
        _update_scan_status(status_key, progress=85)

        if response:
            # 解析JSON
            import json, re
            json_match = re.search(r'\[[\s\S]*\]', response)
            if json_match:
                ai_results = json.loads(json_match.group(0))
                ai_map = {r["idx"]: r for r in ai_results if "idx" in r}

                for i, c in enumerate(top):
                    ai = ai_map.get(i + 1, {})
                    c["action"] = ai.get("action", "关注" if c["total_score"] >= 70 else "观望")
                    c["risk_level"] = ai.get("risk", "中")
                    c["position_pct"] = int(ai.get("position", 20))
                    c["ai_reason"] = ai.get("reason", "")

                # 不再单独计算买入区间（AI已给建议）
                for c in top:
                    c.setdefault("buy_zone", [round(c["price"]*0.97, 2), round(c["price"]*1.01, 2)])
                    c.setdefault("stop_loss", round(c["price"]*0.93, 2))
                    c.setdefault("stop_profit_1", round(c["price"]*1.08, 2))
                    c.setdefault("stop_profit_2", round(c["price"]*1.15, 2))
                    c.setdefault("signals", [])
                    c.setdefault("reason", c.get("ai_reason", ""))
                    c.setdefault("base_score", 0)
                    c.setdefault("strategy_score", 0)

                logger.info("AI enhanced %d stocks", len(ai_map))

    except asyncio.TimeoutError:
        logger.warning("AI batch analysis timeout")
        for c in top:
            c.setdefault("action", "关注" if c["total_score"] >= 70 else "观望")
            c.setdefault("risk_level", "中")
            c.setdefault("position_pct", 20)
            c.setdefault("ai_reason", "")
    except Exception as e:
        logger.warning("AI batch analysis error: %s", str(e)[:100])

    return candidates


# ============================================================
#  主扫描任务
# ============================================================
async def _run_scan_v3(task_id: str, cache_key: str, status_key: str, price_min: float, price_max: float, sectors: List[str]):
    """执行二阶段AI智能选股"""
    t0 = time.time()
    all_codes = get_all_stock_codes()
    if sectors:
        selected = set(sectors)
        all_codes = [code for code in all_codes if find_stock_sector(code) in selected]
    total = len(all_codes)

    _update_scan_status(status_key, status="running", phase="P1", progress=0, total=total, task_id=task_id)

    # Phase 1: 腾讯行情全量扫描
    logger.info("Scan v3: Phase 1 - %d stocks via Tencent", total)
    candidates = await _phase1_tencent_screen(all_codes, price_min, price_max, status_key)

    scan_state = cache.get(status_key) or {}
    coverage = scan_state.get("coverage", candidates[0].get("scan_coverage_pct", 0.0) if candidates else 0.0)
    valid_quote_count = int(scan_state.get("fetched", candidates[0].get("scan_valid_count", 0) if candidates else 0))
    coverage_warning = coverage < 50
    _update_scan_status(status_key, phase="P1-done", progress=60, p1_candidates=len(candidates), coverage=coverage, coverage_warning=coverage_warning)

    # 过滤低分
    results = [c for c in candidates if c["total_score"] >= SCAN_RESULT_MIN]

    if not results:
        result = {"recommendations": [], "total_scanned": total, "total_recommended": 0,
                  "duration_ms": int((time.time()-t0)*1000), "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "data_quality": {"coverage_pct": coverage, "configured_count": total, "valid_count": valid_quote_count, "level": "poor" if coverage < 50 else "good" if coverage >= 90 else "partial"},
                  "phases": {"p1_candidates": len(candidates), "ai_enhanced": 0}}
        cache.set(cache_key, result, SCAN_CACHE_TTL)
        _update_scan_status(status_key, status="done", phase="done", progress=100, result_count=0, coverage=coverage, coverage_warning=True)
        return

    # Phase 2: DeepSeek AI批量分析
    logger.info("Scan v3: Phase 2 - AI on %d stocks", min(SCAN_AI_TOP_N, len(results)))
    results = await _phase2_ai_analyze(results, status_key)

    duration_ms = int((time.time() - t0) * 1000)
    ai_count = sum(1 for r in results[:SCAN_AI_TOP_N] if r.get("ai_reason"))

    result = {
        "recommendations": results,
        "total_scanned": total,
        "total_recommended": len(results),
        "duration_ms": duration_ms,
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_quality": {"coverage_pct": coverage, "configured_count": total, "valid_count": valid_quote_count, "level": "good" if coverage >= 90 else "partial" if coverage >= 50 else "poor"},
        "ai_enhanced": ai_count,
        "probability_note": "上涨概率为未来1-5个交易日的启发式估算，基于行情快照因子，未经历史概率校准，不构成收益保证或投资建议。",
        "phases": {"p1_candidates": len(candidates), "final_results": len(results), "ai_enhanced": ai_count},
    }

    cache.set(cache_key, result, SCAN_CACHE_TTL)
    _update_scan_status(status_key, status="done", progress=100, phase="done",
                       result_count=len(results), duration_ms=duration_ms, ai_enhanced=ai_count, coverage=coverage, coverage_warning=coverage_warning)

    logger.info("Scan v3 done: %d/%d recommended in %dms (P1:%d AI:%d, coverage:%.1f%%)",
                len(results), total, duration_ms, len(candidates), ai_count, coverage)


async def _run_scan_guarded(task_id: str, cache_key: str, status_key: str, price_min: float, price_max: float, sectors: List[str]):
    try:
        await _run_scan_v3(task_id, cache_key, status_key, price_min, price_max, sectors)
    except Exception as exc:
        logger.exception("Scan v3 task failed: %s", task_id)
        _update_scan_status(status_key, status="error", phase="error", progress=100, task_id=task_id, error="扫描任务失败，已记录服务端日志，请稍后重试。")


# ============================================================
#  路由
# ============================================================
@router.get("/scan")
async def scan_market(price_min: float = SCAN_DEFAULT_PRICE_MIN, price_max: float = SCAN_DEFAULT_PRICE_MAX, sectors: str = ""):
    """智能选股 v3：腾讯行情 + DeepSeek AI 二阶段引擎
    
    参数：
    - price_min: 最低价格（默认0元）
    - price_max: 最高价格（默认200元）
    - sectors: 逗号分隔的行业 key，可选
    """
    price_min = max(0.0, float(price_min))
    price_max = float(price_max)
    if price_max < price_min:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="price_max 不能小于 price_min")
    sector_list = [s.strip() for s in sectors.split(",") if s.strip()]
    unknown = sorted(set(sector_list) - set(SECTORS.keys()))
    if unknown:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="未知板块: " + ",".join(unknown))
    request_key = _make_scan_key(price_min, price_max, sector_list)
    cache_key = f"{SCAN_CACHE_PREFIX}:{request_key}"
    status_key = f"{SCAN_STATUS_PREFIX}:{request_key}"

    cached = cache.get(cache_key)
    if cached is not None:
        return {"ok": True, "data": cached, "cached": True}

    status = cache.get(status_key)
    if status and status.get("status") == "running":
        return {"ok": True, "pending": True, "status": status}
    if status and status.get("status") == "error":
        cache.delete(status_key)

    task_id = f"scanv3_{int(time.time())}_{abs(hash(request_key)) % 100000}"
    asyncio.create_task(_run_scan_guarded(task_id, cache_key, status_key, price_min, price_max, sector_list))

    total_codes = len(get_all_stock_codes())
    if sector_list:
        selected = set(sector_list)
        total_codes = sum(1 for code in get_all_stock_codes() if find_stock_sector(code) in selected)

    return {"ok": True, "pending": True, "status": {
        "status": "started", "progress": 0, "total": total_codes,
        "task_id": task_id, "started_at": datetime.now().isoformat(),
        "price_min": price_min, "price_max": price_max, "sectors": sector_list,
    }}


@router.get("/scan/status")
async def scan_status(price_min: float = SCAN_DEFAULT_PRICE_MIN, price_max: float = SCAN_DEFAULT_PRICE_MAX, sectors: str = ""):
    """智能扫描状态：按价格区间与板块筛选后的候选池概况（含过滤原因与样本量）"""
    price_min = max(0.0, float(price_min))
    price_max = float(price_max)
    if price_max < price_min:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="price_max 不能小于 price_min")
    sector_list = [s.strip() for s in sectors.split(",") if s.strip()]
    unknown = sorted(set(sector_list) - set(SECTORS.keys()))
    if unknown:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="未知板块: " + ",".join(unknown))
    status_key = f"{SCAN_STATUS_PREFIX}:{_make_scan_key(price_min, price_max, sector_list)}"
    s = cache.get(status_key)
    if s is None:
        return {"ok": True, "status": {"status": "idle"}}
    return {"ok": True, "status": s}
