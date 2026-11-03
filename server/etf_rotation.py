# -*- coding: utf-8 -*-
"""ETF 双动量轮动（P1-3 / T17）：纯逻辑层，**无 IO、无网络、无全局可变状态**。

规则来源
--------
`docs/20-prd/openbb-dsa-integration-prd.md` §P1-3（第 307 行）与 §151（DSA 规则摘要）、
`docs/10-architecture/openbb-dsa-integration-design.md` §6.3 T17（七条硬纪律）。
**不复制 DSA 源码**，按其规则文档重写。

算法（四条）
------------
1. **相对动量**：``score = close[T] / close[T-lookback] - 1``，按 score 降序排名；
2. **绝对动量**：``score > 0`` 才有资格入选（负动量一律空仓等现金）；
3. **换仓缓冲**：已持有且 ``score > 0``、排名仍在 ``top_n + buffer_slots`` 之内的标的**保留**，
   避免在名次边界来回抖动导致无谓换手；
4. **成本扣减**：``cost = turnover * cost_bps / 10000 * 组合市值``，
   turnover 用标准单边口径 ``0.5 * Σ|w_target - w_current|``（等权），可手算核对。

防未来函数（本次核心风险点）
----------------------------
- T 日信号**只用** ``bars[:T+1]`` 计算（``momentum_rank`` 的 ``as_of_index`` 截断）；
- 成交价取 **T+1 日收盘**；若 T+1 不存在（最后一期）→ **只记信号、不成交**；
- 组合每日按当日收盘价估值，估值只用当日及以前的价格。

样本纪律
--------
回测样本 < ``TRADING_DAYS``(252) 个交易日时：
``annual_return / sharpe / max_drawdown / calmar / volatility`` **一律 None** + ``reason``，
只给 ``total_return``（真实已实现收益，不是年化，不会放大）。绝不外推、不补 0。

数据缺口
--------
多标的日历对齐时，**取各标的交易日的交集**：缺报价日不进入回测轴
（不向前填充成「当日有成交」），缺口信息通过 ``warnings`` 透出。
"""

from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 默认 ETF 池（4 只宽基/风格 ETF，均为前复权可取）
DEFAULT_POOL: List[Dict[str, str]] = [
    {"code": "sh510300", "name": "沪深300ETF"},
    {"code": "sh510500", "name": "中证500ETF"},
    {"code": "sh588000", "name": "科创50ETF"},
    {"code": "sz159915", "name": "创业板ETF"},
]

#: 代码 → 中文名（仅常量查表，不是运行期状态）
_NAME_MAP: Dict[str, str] = {item["code"]: item["name"] for item in DEFAULT_POOL}

#: 年化所需的最小交易日样本（不足则不给年化）
TRADING_DAYS = 252

#: 最小可行样本：至少要能算出一个回撤/波动（否则连 total_return 都无意义）
MIN_SAMPLES = 2

#: 参数平原的推荐网格（前端默认视图用；本模块不预设，由调用方传入）
DEFAULT_LOOKBACKS: Tuple[int, ...] = (10, 20, 40, 60)
DEFAULT_TOP_NS: Tuple[int, ...] = (1, 2, 3)

_EPS = 1e-12


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _code_name(code: str) -> str:
    """代码 → 展示名；未知代码回落为代码本身（不编造名称）。"""
    return _NAME_MAP.get(code, code)


def _as_float(value: Any) -> Optional[float]:
    """把 K 线字段转成 float；不可解析返回 None（不静默变 0）。"""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _bar_date(bar: Dict[str, Any]) -> str:
    """K 线日期统一成 ``YYYY-MM-DD`` 字符串（本仓库 ``/api/kline`` 已是该格式）。"""
    raw = str(bar.get("d") or "").strip()
    return raw


def _bar_close(bar: Dict[str, Any]) -> Optional[float]:
    """收盘价；非正数视为缺失（ETF 不存在 0 价成交）。"""
    close = _as_float(bar.get("c"))
    if close is None or close <= 0:
        return None
    return close


def _pct(value: Optional[float]) -> Optional[float]:
    """内部保留完整精度，仅做长度保护（避免 1e-17 之类的伪精度外泄）。"""
    if value is None:
        return None
    return round(float(value), 10)


def _sorted_codes(bars_by_code: Dict[str, Sequence[Dict[str, Any]]]) -> List[str]:
    return sorted(bars_by_code.keys())


