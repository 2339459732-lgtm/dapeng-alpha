#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""龙虎榜复盘聚合层（lhb-insight.v1）。

为什么单独一层
--------------
`records` 是「谁在哪只票上做了什么」的平铺清单。平铺清单看不出三件事：

1. **一只票上多路资金的合力/分歧**——机构在买、游资在卖，这在单条记录里看不出来，
   必须按标的把方向拼起来才知道是「合力做多」还是「多空对峙」；
2. **一个席位当日的整体动向**——知名游资今天是不是只打一只票（集中狙击）
   还是分散试仓，单看记录也看不出；
3. **钱往哪些题材去**——需要跨标的按行业汇总净额。

所以本层不做新判断，只做**横切重组**：把已有记录按标的 / 席位 / 题材三个视角重排，
再加上少量可验证的派生指标（合力系数、集中度、封板质量标签）。

设计约束
--------
* 输入只有 `report`（含 records 与 context），不 import 抓取层，不发网络请求；
* 所有派生值都写明算法，空数据返回 `None` 而不是 0，避免「没数据」和「数据是 0」混淆；
* 不新增因果结论——归因已经在 `lhb_review.attribute()` 做完，这里只做汇总与排序。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "lhb-insight.v1"

#: 席位类型的中文名（展示层用，避免前端各处重复映射）
SEAT_TYPE_LABEL = {
    "institution": "机构",
    "northbound": "北向",
    "hot_money": "游资",
    "retail": "散户",
    "other": "其他",
}

#: 哪些席位类型是「通道汇总」而不是「一个交易者」。
#: 机构专用与沪深股通背后是几十上百个互不相干的账户，把它们当成一个人去算
#: 「集中狙击 / 分散试仓」，得到的结论是假的——那个名字不构成行为主体。
CHANNEL_TYPES = {"institution", "northbound"}

MOTIVE_ORDER = ["题材炒作", "市场情绪", "资金博弈", "消息面", "技术形态", "证据不足（无法归类）"]


def _net(rec: Dict[str, Any]) -> float:
    try:
        return float(rec.get("net") or 0)
    except (TypeError, ValueError):
        return 0.0


def _side(rec: Dict[str, Any]) -> str:
    return "buy" if _net(rec) >= 0 else "sell"


