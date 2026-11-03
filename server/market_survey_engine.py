"""
DAPENG Alpha - 动态市场调查引擎 (V16.0 真实数据版)

AI agent 自动执行「收集 → 分析 → 汇总」三段式市场调查：
1. 收集 Collect  — 复用大盘纵览真实聚合（指数/宽度/板块/资金流，60s 缓存）
                   + AkShare 宏观指标 + 东方财富财经要闻
2. 分析 Analyze  — 规则引擎诊断市场状态、板块轮动阶段、真实异动预警
3. 汇总 Synthesize — DeepSeek LLM 生成自然语言调查总结（无 Key 时规则模板兜底）

上游数据缺失时显式降级并标注，绝不生成虚构行情。
结果缓存 10 分钟；每次调查写入历史档案供 AI 主脑（brain）学习。

v17（2026-11-02）：把「市场情绪追踪」并入调查流程——
* 采集：情绪追踪（sentiment-flow.v1）与行情/宏观/情报同批取，作为**状态输入**；
* 诊断：市场结构状态（regime）与情绪走向（sentiment.regime）**交叉校验**，
  两者背离时生成「情绪-结构背离」预警（背离往往比一致更有信息量）；
* 结论：synthesis 显式引用情绪走向；
* 连接：每个板块标注它的数据来源与时点（sector_linkage），使「板块→数据源」
  的依赖在 payload 里可见，而不是散落在代码里。
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from server.core.numbers import safe_float as _finite

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 分钟
_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "market_survey_history.json",
)
_HISTORY_KEEP = 50
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


#: 调查流水线阶段（顺序即执行顺序，前端与文档据此渲染）
_SURVEY_STAGES = (
    ("collect", "采集", "行情 / 宏观 / 情报 / 情绪追踪"),
    ("diagnose", "诊断", "市场状态 / 板块轮动 / 异动 / 背离校验"),
    ("conclude", "结论", "规则 + LLM 汇总"),
)

#: 调查状态 → 情绪温度（用于背离判定）
_SURVEY_TONE = {
    "强势上行": "bullish", "震荡上行": "bullish",
    "区间震荡": "neutral",
    "偏弱调整": "bearish", "弱势下行": "bearish",
    "数据不足": "unknown",
}
_FLOW_TONE = {
    "heating": "bullish", "repairing": "bullish", "recovering": "bullish",
    "overheated": "bullish", "steady": "neutral", "flat": "neutral",
    "cooling": "bearish", "cooling_from_hot": "bearish",
    "weakening": "bearish", "freezing": "bearish",
    "unknown": "unknown",
}


def _sentiment_brief(flow: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 sentiment-flow 压成调查可引用的一段（保留原始判据，不二次加工）。"""
    if not flow or not flow.get("ok"):
        return {
            "available": False,
            "level": None,
            "regime": None,
            "note": (flow or {}).get("message") or "情绪追踪不可用",
        }
    regime = flow.get("regime") or {}
    turning = flow.get("turning_point") or {}
    return {
        "available": True,
        "version": flow.get("version"),
        "trace_id": flow.get("trace_id"),
        "as_of": flow.get("as_of"),
        "level": flow.get("level"),
        "status": flow.get("status"),
        "band": (flow.get("band") or {}).get("label"),
        "regime": {"key": regime.get("key"), "label": regime.get("label"),
                   "tone": regime.get("tone"), "note": regime.get("note")},
        "delta_short": flow.get("delta_short"),
        "delta_day": flow.get("delta_day"),
        "slope_per_hour": flow.get("slope_per_hour"),
        "z20": flow.get("z20"),
        "percentile20": flow.get("percentile20"),
        "sample_n": flow.get("sample_n"),
        "persistence": flow.get("persistence"),
        "turning_point": turning if turning.get("detected") else None,
        "drivers": flow.get("drivers") or [],
        "drags": flow.get("drags") or [],
        "flags": flow.get("flags") or [],
    }