# ---------------------------------------------------------------------------
# 1. 动量排名
# ---------------------------------------------------------------------------

def momentum_rank(
    bars_by_code: Dict[str, Sequence[Dict[str, Any]]],
    lookback: int,
    as_of_index: Optional[int] = None,
) -> Dict[str, Any]:
    """相对动量排名。

    :param bars_by_code: ``{code: [{"d","o","h","l","c","v"}, ...]}``，**升序**（旧→新）
    :param lookback: 回看天数（用 ``close[T-lookback]`` 作为基期）
    :param as_of_index: ``None`` 表示用最后一根；否则只用 ``bars[:as_of_index+1]``
                        （**这是防未来函数的唯一入口**，越界/负数会夹到合法范围）
    :return: ``{"as_of", "ranked", "missing", "lookback"}``，
             ``ranked`` 按 score 降序（同分按 code 升序，保证可重复），
             ``missing`` 为 ``[{"code", "reason"}]``。
    """
    lookback = max(1, int(lookback))
    source = bars_by_code or {}

    ranked: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    missing_reason: Dict[str, str] = {}
    as_of_candidates: List[str] = []

    for code in _sorted_codes(source):
        bars = list(source.get(code) or [])

        # ---- 1) 定位 T（信号日） ----
        if as_of_index is None:
            t_index = len(bars) - 1
        else:
            t_index = int(as_of_index)
            if t_index < 0:
                t_index = 0
            if t_index > len(bars) - 1:
                reason = (f"数据不足：需要至少 {t_index + 1} 根，实际 {len(bars)} 根")
                missing.append({"code": code, "reason": reason})
                missing_reason[code] = reason
                continue

        if t_index < 0:
            reason = "无 K 线数据"
            missing.append({"code": code, "reason": reason})
            missing_reason[code] = reason
            continue

        # ---- 2) 样本是否够 lookback + 1 ----
        need = lookback + 1
        if (t_index + 1) < need:
            reason = f"样本不足：需要 {need} 根（lookback={lookback}），实际 {t_index + 1} 根"
            missing.append({"code": code, "reason": reason})
            missing_reason[code] = reason
            continue

        # ---- 3) 取两个收盘价（只用 <=T 的数据） ----
        close_now = _bar_close(bars[t_index])
        close_base = _bar_close(bars[t_index - lookback])
        if close_now is None or close_base is None:
            reason = "收盘价缺失或非正，无法计算动量"
            missing.append({"code": code, "reason": reason})
            missing_reason[code] = reason
            continue
        if close_base <= _EPS:
            reason = "基期收盘价过小，动量无意义"
            missing.append({"code": code, "reason": reason})
            missing_reason[code] = reason
            continue

        score = close_now / close_base - 1.0
        bar_date = _bar_date(bars[t_index])
        if bar_date:
            as_of_candidates.append(bar_date)

        ranked.append({
            "code": code,
            "name": _code_name(code),
            "score": _pct(score),
            "close": round(close_now, 6),
        })

    # 同分按 code 升序，保证输出可重复（参数平原与单测都依赖确定性）
    ranked.sort(key=lambda item: (-item["score"], item["code"]))

    return {
        "as_of": max(as_of_candidates) if as_of_candidates else None,
        "ranked": ranked,
        "missing": missing,
        "missing_reason": missing_reason,
        "lookback": lookback,
    }


# ---------------------------------------------------------------------------
# 2. 选仓（绝对动量 + 相对动量 + 换仓缓冲）
# ---------------------------------------------------------------------------

