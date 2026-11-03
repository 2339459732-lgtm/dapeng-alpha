# -*- coding: utf-8 -*-
"""P1-3 ETF 双动量轮动单测 —— 防未来函数、缓冲、成本、样本纪律、参数平原、缺数据。

八组用例（名字见 ``test_*``）：

1. **无未来函数**（最核心）：同一段数据用两种方式截断，T 日信号必须完全一致；
   把 T 之后的 bar 换成极端值，T 日信号不得变化。
2. 绝对动量为负 → 不入选（空仓）。
3. 缓冲区：刚跌出前 ``top_n`` 但仍在 ``top_n+1`` 内 → 不换仓。
4. 成本扣减：``turnover × cost_bps`` 与手算完全一致。
5. 样本 <252 → ``annual_ok=False`` 且年化/夏普/回撤/Calmar 全 ``None`` + ``reason`` 非空。
6. 参数平原返回全部格子（``len(cells) == len(lookbacks) * len(top_ns)``）。
7. 缺数据：某只 bar 数不足 → 进 ``missing``，不出现在 ``ranked``，不抛异常。
8. 空输入：结构完整、``reason`` 非空、**不返回 0 冒充收益**。

所有 bar 都是手工构造的确定性序列，期望值在注释里给出推导，不从实现里抄。
"""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server import etf_rotation as R  # noqa: E402


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------

def make_bars(closes, start_day=1, code_prefix="2026"):
    """把收盘价序列包成 K 线列表（升序）。

    日期用 ``20260101`` 起的连续「交易日」占位（只用于对齐，不做真实日历校验）。
    """
    bars = []
    for index, close in enumerate(closes):
        day = start_day + index
        bars.append({
            "d": f"{code_prefix}-01-{day:02d}" if day <= 31 else f"{code_prefix}-02-{day - 31:02d}",
            "o": close,
            "h": close,
            "l": close,
            "c": close,
            "v": 1000.0,
        })
    return bars


def ramp(n, start=1.0, step=0.01):
    """线性上涨序列：``1.00, 1.01, 1.02, ...``（长度 n）。"""
    return [round(start + step * i, 6) for i in range(n)]


def flat(n, value=1.0):
    return [value] * n


# ---------------------------------------------------------------------------
# 1. 无未来函数（两组断言）
# ---------------------------------------------------------------------------

def test_no_lookahead_truncation_equivalence():
    """截断到 T 与「用 as_of_index=T 在完整序列上算」必须逐字段一致。

    构造：A 稳步上涨、B 先涨后跌、C 横盘。T = 59。
    """
    bars_full = {
        "sh510300": make_bars(ramp(80, 1.0, 0.002)),
        "sh510500": make_bars(ramp(60, 2.0, 0.001) + [1.5] * 20),
        "sh588000": make_bars(flat(80, 3.0)),
    }
    t_index = 59

    full = R.momentum_rank(bars_full, 20, as_of_index=t_index)

    truncated = {code: bars[: t_index + 1] for code, bars in bars_full.items()}
    cut = R.momentum_rank(truncated, 20)

    assert full["ranked"] == cut["ranked"], "截断前后排名必须完全一致（否则用到了未来数据）"
    assert full["as_of"] == cut["as_of"]
    assert full["missing"] == cut["missing"]

    # 手算核对 sh510300：close[59] = 1.0 + 0.002*59 = 1.118；close[39] = 1.0 + 0.002*39 = 1.078
    # score = 1.118 / 1.078 - 1 = 0.040000/1.078 = 0.037105751391465...
    a = next(item for item in full["ranked"] if item["code"] == "sh510300")
    assert a["score"] == pytest.approx(1.118 / 1.078 - 1.0, rel=0, abs=1e-9)
    assert a["close"] == pytest.approx(1.118)


