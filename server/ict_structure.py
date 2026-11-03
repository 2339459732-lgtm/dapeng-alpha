# -*- coding: utf-8 -*-
"""ICT 结构级统计 —— ``GET /api/ict/structure-stats`` 的纯计算层。

为什么要有这一层：``/api/ict/history`` 证明了「六道门全过的 confirmed」在 A 股 321 个
交易日里出现 0 次，拿它当反馈指标永远没有样本。于是把统计对象下沉到**结构本身**：
FVG 会不会被填、sweep 之后会不会反转、OB 会不会被回踩。这三种结构每天都会出现
（茅台 400 根：107 个 FVG / 21 个 sweep），才具备形成正向反馈的样本量。

检测一律复用 ``server/ict_model.py``（``detect_fvgs`` / ``detect_sweeps`` /
``_order_block_from_fvg`` / ``atr_at``），**不另写一套**，也不放宽任何结构定义。

第一红线：**不许未来函数**
--------------------------------
- FVG 在第 ``e`` 根收盘时成立（需要 ``e-2/e-1/e`` 三根），观察窗口**从 ``e+1`` 开始**；
- sweep 在第 ``s`` 根成立（只用 ``s`` 及之前的 K 线 + 之前已确认的摆动点），
  观察窗口**从 ``s+1`` 开始**；
- 未来根数不足 ``horizon`` 的样本一律记为 ``censored``（右删失）**剔除**，
  而不是默认「未填充」——后者会系统性压低填充率，是典型的回测造假。

基准对照（本模块最关键的设计）
------------------------------
「FVG 填充率 78%」单独看毫无意义：**任何**宽度 w、距现价 d 的价格区间，在
h 天内的触及概率都由 d 与波动率决定，与它是不是 FVG 无关。所以本模块不采用
「随机丢一个区间到价格轴上看触及率」这种距离无关的基准 —— 那种基准会把
「FVG 本来就贴着价格」这件几何事实误读成预测力，凭空造出几十个点的"超额"。

本模块改用 **波动率匹配的零假设基准（volatility-matched excursion baseline）**：
对每个 FVG 样本算出「现价到近端边界的距离 / 当期 ATR」= ``r``，然后在同一根
K 线上枚举**任意一个虚拟入场日 i**（同样不越界、同样右删失处理），问
「未来 h 根内价格是否走够 ``ATR_i × r``」。把这些虚拟日的命中率按样本的 ``r``
逐一对齐后取均值，就是「没有任何结构信息时，一个同等距离的区间会被触及的概率」。
两者之差（pp）才是 FVG 的**增量**。

sweep 同理：阈值固定为 ``reversal_atr_mult × ATR``，基准 = 随机一天出现同等幅度
同向位移的概率。

诚实原则
--------
- 某块 ``samples < MIN_USABLE_SAMPLES``（10）→ 该块比率**置 null**，
  ``available=false`` + note，绝不输出 0%/100% 这种会误导的数字；
- 结论里的「有无超额」用工程阈值 ±5pp 判量级，**明示这不是统计显著性检验**；
- 取不到/算不出 → ``null`` + 原因。
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from server import ict_model
from server.ict_history import MIN_USABLE_SAMPLES, bar_date, grade_samples

#: 默认观察窗口（交易日）
DEFAULT_HORIZON = 10
#: 允许的观察窗口范围
MAX_HORIZON = 60
#: sweep 反转的默认幅度（× ATR(14)）
DEFAULT_REVERSAL_ATR_MULT = 1.0
#: 「有无超额」的工程判定阈值（百分点）。仅为量级判断，**不是**统计显著性检验。
EDGE_THRESHOLD_PP = 5.0
#: ATR 周期（与 ict_model 的 atr_period 默认值一致）
ATR_PERIOD = 14


# ----------------------------------------------------------------------
#  基础序列
# ----------------------------------------------------------------------
def atr_series(candles: Sequence[ict_model.Candle], period: int = ATR_PERIOD
               ) -> List[Optional[float]]:
    """逐根 ATR（复用 ``ict_model.atr_at``，保证与 scan 同口径）。"""
    return [ict_model.atr_at(list(candles), index, period)
            if index >= 1 else None
            for index in range(len(candles))]


def forward_extremes(candles: Sequence[ict_model.Candle], horizon: int
                     ) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """未来 ``horizon`` 根（**不含当日**）的最低/最高价。

    Returns:
        ``(fwd_low, fwd_high)``：``fwd_low[i] = min(low[i+1 .. i+horizon])``；
        未来根数不足时该位置为 ``None``（右删失，不参与统计）。

    Note:
        这是本模块唯一读取「未来」数据的地方，且只服务于**评估**，不参与任何结构检测，
        因此不构成未来函数（结构检测本身只用 ``<= e`` 的 K 线）。
    """
    total = len(candles)
    fwd_low: List[Optional[float]] = [None] * total
    fwd_high: List[Optional[float]] = [None] * total
    window = int(horizon)
    if window <= 0:
        return fwd_low, fwd_high
    for index in range(total):
        end = index + window
        if end > total - 1:
            break
        segment = candles[index + 1:end + 1]
        fwd_low[index] = min(c.low for c in segment)
        fwd_high[index] = max(c.high for c in segment)
    return fwd_low, fwd_high


def excursion_atr(candles: Sequence[ict_model.Candle], fwd_low: Sequence[Optional[float]],
                  fwd_high: Sequence[Optional[float]], atr: Sequence[Optional[float]]
                  ) -> Tuple[List[float], List[float], List[int]]:
    """把未来极值折算成 ATR 倍数：``down_exc[i]`` / ``up_exc[i]``（>=0）。

    Returns:
        ``(down_exc, up_exc, valid_indices)``；``valid_indices`` 为有未来数据、
        有 ATR、ATR>0 的日期索引（基准统计只用这些天）。
    """
    down_exc: List[float] = [0.0] * len(candles)
    up_exc: List[float] = [0.0] * len(candles)
    valid: List[int] = []
    for index, candle in enumerate(candles):
        low, high, unit = fwd_low[index], fwd_high[index], atr[index]
        if low is None or high is None or not unit or unit <= 0:
            continue
        down_exc[index] = max(0.0, (candle.close - low) / unit)
        up_exc[index] = max(0.0, (high - candle.close) / unit)
        valid.append(index)
    return down_exc, up_exc, valid


# ----------------------------------------------------------------------
#  统计块
# ----------------------------------------------------------------------
def matched_rate(pool: Sequence[float], ratio: float) -> Optional[float]:
    """波动率匹配基准：给定一个 ATR 距离 ``ratio``，返回「随机入场日走够该距离」的百分比。

    用全枚举代替随机抽样：结果确定、可复现、无需随机种子。
    ``pool`` 为有效基准日的偏移分布（ATR 倍数），已按方向（向下/向上）筛好。
    """
    if not pool:
        return None
    hits = sum(1 for value in pool if value >= ratio)
    return round(hits / len(pool) * 100.0, 4)


def _days_stats(days: Sequence[int], samples: int) -> Tuple[Optional[float], Optional[float]]:
    """命中滞后天数的 ``(算术平均, 中位数)``，保留 2 位小数。

    口径（与比率字段一致，单点定义避免前端各自解释）：
    - **只对实际命中的样本聚合**：删失（右删失）与窗口内未命中都不参与——
      它们是「没有天数」而不是「0 天」，混进来会把中位数拉向 0；
    - ``samples < MIN_USABLE_SAMPLES``（10）时**两者都给 None**，不给 0、不给数字：
      比率块此时已置 ``available=false`` + 「不具参考价值」的 note，
      若天数还吐一个「平均 2.3 天」，前端会拿它去配一个被标为不可用的比率——
      所以 ``avg`` 与 ``median`` 同进同出，要么都有、要么都无。

    Returns:
        ``(avg, median)``；无命中或样本不足时均为 ``None``。
    """
    if samples < MIN_USABLE_SAMPLES or not days:
        return None, None
    # 显式 float：statistics.median 对奇数长度整型列表返回 int，
    # 若直接输出会出现 1 与 1.0 混排（JSON 类型不一致，前端格式化会不一致）
    return round(float(statistics.fmean(days)), 2), round(float(statistics.median(days)), 2)


def _rate_block(samples: int, hits: int, *,
                detected: int, censored: int, extra: Optional[Dict[str, Any]] = None,
                unit_label: str = "填充") -> Dict[str, Any]:
    """统一的**比率**块：样本不足时比率置 null + available=false + note（不编造 0%）。

    天数聚合（平均 / 中位）由 :func:`_days_stats` 独立给出，本函数不掺和——
    两者口径不同（比率分母是全部可评估样本，天数的分母只是命中样本），
    分开实现才不会出现「比率 null 但天数有值」的错配。
    """
    block: Dict[str, Any] = {
        "samples": int(samples),
        "detected": int(detected),
        "censored": int(censored),
        "available": samples >= MIN_USABLE_SAMPLES,
        "rate_pct": (round(hits / samples * 100.0, 2) if samples else None),
        "note": None,
    }
    if samples <= 0:
        block["note"] = f"样本 0 个（{unit_label}），无法统计"
    elif samples < MIN_USABLE_SAMPLES:
        block["note"] = (f"样本 {samples} 个，不足 {MIN_USABLE_SAMPLES} 个，"
                         f"{unit_label}率不具参考价值")
    if extra:
        block.update(extra)
    return block


# ----------------------------------------------------------------------
#  a) FVG 填充
# ----------------------------------------------------------------------
def _scan_fvg_hits(candles: Sequence[ict_model.Candle], fvg: Dict[str, Any], horizon: int
                   ) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """单个 FVG 的首次「触及」与「穿过 CE」的滞后天数（从 ``e+1`` 开始扫描）。

    Returns:
        - ``None``：**右删失**（未来不足 horizon 根）→ 由调用方计入 censored 并剔除；
        - ``(None, None)``：窗口内**未**填充（是真实观测结果，必须计入分母）；
        - ``(days_partial, days_full)``：命中及滞后天数。

    ⚠️ 这里必须是三态而不是两态：若把「未填充」也算成删失并剔除，填充率会被人为
    抬到 100%（本模块开发中真实踩过这个坑，验收时 91/91 = 100% 即为该缺陷）。
    """
    end_index = int(fvg["end_index"])
    start = end_index + 1
    last = end_index + int(horizon)
    if start >= len(candles) or last > len(candles) - 1:
        return None
    zone_low, zone_high = float(fvg["zone"][0]), float(fvg["zone"][1])
    ce = float(fvg["ce"])
    bullish = fvg["direction"] == "bullish"
    partial: Optional[int] = None
    full: Optional[int] = None
    for index in range(start, last + 1):
        candle = candles[index]
        # 方向感知：多头 FVG 在价格下方，用最低价向下穿透；空头反之。
        if bullish:
            if partial is None and candle.low <= zone_high:
                partial = index - end_index
            if full is None and candle.low <= ce:
                full = index - end_index
        else:
            if partial is None and candle.high >= zone_low:
                partial = index - end_index
            if full is None and candle.high >= ce:
                full = index - end_index
        if partial is not None and full is not None:
            break
    return partial, full


def fvg_stats(candles: Sequence[ict_model.Candle], fvgs: Sequence[Dict[str, Any]],
              horizon: int, atr: Sequence[Optional[float]],
              down_pool: Sequence[float], up_pool: Sequence[float]) -> Dict[str, Any]:
    """FVG 填充统计（overall / bullish / bearish）+ 波动率匹配基准。

    Args:
        down_pool: 有效基准日的「向下偏移 / ATR」分布（多头 FVG 用）。
        up_pool: 有效基准日的「向上偏移 / ATR」分布（空头 FVG 用）。
    """
    buckets: Dict[str, Dict[str, Any]] = {
        "overall": {"samples": 0, "partial": 0, "full": 0, "days_partial": [], "days_full": [],
                    "matched_partial": [], "matched_full": [], "censored": 0},
        "bullish": {"samples": 0, "partial": 0, "full": 0, "days_partial": [], "days_full": [],
                    "matched_partial": [], "matched_full": [], "censored": 0},
        "bearish": {"samples": 0, "partial": 0, "full": 0, "days_partial": [], "days_full": [],
                    "matched_partial": [], "matched_full": [], "censored": 0},
    }
    for fvg in fvgs:
        bucket = buckets["bullish" if fvg["direction"] == "bullish" else "bearish"]
        targets = (buckets["overall"], bucket)
        hits = _scan_fvg_hits(candles, fvg, horizon)
        if hits is None:
            for target in targets:
                target["censored"] += 1
            continue
        days_partial, days_full = hits
        end_index = int(fvg["end_index"])
        unit = atr[end_index]
        zone_low, zone_high = float(fvg["zone"][0]), float(fvg["zone"][1])
        ce = float(fvg["ce"])
        close = candles[end_index].close
        bullish = fvg["direction"] == "bullish"
        # 距离折算成 ATR 倍数：多头看「向下到近端边界 / CE」，空头反过来
        if unit and unit > 0:
            ratio_partial = max(0.0, ((close - zone_high) if bullish else (zone_low - close)) / unit)
            ratio_full = max(0.0, ((close - ce) if bullish else (ce - close)) / unit)
        else:
            ratio_partial = ratio_full = 0.0
        # 波动率匹配基准：同方向、同 ATR 距离的「随机入场日」命中率（该样本一份）
        pool = down_pool if bullish else up_pool
        matched_partial = matched_rate(pool, ratio_partial)
        matched_full = matched_rate(pool, ratio_full)
        for target in targets:
            target["samples"] += 1
            if matched_partial is not None:
                target["matched_partial"].append(matched_partial)
            if matched_full is not None:
                target["matched_full"].append(matched_full)
            if days_partial is not None:
                target["partial"] += 1
                target["days_partial"].append(days_partial)
            if days_full is not None:
                target["full"] += 1
                target["days_full"].append(days_full)

    detected = len(fvgs)
    result: Dict[str, Any] = {}
    for name, data in buckets.items():
        block = _rate_block(data["samples"], data["partial"],
                            detected=detected, censored=data["censored"],
                            unit_label="FVG 填充")
        block["partial_fill_rate_pct"] = block.pop("rate_pct")
        block.pop("avg_days", None)          # 由 _days_stats 统一给出 avg + median
        avg_partial, median_partial = _days_stats(data["days_partial"], data["samples"])
        block["avg_days_to_partial"] = avg_partial
        block["median_days_to_partial"] = median_partial
        block["full_fill_rate_pct"] = (
            round(data["full"] / data["samples"] * 100.0, 2) if data["samples"] else None)
        avg_full, median_full = _days_stats(data["days_full"], data["samples"])
        block["avg_days_to_full"] = avg_full
        block["median_days_to_full"] = median_full
        block["full_hits"] = data["full"]
        block["partial_hits"] = data["partial"]
        block["baseline_partial_pct"] = (
            round(statistics.fmean(data["matched_partial"]), 2)
            if data["matched_partial"] else None)
        block["baseline_full_pct"] = (
            round(statistics.fmean(data["matched_full"]), 2)
            if data["matched_full"] else None)
        if data["samples"] and data["samples"] < MIN_USABLE_SAMPLES:
            block["note"] = (f"样本 {data['samples']} 个，不足 {MIN_USABLE_SAMPLES} 个，"
                             f"填充率不具参考价值")
        result[name] = block

    return result


# ----------------------------------------------------------------------
#  b) Sweep 后反转
# ----------------------------------------------------------------------
def sweep_stats(candles: Sequence[ict_model.Candle], sweeps: Sequence[Dict[str, Any]],
                horizon: int, atr: Sequence[Optional[float]], reversal_mult: float,
                down_pool: Sequence[float], up_pool: Sequence[float]) -> Dict[str, Any]:
    """sweep 后「反向走出 ``reversal_mult × ATR``」的命中率（overall/buyside/sellside）。"""
    buckets: Dict[str, Dict[str, Any]] = {
        "overall": {"samples": 0, "hits": 0, "days": [], "baselines": [], "censored": 0},
        "buyside": {"samples": 0, "hits": 0, "days": [], "baselines": [], "censored": 0},
        "sellside": {"samples": 0, "hits": 0, "days": [], "baselines": [], "censored": 0},
    }
    for sweep in sweeps:
        index = int(sweep["index"])
        bucket = buckets["sellside" if sweep["kind"] == "sellside_sweep" else "buyside"]
        targets = (buckets["overall"], bucket)
        unit = atr[index]
        start, last = index + 1, index + int(horizon)
        if (not unit or unit <= 0 or start >= len(candles)
                or last > len(candles) - 1):
            for target in targets:
                target["censored"] += 1
            continue
        base = candles[index].close
        long_side = sweep["direction"] == "long"
        level = base + unit * reversal_mult if long_side else base - unit * reversal_mult
        hit_day: Optional[int] = None
        for cursor in range(start, last + 1):
            candle = candles[cursor]
            reached = candle.high >= level if long_side else candle.low <= level
            if reached:
                hit_day = cursor - index
                break
        baseline = matched_rate(up_pool if long_side else down_pool, reversal_mult)
        for target in targets:
            target["samples"] += 1
            if baseline is not None:
                target["baselines"].append(baseline)
            if hit_day is not None:
                target["hits"] += 1
                target["days"].append(hit_day)

    detected = len(sweeps)
    result: Dict[str, Any] = {}
    for name, data in buckets.items():
        block = _rate_block(data["samples"], data["hits"],
                            detected=detected, censored=data["censored"],
                            unit_label="反转", extra={"atr_multiple": float(reversal_mult)})
        block["reversal_rate_pct"] = block.pop("rate_pct")
        block.pop("avg_days", None)
        avg_rev, median_rev = _days_stats(data["days"], data["samples"])
        block["avg_days_to_reversal"] = avg_rev
        block["median_days_to_reversal"] = median_rev
        block["hits"] = data["hits"]
        block["baseline_reversal_pct"] = (
            round(statistics.fmean(data["baselines"]), 2) if data["baselines"] else None)
        if data["samples"] and data["samples"] < MIN_USABLE_SAMPLES:
            block["note"] = (f"样本 {data['samples']} 个，不足 {MIN_USABLE_SAMPLES} 个，"
                             f"反转率不具参考价值")
        result[name] = block
    return result


# ----------------------------------------------------------------------
#  c) OB 触碰
# ----------------------------------------------------------------------
def ob_stats(candles: Sequence[ict_model.Candle], fvgs: Sequence[Dict[str, Any]],
             horizon: int) -> Dict[str, Any]:
    """OB（由 FVG 派生，见 ``ict_model._order_block_from_fvg``）在 horizon 内被回踩率。

    为什么从 ``e+1`` 开始看：OB 是「FVG 起点那根反向 K 线」，但它作为 ICT 结构只有在
    位移 FVG 成立（第 ``e`` 根收盘）之后才可交易，因此观察窗口与 FVG 对齐。
    """
    samples = hits = censored = 0
    days: List[int] = []
    for fvg in fvgs:
        ob = ict_model._order_block_from_fvg(list(candles), fvg)  # noqa: SLF001 —— 复用模型定义
        end_index = int(fvg["end_index"])
        start, last = end_index + 1, end_index + int(horizon)
        if start >= len(candles) or last > len(candles) - 1:
            censored += 1
            continue
        zone_low, zone_high = float(ob["zone"][0]), float(ob["zone"][1])
        bullish = ob["direction"] == "bullish"
        samples += 1
        for index in range(start, last + 1):
            candle = candles[index]
            touched = candle.low <= zone_high if bullish else candle.high >= zone_low
            if touched:
                hits += 1
                days.append(index - end_index)
                break

    block = _rate_block(samples, hits, detected=len(fvgs), censored=censored,
                        unit_label="OB 回踩")
    block["touch_rate_pct"] = block.pop("rate_pct")
    block.pop("avg_days", None)
    avg_touch, median_touch = _days_stats(days, samples)
    block["avg_days_to_touch"] = avg_touch
    block["median_days_to_touch"] = median_touch      # 与 FVG/sweep 同口径，便于前端统一渲染
    block["hits"] = hits
    block["derived_from"] = "fvg_pre_candle"
    block["note_definition"] = ("OB 由 FVG 起点反向 K 线近似（ict_model._order_block_from_fvg），"
                               "未经成交量分布验证；多头 OB 上沿 = 该 K 线最高价")
    if samples and samples < MIN_USABLE_SAMPLES:
        block["note"] = f"样本 {samples} 个，不足 {MIN_USABLE_SAMPLES} 个，回踩率不具参考价值"
    return {"overall": block}


# ----------------------------------------------------------------------
#  结论
# ----------------------------------------------------------------------
def _build_verdict(fvg: Dict[str, Any], sweep: Dict[str, Any], ob: Dict[str, Any],
                   horizon: int) -> Dict[str, Any]:
    """结论：FVG 到底有没有超出随机的预测力（严格、不美化）。"""
    overall = fvg["overall"]
    samples = int(overall.get("samples") or 0)
    detected = int(overall.get("detected") or 0)
    grade = grade_samples(samples)

    if samples < MIN_USABLE_SAMPLES:
        return {
            "available": False,
            "grade": grade,
            "fvg_samples": samples,
            "note": (f"FVG 有效样本 {samples} 个（检测到 {detected} 个，右删失 "
                     f"{overall.get('censored')} 个），不足 {MIN_USABLE_SAMPLES} 个，"
                     f"三种结构率均不具参考价值；请加大 limit 或换更长序列"),
        }

    touch = overall.get("partial_fill_rate_pct")
    touch_base = overall.get("baseline_partial_pct")
    full = overall.get("full_fill_rate_pct")
    full_base = overall.get("baseline_full_pct")
    edge_touch = (round(float(touch) - float(touch_base), 2)
                  if touch is not None and touch_base is not None else None)
    edge_full = (round(float(full) - float(full_base), 2)
                 if full is not None and full_base is not None else None)

    edges = [value for value in (edge_touch, edge_full) if value is not None]
    if not edges:
        power = "unknown"
        power_text = "基准无法计算，不能判定"
    elif all(abs(value) < EDGE_THRESHOLD_PP for value in edges):
        power = "none"
        power_text = (f"FVG 填充率与「同等距离区间的波动率匹配基准」差异在 ±{EDGE_THRESHOLD_PP:.0f}pp "
                      f"以内（触及 {edge_touch:+.2f}pp / 穿中 {edge_full:+.2f}pp），"
                      f"落在随机波动范围内 → **未见超出随机的预测力**")
    elif max(edges) >= EDGE_THRESHOLD_PP:
        power = "positive"
        power_text = (f"FVG 填充率高于波动率匹配基准（触及 {edge_touch:+.2f}pp / "
                      f"穿中 {edge_full:+.2f}pp），方向为正但幅度有限，"
                      f"需在更多标的上复现才能确认不是样本噪声")
    else:
        power = "negative"
        power_text = (f"FVG 填充率低于波动率匹配基准（触及 {edge_touch:+.2f}pp / "
                      f"穿中 {edge_full:+.2f}pp），说明价格反而更倾向离开 FVG 而不是回填")

    sweep_note = ""
    sweep_overall = sweep["overall"]
    if (sweep_overall.get("reversal_rate_pct") is not None
            and sweep_overall.get("baseline_reversal_pct") is not None):
        sweep_edge = (float(sweep_overall["reversal_rate_pct"])
                      - float(sweep_overall["baseline_reversal_pct"]))
        sweep_note = (f"；sweep 反转率 {sweep_overall['reversal_rate_pct']:.2f}% vs 基准 "
                      f"{sweep_overall['baseline_reversal_pct']:.2f}%（{sweep_edge:+.2f}pp）")
    else:
        sweep_note = f"；sweep 样本 {sweep_overall.get('samples')} 个，不足以下结论"

    ob_note = ""
    ob_overall = ob["overall"]
    if ob_overall.get("touch_rate_pct") is not None:
        ob_note = f"；OB 回踩率 {ob_overall['touch_rate_pct']:.2f}%（无基准对照）"

    return {
        "available": True,
        "grade": grade,
        "fvg_samples": samples,
        "horizon": int(horizon),
        "edge_threshold_pp": EDGE_THRESHOLD_PP,
        "prediction_power": power,
        "edge_fvg_partial_pp": edge_touch,
        "edge_fvg_full_pp": edge_full,
        "single_symbol_caveat": (
            "单标的结论不可外推：本端点每次只统计一个标的。同一口径横向跑多只标的时，"
            "FVG 超额并不稳定（多数标的落在 ±5pp 内），个别标的的高值更可能是该标的"
            "当期波动/趋势特征造成，而非结构本身有效。"
        ),
        "note": (f"窗口 {horizon} 个交易日，FVG 有效样本 {samples} 个"
                 f"（检测 {detected} 个，右删失 {overall.get('censored')} 个）："
                 f"{power_text}{sweep_note}{ob_note}。"
                 f"判定阈值 ±{EDGE_THRESHOLD_PP:.0f}pp 是工程量级判断，非统计显著性检验"),
    }


# ----------------------------------------------------------------------
#  主入口
# ----------------------------------------------------------------------
def build_structure_stats(candles: List[ict_model.Candle],
                          params: Optional[Dict[str, Any]] = None,
                          *, horizon: int = DEFAULT_HORIZON,
                          reversal_atr_mult: float = DEFAULT_REVERSAL_ATR_MULT,
                          is_daily: bool = True) -> Dict[str, Any]:
    """结构级统计主流程。

    Args:
        candles: 已排序 Candle 列表（长度 ≥ ``ict_model.MIN_CANDLES``）。
        params: ``DEFAULT_PARAMS`` ← KV ← 查询 的合并结果（影响 swing/sweep 检测）。
        horizon: 观察窗口（bar 数）。
        reversal_atr_mult: sweep 反转幅度阈值（× ATR）。
        is_daily: 日线 True。

    Returns:
        响应 ``data`` 的非 IO 部分；``code`` / ``period`` 由路由层补齐。

    Raises:
        ValueError: K 线不足 / horizon 非法。
    """
    total = len(candles)
    if total < ict_model.MIN_CANDLES:
        raise ValueError(f"K 线不足：需要至少 {ict_model.MIN_CANDLES} 根，实际 {total} 根")
    window = int(horizon)
    if window <= 0 or window > MAX_HORIZON:
        raise ValueError(f"horizon 需为 1~{MAX_HORIZON} 之间的整数")
    if reversal_atr_mult <= 0:
        raise ValueError("reversal_atr_mult 必须大于 0")

    effective: Dict[str, Any] = dict(ict_model.DEFAULT_PARAMS)
    if params:
        effective.update({k: v for k, v in params.items() if v is not None})

    started = time.perf_counter()
    width = int(effective["swing_width"])
    lookback = int(effective["sweep_lookback"])
    tz_name = str(effective.get("session_timezone") or "Asia/Shanghai")

    # ---- 检测一律复用 ict_model（全量，不用 annotations 的最近 20 个）----
    swings = ict_model.detect_swings(candles, width)
    fvgs = ict_model.detect_fvgs(candles)
    # swing_width 传入后只引用「已确认」的摆动点（消除 ≤swing_width 根的前视偏差）
    sweeps = ict_model.detect_sweeps(candles, swings, lookback, tz_name, is_daily,
                                     swing_width=width)

    atr = atr_series(candles, int(effective.get("atr_period") or ATR_PERIOD))
    fwd_low, fwd_high = forward_extremes(candles, window)
    down_exc, up_exc, valid = excursion_atr(candles, fwd_low, fwd_high, atr)
    # 基准池只保留「有未来数据 + 有 ATR」的日期，避免把右删失日当作偏移 0 计入
    down_pool = [down_exc[index] for index in valid]
    up_pool = [up_exc[index] for index in valid]

    fvg = fvg_stats(candles, fvgs, window, atr, down_pool, up_pool)
    sweep = sweep_stats(candles, sweeps, window, atr, reversal_atr_mult, down_pool, up_pool)
    ob = ob_stats(candles, fvgs, window)
    verdict = _build_verdict(fvg, sweep, ob, window)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)

    edge = {
        "fvg_partial_fill_vs_baseline_pp": (
            round(float(fvg["overall"]["partial_fill_rate_pct"])
                  - float(fvg["overall"]["baseline_partial_pct"]), 2)
            if (fvg["overall"].get("partial_fill_rate_pct") is not None
                and fvg["overall"].get("baseline_partial_pct") is not None) else None),
        "fvg_full_fill_vs_baseline_pp": (
            round(float(fvg["overall"]["full_fill_rate_pct"])
                  - float(fvg["overall"]["baseline_full_pct"]), 2)
            if (fvg["overall"].get("full_fill_rate_pct") is not None
                and fvg["overall"].get("baseline_full_pct") is not None) else None),
        "sweep_reversal_vs_baseline_pp": (
            round(float(sweep["overall"]["reversal_rate_pct"])
                  - float(sweep["overall"]["baseline_reversal_pct"]), 2)
            if (sweep["overall"].get("reversal_rate_pct") is not None
                and sweep["overall"].get("baseline_reversal_pct") is not None) else None),
        "ob_touch_vs_baseline_pp": None,   # 未做 OB 基准，显式 null 而不是省略
    }

    warnings: List[str] = [
        ("观察窗口从结构成立日的**下一根**开始（FVG 第 e 根收盘成立 → 从 e+1 起算；"
         "sweep 同理），不存在用当日之后数据判定结构本身的情况。"),
        "右删失：未来不足 horizon 根的样本一律计入 censored 并剔除，不按「未填充/未反转」处理。",
        "基准 = 波动率匹配的零假设（同等 ATR 距离/同幅度的随机入场日命中率），"
        "不是「随机丢一个区间到价格轴上」——后者距离无关，会把几何事实误读成预测力。",
        "判定阈值 ±5pp 为工程量级判断，不是统计显著性检验；不同标的/周期可能不同。",
        "sweep 检测：已修正摆动点前视偏差——detect_sweeps / MSS 参照 / 目标位只引用"
        "「右侧 swing_width 根已确认」的摆动点（此前允许引用距当日 1~swing_width 根的"
        "未确认摆动点，会让 sweep 统计偏乐观）。修正后 sweep 样本数会变化，属预期。",
        "FVG 填充率高于基准的解释不是「机构订单块成立」：FVG 近端边界 = 信号那根 K 线的"
        "极值，是一个**刚刚成交过的价位**，而基准用的是同等 ATR 距离但从未被访问过的价位。"
        "因此这部分超额主要反映「价格容易回到刚交易过的位置」（短期均值回复/区间重叠），"
        "属通用价格行为，不构成 ICT 假说的验证。",
        "填充率/反转率都不等于可交易收益：本端点只回答「价格会不会回来/会不会反向走」，"
        "未计算入场后的方向性 P&L（需要止损、目标位、费用模型，且与 confirmed 样本不足同源）。",
        "机械结构统计不等于可交易：未计费用/滑点，未含止损与仓位管理。",
    ]

    return {
        "candles": total,
        "range": {"from": bar_date(candles[0]), "to": bar_date(candles[-1])},
        "horizon": window,
        "params": effective,
        "counts": {
            "swings": len(swings),
            "fvgs": len(fvgs),
            "sweeps": len(sweeps),
            "baseline_days": len(valid),
        },
        "fvg": fvg,
        "sweep": sweep,
        "ob": ob,
        "baseline": {
            "fvg_touch_rate_pct": fvg["overall"].get("baseline_partial_pct"),
            "fvg_full_fill_rate_pct": fvg["overall"].get("baseline_full_pct"),
            "sweep_up_reversal_rate_pct": sweep["sellside"].get("baseline_reversal_pct"),
            "sweep_down_reversal_rate_pct": sweep["buyside"].get("baseline_reversal_pct"),
            "samples": len(valid),
            "matched_fvg_samples": fvg["overall"].get("samples"),
            "matched_sweep_samples": sweep["overall"].get("samples"),
            "method": "volatility_matched_excursion",
            "detail": ("对每个结构样本取「现价到目标位距离 / 当期 ATR」= r，再统计同一根 K 线上"
                       "任意虚拟入场日 i 在 horizon 根内走够 ATR_i × r 的比例（全枚举，非抽样）；"
                       "按样本 r 逐一配对后取均值。"),
        },
        "edge": edge,
        "verdict": verdict,
        "metrics_ms": elapsed_ms,
        "no_lookahead": {
            "structure_window_start": "structure_index + 1",
            "censoring": "future_bars < horizon → censored（剔除）",
            "detect_source": "server/ict_model.py::detect_fvgs / detect_sweeps / atr_at",
        },
        "warnings": warnings,
    }