def select_holdings(
    ranked: Sequence[Dict[str, Any]],
    held: Sequence[str],
    top_n: int,
    buffer_slots: int = 1,
) -> List[str]:
    """选出目标持仓（等权）。

    规则顺序（**不可调换**，否则缓冲会失效）：
      ① **绝对动量**：只有 ``score > 0`` 的标的才有资格入选；
      ② **相对动量**：候选按 score 降序，取前 ``top_n``；
      ③ **换仓缓冲**：已在 ``held`` 里、且 ``score > 0``、且（在合格候选里的）
         名次 ``< top_n + buffer_slots`` 的标的**优先保留**，再用排名更靠前的
         非持仓标的填满剩余仓位。即：**缓冲只降低换手，不放大仓位** ——
         持仓数恒为 ``min(top_n, 合格候选数)``。

    :param ranked: ``momentum_rank()["ranked"]``（已按 score 降序）
    :param held: 当前持仓代码列表
    :param top_n: 目标持仓数
    :param buffer_slots: 缓冲位数（≥0）
    :return: 目标持仓代码列表，按 score 降序（同分按 code 升序）
    """
    top_n = max(0, int(top_n))
    buffer_slots = max(0, int(buffer_slots))
    if top_n == 0:
        return []

    held_set = {str(code) for code in (held or [])}
    eligible = [item for item in (ranked or []) if float(item.get("score") or 0.0) > 0.0]
    if not eligible:
        return []

    buffer_zone = top_n + buffer_slots

    # ③ 缓冲：合格候选里名次仍在缓冲区的持仓 → 优先保留
    kept: List[Dict[str, Any]] = []
    for index, item in enumerate(eligible):
        if index >= buffer_zone:
            break
        if item["code"] in held_set:
            kept.append(item)

    picked: List[Dict[str, Any]] = list(kept)
    picked_codes = {item["code"] for item in picked}

    # ①② 用排名更靠前的合格候选填满剩余仓位
    for item in eligible:
        if len(picked) >= top_n:
            break
        if item["code"] in picked_codes:
            continue
        picked.append(item)
        picked_codes.add(item["code"])

    # 输出排序：score 降序（与 ranked 一致，便于前端直接展示）
    picked.sort(key=lambda item: (-float(item["score"]), item["code"]))
    return [item["code"] for item in picked][:top_n]


# ---------------------------------------------------------------------------
# 3. 回测
# ---------------------------------------------------------------------------