def test_no_lookahead_future_bars_mutation():
    """把 T 之后的 bar 全部改成极端值，T 日信号不得变化。"""
    base = {
        "sh510300": make_bars(ramp(60, 1.0, 0.003)),
        "sh510500": make_bars(ramp(60, 5.0, -0.002)),
    }
    t_index = 40
    before = R.momentum_rank(base, 20, as_of_index=t_index)

    poisoned = {}
    for code, bars in base.items():
        future = []
        for bar in bars[: t_index + 1]:
            future.append(dict(bar))
        for bar in bars[t_index + 1:]:
            clone = dict(bar)
            clone["c"] = 999.0          # 未来极端价
            clone["h"] = 1000.0
            clone["l"] = 0.001
            future.append(clone)
        poisoned[code] = future

    after = R.momentum_rank(poisoned, 20, as_of_index=t_index)
    assert before["ranked"] == after["ranked"], "T 之后的 bar 变化影响了 T 日信号 → 未来函数"


def test_backtest_trades_execute_at_next_day_close():
    """成交价必须是 T+1 收盘：构造单边上涨，首个调仓日 T=lookback，成交日 = T+1。

    30 根单调上涨（每天 +1%），lookback=5、top_n=1、无成本：
    - 信号日索引 5，成交日索引 6；
    - 首笔交易的 ``d`` 必须等于 bars[6] 的日期；
    - 若错误地按 T 日成交，日期会是 bars[5]。
    """
    closes = ramp(30, 1.0, 0.01)
    bars = {"sh510300": make_bars(closes)}
    result = R.backtest(bars, lookback=5, top_n=1, cost_bps=0.0, rebalance_days=100,
                        buffer_slots=0)
    assert result["trades"], "上涨行情应当产生一笔建仓"
    first_trade = result["trades"][0]
    assert first_trade["d"] == bars["sh510300"][6]["d"], \
        f"成交日应为 T+1（{bars['sh510300'][6]['d']}），实际 {first_trade['d']}"


# ---------------------------------------------------------------------------
# 2. 绝对动量
# ---------------------------------------------------------------------------

def test_negative_absolute_momentum_not_selected():
    """全部标的动量为负 → 目标持仓为空（宁可空仓，不硬买下跌品）。"""
    ranked = [
        {"code": "A", "name": "A", "score": -0.01, "close": 1.0},
        {"code": "B", "name": "B", "score": -0.05, "close": 1.0},
        {"code": "C", "name": "C", "score": 0.0, "close": 1.0},
    ]
    assert R.select_holdings(ranked, [], top_n=2) == []
    # score 恰为 0 也不算「正动量」
    assert R.select_holdings([{"code": "C", "score": 0.0}], [], top_n=2) == []


def test_absolute_momentum_filters_mixed():
    """混合场景：只取正动量，负动量即便排名靠后也不补位。"""
    ranked = [
        {"code": "A", "score": 0.10},
        {"code": "B", "score": 0.05},
        {"code": "C", "score": -0.02},
    ]
    assert R.select_holdings(ranked, [], top_n=2) == ["A", "B"]
    # top_n=3 但只有 2 个正动量 → 只买 2 个，不用 C 填满
    assert R.select_holdings(ranked, [], top_n=3) == ["A", "B"]


# ---------------------------------------------------------------------------
# 3. 换仓缓冲
# ---------------------------------------------------------------------------

def test_buffer_keeps_holding_that_slipped_out_of_top_n():
    """A 从第 1 名掉到第 3 名（在 top_n+1=3 的缓冲内）→ 保留 A，不换仓。"""
    ranked = [
        {"code": "B", "score": 0.09},
        {"code": "C", "score": 0.08},
        {"code": "A", "score": 0.07},   # 名次 2（0-based），< 2+1 = 3 → 在缓冲区内
    ]
    # 无缓冲：取前 2 → B、C，A 被换出
    assert R.select_holdings(ranked, ["A"], top_n=2, buffer_slots=0) == ["B", "C"]
    # 有缓冲：A 保留，再用 B 填满 → 持仓仍是 2 个（缓冲只降换手，不放大仓位）
    picked = R.select_holdings(ranked, ["A"], top_n=2, buffer_slots=1)
    assert set(picked) == {"A", "B"}, picked
    assert len(picked) == 2


