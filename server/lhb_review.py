#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""龙虎榜复盘分析（lhb-review.v1）。

做什么
------
收盘后把当日龙虎榜的**营业部级**买卖明细拉下来，筛出「值得跟的席位」（机构、北向、知名游资、
以及 3 日成功率高的活跃席位），逐笔还原它在哪只票上、什么方向、多大规模，然后按五个维度
给动因打分并生成可审计的推演逻辑。

五个动因维度（每项 0~1，取最高者为主因）
------------------------------------
1. 题材炒作：**只算板块效应**——同行业当日涨停家数，以及该行业是否当日最强前三
2. 市场情绪：情绪周期（涨停家数 / 空间高度 / 封板率，**打折计入**）+ 龙头效应
   （是否当日最高板、是否同行业最高板、炸板后是否回封）——后者才提供个股区分度
3. 资金博弈：净买额占成交额比、机构与游资/北向是否构成对手盘、同席位双向（对倒）、集中度
4. 消息面：复盘日前 N 日公告的标题与类型，按「高信号/次信号 × 距复盘日天数」衰减计分
5. 技术形态：是否涨停、连板高度、炸板次数、真实换手（涨停池口径），
   非涨停股用交易所「上榜原因」判断异动性质

必须说清楚的边界
----------------
* 龙虎榜**没有逐笔时间**。能拿到涨停封板时间的标的，时机判断为「封板时刻推断」
  （`inference: "seal-time"`）；拿不到的退回日级推断（`inference: "day-level"`）。
  两者都不是分时级证据，绝不假装精确到分钟成交。
* 营业部→游资别名是市场俗称，未逐条核实，`alias_source` 会写明。
* 取不到的数据一律 `available:false` + `reason`，不填 0、不编造。