def _weighted_motive(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按 |净额| 加权的主因：不以「条数最多」为准，而是「钱最多的那类理由」。

    为什么加权：一家机构砸 3 亿卖出的理由，比一个散户席位买 3000 万的理由重要得多，
    按条数投票会让小额记录淹没主要矛盾。
    """
    buckets: Dict[str, float] = {}
    for rec in recs:
        motive = ((rec.get("motive") or {}).get("primary")) or "未归类"
        buckets[motive] = buckets.get(motive, 0.0) + abs(_net(rec))
    if not buckets:
        return {"primary": None, "weights": {}}
    total = sum(buckets.values()) or 1.0
    ordered = sorted(buckets.items(), key=lambda kv: -kv[1])
    return {
        "primary": ordered[0][0],
        "share": round(ordered[0][1] / total, 3),
        "weights": {k: round(v / total, 3) for k, v in ordered},
        "basis": "按 |净额| 加权，不是按记录条数",
    }


def _seal_quality(price_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """封板质量标签：直接由涨停池字段推出，不引入新数据。"""
    board = price_ctx.get("board_count")
    opens = price_ctx.get("open_times")
    first_seal = price_ctx.get("first_seal")
    if not price_ctx.get("is_limit_up"):
        return {"label": "未涨停", "level": "none"}
    if isinstance(opens, int) and opens >= 3:
        label, level = f"炸板 {opens} 次·封板弱", "weak"
    elif isinstance(opens, int) and opens == 0:
        label, level = "全天未炸板·封板强", "strong"
    elif isinstance(opens, int) and opens > 0:
        label, level = f"炸板 {opens} 次后回封·一般", "mid"
    else:
        label, level = "涨停（封板数据缺失）", "unknown"
    if first_seal:
        label += f" · 首封 {first_seal}"
    if isinstance(board, int) and board >= 2:
        label = f"{board} 连板 · " + label
    return {"label": label, "level": level, "board": board, "first_seal": first_seal,
            "open_times": opens}


def by_stock(records: List[Dict[str, Any]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """标的视角：一只票上所有上榜席位的行为拼图。

    `alignment` 是这一层最有用的派生量——把「多家席位同向」和「多空对峙」分开：
    * `合力做多`：净买席位占绝对多数且无显著卖出
    * `合力出货`：反之
    * `多空对峙`：买卖双方都有实质金额（≥20% 的对向占比）
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        grouped.setdefault(str(rec.get("code") or ""), []).append(rec)

    zt = (ctx or {}).get("zt") or {}
    out: List[Dict[str, Any]] = []
    for code, recs in grouped.items():
        buy_amt = sum(v for v in (_net(r) for r in recs) if v > 0)
        sell_amt = sum(-v for v in (_net(r) for r in recs) if v < 0)
        gross = buy_amt + sell_amt
        ratio = (buy_amt / gross) if gross else None

        if ratio is None:
            alignment = "无方向"
        elif ratio >= 0.8:
            alignment = "合力做多"
        elif ratio <= 0.2:
            alignment = "合力出货"
        else:
            alignment = "多空对峙"

        types: Dict[str, int] = {}
        for rec in recs:
            key = SEAT_TYPE_LABEL.get(str(rec.get("seat_type")), "其他")
            types[key] = types.get(key, 0) + 1

        leads = sorted(recs, key=lambda r: -abs(_net(r)))[:3]
        price_ctx = (recs[0].get("price_context") or {})
        zt_info = zt.get(code) or {}
        industry = zt_info.get("industry") or ""
        out.append({
            "code": code,
            "name": recs[0].get("name"),
            "industry": industry,
            "net": round(sum(_net(r) for r in recs), 2),
            "net_wan": round(sum(_net(r) for r in recs) / 1e4, 1),
            "buy_wan": round(buy_amt / 1e4, 1),
            "sell_wan": round(sell_amt / 1e4, 1),
            "buy_share": round(ratio, 3) if ratio is not None else None,
            "alignment": alignment,
            "records": len(recs),
            "seat_types": types,
            "board_count": price_ctx.get("board_count"),
            "first_seal": price_ctx.get("first_seal"),
            "seal": _seal_quality(price_ctx),
            "inference": price_ctx.get("inference"),
            "stock_chg": recs[0].get("stock_chg"),
            "motive": _weighted_motive(recs),
            "key_seats": [
                {"seat": r.get("seat_alias") or r.get("seat"), "type": r.get("seat_type"),
                 "side": _side(r), "net_wan": r.get("net_wan"),
                 "motive": (r.get("motive") or {}).get("primary")}
                for r in leads
            ],
        })

    out.sort(key=lambda x: -abs(x["net"]))
    return out


def by_seat(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """席位视角：一个席位当日出手的集中度与方向偏好。

    `focus` 用「最大单票占该席位总成交额的比例」衡量：
    ≥0.6 集中狙击，≤0.35 分散试仓。这是判断游资打法最直接的一个量。

    **通道类的 focus 一律为 `不适用`**：`机构专用` / `北向资金` 是几十上百个账户的汇总，
    把它们算成一个「人」的集中度，得到的只是当日上榜金额的分布，不是任何人真实的仓位动作。
    """
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for rec in records:
        seat_type = str(rec.get("seat_type") or "")
        # 通道按**类型**合并，不按名字：沪股通与深股通是同一个「北向」通道的两个入口，
        # 按名字分会得到两行都叫「北向资金」的重复行，读者会误以为是两路资金。
        key = (f"__channel__{seat_type}", seat_type) if seat_type in CHANNEL_TYPES \
            else (str(rec.get("seat") or ""), seat_type)
        grouped.setdefault(key, []).append(rec)

    out: List[Dict[str, Any]] = []
    for (seat, seat_type), recs in grouped.items():
        gross_by_stock: Dict[str, float] = {}
        for rec in recs:
            code = str(rec.get("code") or "")
            gross_by_stock[code] = gross_by_stock.get(code, 0.0) + abs(_net(rec))
        gross = sum(gross_by_stock.values()) or 1.0
        top_share = max(gross_by_stock.values()) / gross if gross_by_stock else None
        is_channel = seat_type in CHANNEL_TYPES

        if is_channel:
            focus = "通道汇总"
        elif top_share is None:
            focus = "无数据"
        elif top_share >= 0.6:
            focus = "集中狙击"
        elif top_share <= 0.35:
            focus = "分散试仓"
        else:
            focus = "适度集中"

        buy_n = sum(1 for r in recs if _side(r) == "buy")
        sell_n = len(recs) - buy_n
        raw_names = sorted({str(r.get("seat") or "") for r in recs})
        out.append({
            # 通道用别名当展示名（"北向资金"），并把原始入口列出来备查
            "seat": (recs[0].get("seat_alias") or "通道") if is_channel else seat,
            "seat_raw": raw_names[0] if len(raw_names) == 1 else raw_names,
            "seat_type": seat_type,
            "type_label": SEAT_TYPE_LABEL.get(seat_type, "其他"),
            "is_channel": is_channel,
            "alias": recs[0].get("seat_alias"),
            "style": recs[0].get("seat_style"),
            "alias_source": recs[0].get("alias_source"),
            "trades": len(recs),
            "buy_count": buy_n,
            "sell_count": sell_n,
            "net": round(sum(_net(r) for r in recs), 2),
            "net_wan": round(sum(_net(r) for r in recs) / 1e4, 1),
            "stocks": len(gross_by_stock),
            "focus": focus,
            "top_share": round(top_share, 3) if (top_share is not None and not is_channel) else None,
            "stocks_detail": [
                {"code": r.get("code"), "name": r.get("name"), "side": _side(r),
                 "net_wan": r.get("net_wan")}
                for r in sorted(recs, key=lambda x: -abs(_net(x)))
            ],
            "win_rate_3d": next((r.get("win_rate_3d") for r in recs
                                 if r.get("win_rate_3d") is not None), None),
        })

    out.sort(key=lambda x: -abs(x["net"]))
    return out


def by_industry(records: List[Dict[str, Any]], stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """题材视角：按行业汇总净买额与席位构成。

    行业来自复盘日涨停池；不在涨停池的标的归入「未归入涨停池」——
    这是真实信息（该票当日没涨停），不是缺失，所以不丢弃也不猜。
    """
    industry_of = {s["code"]: (s.get("industry") or "") for s in stocks}
    buckets: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        code = str(rec.get("code") or "")
        name = industry_of.get(code) or "未归入涨停池"
        slot = buckets.setdefault(name, {"industry": name, "net": 0.0, "records": 0,
                                         "stocks": set(), "buy": 0.0, "sell": 0.0})
        net = _net(rec)
        slot["net"] += net
        slot["records"] += 1
        slot["stocks"].add(code)
        if net >= 0:
            slot["buy"] += net
        else:
            slot["sell"] += -net

    out = []
    for name, slot in buckets.items():
        out.append({
            "industry": name,
            "net_wan": round(slot["net"] / 1e4, 1),
            "buy_wan": round(slot["buy"] / 1e4, 1),
            "sell_wan": round(slot["sell"] / 1e4, 1),
            "records": slot["records"],
            "stocks": len(slot["stocks"]),
        })
    out.sort(key=lambda x: -abs(x["net_wan"]))
    return out


def motive_matrix(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """主因 × 方向 的计数矩阵（给图表用，按 MOTIVE_ORDER 稳定排序）。"""
    cells: Dict[str, Dict[str, int]] = {}
    for rec in records:
        motive = ((rec.get("motive") or {}).get("primary")) or "未归类"
        slot = cells.setdefault(motive, {"buy": 0, "sell": 0, "net_wan": 0.0})
        slot[_side(rec)] += 1
        slot["net_wan"] = round(slot["net_wan"] + _net(rec) / 1e4, 1)
    order = {name: i for i, name in enumerate(MOTIVE_ORDER)}
    items = sorted(cells.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
    return [{"motive": k, "buy": v["buy"], "sell": v["sell"],
             "total": v["buy"] + v["sell"], "net_wan": v["net_wan"]} for k, v in items]


def motive_by_seat_type(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """席位类型 × 主因：看不同资金群体关注的理由是否不同（机构看业绩、游资看情绪）。"""
    cells: Dict[str, Dict[str, int]] = {}
    for rec in records:
        t = SEAT_TYPE_LABEL.get(str(rec.get("seat_type")), "其他")
        motive = ((rec.get("motive") or {}).get("primary")) or "未归类"
        cells.setdefault(t, {})
        cells[t][motive] = cells[t].get(motive, 0) + 1
    out = []
    for t, dist in cells.items():
        top = max(dist.items(), key=lambda kv: kv[1])
        out.append({"seat_type": t, "total": sum(dist.values()),
                    "dist": dist, "top_motive": top[0], "top_count": top[1]})
    out.sort(key=lambda x: -x["total"])
    return out


def factor_stats(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """买卖因素命中统计：哪类理由当天被触发最多、背后涉及多大金额。

    口径：**只统计该方向记录的命中**——买入侧只算买入记录的买入理由，
    卖出侧只算卖出记录的兑现触发。因素评估本身是对标的的双向评估
    （同一只票的买入记录也会带卖出侧命中，作为分歧提示显示在详情里），
    但统计面板回答的是「今天买入/卖出的资金各自按什么理由操作」，
    不按方向过滤会把买入记录的兑现条件混进「为什么卖」里。

    回答的是「今天的上榜资金整体在按什么逻辑操作」——早盘封板抢筹被触发 12 次、
    开板兑现窗口被触发 8 次，这比主因矩阵更接近「买/卖的直接理由」。
    """
    buckets: Dict[str, Dict[str, Dict[str, Any]]] = {"buy": {}, "sell": {}}
    for rec in records:
        fac = rec.get("factors") or {}
        rec_side = _side(rec)
        for side in ("buy", "sell"):
            if side != rec_side:
                continue
            for hit in fac.get(side) or []:
                key = str(hit.get("id") or hit.get("label") or "unknown")
                slot = buckets[side].setdefault(key, {
                    "id": hit.get("id"), "label": hit.get("label"),
                    "weight": hit.get("weight"), "count": 0, "net_wan": 0.0})
                slot["count"] += 1
                slot["net_wan"] = round(slot["net_wan"] + abs(_net(rec)) / 1e4, 1)
    return {side: sorted(slots.values(), key=lambda x: -x["count"])
            for side, slots in buckets.items()}


def highlights(report: Dict[str, Any], stocks: List[Dict[str, Any]],
               seats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """自动提炼要点：只做排序后的「第一名」陈述，不编因果。"""
    recs = report.get("records") or []
    ctx = report.get("context") or {}
    out: List[Dict[str, Any]] = []
    if not recs:
        return out

    # 最大单笔
    biggest = max(recs, key=lambda r: abs(_net(r)))
    out.append({
        "kind": "largest",
        "tone": "buy" if _net(biggest) >= 0 else "sell",
        "title": f"最大单笔：{biggest.get('seat_alias') or biggest.get('seat')}"
                 f"{'买入' if _net(biggest) >= 0 else '卖出'} {biggest.get('name')}",
        "detail": f"净额 {biggest.get('net_wan')} 万 · 主因 "
                  f"{(biggest.get('motive') or {}).get('primary')} · "
                  f"置信度 {biggest.get('confidence')}",
        "code": biggest.get("code"),
    })

    # 最强合力标的（净买额最大且为合力做多）
    bulls = [s for s in stocks if s["alignment"] == "合力做多" and s["net"] > 0]
    if bulls:
        top = max(bulls, key=lambda s: s["net"])
        out.append({
            "kind": "consensus_buy", "tone": "buy",
            "title": f"最强合力买入：{top['name']}（{top['records']} 家席位同向）",
            "detail": f"净买 {top['net_wan']} 万 · {top['seal']['label']} · 主因 "
                      f"{top['motive']['primary']}",
            "code": top["code"],
        })

    # 最大分歧标的
    splits = [s for s in stocks if s["alignment"] == "多空对峙"]
    if splits:
        top = max(splits, key=lambda s: abs(s["net"]))
        out.append({
            "kind": "conflict", "tone": "warn",
            "title": f"分歧最大：{top['name']}（买 {top['buy_wan']} 万 / 卖 {top['sell_wan']} 万）",
            "detail": "买卖双方都有实质金额，次日方向取决于谁先松手",
            "code": top["code"],
        })

    # 最集中的席位（只算真正的交易者，通道汇总没有「集中度」可言）
    focused = [s for s in seats if s["focus"] == "集中狙击" and s["trades"] >= 1]
    if focused:
        top = max(focused, key=lambda s: abs(s["net"]))
        first = (top["stocks_detail"] or [{}])[0]
        out.append({
            "kind": "focus", "tone": "info",
            "title": f"最集中席位：{top['alias'] or top['seat']}（{top['focus']}）",
            "detail": f"{top['trades']} 笔 / {top['stocks']} 只票，主攻 "
                      f"{first.get('name') or '--'}，单票占比 "
                      f"{(top['top_share'] or 0) * 100:.0f}%",
        })

    # 情绪底座
    if ctx.get("zt_count"):
        out.append({
            "kind": "context", "tone": "neutral",
            "title": f"情绪底座：涨停 {ctx.get('zt_count')} 家 / 最高 "
                     f"{ctx.get('max_board')} 板 / 封板率 {ctx.get('seal_rate')}%",
            "detail": f"炸板 {ctx.get('zb_count')} 家、跌停 {ctx.get('dt_count')} 家；"
                      "封板率越低说明当日承接越差，打板成功率随之下降",
        })
    return out


def aggregate(report: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把平铺的 records 重排成四个视角 + 要点，供展示层直接消费。

    `ctx` 必须是**原始市场上下文**（含 `zt` 明细），不能传 `report["context"]`：
    后者是给前端看的精简版，只有计数没有逐票行业，传错会让所有标的都归到「未归入涨停池」。
    """
    recs = report.get("records") or []
    raw_ctx = ctx if isinstance(ctx, dict) and ctx.get("zt") is not None else {}
    stocks = by_stock(recs, raw_ctx)
    seats = by_seat(recs)
    head_ctx = report.get("context") or {}
    return {
        "version": VERSION,
        "by_stock": stocks,
        "by_seat": seats,
        "by_industry": by_industry(recs, stocks),
        "motive_matrix": motive_matrix(recs),
        "motive_by_seat_type": motive_by_seat_type(recs),
        "factor_stats": factor_stats(recs),
        "highlights": highlights(report, stocks, seats),
        "totals": {
            "records": len(recs),
            "stocks": len(stocks),
            "seats": len(seats),
            "net_wan": round(sum(_net(r) for r in recs) / 1e4, 1),
            "buy_net_wan": round(sum(_net(r) for r in recs if _net(r) > 0) / 1e4, 1),
            "sell_net_wan": round(sum(-_net(r) for r in recs if _net(r) < 0) / 1e4, 1),
            "consensus_buy": sum(1 for s in stocks if s["alignment"] == "合力做多"),
            "consensus_sell": sum(1 for s in stocks if s["alignment"] == "合力出货"),
            "conflict": sum(1 for s in stocks if s["alignment"] == "多空对峙"),
        },
        "notes": [
            "合力/对峙按「净买额占买卖总额比例」划分：≥80% 合力做多、≤20% 合力出货、其余对峙",
            "标的与席位的主因按 |净额| 加权，不是按记录条数投票",
            "行业归属取自复盘日涨停池；未涨停的标的归入「未归入涨停池」（事实，非缺失）",
            "本层只做横切重组与排序，不新增因果结论；归因见每条记录的 evidence 与 logic",
        ],
    }


__all__ = ["VERSION", "aggregate", "by_stock", "by_seat", "by_industry",
           "motive_matrix", "motive_by_seat_type", "factor_stats", "highlights",
           "SEAT_TYPE_LABEL"]