def _divergence(regime: Dict[str, Any], flow: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """结构状态与情绪走向的交叉校验。

    同向时只做记录（信息量低）；背离时是**可交易信号**：
    例如结构仍「强势上行」但情绪已「高位回落」，说明推动力在衰减。
    """
    survey_status = (regime or {}).get("status")
    survey_tone = _SURVEY_TONE.get(str(survey_status), "unknown")
    flow_tone = "unknown"
    flow_label = None
    if flow and flow.get("ok"):
        key = ((flow.get("regime") or {}).get("key")) or "unknown"
        flow_tone = _FLOW_TONE.get(key, "unknown")
        flow_label = (flow.get("regime") or {}).get("label")
    if "unknown" in (survey_tone, flow_tone):
        return {"detected": False, "available": False,
                "survey_tone": survey_tone, "sentiment_tone": flow_tone,
                "note": "结构或情绪口径缺失，不做背离判定"}
    detected = survey_tone != flow_tone and "neutral" not in (survey_tone, flow_tone)
    note = (
        f"结构「{survey_status}」与情绪「{flow_label}」背离："
        + ("结构偏强但情绪在退，注意推动力衰减" if survey_tone == "bullish"
           else "结构偏弱但情绪在修复，注意提前反转")
    ) if detected else f"结构「{survey_status}」与情绪「{flow_label}」同向或均中性"
    return {
        "detected": detected,
        "available": True,
        "survey_tone": survey_tone,
        "sentiment_tone": flow_tone,
        "note": note,
    }


def _sector_linkage(
    dash: Optional[Dict[str, Any]],
    rotation: List[Dict[str, Any]],
    coverage: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """板块 → 数据源 的连接描述（每个板块各来自哪张表、什么时点）。"""
    sectors = (dash or {}).get("sectors") or []
    rot_by_name = {str(r.get("sector")): r for r in (rotation or [])}
    as_of = (dash or {}).get("update_time") or (dash or {}).get("as_of")
    cov_as_of = ((coverage or {}).get("as_of") if isinstance(coverage, dict) else None)
    rows: List[Dict[str, Any]] = []
    for item in sectors[:8]:
        name = str(item.get("name") or "")
        rot = rot_by_name.get(name) or {}
        rows.append({
            "sector": name,
            "avg_chg": item.get("avg_chg"),
            "amount": item.get("amount"),
            "stage": rot.get("stage") or "未分类",
            "rank_change": rot.get("rank_change"),
            "sources": {
                "quotes": {"provider": "market_coverage / 东财板块", "as_of": as_of},
                "rotation": {"provider": "market_scanner.build_rotation + sector_history",
                             "as_of": as_of, "window": "1 日 / 5 日 / 20 日"},
                "money_flow": {"provider": "market_scanner.build_money_flow_proxy",
                               "as_of": cov_as_of, "mode": "实测或注意力代理"},
            },
            "linked": bool(rot),
        })
    return rows


async def _sentiment_flow_safe() -> Optional[Dict[str, Any]]:
    """情绪追踪取数：失败不拖垮调查（调查是主链路，情绪是输入之一）。"""
    try:
        from server.sentiment_tracker import get_sentiment_flow

        return await asyncio.wait_for(get_sentiment_flow(), timeout=12)
    except Exception as exc:
        logger.debug("survey: sentiment flow unavailable: %s", exc)
        return None


async def _dashboard_data() -> Optional[Dict[str, Any]]:
    """复用大盘纵览的缓存聚合结果（避免重复抓取全样本行情）。"""
    try:
        from server.cache import cache
        from server.services.market_dashboard import build_dashboard as _build_dashboard, CACHE_KEY, CACHE_TTL

        async def fetch():
            try:
                return await _build_dashboard()
            except Exception as exc:
                logger.warning("survey: dashboard build failed: %s", exc)
                return None

        return await cache.get_or_fetch(CACHE_KEY, fetch, CACHE_TTL)
    except Exception as exc:
        logger.warning("survey: dashboard data unavailable: %s", exc)
        return None


def _limit_threshold(code: str, name: str) -> float:
    numeric = code[2:] if code.startswith(("sh", "sz")) else code
    if "ST" in str(name).upper():
        return 4.8
    if numeric.startswith(("300", "301", "688")):
        return 19.5
    return 9.5


# ------------------------------------------------------------------
# 分析层：市场状态诊断（全部基于真实数据）
# ------------------------------------------------------------------

def _diagnose_regime(dash: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not dash:
        return {
            "status": "数据不足", "confidence": 0,
            "main_driver": "实时行情暂不可用",
            "liquidity_env": "未知",
            "risk_assessment": "数据缺失期间请降低操作频率",
        }

    judgment = dash.get("judgment") or {}
    breadth = dash.get("breadth") or {}
    coverage = dash.get("coverage") or {}
    turnover = dash.get("turnover") or {}
    indices = dash.get("indices") or []
    sectors = dash.get("sectors") or []
    risk_tags = dash.get("risk_tags") or []

    score = judgment.get("score")
    up_ratio = _finite(breadth.get("up_ratio"))
    limit_up = int(_finite(breadth.get("limit_up")))
    limit_down = int(_finite(breadth.get("limit_down")))
    amount_yi = _finite(turnover.get("amount")) / 1e8
    avg_turnover = _finite(turnover.get("avg_turnover"))
    cov_ratio = _finite(coverage.get("ratio"))

    index_chgs = [_finite(i.get("chg")) for i in indices if _finite(i.get("price")) > 0]
    avg_index = sum(index_chgs) / len(index_chgs) if index_chgs else 0.0

    if score is None:
        status = "数据不足"
    elif score >= 68:
        status = "强势上行"
    elif score >= 55:
        status = "震荡上行"
    elif score >= 42:
        status = "区间震荡"
    elif score >= 30:
        status = "偏弱调整"
    else:
        status = "弱势下行"

    confidence = int(max(30, min(95, 45 + cov_ratio * 0.45))) if index_chgs else 30

    top_sectors = [s for s in sectors[:2] if _finite(s.get("avg_chg")) > 0]
    if top_sectors:
        lead_desc = "、".join(
            f"{s.get('name')}({s.get('avg_chg', 0):+.1f}%)" for s in top_sectors
        )
        main_driver = f"领涨方向：{lead_desc}；涨停 {limit_up} 家 / 跌停 {limit_down} 家"
    else:
        main_driver = f"无明显领涨主线；涨停 {limit_up} 家 / 跌停 {limit_down} 家"

    liquidity_env = (
        f"样本成交额约 {amount_yi:.0f} 亿元，平均换手 {avg_turnover:.1f}%，"
        f"上涨家数占比 {up_ratio:.0f}%"
    )

    if risk_tags:
        risk_assessment = "；".join(str(t.get("text", "")) for t in risk_tags[:3])
    else:
        risk_assessment = f"指数平均涨跌 {avg_index:+.2f}%，未发现高优先级系统性风险"

    return {
        "status": status,
        "confidence": confidence,
        "main_driver": main_driver,
        "liquidity_env": liquidity_env,
        "risk_assessment": risk_assessment,
        "score": score,
    }


def _stage_of(avg_chg: float, up_ratio: float) -> str:
    if avg_chg >= 2.0 and up_ratio >= 70:
        return "主升加速"
    if avg_chg >= 0.8:
        return "震荡蓄势"
    if avg_chg > -0.8:
        return "横盘分化"
    return "调整回落"


def _analyze_rotation(dash: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not dash:
        return []
    sectors = dash.get("sectors") or []
    if not sectors:
        return []
    picked = sectors[:4] + ([sectors[-1]] if len(sectors) > 4 and sectors[-1] not in sectors[:4] else [])
    rotation = []
    for s in picked:
        avg_chg = _finite(s.get("avg_chg"))
        up_ratio = _finite(s.get("up_ratio"))
        amount_yi = _finite(s.get("amount")) / 1e8
        leader = s.get("leader") or {}
        rotation.append({
            "sector": s.get("name", "--"),
            "stage": _stage_of(avg_chg, up_ratio),
            "flow_in": f"成交 {amount_yi:.1f}亿",
            "leading_stock": f"{leader.get('name', '--')} ({_finite(leader.get('chg')):+.1f}%)",
            "catalyst": f"上涨占比 {up_ratio:.0f}% · 样本 {int(_finite(s.get('count')))} 只 · 平均涨跌 {avg_chg:+.2f}%",
            # 数值字段（供前端散点图坐标映射，增量兼容）
            "avg_chg": round(avg_chg, 2),
            "up_ratio": round(up_ratio, 1),
            "amount_yi": round(amount_yi, 1),
            "count": int(_finite(s.get("count"))),
        })
    return rotation


def _build_scatter(dash: Optional[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """板块轮动散点数据集：x=平均涨跌，y=上涨家数占比，气泡=成交额，颜色=阶段。

    覆盖比 rotation 列表更广的样本（列表只展示前 5），供散点图完整呈现市场结构。
    """
    if not dash:
        return []
    sectors = dash.get("sectors") or []
    points: List[Dict[str, Any]] = []
    for s in sectors[:limit]:
        avg_chg = _finite(s.get("avg_chg"))
        up_ratio = _finite(s.get("up_ratio"))
        amount_yi = _finite(s.get("amount")) / 1e8
        leader = s.get("leader") or {}
        points.append({
            "sector": s.get("name", "--"),
            "stage": _stage_of(avg_chg, up_ratio),
            "avg_chg": round(avg_chg, 2),
            "up_ratio": round(up_ratio, 1),
            "amount_yi": round(amount_yi, 1),
            "count": int(_finite(s.get("count"))),
            "leader_name": leader.get("name", "--"),
            "leader_chg": round(_finite(leader.get("chg")), 2),
        })
    return points


def _detect_alerts(dash: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """真实异动预警：涨停雷达 / 急涨异动 / 高换手 / 领跌警示 / 资金流。"""
    if not dash:
        return []
    now_hms = datetime.now().strftime("%H:%M:%S")
    alerts: List[Dict[str, Any]] = []
    quotes = dash.get("quotes") or []

    limit_ups = [q for q in quotes if _finite(q.get("chg")) >= _limit_threshold(q.get("code", ""), q.get("name", ""))]
    for q in limit_ups[:3]:
        alerts.append({
            "time": now_hms, "type": "涨停雷达", "level": "high",
            "code": q.get("code"), "name": q.get("name"),
            "desc": f"封涨停（{_finite(q.get('chg')):+.1f}%），成交额 {_finite(q.get('amount')) / 1e8:.1f} 亿元，换手 {_finite(q.get('turnover')):.1f}%",
        })

    surges = [q for q in quotes if 5 <= _finite(q.get("chg")) < _limit_threshold(q.get("code", ""), q.get("name", ""))]
    for q in surges[:2]:
        alerts.append({
            "time": now_hms, "type": "急涨异动", "level": "medium",
            "code": q.get("code"), "name": q.get("name"),
            "desc": f"盘中急涨 {_finite(q.get('chg')):+.1f}%，成交 {_finite(q.get('amount')) / 1e8:.1f} 亿元，关注量能持续性",
        })

    hot = [q for q in quotes if _finite(q.get("turnover")) >= 15]
    for q in hot[:2]:
        alerts.append({
            "time": now_hms, "type": "高换手", "level": "low",
            "code": q.get("code"), "name": q.get("name"),
            "desc": f"换手率达 {_finite(q.get('turnover')):.1f}%，涨跌 {_finite(q.get('chg')):+.1f}%，筹码剧烈交换注意分歧风险",
        })

    dumps = sorted((q for q in quotes if _finite(q.get("chg")) <= -5), key=lambda x: _finite(x.get("chg")))[:2]
    for q in dumps:
        alerts.append({
            "time": now_hms, "type": "领跌警示", "level": "medium",
            "code": q.get("code"), "name": q.get("name"),
            "desc": f"急跌 {_finite(q.get('chg')):+.1f}%，若持仓相关标的注意止损纪律",
        })

    flow = dash.get("capital_flow") or {}
    if flow.get("available"):
        total = _finite(flow.get("flow_a")) + _finite(flow.get("flow_b"))
        direction = "净流入" if total >= 0 else "净流出"
        alerts.append({
            "time": now_hms, "type": "资金流", "level": "low" if abs(total) < 5e9 else "medium",
            "code": "INDEX", "name": "沪深主板",
            "desc": f"指数资金流口径合计{direction} {abs(total) / 1e8:.1f} 亿元（原始分类值，未推断主力口径）",
        })

    return alerts[:8]


def _apply_full_market(dash: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """用后台全 A 覆盖替换 447 只核心样本的广度与成交额，并重算情绪分。"""
    if not dash:
        return dash, None
    try:
        from server.market_coverage import get_cached_coverage

        coverage = get_cached_coverage(max_age=1800)
        ashare = (coverage or {}).get("ashare") or {}
        quotes = ashare.get("quotes") or {}
        if not coverage or not coverage.get("ok") or (quotes.get("valid") or 0) < 3000:
            return dash, coverage
        effective = dict(dash)
        effective["breadth"] = dict(quotes)
        effective["turnover"] = dict(dash.get("turnover") or {})
        if quotes.get("amount") is not None:
            effective["turnover"]["amount"] = quotes.get("amount")
        indices = [i for i in (dash.get("indices") or []) if _finite(i.get("chg")) is not None and (_finite(i.get("price")) or 0) > 0]
        index_chgs = [_finite(i.get("chg")) for i in indices]
        avg_index = sum(index_chgs) / len(index_chgs) if index_chgs else 0.0
        up_ratio = _finite(quotes.get("up_ratio"))
        limit_up = int(_finite(quotes.get("limit_up")) or 0)
        limit_down = int(_finite(quotes.get("limit_down")) or 0)
        if up_ratio is not None:
            score = round(max(0, min(100, 50 + (up_ratio - 50) * 0.55 + avg_index * 7 + (limit_up - limit_down) * 0.3)))
        else:
            score = None
        judgment = dict(dash.get("judgment") or {})
        judgment["score"] = score
        effective["judgment"] = judgment
        effective["breadth_source"] = "market-coverage.v1"
        effective["full_market"] = {
            "total_universe": ashare.get("total_universe"),
            "quality": ashare.get("quality"),
            "fundamentals": ashare.get("fundamentals"),
            "industries": (ashare.get("industries") or [])[:10],
        }
        return effective, coverage
    except Exception as exc:
        logger.warning("survey: full market merge failed: %s", exc)
        return dash, None


def _coverage_brief(coverage: Optional[Dict[str, Any]]) -> str:
    if not coverage:
        return "全市场覆盖尚未就绪"
    a = coverage.get("ashare") or {}
    q = a.get("quotes") or {}
    f = a.get("fundamentals") or {}
    quality = a.get("quality") or {}
    g = ((coverage.get("global") or {}).get("coverage") or {})
    m = ((coverage.get("macro") or {}).get("coverage") or {})
    rates = [
        f"{x.get('label')} {x.get('value')}{x.get('unit')}"
        for x in ((coverage.get("macro") or {}).get("global") or [])
        if x.get("key") in ("ust10y", "ust2y", "effr", "ecb_dfr") and x.get("value") is not None
    ]
    return (
        f"A股 {q.get('valid')}/{a.get('total_universe')} 只有效报价，"
        f"上涨 {q.get('up')} / 下跌 {q.get('down')}，PE中位 {f.get('pe_median')}，"
        f"PB中位 {f.get('pb_median')}，ROE中位 {f.get('roe_median')}%；"
        f"覆盖等级 {quality.get('level')}（报价 {quality.get('quote_coverage')}%、基本面 {quality.get('financial_coverage')}%）。"
        f"全球：指数 {g.get('indices')} / 国家代理 {g.get('countries')} / 行业代理 {g.get('industries')}；"
        f"宏观 {m.get('global_available')}/{m.get('global_total')} 项可用"
        + (("；" + "，".join(rates)) if rates else "") + "。"
    )


async def _load_intel() -> Optional[Dict[str, Any]]:
    """读取主脑情报评估（存在且为中/高风险时融入调查）"""
    try:
        from server.trading import trading_facade
        store = trading_facade.store
        if store is None:
            return None
        return await store.kv_get("brain_intel", None)
    except Exception:
        return None


# ------------------------------------------------------------------
# 汇总层：DeepSeek AI 自然语言调查总结
# ------------------------------------------------------------------

def _rule_synthesis(regime: Dict[str, Any], rotation: List[Dict[str, Any]]) -> str:
    lead = rotation[0]["sector"] if rotation else "暂无主线"
    return (
        f"【市场调查总结（规则引擎生成）】\n"
        f"当前市场状态：{regime['status']}。{regime['main_driver']}。"
        f"{regime['liquidity_env']}。相对最强方向为{lead}。"
        f"风险提示：{regime['risk_assessment']}。"
        f"（未配置 DeepSeek API Key，本总结由规则引擎基于真实行情生成）"
    )


async def _ai_synthesis(
    regime: Dict[str, Any],
    rotation: List[Dict[str, Any]],
    alerts: List[Dict[str, Any]],
    macro: Optional[Dict[str, Any]],
    news: Optional[Dict[str, Any]],
    intel: Optional[Dict[str, Any]] = None,
    coverage: Optional[Dict[str, Any]] = None,
) -> tuple[str, bool]:
    """DeepSeek 生成调查总结；失败返回 (规则总结, False)。"""
    fallback = _rule_synthesis(regime, rotation)
    try:
        from server.llm import get_llm_client
        client = get_llm_client()
        if not client.api_key:
            return fallback, False

        news_titles = "、".join(
            str(n.get("title", ""))[:30]
            for n in ((news or {}).get("data") or [])[:5]
            if n.get("title")
        ) or "暂无"
        macro_brief = "；".join(
            f"{m.get('name')}{m.get('value')}({m.get('status')})"
            for m in ((macro or {}).get("data") or [])[:4]
        ) or "暂无"
        rotation_brief = "；".join(
            f"{r['sector']}{r['stage']}" for r in rotation[:4]
        ) or "暂无"
        alert_brief = "；".join(
            f"{a['type']}:{a['name']}" for a in alerts[:4]
        ) or "无显著异动"
        intel_brief = "无"
        if intel and intel.get("brief"):
            intel_brief = f"风险{intel.get('risk_level')}({intel.get('risk_score')}分)：{intel.get('brief', '')[:80]}"

        prompt = f"""你是 A 股首席策略分析师，正在做今日动态市场调查汇总。以下全部为系统实时采集的真实数据，不得虚构任何数字。

【市场状态】{regime['status']}（情绪分 {regime.get('score') or '--'}）
【全市场覆盖】{_coverage_brief(coverage)}
【资金与宽度】{regime['liquidity_env']}
【板块轮动】{rotation_brief}
【盘中异动】{alert_brief}
【全球情报评估】{intel_brief}
【宏观环境】{macro_brief}
【财经要闻】{news_titles}
【风险提示】{regime['risk_assessment']}

请输出：
1. 一段 120 字以内的市场全景调查总结（状态 + 主线 + 资金特征，若情报风险为中/高须提及）
2. 一条 40 字以内的操作建议（仓位与方向，稳健优先）
用中文，直接输出两段文字，不要标题、不要列表符号、不要引用原文。"""

        system_prompt = "你是严谨的 A 股策略分析师，只基于给定数据总结，禁止编造数字与板块。"
        text = await client.chat(prompt, system_prompt, max_tokens=400)
        if text and len(text.strip()) >= 30:
            return text.strip(), True
        return fallback, False
    except Exception as exc:
        logger.warning("survey: AI synthesis failed: %s", exc)
        return fallback, False


def _append_history(payload: Dict[str, Any]) -> None:
    """调查档案：供 AI 主脑检索学习（保留最近 50 条）。"""
    try:
        os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
        history: List[Dict[str, Any]] = []
        if os.path.exists(_HISTORY_PATH):
            with open(_HISTORY_PATH, "r", encoding="utf-8") as fh:
                history = json.load(fh) or []
        history.append({
            "ts": payload["update_time"],
            "regime": payload["regime"].get("status"),
            "score": payload["regime"].get("score"),
            "ai_powered": payload.get("ai_powered", False),
            "synthesis_brief": (payload.get("synthesis") or "")[:120],
            "alerts_count": len(payload.get("alerts") or []),
            "data_scope": payload.get("data_scope"),
            "coverage_pct": ((payload.get("data_coverage") or {}).get("quality") or {}).get("quote_coverage"),
        })
        history = history[-_HISTORY_KEEP:]
        with open(_HISTORY_PATH, "w", encoding="utf-8") as fh:
            json.dump(history, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        logger.warning("survey: history append failed: %s", exc)


def load_survey_history(limit: int = 20) -> List[Dict[str, Any]]:
    try:
        if os.path.exists(_HISTORY_PATH):
            with open(_HISTORY_PATH, "r", encoding="utf-8") as fh:
                return (json.load(fh) or [])[-limit:]
    except Exception as e:
        logger.warning("survey: load_survey_history failed: %s", e)
    return []


class MarketSurveyEngine:
    """动态市场调查引擎：真实数据收集 → 规则分析 → AI 汇总"""

    @classmethod
    async def get_market_survey(cls, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
            return _cache["data"]

        from server.ak_market import ak_hub

        t_collect = time.perf_counter()
        dash, macro, news, flow_raw = await asyncio.gather(
            _dashboard_data(),
            ak_hub.get_macro_data(),
            ak_hub.get_notices_and_news(limit=10),
            _sentiment_flow_safe(),
            return_exceptions=True,
        )
        if isinstance(dash, Exception):
            dash = None
        if isinstance(macro, Exception):
            macro = None
        if isinstance(news, Exception):
            news = None
        flow = None if isinstance(flow_raw, Exception) else flow_raw
        collect_ms = round((time.perf_counter() - t_collect) * 1000, 1)

        t_diag = time.perf_counter()
        effective_dash, coverage = _apply_full_market(dash)
        regime = _diagnose_regime(effective_dash)
        rotation = _analyze_rotation(effective_dash)
        alerts = _detect_alerts(effective_dash)

        # 情绪追踪是「状态输入」：与结构状态交叉校验，背离时升级为预警
        sentiment = _sentiment_brief(flow)
        divergence = _divergence(regime, flow)

        # 判断层「实际用到的输入」必须显式暴露：跨层对证要拿它和事实层比，
        # 而不是让对证去猜（此前降级时这里为空，导致 4 项无法比对）。
        _breadth = (effective_dash or {}).get("breadth") or {}
        _turnover = (effective_dash or {}).get("turnover") or {}
        facts = {
            "up": _breadth.get("up"),
            "down": _breadth.get("down"),
            "flat": _breadth.get("flat"),
            "up_ratio": _breadth.get("up_ratio"),
            "limit_up": _breadth.get("limit_up"),
            "limit_down": _breadth.get("limit_down"),
            "median_chg": _breadth.get("median_chg"),
            "amount": _turnover.get("amount"),
            "source": (effective_dash or {}).get("breadth_source") or ("market-coverage.v1" if coverage else "core_sample"),
            "as_of": (effective_dash or {}).get("update_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scope": "full_a_share" if coverage else "core_sample",
        }
        if divergence.get("detected"):
            alerts.insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "type": "情绪-结构背离",
                "level": "medium",
                "code": "DIVERGENCE",
                "name": f"情绪{(sentiment.get('regime') or {}).get('label') or '--'}",
                "desc": divergence.get("note", "")[:120],
            })
            alerts = alerts[:8]

        # 情报融合：中/高全球风险 → 置顶预警（情报 Agent 产出，主脑记忆提供）
        intel = await _load_intel()
        if intel and intel.get("risk_level") in ("中", "高"):
            alerts.insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "type": "情报预警",
                "level": "high" if intel["risk_level"] == "高" else "medium",
                "code": "INTEL",
                "name": f"全球风险{intel['risk_level']}（{intel.get('risk_score', 0)}分）",
                "desc": str(intel.get("brief", ""))[:90],
            })
            alerts = alerts[:8]

        diag_ms = round((time.perf_counter() - t_diag) * 1000, 1)
        t_concl = time.perf_counter()
        synthesis, ai_powered = await _ai_synthesis(regime, rotation, alerts, macro, news, intel, coverage)
        if sentiment.get("available"):
            persistence = sentiment.get("persistence") or {}
            ticks = int(persistence.get("ticks") or 0)
            direction = persistence.get("direction")
            if ticks and direction in ("up", "down"):
                persist_text = f"连续 {ticks} 次{'上行' if direction == 'up' else '下行'}"
            else:
                persist_text = "方向不明（无连续同向变化）"
            line = (
                f"\n【情绪追踪】{sentiment.get('level')} 分（{sentiment.get('band')}），"
                f"{((sentiment.get('regime') or {}).get('label')) or '--'}"
                f"；近端变化 {sentiment.get('delta_short')}，"
                f"斜率 {sentiment.get('slope_per_hour')} 分/时，"
                f"{persist_text}。"
            )
            turning = sentiment.get("turning_point")
            if turning:
                line += f"\n【拐点】{turning.get('note')}。"
            if divergence.get("detected"):
                line += f"\n【背离】{divergence.get('note')}。"
            synthesis = f"{synthesis or ''}{line}"
        conclude_ms = round((time.perf_counter() - t_concl) * 1000, 1)

        payload = {
            "ok": True,
            # 契约三件套（version / as_of / trace_id）必须顶层可见：
            # 溯源与对证都靠它们判断「这一层是哪一版、什么时候、挂在哪个快照上」。
            "version": "survey-pipeline.v1",
            "trace_id": (sentiment.get("trace_id") or (dash or {}).get("trace_id")),
            "as_of": (effective_dash or {}).get("update_time")
                     or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "engine": "DAPENG AI Market Survey (Real-Data V16)",
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_source": "realtime" if dash else "degraded",
            "ai_powered": ai_powered,
            "regime": regime,
            "rotation": rotation,
            "scatter": _build_scatter(dash),
            "alerts": alerts,
            "synthesis": synthesis,
            "sentiment": sentiment,
            "facts": facts,
            "divergence": divergence,
            "sector_linkage": _sector_linkage(effective_dash, rotation, coverage),
            "pipeline": {
                "version": "survey-pipeline.v1",
                "stages": [
                    {"key": k, "label": label, "detail": detail,
                     "ms": {"collect": collect_ms, "diagnose": max(0.0, diag_ms),
                            "conclude": conclude_ms}[k]}
                    for k, label, detail in _SURVEY_STAGES
                ],
                "total_ms": round(collect_ms + max(0.0, diag_ms) + conclude_ms, 1),
            },
            "inputs": {
                "dashboard": {"available": bool(dash), "source": "market-dashboard"},
                "macro": {"available": bool(macro), "source": "akshare macro"},
                "news": {"available": bool(news), "source": "eastmoney notices"},
                "sentiment": {"available": bool(sentiment.get("available")),
                              "source": "sentiment-flow.v1",
                              "trace_id": sentiment.get("trace_id")},
                "coverage": {"available": bool(coverage), "source": "market-coverage.v1"},
            },
            "data_scope": "full_a_share" if coverage else "core_sample",
            "data_coverage": {
                "status": "ok" if coverage else "not_ready",
                "summary": _coverage_brief(coverage),
                "ashare": (coverage or {}).get("ashare", {}).get("quotes"),
                "quality": (coverage or {}).get("ashare", {}).get("quality"),
                "fundamentals": (coverage or {}).get("ashare", {}).get("fundamentals"),
                "global": (coverage or {}).get("global", {}).get("coverage"),
                "macro": (coverage or {}).get("macro", {}).get("coverage"),
            },
        }
        _cache["ts"] = now
        _cache["data"] = payload
        await asyncio.to_thread(_append_history, payload)
        # 判断层也要向总线报到：此前只有事实层/情绪/专家会 publish，
        # survey 从不记账 → 链路视图里它永远 stale、能力图把它算成 degraded。
        try:
            from server.data_bus import bus

            await bus.publish(
                "survey", payload, trigger="request",
                elapsed_ms=(payload.get("pipeline") or {}).get("total_ms"),
                trace=payload.get("as_of"),
            )
        except Exception as exc:  # 总线不可用不影响调查结果
            logger.debug("data bus publish(survey) skipped: %s", exc)
        return payload


market_survey_engine = MarketSurveyEngine()
