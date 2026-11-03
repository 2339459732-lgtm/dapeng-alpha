#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""买卖因素库（lhb-factors.v1）。

为什么要把「为什么买 / 为什么卖」做成配置
----------------------------------------
早先这两段文案是写死在 `attribute()` 里的 if 拼接：涨停就写「板上兑现」、
换手过 20 就写「换手过高」。问题有三个——

1. **不可调**：不同市况下「有效买入理由」是不一样的，牛市级别的接力理由在退潮期是陷阱，
   但改一句文案要改代码、跑测试、发版；
2. **不完整**：只有 4~5 个条件，很多真实情形（尾盘偷袭封板、炸板回封、主线板块追涨、
   跌破均价低吸）根本描述不出来；
3. **不可统计**：文案是给人看的字符串，没法回答「哪类买入理由次日更容易被打脸」。

所以改成**条件规则库**：每条因素是一个可配置的判定式 + 权重 + 说明，评估后返回命中的因素，
既能在页面上展示具体理由，也能按因素统计当日命中分布（见 `lhb_insight.factor_stats`），
为后续做「哪类买入理由次日更容易被打脸」的追踪统计留下结构化口径。

判定式怎么写
------------
配置里每条因素有 `when`，键是「事实名 + 比较符」，值是比较对象：

```json
{"id": "seal_early", "label": "早盘封板抢筹", "weight": 0.8,
 "when": {"is_limit_up": true, "first_seal_lte": "10:00"}}
```

支持的后缀：`_gte`（≥）、`_lte`（≤）、`_gt`（>）、`_lt`（<）、`_eq`（==）、`_ne`（!=）。
不带后缀即相等比较。多个键之间是 **AND** 关系（同时满足才算命中）。

时间类字段（`first_seal` / `last_seal`）是零填充的 `HH:MM`，字符串比较等价于时间比较。

设计约束
--------
* 纯函数：输入事实字典，输出命中列表，不打网络、不读文件；
* 事实缺失（`None`）时该条**不命中**，不用 0 或空字符串顶替——「不知道」不等于「不满足」；
* 权重只用于排序与主因素选择，不做概率解释。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

VERSION = "lhb-factors.v1"

#: 比较符 → 判定函数。事实为 None 时一律不命中（见模块 docstring 的约束 2）。
_OPS = {
    "_gte": lambda a, b: a >= b,
    "_lte": lambda a, b: a <= b,
    "_gt": lambda a, b: a > b,
    "_lt": lambda a, b: a < b,
    "_eq": lambda a, b: a == b,
    "_ne": lambda a, b: a != b,
}

#: 事实名后缀里可能出现比较符，拆分时长键优先匹配（`first_seal_lte` → `first_seal` + `_lte`）
_OPS_BY_LEN = sorted(_OPS, key=len, reverse=True)

#: 单位与格式：给证据文案用，避免出现「首封 9.5」这种读不懂的句子
_FACT_META = {
    "board_count": ("连板", "{:.0f} 板"),
    "open_times": ("炸板", "{:.0f} 次"),
    "turnover": ("换手", "{:.1f}%"),
    "chg": ("涨跌幅", "{:+.2f}%"),
    "close": ("收盘", "{:.2f}"),
    "vwap": ("均价", "{:.2f}"),
    "vwap_gap_pct": ("偏离均价", "{:+.1f}%"),
    "industry_rank": ("行业涨停家数排名", "第 {:.0f}"),
    "peers": ("同行业涨停", "{:.0f} 家"),
    "news_score": ("消息面", "{:.2f}"),
    "game_score": ("资金博弈", "{:.2f}"),
    "theme_score": ("题材", "{:.2f}"),
    "technical_score": ("技术形态", "{:.2f}"),
    "mood_score": ("市场情绪", "{:.2f}"),
    "net_ratio": ("净额占成交", "{:.1f}%"),
    "seat_net_wan": ("席位净额", "{:,.0f} 万"),
    "first_seal": ("首封", "{}"),
    "last_seal": ("末封", "{}"),
    "is_limit_up": ("涨停", "{}"),
}

_OP_WORD = {"_gte": "≥", "_lte": "≤", "_gt": ">", "_lt": "<", "_eq": "=", "_ne": "≠", "": "="}


def _split(key: str) -> Tuple[str, str]:
    """把 `first_seal_lte` 拆成 (`first_seal`, `_lte`)；无后缀返回 (`key`, '')。"""
    for op in _OPS_BY_LEN:
        if key.endswith(op):
            return key[: -len(op)], op
    return key, ""


