#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""8 分制策略候选池（strategy-profile.v1）。

来源与定位
----------
规则来自 `交易策略配置文件.docx` 的 `trade_ai` 方案包（config.json + trade_engine.py + prompts.md）。
本模块把它**作为独立纸面策略档案接入**：不覆盖系统现有的六专家研判与风控默认值，
只多输出一份「按 8 分制筛出来的候选池」，让两套结论可以互相对照。

为什么不能直接照搬
------------------
文档里的 `trade_engine.py` 假设外部已经喂进 `ma20 / platform_high / breakout / volume_ratio /
sector_rank / leader_limit_up / has_risk`。本系统没有这些字段的统一出口，所以这里补一层
**数据装配**：从全市场快照取量价，从板块映射算行业强度与龙头，从日线 K 线算 MA20 与平台高点。

评分口径（每项 0~2，总分≥8 且无雷、盈亏比≥2 才入池）
----------------------------------------------------
1. 市场：指数在 20 日线上 + 跌停<10 + 涨停>30 → 2；否则部分满足 → 1；破位/跌停潮 → 0
2. 板块：行业强度前 5 且板块内有龙头涨停 → 2；满足其一 → 1
3. 个股：站上 MA20 且放量突破平台高点 → 2；仅站上 MA20 → 1
4. 量价：涨幅 3~7%、量比>1.5、成交额>2 亿、换手 5~15% 全满足 → 2；满足 2 项以上 → 1
5. 风险：无雷且盈亏比≥2 → 2；无雷但盈亏比不足 → 1；有雷 → 0

边界
----
* 全市场行情来自 `market_coverage_worker._LAST_ROWS`（进程内快照）。刚重启、worker 未跑完时
  返回 `available:false` + reason，不用空列表冒充「今天没有候选」。
* `has_risk` 是**代理指标**：ST/退市风险 + 业绩预亏（profit_yoy ≤ 阈值）。文档里的
  「减持/立案」需要公告解析，本模块没有接入，会在 caveats 里写明，不假装覆盖。
* 只出候选，不下单。`auto_execute` 固定为 false：本系统不接券商通道。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from server.tencent_kline import fetch_tencent_kline

logger = logging.getLogger(__name__)

VERSION = "strategy-profile.v1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "strategy_profile.json")

DEFAULTS: Dict[str, Any] = {
    "total_capital": 3200, "max_loss_per_trade": 64,
    "max_single_stock_pct": 0.6, "min_cash_pct": 0.4,
    "first_entry_min": 800, "first_entry_max": 1000,
    "stop_loss_pct": 0.06, "target_pct_min": 0.12, "min_rr": 2.0,
    "max_trades_per_day": 1, "confirm_start": "09:45", "confirm_end": "10:00",
    "slippage_pct": 0.001, "fee_rate": 0.0003, "max_hold_days": 5,
    "pause_after_losses": 2, "pause_days": 3, "daily_loss_pause_pct": 0.02,
    "auto_execute": False, "execution_mode": "paper",
    "prefilter": {"chg_min": 3.0, "chg_max": 7.0, "amount_min": 2e8,
                  "turnover_min": 5.0, "turnover_max": 15.0,
                  "max_candidates": 40, "min_price": 2.0, "max_price": 300.0},
    "scoring": {
        "pass_score": 8,
        "market": {"limit_up_strong": 30, "limit_down_safe": 10,
                   "limit_down_max": 15, "limit_up_weak": 20},
        "sector": {"top_rank": 5},
        "stock": {"platform_days": 20, "breakout_buffer": 0.001},
        "volume": {"chg_min": 3.0, "chg_max": 7.0, "volume_ratio_min": 1.5,
                   "amount_min": 2e8, "turnover_min": 5.0, "turnover_max": 15.0},
        "risk": {"profit_yoy_danger": -50.0},
    },
    "index_code": "sh000001",
}

