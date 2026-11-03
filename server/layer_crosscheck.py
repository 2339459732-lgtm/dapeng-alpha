"""跨层对证（layer-crosscheck.v1）。

问题
----
主脑中心分三层：事实（行情/情绪）、判断（调查/轮动）、决策（共识/仓位）。
三层各自算数，但**同一件事**往往各有一份数：

* 事实层「情绪分」80.5；
* 判断层「结构分」100，界面上却也叫「情绪分」；
* 决策层沿用事实层的 80.5。

数字不同未必是错（量纲不同），错的是**没有机制去发现它们不一致**。
本模块只做对证，不改任何一层的算法：

1. **同源项**（情绪分、上涨占比、成交额、涨停家数、领涨板块、口径、时点）
   按容差逐项比对，超出即 `warn` / `conflict`；
2. **异量纲项**（结构分 vs 情绪分）不做数值比对，只校验**方向是否同向**，
   方向冲突才是真冲突；
3. 缺数据记 `unavailable`，绝不把缺失当一致。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from server.brain_config import section as cfg_section
from server.core.timeutil import now_iso

logger = logging.getLogger(__name__)

CROSSCHECK_VERSION = "layer-crosscheck.v1"
LATEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LATEST_PATH = os.path.join(LATEST_DIR, "layer_crosscheck.json")


def _path_for(light: bool) -> str:
    """两档结果分开缓存：页面小条走 light（不跑六专家），闭环走 full。"""
    return os.path.join(LATEST_DIR, "layer_crosscheck_light.json" if light else "layer_crosscheck.json")

_STATUS_WEIGHT = {"pass": 1.0, "warn": 0.5, "conflict": 0.0, "incomparable": 1.0, "unavailable": 0.0}

#: 结构状态 → 方向
_STRUCTURE_TONE = {
    "强势上行": "bullish", "震荡上行": "bullish",
    "区间震荡": "neutral",
    "偏弱调整": "bearish", "弱势下行": "bearish",
    "数据不足": "unknown",
}
#: 情绪档位 → 方向
_BAND_TONE = {
    "过热": "bullish", "偏热": "bullish",
    "中性": "neutral",
    "偏冷": "bearish", "极冷": "bearish",
}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> Optional[float]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1) if (" " in text and "T" not in text) else text
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _check(
    check_id: str,
    name: str,
    layers: Tuple[str, str],
    a: Dict[str, Any],
    b: Dict[str, Any],
    status: str,
    note: str,
    delta: Optional[float] = None,
    tolerance: Optional[float] = None,
    hint: str = "",
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "layers": list(layers),
        "a": a,
        "b": b,
        "delta": None if delta is None else round(delta, 3),
        "tolerance": tolerance,
        "status": status,
        "note": note,
        "hint": hint,
    }


def _numeric(
    check_id: str,
    name: str,
    layers: Tuple[str, str],
    a_val: Any,
    b_val: Any,
    tolerance: float,
    a_meta: Dict[str, Any],
    b_meta: Dict[str, Any],
    warn_factor: float,
    unit: str = "",
) -> Dict[str, Any]:
    a_num, b_num = _num(a_val), _num(b_val)
    if a_num is None or b_num is None:
        return _check(check_id, name, layers, {**a_meta, "value": a_val}, {**b_meta, "value": b_val},
                      "unavailable", "一侧缺值，无法比对", hint="检查该层上游是否降级")
    delta = b_num - a_num
    size = max(abs(a_num), abs(b_num), 1e-9)
    if abs(delta) <= tolerance:
        status, note = "pass", f"差值 {delta:+.3f}{unit}，在容差 ±{tolerance}{unit} 内"
    elif abs(delta) <= tolerance * max(1.0, warn_factor):
        status, note = "warn", f"差值 {delta:+.3f}{unit}，超出容差但在 {warn_factor:g}× 容差内"
    else:
        status, note = "conflict", f"差值 {delta:+.3f}{unit}（相对 {abs(delta) / size * 100:.2f}%），超出容差 {warn_factor:g}×"
    return _check(check_id, name, layers, {**a_meta, "value": round(a_num, 3)},
                  {**b_meta, "value": round(b_num, 3)}, status, note,
                  delta=delta, tolerance=tolerance,
                  hint="" if status == "pass" else "两层应对同一批报价聚合；检查缓存时点与样本口径")


def _relative(
    check_id: str,
    name: str,
    layers: Tuple[str, str],
    a_val: Any,
    b_val: Any,
    tolerance_pct: float,
    a_meta: Dict[str, Any],
    b_meta: Dict[str, Any],
    warn_factor: float,
) -> Dict[str, Any]:
    a_num, b_num = _num(a_val), _num(b_val)
    if not a_num or b_num is None:
        return _check(check_id, name, layers, {**a_meta, "value": a_val}, {**b_meta, "value": b_val},
                      "unavailable", "一侧缺值或为 0，无法做相对比对")
    pct = abs(b_num - a_num) / abs(a_num) * 100.0
    delta = b_num - a_num
    if pct <= tolerance_pct:
        status, note = "pass", f"相对偏差 {pct:.3f}%，在容差 {tolerance_pct}% 内"
    elif pct <= tolerance_pct * max(1.0, warn_factor):
        status, note = "warn", f"相对偏差 {pct:.3f}%，超出容差但在 {warn_factor:g}× 内"
    else:
        status, note = "conflict", f"相对偏差 {pct:.3f}%，超出容差 {warn_factor:g}×"
    return _check(check_id, name, layers, {**a_meta, "value": round(a_num, 2)},
                  {**b_meta, "value": round(b_num, 2)}, status, note,
                  delta=delta, tolerance=tolerance_pct,
                  hint="" if status == "pass" else "成交额易受盘中增量影响；若时点偏差大属正常，需同时看 freshness")


def build_crosscheck(
    fact: Dict[str, Any],
    survey: Dict[str, Any],
    decision: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """纯函数：对三层快照做逐项对证。"""
    cfg = cfg_section("crosscheck")
    num_tol = float(cfg.get("number_tolerance", 0.5))
    ratio_tol = float(cfg.get("ratio_tolerance", 0.5))
    amt_tol = float(cfg.get("amount_tolerance_pct", 0.5))
    cnt_tol = float(cfg.get("count_tolerance", 0))
    top_n = int(cfg.get("sector_top_n", 3))
    max_skew = float(cfg.get("max_as_of_skew_s", 300))
    warn_factor = float(cfg.get("warn_factor", 2.0))
    min_ratio = float(cfg.get("min_comparable_ratio", 0.5))

    decision = decision or {}
    checks: List[Dict[str, Any]] = []

    a_meta = {"layer": "fact", "field": "sentiment.score"}
    b_meta = {"layer": "survey", "field": "sentiment.level"}
    checks.append(_numeric("sentiment_fact_survey", "情绪分（事实 ↔ 判断）", ("fact", "survey"),
                           fact.get("sentiment_score"), survey.get("sentiment_level"),
                           num_tol, a_meta, b_meta, warn_factor, " 分"))

    if decision and decision.get("sentiment_score") is not None:
        checks.append(_numeric("sentiment_fact_decision", "情绪分（事实 ↔ 决策）", ("fact", "decision"),
                               fact.get("sentiment_score"), decision.get("sentiment_score"),
                               num_tol, {"layer": "fact", "field": "sentiment.score"},
                               {"layer": "decision", "field": "experts.sentiment.score"},
                               warn_factor, " 分"))

    # 口径不同（全市场 vs 核心样本）时，宽度类数值本就不可比，
    # 应先判「不可比」并阻塞结论，而不是报三个假冲突。
    f_scope, s_scope = fact.get("data_scope"), survey.get("data_scope")
    scope_mismatch = bool(f_scope and s_scope and f_scope != s_scope)

    checks.append(_numeric("breadth_up_ratio", "上涨占比（事实 ↔ 判断）", ("fact", "survey"),
                           fact.get("up_ratio"), survey.get("up_ratio"),
                           ratio_tol, {"layer": "fact", "field": "breadth.up_ratio"},
                           {"layer": "survey", "field": "ashare.up_ratio"},
                           warn_factor, "%"))

    checks.append(_relative("turnover", "成交额（事实 ↔ 判断）", ("fact", "survey"),
                            fact.get("amount"), survey.get("amount"), amt_tol,
                            {"layer": "fact", "field": "turnover.amount"},
                            {"layer": "survey", "field": "ashare.amount"}, warn_factor))

    checks.append(_numeric("limit_up", "涨停家数（事实 ↔ 判断）", ("fact", "survey"),
                           fact.get("limit_up"), survey.get("limit_up"),
                           cnt_tol, {"layer": "fact", "field": "breadth.limit_up"},
                           {"layer": "survey", "field": "ashare.limit_up"}, warn_factor, " 家"))

    if scope_mismatch:
        note = (f"口径不同（{f_scope} vs {s_scope}），样本范围不同，数值不具可比性")
        hint = "等全市场覆盖就绪后重算；核心样本只用于降级展示，不应参与跨层对证"
        for item in checks:
            if item["id"] in ("breadth_up_ratio", "turnover", "limit_up", "sector_lead"):
                item["status"] = "incomparable"
                item["note"] = note
                item["hint"] = hint

    # 领涨板块：先看两层用的是不是同一套分类；不同体系名称必然对不上，
    # 那是「不可比」而不是「冲突」（申万行业 vs 东财板块）。
    taxonomy_labels = cfg.get("sector_taxonomy", {}).get("labels", {})
    default_tax = cfg.get("sector_taxonomy", {}).get("default", "em")
    fa = [s for s in (fact.get("top_sectors") or []) if s]
    sa = [s for s in (survey.get("top_sectors") or []) if s]
    fact_tax = fact.get("sector_taxonomy") or default_tax
    survey_tax = survey.get("sector_taxonomy") or default_tax
    if fact_tax != survey_tax:
        checks.append(_check(
            "sector_lead", "领涨板块（事实 ↔ 判断）", ("fact", "survey"),
            {"layer": "fact", "value": fa[:top_n], "taxonomy": fact_tax},
            {"layer": "survey", "value": sa[:top_n], "taxonomy": survey_tax},
            "incomparable",
            f"分类体系不同（{taxonomy_labels.get(fact_tax, fact_tax)} vs "
            f"{taxonomy_labels.get(survey_tax, survey_tax)}），名称不具可比性",
            hint="需要板块级对证时，两层应统一到同一分类体系",
        ))
    elif not fa or not sa:
        checks.append(_check("sector_lead", "领涨板块（事实 ↔ 判断）", ("fact", "survey"),
                             {"layer": "fact", "value": fa[:top_n]}, {"layer": "survey", "value": sa[:top_n]},
                             "unavailable", "一侧没有板块数据，无法比对"))
    else:
        overlap = set(fa[:top_n]) & set(sa[:top_n])
        if overlap:
            checks.append(_check("sector_lead", "领涨板块（事实 ↔ 判断）", ("fact", "survey"),
                                 {"layer": "fact", "value": fa[:top_n]}, {"layer": "survey", "value": sa[:top_n]},
                                 "pass", f"前 {top_n} 名重合 {len(overlap)} 个：{'、'.join(sorted(overlap))}"))
        else:
            checks.append(_check("sector_lead", "领涨板块（事实 ↔ 判断）", ("fact", "survey"),
                                 {"layer": "fact", "value": fa[:top_n]}, {"layer": "survey", "value": sa[:top_n]},
                                 "conflict", "前 {n} 名没有任何重合，两层对主线的判断不一致".replace("{n}", str(top_n)),
                                 hint="检查板块聚合口径（东财板块 vs 行业分类）与排序字段"))

    # 口径
    fs, ss = fact.get("data_scope"), survey.get("data_scope")
    if not fs or not ss:
        checks.append(_check("scope", "数据口径（事实 ↔ 判断）", ("fact", "survey"),
                             {"layer": "fact", "value": fs}, {"layer": "survey", "value": ss},
                             "unavailable", "一侧未标注口径"))
    elif fs == ss:
        checks.append(_check("scope", "数据口径（事实 ↔ 判断）", ("fact", "survey"),
                             {"layer": "fact", "value": fs}, {"layer": "survey", "value": ss},
                             "pass", f"两层同为 {fs}"))
    else:
        checks.append(_check("scope", "数据口径（事实 ↔ 判断）", ("fact", "survey"),
                             {"layer": "fact", "value": fs}, {"layer": "survey", "value": ss},
                             "warn", "口径不同，数值不可直接比较（全市场 vs 核心样本）",
                             hint="全市场缓存未就绪时回落核心样本，属已知降级，不应视为错误"))

    # 时点
    stamps = {
        "fact": _ts(fact.get("as_of")),
        "survey": _ts(survey.get("as_of")),
        "decision": _ts(decision.get("as_of")),
    }
    known = {k: v for k, v in stamps.items() if v is not None}
    if len(known) < 2:
        checks.append(_check("freshness", "数据时点偏差（三层）", ("fact", "survey"),
                             {"layer": "fact", "value": fact.get("as_of")},
                             {"layer": "survey", "value": survey.get("as_of")},
                             "unavailable", "至少两层缺时点"))
    else:
        skew = max(known.values()) - min(known.values())
        oldest = min(known, key=known.get)
        newest = max(known, key=known.get)
        if skew <= max_skew:
            status, note = "pass", f"最大偏差 {skew:.0f}s（{oldest} → {newest}），在 {max_skew:.0f}s 内"
        elif skew <= max_skew * warn_factor:
            status, note = "warn", f"最大偏差 {skew:.0f}s，超出 {max_skew:.0f}s 但在 {warn_factor:g}× 内"
        else:
            status, note = "conflict", f"最大偏差 {skew:.0f}s，三层不是同一时点的市场"
        checks.append(_check("freshness", "数据时点偏差（三层）", ("fact", "survey"),
                             {"layer": "fact", "value": fact.get("as_of")},
                             {"layer": "survey", "value": survey.get("as_of")},
                             status, note, delta=skew, tolerance=max_skew,
                             hint="" if status == "pass" else "缩短缓存或强制刷新；跨时点结论不可用于同一笔决策"))

    # 异量纲：结构分 vs 情绪分，只校验方向
    structure_score = survey.get("structure_score")
    structure_tone = _STRUCTURE_TONE.get(str(survey.get("structure_status") or ""), "unknown")
    band_tone = _BAND_TONE.get(str(fact.get("band") or ""), "unknown")
    if structure_score is None or structure_tone == "unknown" or band_tone == "unknown":
        checks.append(_check("score_axes", "结构分 ↔ 情绪分（方向一致性）", ("survey", "fact"),
                             {"layer": "survey", "value": structure_score},
                             {"layer": "fact", "value": fact.get("sentiment_score")},
                             "unavailable", "结构分或情绪档位缺失，无法做方向校验"))
    else:
        same = structure_tone == band_tone or "neutral" in (structure_tone, band_tone)
        checks.append(_check(
            "score_axes", "结构分 ↔ 情绪分（方向一致性）", ("survey", "fact"),
            {"layer": "survey", "value": structure_score, "tone": structure_tone,
             "field": "regime.score"},
            {"layer": "fact", "value": fact.get("sentiment_score"), "tone": band_tone,
             "field": "sentiment.score"},
            "incomparable" if same else "conflict",
            (f"量纲不同不做数值比对；方向 {structure_tone} vs {band_tone} 同向/中性 → 通过"
             if same else f"方向冲突：结构 {structure_tone} vs 情绪 {band_tone}"),
            hint="" if same else "结构偏强但情绪转弱（或反之），应下调仓位并复核驱动维度",
        ))

    counts = {key: 0 for key in _STATUS_WEIGHT}
    for item in checks:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    # 只在「可比对」的项上评分：缺数据不应被当成不一致（否则降级会伪装成冲突）
    comparable = [c for c in checks if c["status"] != "unavailable"]
    score = (sum(_STATUS_WEIGHT.get(item["status"], 0.0) for item in comparable) / len(comparable)
             if comparable else 0.0)
    comparable_ratio = len(comparable) / max(1, len(checks))
    min_ratio = float(cfg.get("min_comparable_ratio", 0.5))
    conflicts = [item for item in checks if item["status"] == "conflict"]

    blockers = ["scope_mismatch"] if scope_mismatch else []
    return {
        "ok": (not conflicts) and comparable_ratio >= min_ratio and not blockers,
        "version": CROSSCHECK_VERSION,
        "generated_at": now_iso() if now is None else datetime.fromtimestamp(now).isoformat(),
        "counts": counts,
        "total": len(checks),
        "comparable": len(comparable),
        "comparable_ratio": round(comparable_ratio, 3),
        "score": round(score, 3),
        "conflicts": [{"id": c["id"], "name": c["name"], "note": c["note"]} for c in conflicts],
        "blockers": blockers,
        "checks": checks,
        "config": {
            "number_tolerance": num_tol, "ratio_tolerance": ratio_tol,
            "amount_tolerance_pct": amt_tol, "count_tolerance": cnt_tol,
            "sector_top_n": top_n, "max_as_of_skew_s": max_skew, "warn_factor": warn_factor,
            "min_comparable_ratio": min_ratio,
        },
    }


# ----------------------------------------------------------------------
#  取数与缓存
# ----------------------------------------------------------------------
def _fact_view(sentiment: Dict[str, Any], dashboard: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    inputs = sentiment.get("inputs") or {}
    up, down, flat = _num(inputs.get("up")), _num(inputs.get("down")), _num(inputs.get("flat"))
    up_ratio = None
    if up is not None and down is not None:
        total = up + down + (flat or 0.0)
        up_ratio = round(100.0 * up / total, 2) if total > 0 else None
    sectors = ((dashboard or {}).get("sectors") or [])
    sector_taxonomy = "em" if sectors else None
    if not sectors:
        # 实时大盘不可用时退到「最近一日行业快照」，但那是申万行业，
        # 与判断层的东财板块不是同一套分类 —— 必须标注，否则会误判为冲突。
        try:
            import json as _json
            import os as _os

            path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                 "data", "sector_history.json")
            if _os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    days = (_json.load(fh) or {}).get("days") or {}
                if days:
                    latest = days[sorted(days)[-1]].get("sectors") or {}
                    sectors = [{"name": k, "avg_chg": (v or {}).get("avg_chg")} for k, v in latest.items()]
                    sector_taxonomy = "sw"
        except Exception as exc:
            logger.debug("crosscheck sector history unavailable: %s", exc)
    ranked = sorted(
        [s for s in sectors if _num(s.get("avg_chg")) is not None],
        key=lambda s: _num(s.get("avg_chg")) or 0.0, reverse=True,
    )
    return {
        "sentiment_score": _num(sentiment.get("score")),
        "band": (sentiment.get("band") or {}).get("label") if isinstance(sentiment.get("band"), dict) else sentiment.get("status"),
        "data_scope": sentiment.get("data_scope"),
        "up_ratio": up_ratio,
        "amount": _num(inputs.get("amount")),
        "limit_up": _num(inputs.get("limit_up")),
        "top_sectors": [str(s.get("name")) for s in ranked[:5]],
        "sector_taxonomy": sector_taxonomy,
        "as_of": sentiment.get("as_of"),
        "coverage": _num(sentiment.get("coverage")),
    }


def _survey_view(survey: Dict[str, Any]) -> Dict[str, Any]:
    regime = survey.get("regime") or {}
    ashare = ((survey.get("data_coverage") or {}).get("ashare") or {})
    facts = survey.get("facts") or {}
    # facts 是判断层「实际用到的输入」，优先于 coverage 汇总（降级时后者可能为空）
    src = {**ashare, **{k: v for k, v in facts.items() if v is not None}}
    rotation = survey.get("rotation") or []
    return {
        "structure_score": _num(regime.get("score")),
        "structure_status": regime.get("status"),
        "sentiment_level": _num((survey.get("sentiment") or {}).get("level")),
        "up_ratio": _num(src.get("up_ratio")),
        "amount": _num(src.get("amount")),
        "limit_up": _num(src.get("limit_up")),
        "top_sectors": [str(r.get("sector")) for r in rotation[:5] if r.get("sector")],
        "data_scope": survey.get("data_scope"),
        "as_of": survey.get("update_time"),
    }


def _decision_view(experts: Dict[str, Any]) -> Dict[str, Any]:
    if not experts:
        return {}
    return {
        "sentiment_score": _num((experts.get("sentiment") or {}).get("score")),
        "consensus": (experts.get("consensus") or {}).get("score"),
        "stance": (experts.get("consensus") or {}).get("stance"),
        "as_of": experts.get("as_of"),
    }


async def collect(light: bool = False, force: bool = False) -> Dict[str, Any]:
    """取三层快照并做对证。``light=True`` 时跳过六专家（只比事实 ↔ 判断）。"""
    from server.decision_snapshot import get_sentiment_snapshot
    from server.market_survey_engine import market_survey_engine
    from server.services.market_dashboard import get_dashboard

    sentiment = await get_sentiment_snapshot()
    survey = await market_survey_engine.get_market_survey(force=force)
    dashboard = None
    try:
        dashboard = await get_dashboard()
    except Exception as exc:
        logger.debug("crosscheck dashboard unavailable: %s", exc)
    # 盘中/盘后切换时实时大盘可能短暂为空：退到最近一次成功快照，
    # 否则「领涨板块」这类对证会永久处于 unavailable，形同没做。
    if not (dashboard or {}).get("sectors"):
        try:
            from server.services.market_dashboard import last_good

            dashboard = last_good() or dashboard
        except Exception as exc:
            logger.debug("crosscheck last_good unavailable: %s", exc)

    experts: Dict[str, Any] = {}
    if not light:
        try:
            from server.decision_experts import run_expert_panel

            experts = await run_expert_panel()
        except Exception as exc:
            logger.warning("crosscheck experts unavailable: %s", exc)

    fact = _fact_view(sentiment or {}, dashboard)
    survey_view = _survey_view(survey or {})
    decision_view = _decision_view(experts or {})
    result = build_crosscheck(fact, survey_view, decision_view)
    result["inputs"] = {
        "fact": {"as_of": fact.get("as_of"), "scope": fact.get("data_scope"),
                 "coverage": fact.get("coverage")},
        "survey": {"as_of": survey_view.get("as_of"), "scope": survey_view.get("data_scope")},
        "decision": {"as_of": decision_view.get("as_of"), "stance": decision_view.get("stance")},
        "light": light,
    }
    return result


def write_latest(result: Dict[str, Any], light: Optional[bool] = None) -> None:
    if light is None:
        light = bool((result.get("inputs") or {}).get("light"))
    path = _path_for(light)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".crosscheck-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as exc:
        logger.warning("crosscheck write failed: %s", exc)


def read_latest(light: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    if light is None:
        light = False
    path = _path_for(light)
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("crosscheck read failed: %s", exc)
        return None


def _cache_ttl() -> float:
    return float(cfg_section("crosscheck").get("cache_ttl_s", 60))


async def run(light: bool = False, force: bool = False) -> Dict[str, Any]:
    """带缓存的对证入口：命中文件缓存则直接返回，避免每次请求都拉三层。"""
    if not force:
        latest = read_latest(light=light)
        if latest:
            age = time.time() - (_ts(latest.get("generated_at")) or 0)
            if 0 <= age <= _cache_ttl():
                latest["cached"] = True
                return latest
    result = await collect(light=light, force=force)
    write_latest(result, light=light)
    return result


__all__ = [
    "CROSSCHECK_VERSION",
    "LATEST_PATH",
    "build_crosscheck",
    "collect",
    "run",
    "read_latest",
    "write_latest",
]