模块边界
--------
抓取动作全在 `server/lhb_fetch.py`；本文件只做「配置 → 归因 → 组装 → 落盘」，
`attribute()` 是纯函数，可脱网单测与回放。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from server.lhb_fetch import (  # noqa: F401  (re-export：对外仍从 lhb_review 取)
    _announcements,
    _limit_threshold,
    _prefix_code,
    fetch_limit_pools,
    fetch_seats,
    fetch_stock_list,
    market_context,
)
from server import lhb_insight as insight
from server import lhb_factors as factors_lib

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "lhb_config.json")
STORE_DIR = os.path.join(ROOT, "data", "lhb_review")
LATEST_PATH = os.path.join(STORE_DIR, "latest.json")
VERSION = "lhb-review.v1"
ALIAS_SOURCE = "市场俗称（营业部→游资别名，未逐条核实）"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_STATE: Dict[str, Any] = {"mtime": None, "data": None}
DEFAULTS: Dict[str, Any] = {
    "version": "lhb-config.v1",
    "quality": {"min_net_amount": 3e7, "institution_min_net": 5e7,
                "min_win_rate_3d": 50.0, "min_win_samples": 30, "max_records": 40},
    "hot_money": {},
    "retail_keywords": ["拉萨"],
    "northbound_keywords": ["深股通", "沪股通"],
    "institution_keywords": ["机构专用"],
    "motive": {
        "min_primary_score": 0.25,
        "theme": {"sector_chg_warm": 2.0, "sector_chg_hot": 4.0, "sector_limit_up_hot": 3},
        "mood": {"limit_up_strong": 60, "limit_up_euphoric": 100,
                 "up_ratio_strong": 60, "sentiment_warm": 65, "discount": 0.55},
        "game": {"net_ratio_strong": 5.0, "net_ratio_extreme": 10.0, "concentration_high": 0.6},
        "news": {"lookback_days": 5,
                 "material_keywords": ["重组", "并购", "中标", "业绩预增", "预增", "涨价",
                                       "停牌", "问询", "立案", "控制权", "定增", "重大合同"],
                 "secondary_keywords": ["签订协议", "关联交易", "对外担保", "回购",
                                        "减持", "增持", "重大诉讼"]},
        "technical": {"turnover_hot": 10.0, "turnover_extreme": 20.0},
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：嵌套字典按键合并，其余类型整体覆盖。

    为什么不能 `data.update(raw)`：只要 data/lhb_config.json 存在，顶层 `motive` 就会被
    整体替换掉，之后**新增的任何默认子键都会静默失效**（比如 mood.discount 永远拿不到值）。
    这种 bug 不报错、不告警，只是让规则悄悄退回旧行为，非常难查。
    """
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config(force: bool = False) -> Dict[str, Any]:
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    if not force and _STATE["data"] is not None and _STATE["mtime"] == mtime:
        return _STATE["data"]
    data = json.loads(json.dumps(DEFAULTS))
    # 因素默认值放在 lhb_factors（买卖规则库属于它的领域知识），
    # 合并顺序：review 默认 → factors 默认 → 配置文件覆盖。
    data = _deep_merge(data, {"factors": json.loads(json.dumps(factors_lib.DEFAULT_FACTORS))})
    if mtime is not None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = _deep_merge(data, {k: v for k, v in raw.items() if not k.startswith("_")})
        except Exception as exc:
            logger.warning("lhb config 读取失败，用默认值：%s", exc)
    _STATE.update({"mtime": mtime, "data": data})
    return data


# ---------------------------------------------------------------------------
# 席位分类
# ---------------------------------------------------------------------------
def classify_seat(name: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把营业部名映射成可用的席位标签。类型：institution / northbound / hot_money / retail / other。"""
    cfg = cfg or config()
    text = str(name or "").strip()
    if any(key in text for key in cfg.get("institution_keywords") or []):
        return {"type": "institution", "alias": "机构专用", "style": "机构资金",
                "quality": True, "alias_source": "交易所披露"}
    if any(key in text for key in cfg.get("northbound_keywords") or []):
        return {"type": "northbound", "alias": "北向资金", "style": "外资通道",
                "quality": True, "alias_source": "交易所披露"}
    # 散户关键词要排在游资名单**前面**：拉萨天团在 hot_money 名单里也有一条，
    # 但它的性质是散户集中席位（quality=false）。若先查名单，它会被当成优质游资混进复盘。
    if any(key in text for key in cfg.get("retail_keywords") or []):
        return {"type": "retail", "alias": "散户集中席位", "style": "零售集中",
                "quality": False, "alias_source": "市场俗称"}
    hot = (cfg.get("hot_money") or {}).get(text)
    if hot:
        return {"type": "hot_money", "alias": hot.get("alias") or text,
                "style": hot.get("style") or "", "quality": hot.get("quality", True),
                "alias_source": ALIAS_SOURCE}
    return {"type": "other", "alias": text or "未知席位", "style": "",
            "quality": False, "alias_source": "未收录"}


# ---------------------------------------------------------------------------
# 动因归因
# ---------------------------------------------------------------------------
def _score_theme(code: str, ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, List[str]]:
    """题材炒作：只算**板块效应**（同行业涨停家数），不加连板高度。

    为什么把连板高度挪走：连板高度是「情绪周期/龙头效应」的证据，属于市场情绪维度。
    两个维度都吃连板数会重复计分，同一件事被算两遍，主因就永远偏向其中一边。
    """
    zt = ctx.get("zt") or {}
    info = zt.get(code) or {}
    industry = info.get("industry") or ""
    rule = (cfg.get("motive") or {}).get("theme") or {}
    hot = int(rule.get("sector_limit_up_hot", 3))
    if not ctx.get("available"):
        return 0.0, ["涨停池不可用，题材维度不评分"]
    if not industry:
        return 0.0, ["该股当日未涨停，无行业归属，板块效应无法量化"]
    industries = ctx.get("industries") or {}
    peers = int(industries.get(industry, 0))
    score, notes = 0.0, [f"所属行业「{industry}」当日涨停 {peers} 家"]
    if peers >= hot * 2:
        score += 0.7
    elif peers >= hot:
        score += 0.45
    elif peers >= 2:
        score += 0.25
    # 该行业是否当日最强前三：板块内部也有主次，弱板块的 3 家涨停不算主线
    ranked = sorted(industries.items(), key=lambda kv: -kv[1])
    rank = next((i for i, (name, _) in enumerate(ranked, 1) if name == industry), 99)
    if rank <= 3 and peers >= hot:
        score += 0.15
        notes.append(f"该行业为当日涨停家数第 {rank} 位，属主线板块")
    if peers >= hot:
        notes.append("同行业多股涨停 → 板块效应成立")
    elif peers >= 2:
        notes.append("同行业有零星涨停，题材尚未形成合力")
    return min(1.0, round(score, 4)), notes


def _score_mood(code: str, ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, List[str]]:
    """市场情绪 = 情绪周期（环境）+ 龙头效应（个股在情绪结构中的位置）。

    环境部分是**全市场同一个值**，对当天所有标的一样，本身没有区分度；若原样参与比较，
    一个 120 家涨停的日子会把所有记录都归因成「市场情绪」，归因就失去意义。
    所以环境分按 `mood.discount` 打折，只作背景权重。

    真正提供区分度的是**龙头效应**：是否当日最高板、是否同行业最高板、
    是否炸板后回封（人气有承接）。这三项都只对个别标的成立。
    """
    rule = (cfg.get("motive") or {}).get("mood") or {}
    if not ctx.get("available"):
        return 0.0, ["涨停池不可用，情绪维度不评分"]
    zt = ctx.get("zt") or {}
    info = zt.get(code) or {}
    zt_count = int(ctx.get("zt_count") or 0)
    max_board = int(ctx.get("max_board") or 0)
    seal_rate = ctx.get("seal_rate")
    env, notes = 0.0, [f"涨停 {zt_count} 家", f"最高 {max_board} 连板"]
    if zt_count >= float(rule.get("limit_up_euphoric", 100)):
        env += 0.5
    elif zt_count >= float(rule.get("limit_up_strong", 60)):
        env += 0.3
    if max_board >= 5:
        env += 0.25
        notes.append(f"空间高度 {max_board} 板，情绪周期处于高位")
    if isinstance(seal_rate, (int, float)):
        notes.append(f"封板率 {seal_rate}%（炸板 {ctx.get('zb_count')} 家）")
        if seal_rate >= 80:
            env += 0.2
        elif seal_rate < 60:
            env -= 0.1
    score = max(0.0, min(1.0, env)) * float(rule.get("discount", 1.0))

    # 龙头效应（个股特异）
    board = int(info.get("board_count") or 0)
    if board >= 2 and board >= max_board:
        score += 0.35
        notes.append(f"{board} 连板为当日最高板，是情绪龙头（空间标杆）")
    elif board >= 2 and board >= max_board - 1:
        score += 0.2
        notes.append(f"{board} 连板，紧贴空间高度，属二线龙头")
    industry = info.get("industry") or ""
    if board >= 2 and industry:
        top_in_industry = max((int(v.get("board_count") or 0)
                               for k, v in zt.items() if v.get("industry") == industry), default=0)
        if board >= top_in_industry:
            score += 0.12
            notes.append(f"「{industry}」板块内最高板，题材龙头")
    open_times = info.get("open_times")
    if isinstance(open_times, (int, float)) and open_times >= 1 and board >= 1:
        score += 0.08
        notes.append(f"炸板 {int(open_times)} 次后回封，人气有承接")
    return min(1.0, round(score, 4)), notes


def _score_game(rows: List[Dict[str, Any]], cfg: Dict[str, Any],
                stock_meta: Dict[str, Any]) -> Tuple[float, List[str]]:
    rule = (cfg.get("motive") or {}).get("game") or {}
    score, notes = 0.0, []
    total_net = sum(float(r.get("NET") or 0) for r in rows)
    amount = float(stock_meta.get("ACCUM_AMOUNT") or 0) or float(stock_meta.get("BILLBOARD_DEAL_AMT") or 0)
    if amount > 0:
        ratio = abs(total_net) / amount * 100
        notes.append(f"榜内净额 {total_net/1e4:.0f} 万，占成交额 {ratio:.1f}%")
        if ratio >= float(rule.get("net_ratio_extreme", 10.0)):
            score += 0.5
        elif ratio >= float(rule.get("net_ratio_strong", 5.0)):
            score += 0.3
    seats = [classify_seat(str(r.get("OPERATEDEPT_NAME") or ""), cfg) for r in rows]
    # 按席位类型汇总净额，看的是「机构在做多、游资在做空」这种真正的对手盘；
    # 只判断「两种类型都出现过」没有意义——龙虎榜买五卖五摆在那，几乎天天都同时存在。
    net_by_type: Dict[str, float] = {}
    for row, seat in zip(rows, seats):
        net_by_type[seat["type"]] = net_by_type.get(seat["type"], 0.0) + float(row.get("NET") or 0)
    inst_net = net_by_type.get("institution", 0.0)
    for other, other_label in (("hot_money", "游资"), ("northbound", "北向")):
        other_net = net_by_type.get(other, 0.0)
        if inst_net and other_net and inst_net * other_net < 0:
            score += 0.3
            # 文案里不能出现 northbound / hot_money 这类内部枚举：这段会原样显示在页面上
            notes.append(f"机构净{inst_net/1e4:.0f} 万 / {other_label}净{other_net/1e4:.0f} 万，"
                         "方向相反 → 明确对手盘")
            break
    if "institution" in {s["type"] for s in seats} and \
            sum(1 for s in seats if s["type"] == "institution") >= 2:
        notes.append("多家机构同时出现")
    # 同席位双向（对倒迹象）：合并后 BUY/SELL 同时非零才是真双向。
    # 注意不能用「买卖分侧」判断——build() 已把同一席位合并成一行，那样永远判不出来。
    double = [str(r.get("OPERATEDEPT_NAME") or "") for r in rows
              if float(r.get("BUY") or 0) > 0 and float(r.get("SELL") or 0) > 0]
    if double:
        score += 0.3
        notes.append(f"同席位双向成交（对倒迹象）：{double[0]}")
    net_amounts = [abs(float(r.get("NET") or 0)) for r in rows]
    if net_amounts and sum(net_amounts) > 0:
        concentration = max(net_amounts) / sum(net_amounts)
        if concentration >= float(rule.get("concentration_high", 0.6)):
            score += 0.2
            notes.append(f"单一席位占比 {concentration*100:.0f}%，主导明显")
    if not notes:
        notes = ["榜内资金结构无显著特征"]
    return min(1.0, score), notes


def _score_news(ann: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, List[str]]:
    """消息面：实质性公告 × 时效性。

    为什么按时效衰减：三天前的一份「中标公告」和当天早上的「中标公告」，对当天打板
    的解释力完全不同。不做衰减的话，只要窗口里有公告就一律满分，消息面会变成
    「最近几天发过东西」的同义词。
    """
    if not ann.get("available"):
        return 0.0, [f"公告源不可用（{ann.get('reason')}），消息面不评分"]
    news_cfg = (cfg.get("motive") or {}).get("news") or {}
    lookback = int(news_cfg.get("lookback_days", 5))
    strong = news_cfg.get("material_keywords") or []
    weak = news_cfg.get("secondary_keywords") or []
    try:
        base = datetime.strptime(str(ann.get("as_of") or "")[:10], "%Y-%m-%d")
    except ValueError:
        base = datetime.now()
    hits: List[str] = []
    best = 0.0
    for item in ann.get("items") or []:
        text = f"{item.get('title')} {item.get('type')}"
        weight = 0.0
        for kw in strong:
            if kw in text:
                weight = 0.4
                break
        if not weight:
            for kw in weak:
                if kw in text:
                    weight = 0.2
                    break
        if not weight:
            continue
        try:
            gap = (base - datetime.strptime(str(item.get("date"))[:10], "%Y-%m-%d")).days
        except ValueError:
            gap = lookback
        if gap < 0 or gap > lookback:
            continue
        decay = 1.0 if gap <= 1 else (0.75 if gap <= 3 else 0.5)
        best = max(best, weight * decay)
        hits.append(f"{item.get('date')} {item.get('title')}（{gap} 天前）")
    if not hits:
        return 0.0, [f"复盘日前 {lookback} 日无实质性公告（已排除复盘日之后的公告）"]
    return min(1.0, round(best + 0.1 * min(2, len(hits) - 1), 4)), ["公告命中：" + "；".join(hits[:2])]


def _seal_text(raw: Any) -> str:
    """涨停池的封板时间是 `HHMMSS` 字符串，转成 `HH:MM`；拿不到就返回空串。"""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 4:
        return ""
    return f"{digits[:2]}:{digits[2:4]}"


def _score_technical(stock_meta: Dict[str, Any], cfg: Dict[str, Any],
                     ctx: Optional[Dict[str, Any]] = None) -> Tuple[float, List[str]]:
    """技术形态：涨停封板质量（连板高度 / 炸板次数 / 真实换手）。

    为什么优先取涨停池：打板场景真正关心的是「几连板、炸过几次、封板质量如何」，
    这三个字段只有涨停池给；`stock_meta["turnover"]` 是「上榜成交额/流通市值」，
    分母口径不同，不能当真实换手用，只作涨停池缺失时的降级估算，并在 note 里标明。
    """
    rule = (cfg.get("motive") or {}).get("technical") or {}
    code = stock_meta.get("code") or ""
    zt = ((ctx or {}).get("zt") or {}).get(code) or {}
    chg = stock_meta.get("chg")
    score, notes = 0.0, []
    threshold = _limit_threshold(code, stock_meta.get("name") or "")
    is_limit = isinstance(chg, (int, float)) and chg >= threshold
    if isinstance(chg, (int, float)):
        notes.append(f"当日 {chg:+.2f}%")
        if is_limit:
            score += 0.3
            notes.append("收盘涨停（打板/接力特征）")

    board = int(zt.get("board_count") or 0)
    if board >= 2:
        score += min(0.35, 0.15 + 0.05 * board)
        notes.append(f"{board} 连板，处于题材梯队前列")
    elif board == 1 and is_limit:
        notes.append("首板（题材刚启动，接力资金首次介入）")

    open_times = zt.get("open_times")
    if isinstance(open_times, (int, float)):
        if open_times >= 3:
            score -= 0.2
            notes.append(f"盘中炸板 {int(open_times)} 次，封板质量差（分歧剧烈）")
        elif open_times == 0:
            score += 0.1
            notes.append("全天未炸板，封板质量好")

    turnover = zt.get("turnover")
    basis = "涨停池口径"
    if not isinstance(turnover, (int, float)):
        turnover, basis = stock_meta.get("turnover"), (stock_meta.get("turnover_basis") or "估算")
    if isinstance(turnover, (int, float)):
        notes.append(f"换手 {turnover:.1f}%（{basis}）")
        if turnover >= float(rule.get("turnover_extreme", 20.0)):
            score += 0.4
        elif turnover >= float(rule.get("turnover_hot", 10.0)):
            score += 0.25

    # 不在涨停池里的标的，用「上榜原因」判断异动性质——这是交易所口径的原始事实，
    # 比凭涨跌幅猜要可靠。没有这段，非涨停股的动因只能落到「情绪」上，归因趋同。
    reason_text = str(stock_meta.get("reason") or "")
    if not zt and reason_text:
        if "跌幅" in reason_text:
            score -= 0.1
            notes.append("上榜原因为跌幅偏离 → 属弱势异动，非进攻形态")
        elif "连续三个交易日" in reason_text:
            score += 0.2
            notes.append("上榜原因为三日累计涨幅偏离 → 多日持续异动，非单日脉冲")
        elif "振幅" in reason_text:
            score += 0.1
            notes.append("上榜原因为振幅异动 → 多空分歧剧烈")
        elif "换手率" in reason_text:
            score += 0.12
            notes.append("上榜原因为换手率异动 → 筹码大换手")
    if not notes:
        notes = ["涨跌幅/换手不可用，技术维度不评分"]
    return max(0.0, min(1.0, round(score, 4))), notes


MOTIVE_LABEL = {
    "theme": "题材炒作", "mood": "市场情绪", "game": "资金博弈",
    "news": "消息面", "technical": "技术形态",
}


def attribute(code: str, name: str, rows: List[Dict[str, Any]], stock_meta: Dict[str, Any],
              ctx: Dict[str, Any], cfg: Dict[str, Any],
              ann: Optional[Dict[str, Any]] = None,
              side: str = "buy", seat_net: Optional[float] = None) -> Dict[str, Any]:
    """五维打分 → 主因 + 推演逻辑。纯函数，便于单测与回放。

    `side` 与 `seat_net` 用于区分「买方主导」与「卖方兑现」两种语境：
    同一只票上，机构卖出与游资买入的动因不该给同一个结论。
    """
    scores: Dict[str, float] = {}
    evidence: Dict[str, List[str]] = {}
    for key, (score, notes) in {
        "theme": _score_theme(code, ctx, cfg),
        "mood": _score_mood(code, ctx, cfg),
        "game": _score_game(rows, cfg, stock_meta),
        "news": _score_news(ann or {"available": False, "reason": "未取"}, cfg),
        "technical": _score_technical(stock_meta, cfg, ctx),
    }.items():
        scores[key] = round(score, 2)
        evidence[key] = notes

    min_primary = float(((cfg.get("motive") or {}).get("min_primary_score")) or 0.25)
    # 同分时的优先序：先个股特异证据，最后才是全市场共用的情绪。
    # 不打这个补丁的话，sorted 是稳定排序，按字典插入顺序 mood 排在 theme 后面、
    # 却在 game/news/technical 前面，凡是 0.52 打平的记录会莫名其妙全落到「市场情绪」。
    _tie_rank = {"theme": 0, "game": 1, "technical": 2, "news": 3, "mood": 9}
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], _tie_rank.get(kv[0], 8)))
    primary_key, primary_score = ordered[0]
    secondary = [MOTIVE_LABEL[k] for k, v in ordered[1:] if v >= min_primary and v > 0]
    # 去掉情绪维度后的最高分：用来看「这只票有没有个股级证据」
    specific = [(k, v) for k, v in ordered if k != "mood"]
    best_specific = specific[0][1] if specific else 0.0
    mood_only = primary_key == "mood" and best_specific < min_primary
    if primary_score < min_primary:
        primary = "证据不足（无法归类）"
        confidence = 0.25
    else:
        primary = MOTIVE_LABEL[primary_key]
        gap = primary_score - (ordered[1][1] if len(ordered) > 1 else 0)
        confidence = round(min(0.9, 0.45 + 0.3 * primary_score + 0.5 * max(0.0, gap)), 2)
        if mood_only:
            # 情绪是全市场共用的值，不能靠它把个股归因说得太肯定
            confidence = round(min(confidence, 0.45), 2)

    # 价位与时机（日级推断，绝不假装有分时）
    close = stock_meta.get("price")
    amount = float(stock_meta.get("accum_amount") or 0)
    volume = float(stock_meta.get("accum_volume") or 0)
    vwap = round(amount / volume, 3) if amount and volume else None
    zone = "—"
    if isinstance(close, (int, float)) and vwap:
        diff = (close - vwap) / vwap * 100
        zone = f"收盘 {close}，VWAP {vwap}（{'高于' if diff >= 0 else '低于'}均价 {abs(diff):.1f}%）"
    threshold = _limit_threshold(code, name)
    pool = (ctx.get("zt") or {}).get(code) or {}
    is_limit = isinstance(stock_meta.get("chg"), (int, float)) and stock_meta["chg"] >= threshold
    board = int(pool.get("board_count") or 0)
    open_times = pool.get("open_times")
    first_seal = _seal_text(pool.get("first_seal"))
    last_seal = _seal_text(pool.get("last_seal"))

    # 买卖因素评估（可配置规则库）：把「为什么买/为什么卖」从写死的 if 拼接改成
    # 条件规则命中，规则与阈值在 data/lhb_config.json 的 factors 段可调。
    total_net_all = sum(float(r.get("NET") or 0) for r in rows)
    net_ratio = (abs(total_net_all) / amount * 100) if amount else None
    facts = factors_lib.facts_from(
        {"close": close, "vwap": vwap, "is_limit_up": bool(is_limit),
         "board_count": board or None, "open_times": open_times,
         "first_seal": first_seal or None, "last_seal": last_seal or None},
        stock_meta, scores, ctx, code, net_ratio=net_ratio)
    fac = factors_lib.evaluate(facts, cfg)

    # 时机：能从涨停池拿到封板时间，就把结论从「日级」抬到「封板时刻级」；
    # 拿不到仍退回日级推断，推断等级如实写在 inference 里。
    if first_seal:
        early = first_seal <= "10:00"
        if side == "buy":
            if early:
                timing = (f"封板时刻推断：{first_seal} 即封板（早盘快速封板），"
                          f"买入更可能发生在封板前抢筹或封板瞬间排队"
                          + ("；当日开过板，也可能在回封时介入" if isinstance(open_times, (int, float)) and open_times > 0 else ""))
            else:
                timing = (f"封板时刻推断：{first_seal} 才封板（尾盘回封/午后拉升），"
                          "买入更可能发生在盘中拉升段而非一字板上")
        else:
            if last_seal and last_seal > first_seal:
                timing = (f"封板时刻推断：{first_seal} 首封、{last_seal} 最后封板，"
                          "卖出更可能发生在开板区间或尾盘炸板时（板上兑现需要承接）")
            else:
                timing = (f"封板时刻推断：{first_seal} 首封后未再开板，"
                          "卖出更可能是板上排队成交（板上兑现）")
        inference = "seal-time"
    else:
        if side == "buy":
            timing = ("日级推断：收盘涨停 → 倾向于封板前/封板中介入（龙虎榜无逐笔时间，无法精确到分时）"
                      if is_limit else
                      "日级推断：非涨停收盘 → 更可能是盘中接力或低吸（无逐笔时间，仅能定性）")
        else:
            timing = ("日级推断：收盘涨停 → 更可能是板上兑现/换手（无法确认是开板卖出还是尾盘挂单成交）"
                      if is_limit else
                      "日级推断：非涨停收盘 → 更可能是盘中派发或止盈止损（无逐笔时间，仅能定性）")
        inference = "day-level"

    # 因素链补充进时机文案：时机说「什么时候」，因素链说「为什么是这个理由」
    if side == "buy" and fac["buy"]:
        timing += "。买入侧因素链（按权重）：" + "、".join(f"「{f['label']}」" for f in fac["buy"][:3])

    # 席位角色：该席位净额占榜内净额的比例，区分主导方与跟风方
    total_net_abs = sum(abs(float(r.get("NET") or 0)) for r in rows) or 1
    share = abs(float(seat_net or 0)) / total_net_abs
    role = "主导方" if share >= 0.35 else ("重要参与方" if share >= 0.15 else "跟风/次要参与方")
    role_note = f"该席位占榜内净额 {share*100:.0f}%，属{role}"
    logic_parts = [f"主因判定为「{primary}」，依据：" + "；".join(evidence[primary_key][:3])]
    if mood_only:
        logic_parts.append("注意：个股层面无特异证据（题材/博弈/消息/技术均未达标），"
                           "该归因属环境驱动，仅说明当日情绪允许这类资金出手，不代表个股自身逻辑")
    if side == "sell":
        logic_parts.append(f"方向为卖出：{role_note}，更可能是兑现/换手，而非建仓")
    else:
        logic_parts.append(f"方向为买入：{role_note}")
    if secondary:
        logic_parts.append("次要因素：" + "、".join(secondary[:2]))
    if fac["buy"] or fac["sell"]:
        logic_parts.append(factors_lib.summarize(fac))
    if vwap:
        logic_parts.append(f"价位：{zone}")
    logic_parts.append(timing)
    sell_trigger = None
    if side == "sell":
        # 卖出触发改为因素库命中：每条因素自带证据链（哪个条件、什么阈值），
        # 页面可以直接展示「为什么在这里卖」，而不是一句笼统的总结。
        if fac["sell"]:
            sell_trigger = "；".join(
                f"{h['label']}（{'；'.join(h['evidence'][:2])}）" for h in fac["sell"][:4])
        else:
            sell_trigger = "无显著触发特征（证据不足）"

    return {
        "seat_role": role,
        "seat_share": round(share, 2),
        "sell_trigger": sell_trigger,
        "factors": fac,
        "motive": {"primary": primary, "scores": scores,
                   "evidence": {MOTIVE_LABEL[k]: v for k, v in evidence.items()},
                   "secondary": secondary, "mood_only": bool(mood_only)},
        "price_context": {"close": close, "vwap": vwap, "zone": zone,
                          "is_limit_up": bool(is_limit),
                          "board_count": board or None,
                          "first_seal": first_seal or None,
                          "last_seal": last_seal or None,
                          "open_times": int(open_times) if isinstance(open_times, (int, float)) else None,
                          "inference": inference},
        "timing": timing,
        "confidence": confidence,
        "logic": "。".join(logic_parts) + "。",
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def _stock_meta_from_row(row: Dict[str, Any], turnover: Optional[float],
                         seat_row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """个股元信息。成交额/成交量优先取个股行，缺失时回落营业部行（同一天同一只票，口径一致）。

    为什么要回落：`RPT_DAILYBILLBOARD_DETAILS` 不返回 `ACCUM_VOLUME`，而营业部明细有；
    没有成交量就算不出 VWAP，也就无法回答「买入为什么选这个价位」。
    """
    code = str(row.get("SECURITY_CODE") or "")
    full_code = _prefix_code(code)
    if len(full_code) != 8:  # 代码异常（非 6 位）时退回深市，至少不抛错
        full_code = "sz" + code
    seat_row = seat_row or {}
    amount = row.get("ACCUM_AMOUNT") or seat_row.get("ACCUM_AMOUNT")
    volume = row.get("ACCUM_VOLUME") or seat_row.get("ACCUM_VOLUME")
    rate = row.get("TURNOVERRATE")
    if not isinstance(rate, (int, float)):
        rate = turnover
    return {
        "code": full_code, "name": row.get("SECURITY_NAME_ABBR") or row.get("name") or code,
        "price": row.get("CLOSE_PRICE") or seat_row.get("CLOSE_PRICE"),
        "chg": row.get("CHANGE_RATE") if row.get("CHANGE_RATE") is not None else seat_row.get("CHANGE_RATE"),
        "accum_amount": amount, "accum_volume": volume,
        "turnover": rate,
        "turnover_basis": "交易所换手率" if isinstance(row.get("TURNOVERRATE"), (int, float))
                          else ("上榜额/流通市值估算" if turnover else None),
        "reason": row.get("EXPLANATION") or row.get("reason"),
        "explain": row.get("EXPLAIN"),
        "free_cap": row.get("FREE_MARKET_CAP"),
    }


async def build(date: Optional[str] = None) -> Dict[str, Any]:
    """生成某日复盘并落盘。date 缺省 = 最近交易日（以东财返回数据的最大日期为准）。

    没有 `force` 参数：这个函数本来就不读缓存，每次都直接打上游。
    挂一个用不上的 `force` 只会让调用方以为「不传 force 就会走缓存」——
    接口的 `?refresh=true` 语义是「立刻重算」，直接调 build() 即可。
    """
    cfg = config()
    if not date:
        # 往前探 7 天：周末/节假日/当日尚未披露时，最近一个自然日往往没有数据，
        # 只探一次会把 date 落在空日期上，后面全部指标降级。
        date = ""
        now = datetime.now()
        for offset in range(0, 7):
            guess = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            probe = await fetch_stock_list(guess)
            dates = sorted({str(r.get("TRADE_DATE", ""))[:10] for r in probe.get("rows") or []})
            if dates:
                date = dates[-1]
                break
        if not date:
            date = now.strftime("%Y-%m-%d")

    seats_task = fetch_seats(date)
    stocks_task = fetch_stock_list(date)
    ctx_task = market_context(date)
    seats, stocks, ctx = await asyncio.gather(seats_task, stocks_task, ctx_task)

    if not seats.get("ok"):
        return {"ok": False, "version": VERSION, "date": date,
                "message": "未取到当日龙虎榜营业部明细（可能尚未披露或接口异常）",
                "errors": seats.get("errors") or []}

    stock_by_code = {str(r.get("SECURITY_CODE")): r for r in stocks.get("rows") or []}
    # 换手率：成交额 / 流通市值（东财该行同时给出两者）
    turnovers: Dict[str, float] = {}
    for code, row in stock_by_code.items():
        cap = float(row.get("FREE_MARKET_CAP") or 0)
        amt = float(row.get("BILLBOARD_DEAL_AMT") or row.get("ACCUM_AMOUNT") or 0)
        if cap > 0 and amt > 0:
            turnovers[code] = round(amt / cap * 100, 2)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in seats["rows"]:
        grouped.setdefault(str(row.get("SECURITY_CODE")), []).append(row)

    # 同一席位可能同时出现在买入榜与卖出榜：先按 (代码, 席位) 合并，
    # 否则同一笔资金会产出两条方向相反的记录，读起来像两个人。
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for code, rows in grouped.items():
        for row in rows:
            key = (code, str(row.get("OPERATEDEPT_NAME") or ""))
            slot = merged.get(key)
            if slot is None:
                merged[key] = {**row, "BUY": float(row.get("BUY") or 0),
                               "SELL": float(row.get("SELL") or 0),
                               "_rows": [row]}
            else:
                slot["BUY"] += float(row.get("BUY") or 0)
                slot["SELL"] += float(row.get("SELL") or 0)
                slot["_rows"].append(row)
    grouped = {}
    for (code, _seat), row in merged.items():
        row["NET"] = row["BUY"] - row["SELL"]
        grouped.setdefault(code, []).append(row)

    quality = cfg.get("quality") or {}
    min_net = float(quality.get("min_net_amount") or 0)
    inst_net = float(quality.get("institution_min_net") or 0)
    min_win = float(quality.get("min_win_rate_3d") or 0)
    min_samples = int(quality.get("min_win_samples") or 0)
    records: List[Dict[str, Any]] = []
    # 公告一次性并发取（按标的去重），避免「每家席位一次请求」把 20 秒耗在这上面
    lookback = ((cfg.get("motive") or {}).get("news") or {}).get("lookback_days", 5)
    wanted_codes = sorted({_prefix_code(c) for c in grouped})
    ann_results = await asyncio.gather(*[
        asyncio.to_thread(_announcements, code, lookback, date) for code in wanted_codes
    ], return_exceptions=True)
    ann_cache: Dict[str, Dict[str, Any]] = {}
    for code, result in zip(wanted_codes, ann_results):
        ann_cache[code] = (result if isinstance(result, dict)
                           else {"available": False, "reason": "公告抓取异常", "items": []})

    for code, rows in grouped.items():
        stock_row = stock_by_code.get(code) or {}
        meta = _stock_meta_from_row(stock_row or {"SECURITY_CODE": code}, turnovers.get(code),
                                    seat_row=(rows[0] if rows else None))
        for row in rows:
            seat_name = str(row.get("OPERATEDEPT_NAME") or "")
            seat = classify_seat(seat_name, cfg)
            buy = float(row.get("BUY") or 0)
            sell = float(row.get("SELL") or 0)
            net = float(row.get("NET") or (buy - sell))
            win = row.get("RISE_PROBABILITY_3DAY")
            samples = row.get("TOTAL_BUYER_SALESTIMES_3DAY")
            # 优质交易者筛选：机构/北向按金额；游资按名单；其余要有 3 日成功率背书，
            # 且样本数要够——成功率 100% 只出现过 2 次是噪声，不是能力。
            if seat["type"] == "institution":
                keep = abs(net) >= inst_net
            elif seat["type"] in ("northbound", "hot_money"):
                keep = abs(net) >= min_net
            else:
                enough = (not isinstance(samples, (int, float))) or samples >= min_samples
                keep = (isinstance(win, (int, float)) and win >= min_win
                        and abs(net) >= min_net and enough)
            if not keep:
                continue

            ann = ann_cache.get(meta["code"])
            side = "buy" if net >= 0 else "sell"
            attributed = attribute(meta["code"], meta["name"], rows, meta, ctx, cfg, ann,
                                   side=side, seat_net=net)
            buy_rio = row.get("TOTAL_BUYRIO")
            sell_rio = row.get("TOTAL_SELLRIO")
            record = {
                "date": date, "code": meta["code"], "name": meta["name"],
                "seat": seat_name, "seat_type": seat["type"], "seat_alias": seat["alias"],
                "seat_style": seat["style"], "alias_source": seat["alias_source"],
                "side": side, "buy": round(buy, 2), "sell": round(sell, 2), "net": round(net, 2),
                "net_wan": round(net / 1e4, 1),
                # 东财 TOTAL_BUYRIO 是「占当日成交额」的小数（0.0211 = 2.11%），统一换成百分数
                "buy_ratio_pct": round(float(buy_rio) * 100, 2) if isinstance(buy_rio, (int, float)) else None,
                "sell_ratio_pct": round(float(sell_rio) * 100, 2) if isinstance(sell_rio, (int, float)) else None,
                "win_rate_3d": round(float(win), 1) if isinstance(win, (int, float)) else None,
                "win_samples": int(samples) if isinstance(samples, (int, float)) else None,
                "stock_chg": meta["chg"], "reason": meta["reason"],
                "stock_explain": meta.get("explain"),
                "board_count": (attributed.get("price_context") or {}).get("board_count"),
                **attributed,
            }
            records.append(record)

    records.sort(key=lambda r: abs(r["net"]), reverse=True)
    limit = int(quality.get("max_records") or 40)
    records = records[:limit]
    by_motive: Dict[str, int] = {}
    for rec in records:
        by_motive[rec["motive"]["primary"]] = by_motive.get(rec["motive"]["primary"], 0) + 1

    report = {
        "ok": True, "version": VERSION, "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "东方财富龙虎榜营业部明细（RPT_BILLBOARD_DAILYDETAILSBUY/SELL）；涨停/炸板/跌停池取自 akshare 东财同口径",
        "context": {
            "available": bool(ctx.get("available")),
            "reason": ctx.get("reason"),
            "zt_count": ctx.get("zt_count"),
            "zb_count": ctx.get("zb_count"),
            "dt_count": ctx.get("dt_count"),
            "seal_rate": ctx.get("seal_rate"),
            "max_board": ctx.get("max_board"),
            "board_distribution": ctx.get("board_distribution"),
            "top_industries": sorted((ctx.get("industries") or {}).items(),
                                     key=lambda kv: -kv[1])[:8],
        },
        "summary": {
            "stocks": len({r["code"] for r in records}),
            "records": len(records),
            "buy_records": len([r for r in records if r["side"] == "buy"]),
            "sell_records": len([r for r in records if r["side"] == "sell"]),
            "by_motive": by_motive,
            "seal_time_inferred": len([r for r in records
                                       if (r.get("price_context") or {}).get("inference") == "seal-time"]),
            # 因素覆盖：该方向至少命中一条因素的记录数。覆盖不足说明事实面缺失，
            # 因素库的结论要按「证据不足」读，不能当作全量统计。
            "factor_covered": len([r for r in records
                                   if (r.get("factors") or {}).get(r.get("side") or "buy")]),
            "net_buy_wan": round(sum(r["net"] for r in records if r["side"] == "buy") / 1e4, 1),
            "net_sell_wan": round(sum(r["net"] for r in records if r["side"] == "sell") / 1e4, 1),
        },
        "records": records,
        "caveats": [
            "龙虎榜不提供逐笔时间：能取到涨停封板时间的标的，时机判断为「封板时刻推断」(inference=seal-time)，"
            "其余退回日级推断 (inference=day-level)，均非分时级证据",
            "营业部→游资别名属市场俗称，未逐条核实（alias_source 已标注）",
            "动因为规则打分的启发式结论，不构成投资建议",
        ],
        "errors": seats.get("errors") or [],
    }
    # 横切重组（按标的 / 席位 / 题材）在 insight 层完成，本层只管「一条记录长什么样」。
    # 传原始 ctx 而不是 report["context"]：后者已裁成计数，没有逐票行业。
    report["insight"] = insight.aggregate(report, ctx)
    _persist(report)
    return report


def _persist(report: Dict[str, Any]) -> None:
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        path = os.path.join(STORE_DIR, f"{report['date']}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        latest_tmp = LATEST_PATH + ".tmp"
        with open(latest_tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        os.replace(latest_tmp, LATEST_PATH)
    except Exception as exc:
        logger.warning("lhb report persist failed: %s", exc)


def load(date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    path = LATEST_PATH if not date else os.path.join(STORE_DIR, f"{date}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def recent(limit: int = 10) -> List[Dict[str, Any]]:
    try:
        files = sorted((f for f in os.listdir(STORE_DIR) if re.match(r"\d{4}-\d{2}-\d{2}\.json$", f)),
                       reverse=True)
    except OSError:
        return []
    out = []
    for name in files[:limit]:
        data = load(name[:10])
        if data:
            out.append({"date": data.get("date"), "records": (data.get("summary") or {}).get("records"),
                        "by_motive": (data.get("summary") or {}).get("by_motive"),
                        "generated_at": data.get("generated_at")})
    return out


__all__ = ["VERSION", "config", "classify_seat", "attribute", "build", "load", "recent",
           "fetch_seats", "fetch_stock_list", "market_context", "fetch_limit_pools"]