_STATE: Dict[str, Any] = {"mtime": None, "data": None, "result": None, "result_ts": 0.0}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config(force: bool = False) -> Dict[str, Any]:
    """读配置；mtime 热重载 + 深合并，新增默认键不会因配置文件存在而失效。"""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    if not force and _STATE["data"] is not None and _STATE["mtime"] == mtime:
        return _STATE["data"]
    data = json.loads(json.dumps(DEFAULTS))
    if mtime is not None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = _deep_merge(data, {k: v for k, v in raw.items() if not k.startswith("_")})
        except Exception as exc:
            logger.warning("strategy profile config 读取失败，用默认值：%s", exc)
    _STATE.update({"mtime": mtime, "data": data})
    return data


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def limit_threshold(code: str, name: str = "") -> float:
    """涨停阈值（%）。与交易所口径一致：主板 10、双创 20、北交所 30、ST 5。"""
    text = (name or "").upper()
    if "ST" in text:
        return 4.8
    if code.startswith("bj"):
        return 29.5
    if code.startswith(("sh688", "sz300", "sz301")):
        return 19.5
    return 9.5


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _sma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


# ---------------------------------------------------------------------------
# 数据装配
# ---------------------------------------------------------------------------
def _universe_rows() -> List[Dict[str, Any]]:
    """进程内全市场快照。worker 未跑完时为空，由调用方给出 reason。"""
    try:
        from server.market_coverage_worker import _LAST_ROWS

        return list(_LAST_ROWS)
    except Exception:
        return []


def _sector_lookup() -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """返回 (股票→行业名, 行业名→股票列表)。

    行业取板块层级 2（行业Ⅱ）优先，其次 3（细分Ⅲ）：层级 2 样本更厚，
    排名不会被只有 5 只票的细分板块偶然拉到第一。
    """
    try:
        from server.sector_map import load_sector_map

        data = load_sector_map() or {}
    except Exception:
        return {}, {}
    sectors = data.get("sectors") or {}
    index = data.get("stock_index") or {}
    best: Dict[str, Tuple[int, str]] = {}
    for code, bks in index.items():
        for bk in bks or []:
            item = sectors.get(bk) or {}
            name = item.get("name")
            level = int(item.get("level") or 9)
            if not name:
                continue
            score = 0 if level == 2 else (1 if level == 3 else 2)
            if code not in best or score < best[code][0]:
                best[code] = (score, name)
    stock_sector = {code: name for code, (_, name) in best.items()}
    members: Dict[str, List[str]] = {}
    for code, name in stock_sector.items():
        members.setdefault(name, []).append(code)
    return stock_sector, members