def _align_bars(
    bars_by_code: Dict[str, Sequence[Dict[str, Any]]],
) -> Tuple[List[str], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """把多标的 K 线对齐到**共同交易日**（交集），返回 ``(dates, aligned, warnings)``。

    交集口径：缺报价日**不进入回测轴**，绝不向前填充成「当日有成交」。
    被剔除的日期数量作为告警透出。
    """
    warnings: List[Dict[str, Any]] = []
    per_code_dates: Dict[str, List[str]] = {}
    per_code_bars: Dict[str, Dict[str, Dict[str, Any]]] = {}
    date_sets: List[set] = []

    for code in _sorted_codes(bars_by_code):
        mapping: Dict[str, Dict[str, Any]] = {}
        ordered: List[str] = []
        for bar in bars_by_code.get(code) or []:
            date = _bar_date(bar)
            if not date or _bar_close(bar) is None:
                continue
            if date in mapping:
                continue  # 重复日期只认第一根，避免同日两条记录互相覆盖
            mapping[date] = bar
            ordered.append(date)
        per_code_dates[code] = ordered
        per_code_bars[code] = mapping
        date_sets.append(set(ordered))

    if not date_sets:
        return [], {}, warnings

    common = set.intersection(*date_sets) if len(date_sets) > 1 else set(date_sets[0])
    dates = sorted(common)
    if not dates:
        warnings.append({
            "code": "*",
            "reason": "各标的交易日无交集，无法构建回测轴",
        })
        return [], {}, warnings

    aligned: Dict[str, List[Dict[str, Any]]] = {}
    total_dropped = 0
    for code in _sorted_codes(bars_by_code):
        dropped = len(per_code_dates[code]) - len(dates)
        total_dropped += max(0, dropped)
        if dropped > 0:
            warnings.append({
                "code": code,
                "reason": f"有 {dropped} 个交易日不在共同交易日历内（已跳过，未用前值填充）",
            })
        aligned[code] = [per_code_bars[code][date] for date in dates]

    if total_dropped > 0:
        warnings.insert(0, {
            "code": "*",
            "reason": f"多标的日历对齐共跳过 {total_dropped} 个非共同交易日（缺报价日不调仓、不估值）",
        })
    return dates, aligned, warnings


def _turnover_of(current: Sequence[str], target: Sequence[str]) -> float:
    """单边换手率：``0.5 * Σ|w_target - w_current|``（等权权重）。"""
    cur = list(current or [])
    tgt = list(target or [])
    if not cur and not tgt:
        return 0.0
    w_cur = {code: 1.0 / len(cur) for code in cur} if cur else {}
    w_tgt = {code: 1.0 / len(tgt) for code in tgt} if tgt else {}
    codes = set(w_cur) | set(w_tgt)
    return 0.5 * sum(abs(w_tgt.get(code, 0.0) - w_cur.get(code, 0.0)) for code in codes)


def _daily_returns(navs: Sequence[float]) -> List[float]:
    out: List[float] = []
    for index in range(1, len(navs)):
        prev = navs[index - 1]
        if prev is None or abs(prev) <= _EPS:
            continue
        out.append(navs[index] / prev - 1.0)
    return out


def _max_drawdown(navs: Sequence[float]) -> Optional[float]:
    """最大回撤（负数，如 -0.12 表示 -12%）；不足 2 点返回 None。"""
    if len(navs) < 2:
        return None
    peak = navs[0]
    worst = 0.0
    for nav in navs:
        if nav > peak:
            peak = nav
        if peak <= _EPS:
            continue
        drawdown = nav / peak - 1.0
        if drawdown < worst:
            worst = drawdown
    return worst


def _yearly_returns(equity: Sequence[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """分年度收益：按自然年切分净值，首年以曲线起点为基期。不足 2 点返回 None。"""
    if len(equity) < 2:
        return None
    out: Dict[str, Optional[float]] = {}
    base_nav = float(equity[0]["nav"])
    prev_year_end = base_nav
    current_year = str(equity[0]["d"])[:4]
    year_end_nav = base_nav
    for point in equity:
        year = str(point["d"])[:4]
        if year != current_year:
            if prev_year_end > _EPS:
                out[current_year] = _pct(year_end_nav / prev_year_end - 1.0)
            prev_year_end = year_end_nav
            current_year = year
        year_end_nav = float(point["nav"])
    if prev_year_end > _EPS:
        out[current_year] = _pct(year_end_nav / prev_year_end - 1.0)
    return {key: value for key, value in out.items() if value is not None}


def _compute_metrics(
    equity: Sequence[Dict[str, Any]],
    trades: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """由净值序列算指标。**样本 < 252 交易日时年化口径一律 None**。"""
    samples = len(equity)
    total_return: Optional[float] = None
    navs = [float(point["nav"]) for point in equity] if equity else []
    if samples >= MIN_SAMPLES and navs and navs[0] > _EPS:
        total_return = _pct(navs[-1] / navs[0] - 1.0)

    annual_ok = samples >= TRADING_DAYS
    reason: Optional[str] = None
    if samples < MIN_SAMPLES:
        reason = f"样本 {samples} 个交易日，不足以计算任何回测指标"
    elif not annual_ok:
        reason = f"样本 {samples}/{TRADING_DAYS} 交易日不足一年，不提供年化"

    metrics: Dict[str, Any] = {
        "total_return": total_return,
        "annual_return": None,
        "max_drawdown": None,
        "sharpe": None,
        "calmar": None,
        "volatility": None,
        "turnover": _pct(sum(float(t["turnover"]) for t in trades)) if trades else 0.0,
        "trades": len(trades),
        "yearly_returns": _yearly_returns(equity),
    }

    if not annual_ok:
        # 不足一年：年化/风险指标全 null，只留 total_return 与换手事实。
        # reason 必须带出去，否则调用方看到 annual_ok=False 却不知道为什么。
        metrics["reason"] = reason
        return metrics

    navs_len = len(navs)
    annual_return = (navs[-1] / navs[0]) ** (TRADING_DAYS / max(1, navs_len - 1)) - 1.0 \
        if navs and navs[0] > _EPS and navs_len > 1 else None
    returns = _daily_returns(navs)
    volatility: Optional[float] = None
    sharpe: Optional[float] = None
    if len(returns) >= 2:
        try:
            sd = stdev(returns)
        except (ValueError, ZeroDivisionError):
            sd = None
        if sd is not None and sd > _EPS:
            volatility = _pct(sd * math.sqrt(TRADING_DAYS))
            sharpe = _pct(mean(returns) / sd * math.sqrt(TRADING_DAYS))
        elif sd is not None:
            # 零波动：夏普无定义（不是无穷大），如实 None
            volatility = 0.0
            sharpe = None

    max_dd = _max_drawdown(navs)
    calmar: Optional[float] = None
    if annual_return is not None and max_dd is not None and max_dd < -_EPS:
        calmar = _pct(annual_return / abs(max_dd))

    metrics.update({
        "annual_return": _pct(annual_return),
        "max_drawdown": _pct(max_dd),
        "sharpe": sharpe,
        "calmar": calmar,
        "volatility": volatility,
        "reason": reason,
    })
    return metrics


def backtest(
    bars_by_code: Dict[str, Sequence[Dict[str, Any]]],
    lookback: int = 20,
    top_n: int = 2,
    cost_bps: float = 5.0,
    rebalance_days: int = 20,
    buffer_slots: int = 1,
) -> Dict[str, Any]:
    """逐调仓日推进的双动量回测。

    时间线（**防未来函数的定义**）::

        ... T-1 收盘 | T 收盘（算信号，只用 <=T 数据） | T+1 收盘（按此价成交） ...

    :return: ``{"equity": [{"d","nav"}], "trades": [{"d","from","to","turnover","cost"}],
              "metrics": {...}, "samples": int, "annual_ok": bool, "reason": str|None,
              "warnings": [...], "rebalance_days": int, "params": {...}}``
    """
    lookback = max(1, int(lookback))
    top_n = max(0, int(top_n))
    rebalance_days = max(1, int(rebalance_days))
    buffer_slots = max(0, int(buffer_slots))
    cost_rate = max(0.0, float(cost_bps)) / 10000.0

    params = {
        "lookback": lookback,
        "top_n": top_n,
        "cost_bps": float(cost_bps),
        "rebalance_days": rebalance_days,
        "buffer_slots": buffer_slots,
    }

    empty_metrics: Dict[str, Any] = {
        "total_return": None, "annual_return": None, "max_drawdown": None,
        "sharpe": None, "calmar": None, "volatility": None,
        "turnover": 0.0, "trades": 0, "yearly_returns": None,
    }

    dates, aligned, warnings = _align_bars(bars_by_code or {})
    if not dates or not aligned:
        return {
            "equity": [], "trades": [], "metrics": dict(empty_metrics),
            "samples": 0, "annual_ok": False,
            "reason": "无可用 K 线数据（各标的均无有效收盘价，或交易日无交集）",
            "warnings": warnings, "params": params,
            "rebalance_days": rebalance_days,
        }

    start = lookback  # 第一个能算出动量的索引
    if len(dates) <= start + 1:
        return {
            "equity": [], "trades": [], "metrics": dict(empty_metrics),
            "samples": 0, "annual_ok": False,
            "reason": f"样本不足：共同交易日 {len(dates)} 个，需要至少 {lookback + 2} 个"
                      f"（lookback={lookback} + 买入后至少 1 个估值日）",
            "warnings": warnings, "params": params,
            "rebalance_days": rebalance_days,
        }

    # ---- 组合状态：从 index=start 起，全现金、净值 1.0 ----
    nav = 1.0
    cash = 1.0
    shares: Dict[str, float] = {}
    held: List[str] = []

    equity: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []

    def _close_on(code: str, index: int) -> Optional[float]:
        return _bar_close(aligned[code][index])

    def _mark_to_market(index: int) -> float:
        value = cash
        for code, qty in shares.items():
            price = _close_on(code, index)
            if price is None:
                # 缺价不补 0、也不前值填充成「当日有成交」：
                # 保守用**上一次有效价**（只用 <=index 的价格，不引入未来信息）
                price = last_price.get(code)
            if price is None:
                continue
            value += qty * price
        return value

    # 记录每日持仓数量对应的最后有效价，供缺价日保守估值（不引入未来价）
    last_price: Dict[str, float] = {}

    for index in range(start, len(dates)):
        # 每日先按当日收盘估值（只用到 index 及以前的价格）
        for code in shares:
            price = _close_on(code, index)
            if price is not None:
                last_price[code] = price
        nav = cash + sum(qty * last_price.get(code, 0.0) for code, qty in shares.items())
        if nav <= 0:
            nav = 1.0
        equity.append({"d": dates[index], "nav": round(nav, 8)})

        # 是否为调仓日（相对 start 的第 0/1/2… 个调仓点）
        if (index - start) % rebalance_days != 0:
            continue

        # ---- T 日算信号：只用 dates[:index+1] ----
        rank = momentum_rank(aligned, lookback, as_of_index=index)
        if not rank["ranked"]:
            continue
        target = select_holdings(rank["ranked"], held, top_n, buffer_slots)

        exec_index = index + 1
        if exec_index > len(dates) - 1:
            # T+1 不存在：只记信号、不成交（绝不拿 T 的收盘当成交价）
            warnings.append({
                "code": "*",
                "reason": f"{dates[index]} 为最后一个交易日，调仓信号已产生但无 T+1 可成交，未执行",
            })
            continue

        turnover = _turnover_of(held, target)
        if turnover <= _EPS and set(held) == set(target):
            continue  # 组合未变化 → 不交易（"只在组合变化时交易"）

        exec_value = _mark_to_market(exec_index)
        cost = turnover * cost_rate * exec_value
        after_cost = max(0.0, exec_value - cost)

        # 等权重建仓：先清仓再按目标权重买入（等价于一次性调仓）
        old_held = list(held)
        shares = {}
        if target and after_cost > _EPS:
            weight_value = after_cost / len(target)
            for code in target:
                price = _close_on(code, exec_index)
                if price is None or price <= _EPS:
                    # 成交日缺价 → 该标的不建仓（不猜价格），并告警
                    warnings.append({
                        "code": code,
                        "reason": f"{dates[exec_index]} 无有效收盘价，成交价不可得，本次未建仓该标的",
                    })
                    continue
                shares[code] = weight_value / price
                last_price[code] = price
        held = [code for code in target if code in shares]
        cash = max(0.0, after_cost - sum(qty * last_price.get(code, 0.0)
                                        for code, qty in shares.items()))

        trades.append({
            "d": dates[exec_index],
            "from": old_held,
            "to": list(held),
            "turnover": _pct(turnover),
            "cost": round(cost, 10),
        })

    navs = [point["nav"] for point in equity]
    metrics = _compute_metrics(equity, trades)
    samples = len(equity)
    annual_ok = samples >= TRADING_DAYS
    reason = metrics.pop("reason", None)

    return {
        "equity": equity,
        "trades": trades,
        "metrics": metrics,
        "samples": samples,
        "annual_ok": annual_ok,
        "reason": reason,
        "warnings": warnings,
        "params": params,
        "rebalance_days": rebalance_days,
        "final_holding": list(held),
        "final_nav": navs[-1] if navs else None,
    }


# ---------------------------------------------------------------------------
# 4. 等权基准
# ---------------------------------------------------------------------------

def equal_weight_benchmark(
    bars_by_code: Dict[str, Sequence[Dict[str, Any]]],
    start_index: int = 0,
) -> Dict[str, Any]:
    """等权买入持有基准（同一批标的，从 ``start_index`` 等额买入，不调仓、不计成本）。

    与策略在同一时间窗内比较，保证 ``vs_equal_weight`` 是**同口径**的差值。
    """
    dates, aligned, _warnings = _align_bars(bars_by_code or {})
    if len(dates) < 2:
        return {"equal_weight_return": None, "samples": 0,
                "reason": "共同交易日不足 2 个，无法计算等权基准"}
    codes = [code for code in aligned if aligned[code]]
    if not codes:
        return {"equal_weight_return": None, "samples": 0, "reason": "无可用标的"}
    begin = max(0, min(int(start_index), len(dates) - 2))
    returns: List[float] = []
    per_code: Dict[str, Optional[float]] = {}
    for code in codes:
        first = _bar_close(aligned[code][begin])
        last = _bar_close(aligned[code][-1])
        if first is None or last is None or first <= _EPS:
            per_code[code] = None
            continue
        value = last / first - 1.0
        per_code[code] = _pct(value)
        returns.append(value)
    if not returns:
        return {"equal_weight_return": None, "samples": 0,
                "per_code": per_code, "reason": "基准标的收盘价不可用"}
    return {
        "equal_weight_return": _pct(sum(returns) / len(returns)),
        "samples": len(dates) - begin,
        "per_code": per_code,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# 5. 当前信号快照
# ---------------------------------------------------------------------------

def rotation_snapshot(bars_by_code: Dict[str, Sequence[Dict[str, Any]]], params: Dict[str, Any]) -> Dict[str, Any]:
    """当前信号快照：当前持仓 / 目标持仓 / 动作。

    ``current_holding`` 取回测**末态**（即「如果历史一直按这套规则跑，现在手上是什么」），
    不是账户真实持仓 —— 真实持仓属于交易中心，本函数只做策略侧信号。

    :param params: 至少含 ``lookback`` / ``top_n``，可选 ``cost_bps`` /
                   ``rebalance_days`` / ``buffer_slots``
    """
    lookback = int(params.get("lookback") or 20)
    top_n = int(params.get("top_n") or 2)
    cost_bps = float(params.get("cost_bps") or 5.0)
    rebalance_days = int(params.get("rebalance_days") or 20)
    buffer_slots = int(params.get("buffer_slots") or 1)

    result = backtest(
        bars_by_code,
        lookback=lookback,
        top_n=top_n,
        cost_bps=cost_bps,
        rebalance_days=rebalance_days,
        buffer_slots=buffer_slots,
    )

    rank = momentum_rank(bars_by_code, lookback)
    current = list(result.get("final_holding") or [])
    target = select_holdings(rank["ranked"], current, top_n, buffer_slots) if rank["ranked"] else []

    if not target:
        action = "empty"
    elif set(target) == set(current):
        action = "hold"
    else:
        action = "rotate"

    warnings: List[Dict[str, Any]] = list(result.get("warnings") or [])
    if rank["missing"]:
        for item in rank["missing"]:
            warnings.append({
                "code": item["code"],
                "reason": f"未参与排名：{item['reason']}",
            })
    if not result.get("annual_ok") and result.get("reason"):
        warnings.append({"code": "*", "reason": result["reason"]})

    return {
        "as_of": rank["as_of"],
        "action": action,
        "current_holding": current,
        "target_holding": target,
        "momentum_rank": rank["ranked"],
        "missing": rank["missing"],
        "params": result["params"],
        "samples": result["samples"],
        "annual_ok": result["annual_ok"],
        "reason": result.get("reason"),
        "warnings": warnings,
        "equity": result["equity"],
        "trades": result["trades"],
        "metrics": result["metrics"],
    }


# ---------------------------------------------------------------------------
# 6. 参数平原
# ---------------------------------------------------------------------------

def parameter_plateau(
    bars_by_code: Dict[str, Sequence[Dict[str, Any]]],
    lookbacks: Sequence[int],
    top_ns: Sequence[int],
    **kw: Any,
) -> Dict[str, Any]:
    """参数平原：``lookbacks × top_ns`` 全网格回测，**每格都返回**。

    :param kw: 透传给 ``backtest``（``cost_bps`` / ``rebalance_days`` / ``buffer_slots``）
    :return: ``{"lookbacks", "top_ns", "cells", "note"}``；
             ``cells`` 每格含 ``lookback, top_n, total_return, annual_return,
             max_drawdown, vs_equal_weight, samples, annual_ok, reason``。
    """
    lookbacks = [int(x) for x in (lookbacks or []) if int(x) > 0]
    top_ns = [int(x) for x in (top_ns or []) if int(x) > 0]

    cells: List[Dict[str, Any]] = []
    for lookback in lookbacks:
        for top_n in top_ns:
            result = backtest(bars_by_code, lookback=lookback, top_n=top_n, **kw)
            bench = equal_weight_benchmark(bars_by_code, start_index=lookback)
            total = result["metrics"].get("total_return")
            bench_value = bench.get("equal_weight_return")
            vs_bench = None
            if total is not None and bench_value is not None:
                vs_bench = _pct(total - bench_value)
            cells.append({
                "lookback": lookback,
                "top_n": top_n,
                "total_return": total,
                "annual_return": result["metrics"].get("annual_return"),
                "max_drawdown": result["metrics"].get("max_drawdown"),
                "vs_equal_weight": vs_bench,
                "samples": result["samples"],
                "annual_ok": result["annual_ok"],
                "reason": result.get("reason"),
            })

    annual_ok_any = any(cell["annual_ok"] for cell in cells)
    note = (
        "参数平原逐格如实输出：年化口径仅在样本 ≥252 交易日时给出，"
        "不足一年的格子 annual_return/max_drawdown 为 null 并附 reason；"
        "vs_equal_weight 为负表示该组参数跑输等权基准，不得隐藏。"
        if cells else "无可用数据，参数平原为空。"
    )
    if cells and not annual_ok_any:
        note += " 当前全部格子样本均不足一年，Table 仅可用于观察相对优劣，不可据此判断年化收益。"

    return {
        "lookbacks": lookbacks,
        "top_ns": top_ns,
        "cells": cells,
        "note": note,
    }