def test_buffer_does_not_keep_holding_outside_buffer_zone():
    """A 掉到第 4 名（超出 top_n+1=3）→ 必须换出。"""
    ranked = [
        {"code": "B", "score": 0.09},
        {"code": "C", "score": 0.08},
        {"code": "D", "score": 0.07},
        {"code": "A", "score": 0.06},   # 名次 3，>= 3 → 超出缓冲区
    ]
    assert R.select_holdings(ranked, ["A"], top_n=2, buffer_slots=1) == ["B", "C"]


def test_buffer_does_not_exceed_top_n():
    """即使持有两只都在缓冲区内，持仓数也不得超过 top_n。"""
    ranked = [
        {"code": "A", "score": 0.09},
        {"code": "B", "score": 0.08},
        {"code": "C", "score": 0.07},
    ]
    picked = R.select_holdings(ranked, ["A", "B"], top_n=2, buffer_slots=2)
    assert picked == ["A", "B"]
    picked_one = R.select_holdings(ranked, ["B", "C"], top_n=1, buffer_slots=2)
    assert len(picked_one) == 1


# ---------------------------------------------------------------------------
# 4. 成本扣减（手算断言）
# ---------------------------------------------------------------------------

def test_turnover_hand_calculation():
    """换手率口径：``0.5 * Σ|w_target - w_current|``（等权）。

    手算：
    - 全换（{A,B} → {C,D}）：w 各 0.5，Σ|Δ| = 4×0.5 = 2 → turnover = 1.0
    - 换一半（{A,B} → {A,C}）：Σ|Δ| = |0.5-0.5| + |0-0.5| + |0.5-0| = 1.0 → 0.5
    - 不变（{A,B} → {A,B}）：0
    - 清仓（{A} → {}）：Σ|Δ| = |0-1.0| = 1.0 → 0.5
    """
    assert R._turnover_of(["A", "B"], ["C", "D"]) == pytest.approx(1.0)
    assert R._turnover_of(["A", "B"], ["A", "C"]) == pytest.approx(0.5)
    assert R._turnover_of(["A", "B"], ["A", "B"]) == pytest.approx(0.0)
    assert R._turnover_of(["A"], []) == pytest.approx(0.5)
    assert R._turnover_of([], ["A"]) == pytest.approx(0.5)


def test_cost_deduction_is_turnover_times_bps():
    """首笔建仓成本 = turnover(1.0 的一半) × cost_bps × 市值。

    空仓 → {A}：turnover = 0.5；cost_bps=10 → 成本率 = 10/10000 = 0.001；
    建仓时市值 = 1.0（初始净值）→ cost = 0.5 × 0.001 × 1.0 = 0.0005。
    """
    bars = {"sh510300": make_bars(ramp(30, 1.0, 0.01))}
    result = R.backtest(bars, lookback=5, top_n=1, cost_bps=10.0,
                        rebalance_days=100, buffer_slots=0)
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["turnover"] == pytest.approx(0.5), trade
    assert trade["cost"] == pytest.approx(0.5 * 10.0 / 10000.0 * 1.0, rel=0, abs=1e-12), trade
    # 净值曲线必须体现出这笔成本：最后一个交易日的净值 < 无成本情形
    no_cost = R.backtest(bars, lookback=5, top_n=1, cost_bps=0.0,
                         rebalance_days=100, buffer_slots=0)
    assert result["equity"][-1]["nav"] < no_cost["equity"][-1]["nav"]


def test_no_trade_when_holdings_unchanged():
    """组合未变化时不产生交易（"只在组合变化时交易"）。"""
    bars = {"sh510300": make_bars(ramp(40, 1.0, 0.01))}
    result = R.backtest(bars, lookback=5, top_n=1, cost_bps=5.0,
                        rebalance_days=3, buffer_slots=1)
    # 单调上涨且只有一只标的 → 首笔建仓后不再换仓
    assert len(result["trades"]) == 1, result["trades"]