def sector_strength(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按行业聚合：平均涨幅、上涨占比、样本数、龙头（涨幅最高）与龙头是否涨停。"""
    stock_sector, members = _sector_lookup()
    by_code = {str(r.get("code")): r for r in rows if r.get("code")}
    out: Dict[str, Dict[str, Any]] = {}
    for name, codes in members.items():
        picked = [by_code[c] for c in codes if c in by_code and _num(by_code[c].get("chg")) is not None]
        if len(picked) < 3:
            continue
        chgs = [_num(r.get("chg")) or 0.0 for r in picked]
        leader = max(picked, key=lambda r: _num(r.get("chg")) or -99)
        leader_chg = _num(leader.get("chg")) or 0.0
        out[name] = {
            "name": name,
            "count": len(picked),
            "avg_chg": round(sum(chgs) / len(chgs), 2),
            "up_ratio": round(sum(1 for c in chgs if c > 0) / len(chgs) * 100, 1),
            "leader": {"code": leader.get("code"), "name": leader.get("name"), "chg": leader_chg},
            "leader_limit_up": leader_chg >= limit_threshold(str(leader.get("code")), str(leader.get("name"))),
        }
    ranked = sorted(out.values(), key=lambda x: -x["avg_chg"])
    for i, item in enumerate(ranked, 1):
        item["rank"] = i
    return {item["name"]: item for item in ranked}


async def _index_above_ma20(code: str) -> Tuple[Optional[bool], Optional[float], Optional[float]]:
    """指数是否在 20 日线上，返回 (above, close, ma20)。拿不到就 (None, None, None)。"""
    try:
        bars = await fetch_tencent_kline(code, period="day", limit=30, timeout=4.0)
    except Exception:
        bars = None
    if not bars or len(bars) < 21:
        return None, None, None
    closes = [_num(b.get("c")) for b in bars]
    closes = [c for c in closes if c is not None]
    if len(closes) < 21:
        return None, None, None
    ma20 = _sma(closes, 20)
    if ma20 is None:
        return None, None, None
    return closes[-1] > ma20, round(closes[-1], 2), round(ma20, 2)


async def _stock_features(code: str, platform_days: int, buffer: float,
                          timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """日线派生：MA20、平台高点（前 N 日最高）、量比、是否突破。"""
    try:
        bars = await fetch_tencent_kline(code, period="day", limit=platform_days + 10, timeout=timeout)
    except Exception:
        return None
    if not bars or len(bars) < platform_days + 2:
        return None
    closes = [_num(b.get("c")) for b in bars]
    highs = [_num(b.get("h")) for b in bars]
    vols = [_num(b.get("v")) for b in bars]
    if any(v is None for v in closes[-platform_days - 1:]) or any(v is None for v in highs[-platform_days - 1:]):
        return None
    today_close = closes[-1]
    ma20 = _sma([c for c in closes if c is not None], 20)
    # 平台高点：不含今天的前 N 日最高（用今天去突破它才有意义）
    platform_high = max(highs[-platform_days - 1:-1])
    prev_vols = [v for v in vols[-6:-1] if v]
    volume_ratio = round((vols[-1] / (sum(prev_vols) / len(prev_vols))), 2) if prev_vols else None
    breakout = bool(platform_high and today_close > platform_high * (1 + buffer))
    return {
        "ma20": round(ma20, 3) if ma20 else None,
        "platform_high": round(platform_high, 3),
        "volume_ratio": volume_ratio,
        "breakout": breakout,
        "above_ma20": bool(ma20 and today_close > ma20),
    }


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def score_market(above_ma20: Optional[bool], limit_up: int, limit_down: int,
                 rule: Dict[str, Any]) -> int:
    strong = int(rule.get("limit_up_strong", 30))
    safe = int(rule.get("limit_down_safe", 10))
    weak_up = int(rule.get("limit_up_weak", 20))
    max_down = int(rule.get("limit_down_max", 15))
    if above_ma20 and limit_down < safe and limit_up > strong:
        return 2
    if above_ma20 or (limit_down < max_down and limit_up > weak_up):
        return 1
    return 0


def score_sector(strength: Optional[Dict[str, Any]], rule: Dict[str, Any]) -> int:
    if not strength:
        return 0
    top = int(rule.get("top_rank", 5))
    rank_ok = int(strength.get("rank") or 99) <= top
    leader_ok = bool(strength.get("leader_limit_up"))
    if rank_ok and leader_ok:
        return 2
    if rank_ok or leader_ok:
        return 1
    return 0


def score_stock(features: Optional[Dict[str, Any]]) -> int:
    if not features:
        return 0
    if features.get("above_ma20") and features.get("breakout"):
        return 2
    if features.get("above_ma20"):
        return 1
    return 0


def score_volume(row: Dict[str, Any], volume_ratio: Optional[float],
                 rule: Dict[str, Any]) -> int:
    chg = _num(row.get("chg"))
    amount = _num(row.get("amount"))
    turnover = _num(row.get("turnover"))
    checks = [
        chg is not None and float(rule.get("chg_min", 3.0)) <= chg <= float(rule.get("chg_max", 7.0)),
        volume_ratio is not None and volume_ratio > float(rule.get("volume_ratio_min", 1.5)),
        amount is not None and amount > float(rule.get("amount_min", 2e8)),
        turnover is not None and float(rule.get("turnover_min", 5.0)) <= turnover <= float(rule.get("turnover_max", 15.0)),
    ]
    if all(checks):
        return 2
    return 1 if sum(checks) >= 2 else 0


def has_risk(row: Dict[str, Any], rule: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """风险代理：ST/退市 + 业绩预亏。公告类风险（减持/立案）未接入，caveats 里注明。"""
    flags: List[str] = []
    name = str(row.get("name") or "")
    if "ST" in name.upper():
        flags.append("ST/风险警示")
    if "退" in name:
        flags.append("退市风险")
    profit = _num(row.get("profit_yoy"))
    if profit is not None and profit <= float(rule.get("profit_yoy_danger", -50.0)):
        flags.append(f"业绩大幅下滑 {profit:.0f}%")
    return bool(flags), flags


def position_plan(cfg: Dict[str, Any], score: int, price: float,
                  cash: Optional[float] = None) -> Dict[str, Any]:
    """按文档的资金约束算首笔金额与股数（100 股整手）。"""
    capital = float(cfg.get("total_capital") or 0) or 0
    available = float(cash if cash is not None else capital)
    single_cap = capital * float(cfg.get("max_single_stock_pct", 0.6))
    cash_cap = available - capital * float(cfg.get("min_cash_pct", 0.4))
    risk_cap = float(cfg.get("max_loss_per_trade", 64)) / float(cfg.get("stop_loss_pct", 0.06))
    want = float(cfg.get("first_entry_max", 1000) if score >= 9 else cfg.get("first_entry_min", 800))
    amount = max(0.0, min(want, single_cap, cash_cap, risk_cap))
    shares = int(amount / price / 100) * 100 if price > 0 else 0
    return {
        "amount": round(shares * price, 2),
        "shares": shares,
        "caps": {"want": want, "single_stock": round(single_cap, 2),
                 "cash_floor": round(cash_cap, 2), "risk_budget": round(risk_cap, 2)},
        "binding": min(
            [("first_entry", want), ("single_stock", single_cap),
             ("cash_floor", cash_cap), ("risk_budget", risk_cap)],
            key=lambda kv: kv[1])[0],
    }


def _price_plan(cfg: Dict[str, Any], entry: float) -> Dict[str, Any]:
    stop = entry * (1 - float(cfg.get("stop_loss_pct", 0.06)))
    target = entry * (1 + float(cfg.get("target_pct_min", 0.12)))
    rr = (target - entry) / (entry - stop) if entry > stop else 0.0
    return {"stop": round(stop, 2), "target": round(target, 2), "rr": round(rr, 2)}


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
async def build(force: bool = False, ttl: int = 180) -> Dict[str, Any]:
    """生成候选池。结果缓存 `ttl` 秒：K 线请求有成本，面板轮询不应反复打上游。"""
    now = time.time()
    cached = _STATE.get("result")
    if not force and cached and now - float(_STATE.get("result_ts") or 0) < ttl:
        return cached

    cfg = config(force=force)
    rows = _universe_rows()
    if not rows:
        return {
            "ok": False, "version": VERSION, "available": False,
            "reason": "全市场快照尚未就绪（后台覆盖任务未完成），稍后重试",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    # 市场维度：指数 MA20 + 全市场涨跌停家数
    above, idx_close, idx_ma20 = await _index_above_ma20(str(cfg.get("index_code") or "sh000001"))
    quotes = [r for r in rows if _num(r.get("chg")) is not None]
    limit_up = sum(1 for r in quotes if (_num(r.get("chg")) or 0) >= limit_threshold(str(r.get("code")), str(r.get("name"))))
    limit_down = sum(1 for r in quotes if (_num(r.get("chg")) or 0) <= -limit_threshold(str(r.get("code")), str(r.get("name"))))
    market_score = score_market(above, limit_up, limit_down, (cfg.get("scoring") or {}).get("market") or {})

    strength = sector_strength(quotes)

    # 预筛：量价条件先收敛，控制 K 线请求量
    pre = cfg.get("prefilter") or {}
    pool = []
    for r in quotes:
        price = _num(r.get("price"))
        chg = _num(r.get("chg")) or 0
        amount = _num(r.get("amount")) or 0
        turnover = _num(r.get("turnover"))
        if price is None or price < float(pre.get("min_price", 2)) or price > float(pre.get("max_price", 300)):
            continue
        if not (float(pre.get("chg_min", 3)) <= chg <= float(pre.get("chg_max", 7))):
            continue
        if amount < float(pre.get("amount_min", 2e8)):
            continue
        if turnover is None or not (float(pre.get("turnover_min", 5)) <= turnover <= float(pre.get("turnover_max", 15))):
            continue
        pool.append(r)
    pool.sort(key=lambda r: -(_num(r.get("amount")) or 0))
    pool = pool[: int(pre.get("max_candidates", 40))]

    stock_rule = (cfg.get("scoring") or {}).get("stock") or {}
    platform_days = int(stock_rule.get("platform_days", 20))
    buffer = float(stock_rule.get("breakout_buffer", 0.001))

    sem = asyncio.Semaphore(10)

    async def enrich(row: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            feats = await _stock_features(str(row.get("code")), platform_days, buffer)
        return {**row, "_features": feats}

    enriched = await asyncio.gather(*[enrich(r) for r in pool], return_exceptions=False)

    vol_rule = (cfg.get("scoring") or {}).get("volume") or {}
    risk_rule = (cfg.get("scoring") or {}).get("risk") or {}
    pass_score = int((cfg.get("scoring") or {}).get("pass_score", 8))
    stock_sector, _ = _sector_lookup()

    candidates: List[Dict[str, Any]] = []
    evaluated = 0
    for row in enriched:
        feats = row.get("_features")
        if not feats:
            continue
        evaluated += 1
        code = str(row.get("code"))
        name = str(row.get("name") or code)
        price = _num(row.get("price")) or 0.0
        plan = _price_plan(cfg, price)
        risky, risk_flags = has_risk(row, risk_rule)
        sec = strength.get(stock_sector.get(code, ""))
        detail = {
            "market": market_score,
            "sector": score_sector(sec, (cfg.get("scoring") or {}).get("sector") or {}),
            "stock": score_stock(feats),
            "volume": score_volume(row, feats.get("volume_ratio"), vol_rule),
            "risk": 2 if (not risky and plan["rr"] >= float(cfg.get("min_rr", 2.0))) else (1 if not risky else 0),
        }
        total = sum(detail.values())
        if total < pass_score or risky or plan["rr"] < float(cfg.get("min_rr", 2.0)):
            continue
        sizing = position_plan(cfg, total, price)
        candidates.append({
            "code": code, "name": name,
            "sector": stock_sector.get(code) or "",
            "sector_rank": (sec or {}).get("rank"),
            "sector_leader_limit_up": bool((sec or {}).get("leader_limit_up")),
            "score": total, "score_detail": detail,
            "price": price, "chg": _num(row.get("chg")),
            "amount": _num(row.get("amount")), "turnover": _num(row.get("turnover")),
            "ma20": feats.get("ma20"), "platform_high": feats.get("platform_high"),
            "volume_ratio": feats.get("volume_ratio"), "breakout": feats.get("breakout"),
            "risk_flags": risk_flags,
            "stop": plan["stop"], "target": plan["target"], "rr": plan["rr"],
            "first_entry_amount": sizing["amount"], "shares": sizing["shares"],
            "sizing_caps": sizing["caps"], "binding_constraint": sizing["binding"],
            "trigger": f"次日 {cfg.get('confirm_start')}~{cfg.get('confirm_end')}，"
                       "高开 0~3%、价格≥VWAP 且量比>1.5",
        })

    candidates.sort(key=lambda x: (-x["score"], -(x["rr"] or 0), -(x["amount"] or 0)))
    candidates = candidates[:8]

    report = {
        "ok": True,
        "version": VERSION,
        "available": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": time.strftime("%Y-%m-%d"),
        "profile": "trade_ai 8 分制（独立纸面档案）",
        "market": {
            "index_code": cfg.get("index_code"),
            "index_close": idx_close, "index_ma20": idx_ma20,
            "above_ma20": above,
            "limit_up": limit_up, "limit_down": limit_down,
            "sample": len(quotes),
            "score": market_score,
        },
        "funnel": {
            "universe": len(rows), "quoted": len(quotes),
            "prefiltered": len(pool), "scored": evaluated,
            "passed": len(candidates),
        },
        "candidates": candidates,
        "runtime": {
            "total_capital": cfg.get("total_capital"),
            "max_loss_per_trade": cfg.get("max_loss_per_trade"),
            "stop_loss_pct": cfg.get("stop_loss_pct"),
            "target_pct_min": cfg.get("target_pct_min"),
            "min_rr": cfg.get("min_rr"),
            "max_trades_per_day": cfg.get("max_trades_per_day"),
            "confirm_window": [cfg.get("confirm_start"), cfg.get("confirm_end")],
            "auto_execute": False,
            "execution_mode": "paper",
        },
        "caveats": [
            "风险项为代理指标（ST/退市风险 + 业绩预亏）；减持/立案需公告解析，未接入",
            "板块强度按行业Ⅱ聚合全市场快照；样本少于 3 只的行业不参与排名",
            "平台高点取前 20 日最高价（不含当日），量比为当日量/前 5 日均量",
            "本档案只出候选，不接券商通道；auto_execute 固定关闭",
        ],
    }
    _STATE.update({"result": report, "result_ts": now})
    return report


__all__ = ["VERSION", "config", "build", "score_market", "score_sector", "score_stock",
           "score_volume", "has_risk", "position_plan", "limit_threshold", "sector_strength"]