def _fmt(fact: str, value: Any) -> str:
    meta = _FACT_META.get(fact)
    if meta is None:
        return str(value)
    label, tpl = meta
    if isinstance(value, bool):
        return f"{label}={'是' if value else '否'}"
    try:
        return label + " " + tpl.format(value)
    except (ValueError, TypeError):
        return f"{label} {value}"


def _cmp(a: Any, b: Any, op: str) -> Optional[bool]:
    """比较；不可比较时返回 None（调用方视为不命中）。"""
    if a is None or b is None:
        return None
    if isinstance(b, bool) or isinstance(a, bool):
        return a == b if op in ("", "_eq") else (a != b if op == "_ne" else None)
    if isinstance(b, str):
        # 时间字符串（"09:32"）与普通字符串都按字典序比较，零填充保证等价于时间序
        text = str(a)
        if op in ("", "_eq"):
            return text == b
        if op == "_ne":
            return text != b
        if op == "_gte":
            return text >= b
        if op == "_lte":
            return text <= b
        if op == "_gt":
            return text > b
        if op == "_lt":
            return text < b
        return None
    try:
        num_a, num_b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if op == "":
        op = "_eq"
    return _OPS[op](num_a, num_b)


#: 默认因素规则库。data/lhb_config.json 的 `factors` 段会深合并覆盖这里，
#: 所以调阈值/改文案/加减因素都改配置文件即可，不用动代码。
#: weight 只用于排序与主因素选择：买入侧命中多条时按 weight 降序，
#: 「为什么买」取第一条，「为什么卖」同理。
DEFAULT_FACTORS: Dict[str, Any] = {
    "buy": [
        {"id": "seal_early", "label": "早盘封板抢筹", "weight": 0.8,
         "note": "10:00 前封板说明抢筹意愿强，买入多发生在封板前或封板瞬间排队",
         "when": {"is_limit_up": True, "first_seal_lte": "10:00"}},
        {"id": "theme_mainline", "label": "主线板块追涨", "weight": 0.7,
         "note": "同行业多家涨停且行业排名前三，属主线题材的正宗标的",
         "when": {"peers_gte": 3, "industry_rank_lte": 3}},
        {"id": "board_ladder", "label": "连板梯队接力", "weight": 0.6,
         "note": "2 板以上处于题材梯队前列，接力资金沿高度做文章",
         "when": {"board_count_gte": 2}},
        {"id": "news_material", "label": "实质消息驱动", "weight": 0.6,
         "note": "复盘日前 5 日内有重组/并购/中标等实质性公告",
         "when": {"news_score_gte": 0.4}},
        {"id": "seal_rebound", "label": "炸板回封承接", "weight": 0.5,
         "note": "盘中炸板后回封，说明分歧后仍有资金愿意接",
         "when": {"is_limit_up": True, "open_times_gte": 1}},
        {"id": "dip_vwap", "label": "均价下方低吸", "weight": 0.4,
         "note": "收盘低于当日均价，买入更可能发生在回踩低吸段而非追高",
         "when": {"vwap_gap_pct_lt": 0}},
        {"id": "turnover_active", "label": "高换手活跃票", "weight": 0.3,
         "note": "换手充分、流动性好，大资金进出都不冲击价格",
         "when": {"turnover_gte": 10}},
    ],
    "sell": [
        {"id": "open_window", "label": "开板兑现窗口", "weight": 0.7,
         "note": "当日炸过板，开板区间即兑现窗口，卖在板上需要承接",
         "when": {"open_times_gte": 1}},
        {"id": "seal_cash", "label": "板上兑现", "weight": 0.6,
         "note": "收盘仍涨停但榜上有卖，属板上排队兑现",
         "when": {"is_limit_up": True}},
        {"id": "turnover_excess", "label": "换手过高出货", "weight": 0.6,
         "note": "换手超过 20%，筹码大换手，存在出货条件",
         "when": {"turnover_gte": 20}},
        {"id": "theme_peak", "label": "题材发酵高点兑现", "weight": 0.5,
         "note": "板块已充分发酵，选择在情绪高点兑现",
         "when": {"theme_score_gte": 0.4}},
        {"id": "game_intense", "label": "博弈激烈触发", "weight": 0.5,
         "note": "榜内多空对峙/对倒明显，触发对手盘承接或砸盘",
         "when": {"game_score_gte": 0.5}},
        {"id": "premium_vwap", "label": "大幅偏离均价", "weight": 0.4,
         "note": "收盘价大幅高于当日均价，获利盘有兑现动力",
         "when": {"vwap_gap_pct_gte": 8}},
    ],
}


