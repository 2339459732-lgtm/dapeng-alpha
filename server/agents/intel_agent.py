"""DAPENG Alpha - 情报分析 AI Agent (V1.0)

采集世界局势、地缘政治动荡、重大新闻事件，评估其对市场的潜在影响：
1. 采集 Collect  — 东方财富财经要闻流 + 关键词过滤（地缘/宏观/避险）
2. 佐证 CrossCheck — 全球资产快照（黄金/原油/美元/美股是市场对情报的"投票"）
3. 评估 Assess  — DeepSeek LLM 输出风险等级、传导渠道、受影响板块、观察点
4. 融合 Fuse     — 写入主脑记忆（brain_intel），供主脑研判、市场调查总结与预警复用

无 LLM Key 时按关键词热度 + 资产异动规则降级评估，并显式标注 ai_powered=false。
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 地缘/宏观/重大事件关键词表
INTEL_KEYWORDS = [
    "关税", "制裁", "战争", "冲突", "地缘", "央行", "美联储", "加息", "降息",
    "选举", "导弹", "核", "原油", "黄金", "避险", "贸易战", "停火", "协议",
    "政变", "恐袭", "军演", "封锁", "稀土", "芯片", "禁令", "债务", "违约",
    "通胀", "CPI", "非农", "汇率", "人民币", "美元", "危机", "衰退",
]

_HISTORY_KEEP = 20


def _filter_intel_news(news_items: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """按关键词命中数排序过滤情报类新闻"""
    scored = []
    for item in news_items:
        title = str(item.get("title", ""))
        digest = str(item.get("digest", ""))
        text = title + digest
        hits = sum(1 for kw in INTEL_KEYWORDS if kw in text)
        if hits > 0:
            scored.append((hits, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _global_snapshot(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从全球资产快照提取情报相关品种（黄金/原油/汇率/美股）"""
    out: Dict[str, Any] = {}
    if not payload:
        return out
    for cat, items in (payload.get("categories") or {}).items():
        for item in items:
            if item.get("available") and item.get("key") in {"gold", "oil", "usdcnh", "spx", "ndx", "silver"}:
                out[item["key"]] = {"name": item["name"], "chg": item.get("chg"), "price": item.get("price")}
    return out