# ---------------------------------------------------------------------------
# 5. 样本纪律
# ---------------------------------------------------------------------------

def test_short_sample_gives_no_annualized_metrics():
    """样本 <252 交易日 → 年化四件套全 None + reason 非空（绝不外推）。"""
    bars = {"sh510300": make_bars(ramp(60, 1.0, 0.01))}
    result = R.backtest(bars, lookback=10, top_n=1)
    assert result["samples"] < R.TRADING_DAYS
    assert result["annual_ok"] is False
    metrics = result["metrics"]
    for key in ("annual_return", "sharpe", "max_drawdown", "calmar", "volatility"):
        assert metrics[key] is None, f"{key} 在样本不足一年时必须为 None，实际 {metrics[key]}"
    assert result["reason"], "样本不足必须给出 reason"
    assert "不足一年" in result["reason"]
    # total_return 是已实现收益，允许给出（不是外推）
    assert metrics["total_return"] is not None


def test_long_sample_provides_annualized_metrics():
    """样本 ≥252 交易日 → 年化指标必须给出（不能一律 None）。"""
    bars = {"sh510300": make_bars(ramp(320, 1.0, 0.001))}
    result = R.backtest(bars, lookback=20, top_n=1)
    assert result["samples"] >= R.TRADING_DAYS
    assert result["annual_ok"] is True
    metrics = result["metrics"]
    assert metrics["annual_return"] is not None
    assert metrics["max_drawdown"] is not None
    assert metrics["volatility"] is not None
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# 6. 参数平原
# ---------------------------------------------------------------------------

def test_parameter_plateau_returns_every_cell():
    """参数平原必须返回全网格，且每格都带 annual_ok / samples（不许只回最优）。"""
    bars = {
        "sh510300": make_bars(ramp(300, 1.0, 0.002)),
        "sh510500": make_bars(ramp(300, 2.0, -0.001)),
    }
    lookbacks = [5, 10, 20]
    top_ns = [1, 2]
    plateau = R.parameter_plateau(bars, lookbacks, top_ns, cost_bps=5.0, rebalance_days=20)

    assert len(plateau["cells"]) == len(lookbacks) * len(top_ns) == 6
    assert plateau["lookbacks"] == lookbacks
    assert plateau["top_ns"] == top_ns
    assert plateau["note"]

    keys = {"lookback", "top_n", "total_return", "annual_return", "max_drawdown",
            "vs_equal_weight", "samples", "annual_ok", "reason"}
    seen_pairs = set()
    for cell in plateau["cells"]:
        assert keys <= set(cell), cell
        assert isinstance(cell["annual_ok"], bool)
        assert isinstance(cell["samples"], int)
        seen_pairs.add((cell["lookback"], cell["top_n"]))
    assert seen_pairs == {(lb, tn) for lb in lookbacks for tn in top_ns}


# ---------------------------------------------------------------------------
# 7. 缺数据
# ---------------------------------------------------------------------------

def test_insufficient_bars_excluded_from_ranking():
    """某只 bar 数不足 lookback → 进 missing，不进 ranked，不抛异常。"""
    bars = {
        "sh510300": make_bars(ramp(60, 1.0, 0.005)),   # 够
        "sh510500": make_bars(ramp(10, 2.0, 0.005)),   # 只有 10 根 < lookback+1
    }
    rank = R.momentum_rank(bars, lookback=20)
    codes = [item["code"] for item in rank["ranked"]]
    assert codes == ["sh510300"], codes
    missing_codes = [item["code"] for item in rank["missing"]]
    assert missing_codes == ["sh510500"]
    reason = rank["missing"][0]["reason"]
    assert "样本不足" in reason and "21" in reason, reason