def match(rule: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """判定单条因素是否命中，并给出可读证据。"""
    when = rule.get("when") or {}
    if not when:
        return False, []
    evidence: List[str] = []
    for key, expected in when.items():
        fact, op = _split(key)
        if fact not in facts:
            return False, []          # 配置引用了不存在的事实：视为不命中，不静默通过
        actual = facts.get(fact)
        ok = _cmp(actual, expected, op)
        if not ok:
            return False, []
        evidence.append(f"{_fmt(fact, actual)}（{_OP_WORD.get(op, '=')} {expected}）"
                        if not isinstance(expected, bool) else _fmt(fact, actual))
    return True, evidence


def evaluate(facts: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """评估一只票在当前时点具备的买入条件与卖出条件。

    注意这是**对标的的条件评估**，不是对某条记录的方向判断：
    同一只票可以同时「具备买入理由」和「具备兑现条件」，这正是分歧的来源。
    """
    lib = ((cfg or {}).get("factors") or {})
    out: Dict[str, List[Dict[str, Any]]] = {"buy": [], "sell": []}
    for side in ("buy", "sell"):
        for rule in lib.get(side) or []:
            ok, evidence = match(rule, facts)
            if not ok:
                continue
            out[side].append({
                "id": rule.get("id") or rule.get("label") or "unknown",
                "label": rule.get("label") or rule.get("id") or "未命名因素",
                "weight": float(rule.get("weight") or 0),
                "note": rule.get("note") or "",
                "evidence": evidence,
            })
        out[side].sort(key=lambda x: -x["weight"])

    return {
        "version": VERSION,
        "buy": out["buy"],
        "sell": out["sell"],
        "primary_buy": out["buy"][0] if out["buy"] else None,
        "primary_sell": out["sell"][0] if out["sell"] else None,
        "balance": round((out["buy"][0]["weight"] if out["buy"] else 0.0)
                         - (out["sell"][0]["weight"] if out["sell"] else 0.0), 3),
    }


def summarize(factors: Dict[str, Any]) -> str:
    """一行摘要：命中的主要买入/卖出理由，给 timing / sell_trigger 文案用。"""
    buy = factors.get("primary_buy")
    sell = factors.get("primary_sell")
    bits = []
    if buy:
        bits.append(f"买入侧最强因素是「{buy['label']}」")
    if sell:
        bits.append(f"卖出侧最强因素是「{sell['label']}」")
    return "；".join(bits)


def facts_from(price_ctx: Dict[str, Any], stock_meta: Dict[str, Any],
               scores: Dict[str, Any], ctx: Dict[str, Any],
               code: str, net_ratio: Optional[float] = None) -> Dict[str, Any]:
    """把分散在各处的字段收拢成一张「事实表」，供判定式引用。

    只放**已有的事实**，不做任何推断；缺失就是 None。
    """
    close = price_ctx.get("close")
    vwap = price_ctx.get("vwap")
    gap = None
    if isinstance(close, (int, float)) and isinstance(vwap, (int, float)) and vwap:
        gap = round((close - vwap) / vwap * 100, 3)

    zt = (ctx or {}).get("zt") or {}
    info = zt.get(code) or {}
    industry = info.get("industry") or ""
    industries = (ctx or {}).get("industries") or {}
    peers = int(industries.get(industry, 0)) if industry else 0
    rank = None
    if industry and industries:
        ranked = sorted(industries.items(), key=lambda kv: -kv[1])
        for i, (name, _) in enumerate(ranked, 1):
            if name == industry:
                rank = i
                break

    turnover = info.get("turnover")
    if not isinstance(turnover, (int, float)):
        turnover = stock_meta.get("turnover")

    return {
        "is_limit_up": bool(price_ctx.get("is_limit_up")),
        "board_count": price_ctx.get("board_count"),
        "open_times": price_ctx.get("open_times"),
        "first_seal": price_ctx.get("first_seal"),
        "last_seal": price_ctx.get("last_seal"),
        "turnover": turnover,
        "chg": stock_meta.get("chg"),
        "close": close,
        "vwap": vwap,
        "vwap_gap_pct": gap,
        "industry_rank": rank,
        "peers": peers,
        "news_score": scores.get("news"),
        "game_score": scores.get("game"),
        "theme_score": scores.get("theme"),
        "technical_score": scores.get("technical"),
        "mood_score": scores.get("mood"),
        "net_ratio": net_ratio,
    }


__all__ = ["VERSION", "DEFAULT_FACTORS", "evaluate", "match", "summarize", "facts_from"]
