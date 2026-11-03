"""共享基座与 E1–E3：工具函数、趋势、轮动、资金专家"""

from __future__ import annotations
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from server.core.timeutil import now_iso as _now_iso
from server.core.numbers import clamp
from server.infra.kv import kv_get, kv_set

logger = logging.getLogger(__name__)

EXPERT_VERSION = "brain-panel.v3"
_KV_LATEST = "decision_experts_latest"
_KV_HISTORY = "decision_experts_history"
EXPERT_LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "expert_ledger.json"
)
_HISTORY_KEEP = 20
_CACHE_SECONDS = 300


def _fin(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _evidence(metric: str, value: Any, unit: str, source: str, as_of: Any, sample: Any = None) -> Dict[str, Any]:
    return {
        "metric": metric,
        "value": value,
        "unit": unit,
        "source": source,
        "as_of": as_of,
        "sample": sample,
    }


def _opinion(
    expert_id: str,
    name: str,
    stance: str,
    horizon: str,
    confidence: float,
    thesis: str,
    evidence: List[Dict[str, Any]],
    counter: List[str],
    invalidation: str,
    position_cap: int,
    veto: bool = False,
    veto_reason: str = "",
    abstain: bool = False,
    questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "expert_id": expert_id,
        "name": name,
        "stance": stance,
        "horizon": horizon,
        "confidence": round(clamp(confidence, 0.0, 1.0), 2),
        "thesis": thesis[:300],
        "evidence": evidence,
        "counter_evidence": counter,
        "invalidation": invalidation,
        "position_cap": int(position_cap),
        "veto": bool(veto),
        "veto_reason": veto_reason,
        "abstain": bool(abstain),
        "questions": questions or [],
        "method": "rule_v1",
    }


def _breadth_ratio(breadth: Dict[str, Any]) -> Optional[float]:
    up, down = _fin(breadth.get("up")), _fin(breadth.get("down"))
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def _trend_expert(packet: Dict[str, Any]) -> Dict[str, Any]:
    market = packet.get("market") or {}
    indices = market.get("indices") or []
    breadth = market.get("breadth") or {}
    quality = packet.get("quality") or {}
    changes: List[float] = []
    index_names: List[str] = []
    for item in indices:
        price = _fin(item.get("price"))
        chg = _fin(item.get("chg"))
        if price is not None and price > 0 and chg is not None:
            changes.append(chg)
            index_names.append(str(item.get("name") or item.get("code") or ""))
    up_ratio = _breadth_ratio(breadth)
    if not changes and up_ratio is None:
        return _opinion("E1", "趋势与结构专家", "neutral", "1-3d", 0.15,
                        "有效指数与涨跌宽度均缺失，无法判断趋势结构。",
                        [], ["缺少可验证的趋势输入"], "补齐指数与宽度后重新评估", 0,
                        abstain=True)
    avg_chg = sum(changes) / len(changes) if changes else None
    breadth_pct = (up_ratio * 100) if up_ratio is not None else None
    if avg_chg is not None and avg_chg >= 0.4 and (breadth_pct is None or breadth_pct >= 55):
        stance, cap = "attack", 65
    elif (avg_chg is not None and avg_chg <= -0.4) or (breadth_pct is not None and breadth_pct < 35):
        stance, cap = "defend", 25
    else:
        stance, cap = "neutral", 45
    conf = 0.48 + 0.18 * (1 if changes else 0) + 0.14 * (1 if breadth_pct is not None else 0) + 0.12 * (_fin(quality.get("score")) or 0.0)
    parts = []
    if avg_chg is not None:
        parts.append(f"{len(changes)} 个有效指数平均 {avg_chg:+.2f}%")
    if breadth_pct is not None:
        parts.append(f"上涨占比 {breadth_pct:.1f}%")
    thesis = "；".join(parts) + ("，趋势偏强。" if stance == "attack" else "，趋势偏弱。" if stance == "defend" else "，方向未形成一致。")
    ev = []
    if avg_chg is not None:
        ev.append(_evidence("avg_index_chg", round(avg_chg, 2), "%", packet.get("source") or "market-dashboard", packet.get("as_of"), len(changes)))
    if breadth_pct is not None:
        ev.append(_evidence("breadth_up_ratio", round(breadth_pct, 1), "%", "market-dashboard", packet.get("as_of"), int(_fin(breadth.get("up")) or 0) + int(_fin(breadth.get("down")) or 0)))
    # 全市场横截面结构（market-scanner.v1）
    scan = packet.get("scan") or {}
    cross = (scan.get("cross_section") or {}) if isinstance(scan, dict) else {}
    if cross.get("available"):
        ev.append(_evidence("cross_strength_score", cross.get("strength_score"), "分", "market-scanner.cross_section(全市场)", packet.get("as_of"), cross.get("sample")))
        if _fin(cross.get("median_chg")) is not None:
            ev.append(_evidence("full_market_median_chg", cross.get("median_chg"), "%", "market-scanner.cross_section", packet.get("as_of"), cross.get("sample")))
        parts.append(f"全市场 {cross.get('sample')} 只中位 {cross.get('median_chg'):+.2f}%（强弱分 {cross.get('strength_score')}）")
    return _opinion("E1", "趋势与结构专家", stance, "1-3d", conf, thesis, ev,
                    ["指数间可能分化，均值会掩盖强弱差"],
                    "上证与创业板同时转负或上涨占比跌破 35% 时撤销进攻判断",
                    cap)


def _rotation_expert(packet: Dict[str, Any]) -> Dict[str, Any]:
    market = packet.get("market") or {}
    scan = packet.get("scan") or {}
    rotation = (scan.get("rotation") or {}) if isinstance(scan, dict) else {}
    industries = rotation.get("industries") or []
    quality = packet.get("quality") or {}

    # ---- 首选：全市场口径（东财 BOARD_NAME 聚合，~5700 只）----
    if rotation.get("available") and len(industries) >= 10:
        top3 = industries[:3]
        top_avg = sum(_fin(x.get("avg_chg")) or 0 for x in top3) / len(top3)
        positive_share = sum(1 for x in industries if (_fin(x.get("avg_chg")) or 0) > 0) / len(industries)
        signal = rotation.get("signal") or "baseline"
        signal_label = rotation.get("signal_label") or ""
        top_share = sum(_fin(x.get("amount_share")) or 0 for x in top3)
        risers = [x for x in industries if (_fin(x.get("rank_change")) or 0) >= 5]
        history_days = int(rotation.get("history_days") or 1)

        if top_avg >= 1.0 and positive_share >= 0.55 and signal in ("continuity", "rotation"):
            stance, cap = "attack", 65
        elif top_avg <= 0 or positive_share < 0.30 or signal == "churn":
            stance, cap = "defend" if (top_avg <= 0 or positive_share < 0.30) else "neutral", 25 if top_avg <= 0 else 40
        else:
            stance, cap = "neutral", 45
        # 快速轮动且无延续主线时压仓位
        if signal == "churn" and stance == "attack":
            cap = min(cap, 45)
        # 主线成交额占比过高（拥挤）
        crowded = top_share >= 30.0

        conf = (0.45 + 0.18 * min(1.0, len(industries) / 20)
                + 0.12 * min(1.0, history_days / 20)
                + 0.12 * (_fin(quality.get("score")) or 0.0))
        if signal == "churn":
            conf -= 0.08
        if crowded:
            conf = min(conf, 0.65)

        top_names = "、".join(str(x.get("name") or "") for x in top3)
        thesis = (
            f"全市场 {len(industries)} 个行业中，{top_names} 领涨（平均 {top_avg:+.2f}%），"
            f"{positive_share * 100:.0f}% 行业上涨，{signal_label}（历史 {history_days} 日）"
        )
        thesis += "，主线扩散。" if stance == "attack" else "，轮动偏弱。" if stance == "defend" else "，轮动分化。"
        if risers:
            thesis += f"排名跃升行业：{'、'.join(str(x['name']) for x in risers[:3])}。"

        ev = [
            _evidence("top3_avg_chg", round(top_avg, 2), "%", "market-scanner.rotation(全市场行业)", packet.get("as_of"), len(industries)),
            _evidence("positive_industry_share", round(positive_share * 100, 1), "%", "market-scanner.rotation", packet.get("as_of"), len(industries)),
            _evidence("rotation_signal", signal, "", "market-scanner.rotation", packet.get("as_of"), history_days),
        ]
        if top_share:
            ev.append(_evidence("top3_amount_share", round(top_share, 1), "%", "market-scanner.rotation", packet.get("as_of")))
        if risers:
            ev.append(_evidence("rank_riser_count", len(risers), "个", "market-scanner.rotation(排名+5以上)", packet.get("as_of")))
        return _opinion("E2", "板块轮动专家", stance, "1-3d", conf, thesis, ev,
                        (["前 3 行业成交额占比 ≥ 30%，主线拥挤，扩散不足"] if crowded else [])
                        + (["轮动动量窗口未满 20 日，mom5/mom20 不可用"] if history_days < 20 else []),
                        "前三行业平均转负、上涨行业占比跌破 30% 或轮动信号转为快速轮动时撤销进攻判断",
                        min(cap, 45) if crowded else cap,
                        questions=["主线内部是否出现龙头断层？"] if stance == "attack" else [])

    # ---- 降级：核心样本板块（原有逻辑）----
    sectors = [item for item in (market.get("sectors") or []) if _fin(item.get("avg_chg")) is not None]
    if not sectors:
        return _opinion("E2", "板块轮动专家", "neutral", "1-3d", 0.15,
                        "板块样本不可用，无法判断轮动阶段。", [], ["缺少板块数据"],
                        "补齐板块排行后重新评估", 0, abstain=True)
    ordered = sorted(sectors, key=lambda item: _fin(item.get("avg_chg")) or 0, reverse=True)
    top = ordered[:3]
    positive_share = sum(1 for item in sectors if (_fin(item.get("avg_chg")) or 0) > 0) / len(sectors)
    top_avg = sum(_fin(item.get("avg_chg")) or 0 for item in top) / len(top)
    top_up = sum(_fin(item.get("up_ratio")) or 0 for item in top) / len(top)
    if top_avg >= 1.5 and positive_share >= 0.60:
        stance, cap = "attack", 65
    elif top_avg <= 0 or positive_share < 0.30:
        stance, cap = "defend", 25
    else:
        stance, cap = "neutral", 45
    amounts = sorted([_fin(item.get("amount")) or 0 for item in sectors], reverse=True)
    total = sum(amounts)
    concentration = (sum(amounts[:3]) / total) if total > 0 else None
    crowded = concentration is not None and concentration >= 0.60
    conf = 0.45 + 0.18 * min(1.0, len(sectors) / 10) + 0.15 * (1 if top_up >= 60 else 0) + 0.10 * (_fin(quality.get("score")) or 0.0)
    if crowded:
        conf = min(conf, 0.65)
    top_names = "、".join(str(item.get("name") or "") for item in top)
    thesis = f"前三方向 {top_names} 平均 {top_avg:+.2f}%，全样本上涨板块占比 {positive_share * 100:.0f}%"
    thesis += "，主线扩散。" if stance == "attack" else "，轮动偏弱。" if stance == "defend" else "，轮动分化。"
    ev = [
        _evidence("top3_avg_chg", round(top_avg, 2), "%", "themes/sector ranking", packet.get("as_of"), len(top)),
        _evidence("positive_sector_share", round(positive_share * 100, 1), "%", "market-dashboard.sectors", packet.get("as_of"), len(sectors)),
    ]
    if concentration is not None:
        ev.append(_evidence("top3_amount_share", round(concentration * 100, 1), "%", "market-dashboard.sectors", packet.get("as_of")))
    return _opinion("E2", "板块轮动专家", stance, "1-3d", conf, thesis, ev,
                    (["前 3 板块成交额高度集中，主线拥挤，扩散不足"] if crowded else ["板块均值可能被少数大市值样本拉动"]),
                    "前三方向平均转负或上涨板块占比跌破 30% 时撤销进攻判断",
                    min(cap, 45) if crowded else cap,
                    questions=["主线内部是否出现龙头断层？"] if stance == "attack" else [])


def _funding_expert(packet: Dict[str, Any]) -> Dict[str, Any]:
    market = packet.get("market") or {}
    flow = market.get("capital_flow") or {}
    turnover = market.get("turnover") or {}
    north_raw = packet.get("north") or {}
    north = north_raw.get("data") if isinstance(north_raw.get("data"), dict) else north_raw
    amount = _fin(turnover.get("amount"))
    up = _fin(north.get("hgt_up")) if isinstance(north, dict) else None
    down = _fin(north.get("hgt_down")) if isinstance(north, dict) else None
    net = None
    if flow.get("available"):
        a, b = _fin(flow.get("flow_a")), _fin(flow.get("flow_b"))
        if a is not None or b is not None:
            net = (a or 0) + (b or 0)
    if amount is None and net is None and (up is None or down is None):
        return _opinion("E3", "资金与流动性专家", "neutral", "intraday", 0.18,
                        "成交额、资金流与北向结构均缺失，不能判断承接质量。",
                        [], ["缺少资金与流动性输入"], "补齐资金面后重新评估", 0,
                        veto=True, veto_reason="流动性数据缺失，禁止输出可执行仓位", abstain=True)
    north_ratio = (up / (up + down)) if up is not None and down is not None and up + down > 0 else None

    # ---- 全市场资金流向（market-scanner.v1：东财主力净流入实测 / 注意力代理） ----
    scan = packet.get("scan") or {}
    money_flow = (scan.get("money_flow") or {}) if isinstance(scan, dict) else {}
    mf_mode = money_flow.get("mode")
    mf_net = _fin(money_flow.get("net_total_yi")) if mf_mode == "eastmoney" else None
    mf_attention = _fin(money_flow.get("net_attention")) if mf_mode == "proxy" else None
    mf_top_in = (money_flow.get("top_inflow") or [{}])[0] if mf_mode == "eastmoney" else None

    positive_signals = sum([
        1 if net is not None and net > 0 else 0,
        1 if north_ratio is not None and north_ratio > 0.55 else 0,
        1 if mf_net is not None and mf_net > 30 else 0,
        1 if mf_attention is not None and mf_attention > 0.5 else 0,
    ])
    negative_signals = sum([
        1 if net is not None and net < 0 else 0,
        1 if north_ratio is not None and north_ratio < 0.45 else 0,
        1 if mf_net is not None and mf_net < -30 else 0,
        1 if mf_attention is not None and mf_attention < -0.5 else 0,
    ])
    if positive_signals > negative_signals and positive_signals >= 2:
        stance, cap = "attack", 60
    elif negative_signals > positive_signals and negative_signals >= 2:
        stance, cap = "defend", 25
    else:
        stance, cap = "neutral", 40
    veto = amount is None or amount <= 0
    conf = (0.42 + 0.16 * (1 if net is not None else 0) + 0.15 * (1 if north_ratio is not None else 0)
            + 0.14 * (1 if mf_net is not None else 0) + 0.08 * (1 if mf_attention is not None else 0)
            + 0.12 * (_fin((packet.get("quality") or {}).get("score")) or 0.0))
    if veto:
        conf = min(conf, 0.35)
    parts = []
    if mf_net is not None:
        parts.append(f"全行业主力净流入合计 {mf_net:+.1f} 亿元（东财实测）")
        if mf_top_in:
            parts.append(f"净流入首位 {mf_top_in.get('name')} {(_fin(mf_top_in.get('main_net_yi')) or 0):+.1f} 亿")
    elif mf_attention is not None:
        parts.append(f"资金注意力净迁移 {mf_attention:+.2f}%（代理口径）")
    if net is not None:
        parts.append(f"指数资金流原始分类合计 {net / 1e8:+.1f} 亿元")
    if north_ratio is not None:
        parts.append(f"沪股通样本上涨占比 {north_ratio * 100:.0f}%")
    if amount is not None:
        parts.append(f"样本成交 {amount / 1e8:.0f} 亿元")
    thesis = "；".join(parts) + "。"
    thesis += "量价承接尚可。" if stance == "attack" else "资金承接偏弱。" if stance == "defend" else "资金方向未确认。"
    ev = []
    if mf_net is not None:
        ev.append(_evidence("industry_main_net_total", round(mf_net, 2), "亿元", "market-scanner.money_flow(eastmoney)", money_flow.get("as_of") or packet.get("as_of"), len(money_flow.get("industries") or [])))
        if mf_top_in:
            ev.append(_evidence("top_inflow_industry", str(mf_top_in.get("name")), "", "market-scanner.money_flow", money_flow.get("as_of")))
    elif mf_attention is not None:
        ev.append(_evidence("net_attention_shift", round(mf_attention, 3), "%(占比迁移)", "market-scanner.money_flow(proxy)", money_flow.get("as_of")))
    if net is not None:
        ev.append(_evidence("capital_flow_raw_sum", round(net / 1e8, 2), "亿元", "eastmoney fflow", packet.get("as_of")))
    if amount is not None:
        ev.append(_evidence("sample_amount", round(amount / 1e8, 1), "亿元", "market-dashboard", packet.get("as_of")))
    if north_ratio is not None:
        ev.append(_evidence("north_up_ratio", round(north_ratio * 100, 1), "%", "market/north", north.get("update_time") if isinstance(north, dict) else None))
    counter_notes = ["北向净买额已停止披露，只能用成分股涨跌结构代理"]
    if mf_mode == "proxy":
        counter_notes.append("主力资金通道被频控，注意力代理非实测净流入，证据权重已下调")
    return _opinion("E3", "资金与流动性专家", stance, "intraday", conf, thesis, ev,
                    counter_notes,
                    "样本成交额显著萎缩或资金流与北向结构同时转负时撤销进攻判断",
                    25 if veto else cap,
                    veto=veto,
                    veto_reason="成交额不可用，无法确认流动性承接" if veto else "")