def test_missing_close_price_excluded():
    """收盘价缺失（None / 非正）→ 该标的不进 ranked，且 reason 可读。"""
    bars = {
        "sh510300": make_bars(ramp(40, 1.0, 0.01)),
        "sh510500": make_bars(ramp(40, 1.0, 0.01)),
    }
    bars["sh510500"][-1]["c"] = 0.0          # 最新一根收盘为 0（异常数据）
    rank = R.momentum_rank(bars, lookback=10)
    assert [item["code"] for item in rank["ranked"]] == ["sh510300"]
    missing = {item["code"]: item["reason"] for item in rank["missing"]}
    assert "sh510500" in missing and "收盘价" in missing["sh510500"]


def test_uneven_calendar_uses_intersection_and_warns():
    """两标的交易日不完全一致 → 取交集回测，并给出缺口告警（不前值填充）。"""
    a = make_bars(ramp(60, 1.0, 0.005))
    b = make_bars(ramp(60, 2.0, 0.005))
    b = [bar for bar in b if bar["d"] != a[30]["d"]]   # B 缺一天
    result = R.backtest({"sh510300": a, "sh510500": b}, lookback=10, top_n=1)
    assert result["samples"] > 0
    text = " ".join(str(item.get("reason", "")) for item in result["warnings"])
    assert "跳过" in text or "共同交易日" in text, result["warnings"]


# ---------------------------------------------------------------------------
# 8. 空输入 / 极端输入
# ---------------------------------------------------------------------------

def test_empty_input_returns_structure_without_fake_zero():
    """空输入：结构完整、reason 非空、total_return 必须是 None（不是 0）。"""
    result = R.backtest({}, lookback=20)
    assert result["equity"] == []
    assert result["trades"] == []
    assert result["samples"] == 0
    assert result["annual_ok"] is False
    assert result["reason"]
    metrics = result["metrics"]
    assert metrics["total_return"] is None, "空输入不得返回 0 冒充收益"
    for key in ("annual_return", "max_drawdown", "sharpe", "calmar", "volatility"):
        assert metrics[key] is None


def test_empty_bars_and_short_series():
    """空列表 / 只有 1 根 → 不抛异常，返回带 reason 的空结果。"""
    assert R.momentum_rank({}, 20)["ranked"] == []
    rank_empty = R.momentum_rank({"sh510300": []}, 20)
    assert rank_empty["ranked"] == []
    assert rank_empty["missing"][0]["code"] == "sh510300"

    single = R.backtest({"sh510300": make_bars([1.0])}, lookback=20)
    assert single["samples"] == 0
    assert single["reason"]

    # 参数平原在空数据下也要有结构（cells 全为格，每格 annual_ok=False）
    plateau = R.parameter_plateau({}, [10, 20], [1, 2])
    assert len(plateau["cells"]) == 4
    assert all(cell["annual_ok"] is False for cell in plateau["cells"])
    assert all(cell["reason"] for cell in plateau["cells"])


def test_select_holdings_edge_cases():
    """top_n=0 / 空 ranked / 空 held 的边界。"""
    assert R.select_holdings([{"code": "A", "score": 0.1}], ["A"], top_n=0) == []
    assert R.select_holdings([], ["A"], top_n=2) == []
    assert R.select_holdings([{"code": "A", "score": 0.1}], [], top_n=2) == ["A"]


def test_input_not_mutated():
    """纯函数：调用前后入参不得被修改（防止调用方数据被悄悄排序/截断）。"""
    bars = {"sh510300": make_bars(ramp(40, 1.0, 0.01))}
    snapshot = [dict(bar) for bar in bars["sh510300"]]
    ranked = [{"code": "A", "score": 0.1}, {"code": "B", "score": 0.2}]
    ranked_snapshot = [dict(item) for item in ranked]
    held = ["A"]
    held_snapshot = list(held)

    R.momentum_rank(bars, 10)
    R.select_holdings(ranked, held, top_n=2, buffer_slots=1)
    R.backtest(bars, lookback=10, top_n=1)

    assert bars["sh510300"] == snapshot
    assert ranked == ranked_snapshot
    assert held == held_snapshot