def _rule_assess(news_used: int, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """无 LLM 时的规则降级评估：关键词热度 + 避险资产异动"""
    score = min(60, news_used * 8)
    signals: List[str] = []
    gold_chg = (snapshot.get("gold") or {}).get("chg")
    oil_chg = (snapshot.get("oil") or {}).get("chg")
    if isinstance(gold_chg, (int, float)) and gold_chg >= 1.0:
        score += 20
        signals.append(f"黄金 +{gold_chg:.1f}% 避险升温")
    if isinstance(oil_chg, (int, float)) and abs(oil_chg) >= 2.0:
        score += 10
        signals.append(f"原油 {oil_chg:+.1f}% 供应/需求扰动")
    if score >= 60:
        level = "高"
    elif score >= 35:
        level = "中"
    elif score >= 15:
        level = "关注"
    else:
        level = "低"
    brief = f"情报关键词命中 {news_used} 条；" + ("；".join(signals) if signals else "避险资产波动温和") + "（规则引擎评估）"
    return {
        "risk_level": level,
        "risk_score": min(100, score),
        "brief": brief,
        "channels": [],
        "sectors": [],
        "watch_points": [],
    }


async def _ai_assess(
    filtered: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """DeepSeek 情报影响评估（JSON 输出）"""
    try:
        from server.llm import get_llm_client
        client = get_llm_client()
        if not client.api_key:
            return None
    except Exception:
        return None

    news_text = "\n".join(
        f"- {str(n.get('title', ''))[:60]}" for n in filtered[:8]
    ) or "- 今日无显著情报类新闻"
    snap_text = "；".join(
        f"{v['name']} {v['chg']:+.2f}%" for k, v in snapshot.items() if isinstance(v.get("chg"), (int, float))
    ) or "全球资产数据暂缺"

    prompt = f"""你是地缘政治与宏观情报分析专家，服务于一个 A 股量化交易平台。基于以下真实信息评估当前世界局势对 A 股市场的潜在影响。

【今日情报类新闻】
{news_text}

【全球资产实时表现（市场投票）】{snap_text}

请仅输出严格 JSON（不要 markdown 标记）：
{{
  "risk_level": "低/关注/中/高 四选一（对 A 股的潜在扰动程度）",
  "risk_score": 0到100的整数,
  "brief": "80字以内情报评估结论：世界局势要点+对 A 股的影响路径",
  "channels": ["影响传导渠道1", "渠道2"],
  "sectors": ["受影响板块1", "板块2"],
  "watch_points": ["未来48小时观察点1", "观察点2"]
}}"""

    try:
        import json as _json
        import re as _re
        text = await client.chat(
            prompt,
            "你是严谨的情报分析专家，只基于给定信息评估，禁止编造事件。",
            max_tokens=420,
        )
        if not text:
            return None
        match = _re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        data = _json.loads(match.group(0))
        if "risk_level" not in data:
            return None
        return {
            "risk_level": str(data.get("risk_level", "低")),
            "risk_score": int(data.get("risk_score", 0) or 0),
            "brief": str(data.get("brief", ""))[:200],
            "channels": [str(c)[:40] for c in (data.get("channels") or [])][:4],
            "sectors": [str(s)[:20] for s in (data.get("sectors") or [])][:5],
            "watch_points": [str(w)[:50] for w in (data.get("watch_points") or [])][:3],
        }
    except Exception as exc:
        logger.debug("intel ai assess failed: %s", exc)
        return None


async def scan_intel() -> Dict[str, Any]:
    """执行一轮情报扫描评估，返回完整报告"""
    from server.ak_market import ak_hub
    from server.global_assets import get_global_assets

    news_resp, assets = await asyncio.gather(
        ak_hub.get_notices_and_news(limit=20),
        get_global_assets(),
        return_exceptions=True,
    )
    news_items = [] if isinstance(news_resp, Exception) else (news_resp.get("data") or [])
    assets = None if isinstance(assets, Exception) else assets

    filtered = _filter_intel_news(news_items)
    snapshot = _global_snapshot(assets)

    report = await _ai_assess(filtered, snapshot)
    ai_powered = report is not None
    if not ai_powered:
        report = _rule_assess(len(filtered), snapshot)

    payload = {
        "ok": True,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ai_powered": ai_powered,
        "news_scanned": len(news_items),
        "news_matched": len(filtered),
        "headlines": [str(n.get("title", ""))[:60] for n in filtered[:5]],
        "global_snapshot": snapshot,
        **report,
    }
    return payload


async def scan_and_persist(store_get, store_set) -> Dict[str, Any]:
    """扫描并写入主脑记忆（store_get/store_set 为 brain 的 KV 协程）。

    ⚠️ 防退化护栏：
    scan_intel 对新闻与全球资产取数使用 ``return_exceptions=True``，
    任一取数失败时 ``headlines`` / ``global_snapshot`` 会**静默退化为空**。
    若此处无条件覆盖 ``brain_intel``，就会把上一次的富情报整体顶掉
    （前端表现为「影响渠道 / 全球快照 / 命中新闻」突然全部消失，
    而 AI 研判文本还在 —— 属于最难排查的一类数据退化）。

    故：新扫描中为空、而上次非空的**结构化字段**沿用上次值，并记录
    ``carried_forward`` / ``carried_from_ts`` 供前端如实标注「沿用上次扫描」。
    注意 brief / risk 等**结论性字段始终用本次结果**，不做沿用。
    """
    payload = await scan_intel()
    prev = await store_get("brain_intel", None)

    if isinstance(prev, dict):
        carried: List[str] = []
        for key in ("global_snapshot", "headlines", "channels"):
            if not payload.get(key) and prev.get(key):
                payload[key] = prev[key]
                carried.append(key)
        if carried:
            payload["carried_forward"] = carried
            payload["carried_from_ts"] = prev.get("ts")

    history = await store_get("brain_intel_history", [])
    history.append({
        "ts": payload["ts"],
        "risk_level": payload["risk_level"],
        "risk_score": payload["risk_score"],
        "brief": payload["brief"][:120],
    })
    await store_set("brain_intel_history", history[-_HISTORY_KEEP:])
    await store_set("brain_intel", payload)
    return payload
