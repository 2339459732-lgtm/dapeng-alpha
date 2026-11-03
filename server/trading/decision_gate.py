"""决策闸（DecisionGate）—— 价值 + 事件 + 板块 + 技术 四维评分

定位（用户诉求「买股票就是买公司」）：让每一笔**买入**都必须过闸，产出
`allow / confidence / 可追溯的结构化理由`。**拒绝是一种能力**——系统此前没有
"说不"的能力。

四维（可用性由只读审计实证确定，拿不到一律 `null + reason`，绝不编造）：
  ① sector    板块强度   ← server.sector_map + /sector-ranking + /sector-linkage + /sector-clusters（全可用）
  ② valuation 估值       ← 个股 PE（腾讯实时 field39，非 PE_TTM，degraded）；PB/财报/现金流**不可得**
  ③ events    事件       ← ak_hub.get_notices_and_news（新浪要闻流，可用）；仅做**保守的标题匹配**，
                            不编造情绪分；源不可达 → has_event=None + reason
  ④ technical 技术       ← 现有策略信号（保留为入场触发，但不再是唯一依据）

红线：**拿不到就 null + reason**，绝不编造现金流/PB/股权结构/情绪分。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from server.cache import cache
from server.core.numbers import clamp as _clamp

logger = logging.getLogger(__name__)

# 四维权重（buy 方向）；某维不可得时在"可得维"上重新归一化
_WEIGHTS = {"sector": 0.35, "valuation": 0.20, "events": 0.15, "technical": 0.30}
_ALLOW_THRESHOLD = 0.50
# 目标日波动（%）：低波动标的给予更大仓位权重（波动率倒数）
_TARGET_DAILY_VOL_PCT = 2.0
_VOL_FACTOR_MIN, _VOL_FACTOR_MAX = 0.5, 1.5

# 各策略买入信号的基础技术分（0~1）；有信号分值时优先用 score/100
_TECH_BASE = {
    "strategy_buy": 0.60, "score": 0.60, "momentum": 0.60, "breakout": 0.65,
    "macd_gold": 0.60, "ma_cross_gold": 0.60, "volume_divergence": 0.55,
    "ma_bullish": 0.60, "pullback": 0.60, "bottom_reversal": 0.55,
    "dragon_head": 0.60, "momentum_reversal": 0.55,
}

_clusters_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_CLUSTERS_TTL = 300.0


# ============================================================
# 决策闸参数可配置（KV "decision_gate"，缺省=下方默认；异常回退默认）
# ============================================================
DEFAULT_GATE_CONFIG: Dict[str, Any] = {
    "weights": dict(_WEIGHTS),
    "allow_threshold": _ALLOW_THRESHOLD,
    "target_daily_vol_pct": _TARGET_DAILY_VOL_PCT,
    "vol_factor_min": _VOL_FACTOR_MIN,
    "vol_factor_max": _VOL_FACTOR_MAX,
    "tech_base": dict(_TECH_BASE),
}

_gate_cfg_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_GATE_CFG_TTL = 30.0


def normalize_gate_config(saved: Any) -> Dict[str, Any]:
    """把 KV 里的用户配置合并到默认值上，逐项校验；非法项丢弃（回退默认）。"""
    import copy
    cfg = copy.deepcopy(DEFAULT_GATE_CONFIG)
    if not isinstance(saved, dict):
        return cfg
    sw = saved.get("weights")
    if isinstance(sw, dict):
        for k in cfg["weights"]:
            v = _f(sw.get(k))
            if v is not None and 0.0 <= v <= 1.0:
                cfg["weights"][k] = v
    if all(v <= 0 for v in cfg["weights"].values()):
        cfg["weights"] = dict(_WEIGHTS)
    v = _f(saved.get("allow_threshold"))
    if v is not None and 0.0 < v < 1.0:
        cfg["allow_threshold"] = v
    v = _f(saved.get("target_daily_vol_pct"))
    if v is not None and v > 0:
        cfg["target_daily_vol_pct"] = v
    lo = _f(saved.get("vol_factor_min"))
    hi = _f(saved.get("vol_factor_max"))
    if lo is not None and lo > 0:
        cfg["vol_factor_min"] = lo
    if hi is not None and hi >= cfg["vol_factor_min"]:
        cfg["vol_factor_max"] = hi
    st = saved.get("tech_base")
    if isinstance(st, dict):
        for k, vv in st.items():
            fv = _f(vv)
            if fv is not None and 0.0 <= fv <= 1.0:
                cfg["tech_base"][str(k)] = fv
    return cfg


async def load_gate_config(store: Any = None) -> Dict[str, Any]:
    """读 KV "decision_gate"（30s 缓存）；store 为 None 或异常时回退默认。"""
    now = time.time()
    if _gate_cfg_cache["data"] is not None and now - _gate_cfg_cache["ts"] < _GATE_CFG_TTL:
        return _gate_cfg_cache["data"]
    cfg = DEFAULT_GATE_CONFIG
    if store is not None:
        try:
            cfg = normalize_gate_config(await store.kv_get("decision_gate"))
        except Exception:
            logger.warning("decision_gate: 读取配置失败，回退默认", exc_info=True)
            cfg = DEFAULT_GATE_CONFIG
    _gate_cfg_cache["ts"] = now
    _gate_cfg_cache["data"] = cfg
    return cfg


def bust_gate_config_cache() -> None:
    _gate_cfg_cache["ts"] = 0.0
    _gate_cfg_cache["data"] = None


def _f(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


# ============================================================
# 数据获取（内部函数直连，避免 self-HTTP；均容错）
# ============================================================
async def _sector_ranking_rows() -> Optional[List[Dict[str, Any]]]:
    """板块排行全量（与 /sector-ranking 同源同 cache key，复用其缓存）。"""
    async def _fetch():
        try:
            from server.stable_market_provider import stable_provider
            res = await stable_provider.get_sector_ranking_data()
            if res:
                return res
        except Exception:
            logger.debug("decision_gate: stable_provider sector ranking failed", exc_info=True)
        try:
            from server.westock import westock
            return await westock.sector_ranking()
        except Exception:
            return []
    try:
        from server.config import CACHE_TTL_SECTOR_RANK
        rows = await cache.get_or_fetch("sector_ranking_v2", _fetch, CACHE_TTL_SECTOR_RANK)
        return rows if isinstance(rows, list) else []
    except Exception:
        logger.warning("decision_gate: sector ranking unavailable", exc_info=True)
        return None


def _stock_sector_rows(code: str) -> Optional[List[Dict[str, Any]]]:
    try:
        from server.sector_map import get_stock_sectors_detailed
        return get_stock_sectors_detailed(code)
    except Exception:
        logger.debug("decision_gate: sector_map failed", exc_info=True)
        return None


def _cluster_id_of(sector_code: str) -> Tuple[Optional[str], Optional[int]]:
    """该板块所属同涨同跌簇（去市场因子相关聚类）。返回 (簇id, 簇大小)。"""
    now = time.time()
    if _clusters_cache["data"] is None or now - _clusters_cache["ts"] > _CLUSTERS_TTL:
        try:
            from server.sector_graph import get_clusters
            _clusters_cache["data"] = get_clusters()
            _clusters_cache["ts"] = now
        except Exception:
            logger.debug("decision_gate: clusters failed", exc_info=True)
            _clusters_cache["data"] = {}
            _clusters_cache["ts"] = now
    data = _clusters_cache["data"] or {}
    for idx, cl in enumerate(data.get("clusters") or []):
        for m in cl.get("members") or []:
            if m.get("code") == sector_code:
                return f"C{idx}", cl.get("size")
    return None, None


def _sector_linkage_pick(sector_code: str) -> Optional[Dict[str, Any]]:
    """该板块对外部因子敏感度中 |corr| 最大者（status=ok）。"""
    try:
        from server.sector_linkage import get_sector_linkage
        data = get_sector_linkage(code=sector_code)
        if not data.get("ok"):
            return None
        sens = data.get("sensitivities")
        items: List[Dict[str, Any]] = []
        if isinstance(sens, dict):
            items = list(sens.values())
        elif isinstance(sens, list):
            items = sens
        best = None
        for it in items:
            if not isinstance(it, dict) or it.get("status") != "ok":
                continue
            c = _f(it.get("corr"))
            if c is None:
                continue
            if best is None or abs(c) > abs(_f(best.get("corr")) or 0.0):
                best = it
        return best
    except Exception:
        logger.debug("decision_gate: linkage failed", exc_info=True)
        return None


async def _stock_pe(code: str) -> Optional[float]:
    try:
        from server.realtime import fetch_single_quote
        q = await fetch_single_quote(code)
        return _f((q or {}).get("pe"))
    except Exception:
        logger.debug("decision_gate: realtime pe failed", exc_info=True)
        return None


async def _klines(code: str) -> List[Dict[str, Any]]:
    try:
        from server.config import KLINE_LIMIT, CACHE_TTL_KLINE
        from server.tencent_kline import fetch_tencent_kline
        return await cache.get_or_fetch(
            f"raw_kline:{code}:day",
            lambda: fetch_tencent_kline(code, period="day", limit=KLINE_LIMIT),
            CACHE_TTL_KLINE,
        ) or []
    except Exception:
        logger.debug("decision_gate: klines failed", exc_info=True)
        return []


def _volatility_pct(klines: List[Dict[str, Any]], window: int = 20) -> Optional[float]:
    if not klines or len(klines) < window + 1:
        return None
    closes = np.array([_f(k.get("c")) or 0.0 for k in klines[-(window + 1):]], dtype=float)
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    if len(rets) < 5:
        return None
    return float(np.std(rets, ddof=1) * 100.0)


async def _news(limit: int = 60) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """市场要闻流。返回 (items, source)；不可达 → (None, None)。"""
    try:
        from server.ak_market import ak_hub
        res = await ak_hub.get_notices_and_news(limit=limit)
        if isinstance(res, dict) and res.get("ok") and isinstance(res.get("data"), list):
            return res["data"], res.get("source")
        return None, None
    except Exception:
        logger.debug("decision_gate: news source failed", exc_info=True)
        return None, None


# ============================================================
# 四维评分
# ============================================================
async def _dim_sector(code: str) -> Dict[str, Any]:
    sectors = _stock_sector_rows(code)
    if sectors is None:
        return {"score": None, "detail": "板块映射未构建", "source": "sector_map",
                "degraded": True, "sector": None, "chg": None, "net_flow": None,
                "net_flow_reason": "sector_map_missing"}
    rows = await _sector_ranking_rows()
    if not rows:
        return {"score": None, "detail": "板块排行不可用", "source": "sector-ranking",
                "degraded": True, "sector": (sectors[0].get("name") if sectors else None),
                "chg": None, "net_flow": None}
    by = {r.get("code"): r for r in rows if isinstance(r, dict)}
    pick = next((s for s in sectors if s.get("code") in by), None)
    if pick is None:
        return {"score": None, "detail": "所属板块不在排行宇宙", "source": "sector-ranking",
                "degraded": True, "sector": (sectors[0].get("name") if sectors else None),
                "chg": None, "net_flow": None}
    r = by[pick["code"]]
    chg = _f(r.get("chg"))
    nf = _f(r.get("net_flow"))
    nf_reason = r.get("net_flow_reason")
    up, dn = _f(r.get("up_count")), _f(r.get("down_count"))

    parts: List[float] = []
    evid: List[str] = []
    if chg is not None:
        parts.append(_clamp(0.5 + chg / 20.0, 0.0, 1.0)); evid.append(f"{pick.get('name')} chg{chg:+.2f}%")
    if nf is not None:
        parts.append(_clamp(0.5 + nf / 2e8, 0.0, 1.0)); evid.append(f"net_flow{nf/1e8:+.2f}亿")
    elif nf_reason:
        evid.append(f"net_flow=null({nf_reason})")
    breadth = None
    if up is not None and dn is not None and (up + dn) > 0:
        breadth = up / (up + dn); parts.append(breadth); evid.append(f"涨跌{int(up)}/{int(dn)}")
    score = float(np.mean(parts)) if parts else None

    cid, csize = _cluster_id_of(pick["code"])
    if cid:
        evid.append(f"簇{cid}(n={csize})")
    link = _sector_linkage_pick(pick["code"])
    if link:
        evid.append(f"外因{link.get('name') or link.get('key')} corr{_f(link.get('corr')):+.2f}")

    degraded = bool(nf_reason) and nf is None
    return {
        "score": round(score, 3) if score is not None else None,
        "detail": "，".join(evid) or "无板块证据",
        "source": "sector_map+sector-ranking+sector-linkage+sector-clusters",
        "degraded": degraded,
        "sector": pick.get("name"), "sector_code": pick["code"],
        "chg": chg, "net_flow": nf, "breadth": breadth,
        "cluster": cid, "linkage": (link or None),
    }


async def _dim_valuation(code: str, sector_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    pe = await _stock_pe(code)
    out: Dict[str, Any] = {
        "score": None, "degraded": True, "source": "tencent_realtime_field39",
        "note": "个股/板块 PE 均为腾讯实时口径（非 PE_TTM，无历史序列）",
        "ep": None, "pe": pe, "vs_sector": None, "pb": None,
        "pb_reason": "基本面源不可用（westock CLI 已废弃）",
        "cashflow_reason": "现金流/资产负债/股本结构不可得（westock CLI 已废弃，/api/finance 全 null）",
    }
    sec_pe = None
    if sector_row is not None:
        sec_pe = _f(sector_row.get("pe"))
    if pe is None or pe <= 0:
        out["detail"] = "个股 PE 不可得，估值维度降级"
        return out
    out["ep"] = round(1.0 / pe, 5)
    if sec_pe is not None and sec_pe > 0:
        ratio = pe / sec_pe
        vs = "便宜" if ratio <= 0.9 else ("偏贵" if ratio >= 1.1 else "接近")
        out["vs_sector"] = vs
        out["sector_pe"] = sec_pe
        out["score"] = {"便宜": 0.70, "接近": 0.50, "偏贵": 0.30}[vs]
        out["detail"] = f"个股PE{pe:.1f} vs 板块PE{sec_pe:.1f} → {vs}（EP={out['ep']:.4f}）"
    else:
        out["vs_sector"] = "不可比"
        out["score"] = 0.50
        out["detail"] = f"个股PE{pe:.1f}（板块PE不可比）（EP={out['ep']:.4f}）"
    return out


async def _dim_events(code: str, name: str, sector_names: List[str]) -> Dict[str, Any]:
    items, source = await _news(60)
    if items is None:
        return {"score": None, "has_event": None, "reason": "新闻源不可达",
                "source": None, "degraded": True, "matched": []}
    tokens = set()
    if name:
        tokens.add(str(name).replace(" ", "").upper())
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) >= 4:
        tokens.add(digits)
    for s in (sector_names or []):
        if s:
            tokens.add(str(s).replace(" ", "").upper())
    matched: List[str] = []
    for it in items:
        title = str((it or {}).get("title") or "")
        tu = title.replace(" ", "").upper()
        if any(tok and tok in tu for tok in tokens):
            matched.append(title)
    # 仅做保守的"是否有相关事件"判定；无情绪源 → 不编造方向
    return {
        "score": 0.50, "has_event": bool(matched),
        "reason": ("命中相关新闻" if matched else "无相关事件"),
        "source": source, "degraded": False,
        "matched": matched[:3],
    }


def _dim_technical(signal_type: str, signal_score: Optional[int],
                   tech_base: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    base = (tech_base or _TECH_BASE).get(signal_type, 0.55)
    if signal_score is not None:
        try:
            base = _clamp(float(signal_score) / 100.0, 0.0, 1.0)
        except (TypeError, ValueError):
            pass
    return {"score": round(base, 3), "signal": signal_type,
            "score_raw": signal_score, "source": "strategy_signal", "degraded": False}


# ============================================================
# 主入口
# ============================================================
async def evaluate(*, code: str, name: str = "", side: str = "buy",
                   signal_type: str = "", signal_score: Optional[int] = None,
                   price: Optional[float] = None,
                   gate_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """四维评分 → allow/confidence/理由。任何异常均降级，绝不抛出。

    gate_cfg：决策闸参数（权重/阈值/波动目标等，见 load_gate_config）；
    None 时使用模块默认（保持向后兼容）。
    """
    cfg = gate_cfg or DEFAULT_GATE_CONFIG
    weights = cfg["weights"]
    allow_threshold = cfg["allow_threshold"]
    reasons: Dict[str, Any] = {}
    vol_pct: Optional[float] = None

    # 板块
    try:
        sector = await _dim_sector(code)
    except Exception:
        logger.warning("decision_gate: sector dim failed", exc_info=True)
        sector = {"score": None, "detail": "板块维度异常", "source": "sector", "degraded": True}
    reasons["sector"] = sector

    # 估值（需要板块行做对比）
    sector_row = None
    if sector.get("sector_code"):
        rows = await _sector_ranking_rows() or []
        sector_row = next((r for r in rows if r.get("code") == sector.get("sector_code")), None)
    try:
        reasons["valuation"] = await _dim_valuation(code, sector_row)
    except Exception:
        logger.warning("decision_gate: valuation dim failed", exc_info=True)
        reasons["valuation"] = {"score": None, "detail": "估值维度异常", "source": "valuation", "degraded": True}

    # 事件
    sector_names = []
    try:
        secs = _stock_sector_rows(code) or []
        sector_names = [s.get("name") for s in secs]
    except Exception:
        pass
    try:
        reasons["events"] = await _dim_events(code, name, sector_names)
    except Exception:
        logger.warning("decision_gate: events dim failed", exc_info=True)
        reasons["events"] = {"score": None, "has_event": None, "reason": "事件维度异常", "degraded": True}

    # 技术
    reasons["technical"] = _dim_technical(signal_type, signal_score, cfg.get("tech_base"))

    # 波动率（供仓位）
    try:
        ks = await _klines(code)
        vol_pct = _volatility_pct(ks)
    except Exception:
        vol_pct = None

    # 聚合（在可得维上重新归一化）
    num = den = 0.0
    for k, w in weights.items():
        s = reasons.get(k, {}).get("score")
        if s is None:
            continue
        num += w * float(s)
        den += w
    confidence = round(num / den, 3) if den > 0 else 0.0

    # 硬否决（买入）
    vetoes: List[str] = []
    if side == "buy":
        sec_score = reasons["sector"].get("score")
        chg = reasons["sector"].get("chg")
        nf = reasons["sector"].get("net_flow")
        breadth = reasons["sector"].get("breadth")
        vs = reasons["valuation"].get("vs_sector")
        if sec_score is not None and chg is not None and chg <= -2.0 and (nf is None or nf < 0):
            vetoes.append(f"板块走弱（{reasons['sector'].get('sector')} chg{chg:+.2f}%）且资金流出")
        if sec_score is not None and breadth is not None and breadth < 0.35:
            vetoes.append("板块内跌多涨少（breadth<0.35）")
        if vs == "偏贵" and (sec_score is None or sec_score < 0.5):
            vetoes.append("相对板块估值偏贵且板块不强")
        if confidence < allow_threshold:
            vetoes.append(f"综合置信度 {confidence:.2f} < 阈值 {allow_threshold}")

    allow = (not vetoes)
    if side == "sell":
        allow = True  # 卖出永远允许，闸只为记录理由
        verdict = "卖出记录（闸不拦截）"
    elif allow:
        verdict = reasons["sector"].get("detail", "") + "；技术共振，决策通过"
    else:
        verdict = "；".join(vetoes)

    # 仓位权重：confidence × 波动率倒数（目标日波动可配，默认 2%）
    if vol_pct is not None and vol_pct > 0:
        vol_factor = _clamp(cfg["target_daily_vol_pct"] / vol_pct,
                            cfg["vol_factor_min"], cfg["vol_factor_max"])
    else:
        vol_factor = 1.0
    size_weight = _clamp(confidence * vol_factor, 0.1, 1.0)

    return {
        "allow": bool(allow),
        "side": side,
        "confidence": confidence,
        "reasons": reasons,
        "verdict_reason": verdict,
        "volatility_pct": round(vol_pct, 3) if vol_pct is not None else None,
        "vol_factor": round(vol_factor, 3),
        "size_weight": round(size_weight, 3),
        "threshold": allow_threshold,
    }


def snapshot_ep(code: str) -> Optional[float]:
    """同步快照 EP（1/实时PE）——供 qlib 因子等外部消费（degraded，非 TTM，无历史）。

    注：当前实现为同步占位（返回 None），真正的取数在 evaluate() 内异步完成；
    qlib 引擎如需序列，应使用异步接口（见 evaluate 的 valuation.reasons）。
    """
    return None
