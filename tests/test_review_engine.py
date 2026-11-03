"""打板复盘引擎：FIFO 配对、费用分摊、止损止盈执行判定。

这些用例用的是构造数据（不涉及任何真实交易者），守的是「算法不能骗人」：
- 卖出必须优先冲抵最早买入批次；
- 费用按数量摊到每个平仓回合，不能重复计；
- 有计划但没触发的离场，必须标成「计划未触发」而不是「没设计划」。
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.review.build_review import classify_exit, match_round_trips  # noqa: E402


def _df(rows):
    cols = ["trade_date", "trade_time", "code", "name", "side", "price", "qty", "amount",
            "commission", "stamp_tax", "transfer_fee", "other_fee", "fee_total",
            "order_id", "board_type", "plan_stop", "plan_target", "note"]
    return pd.DataFrame(rows, columns=cols)


def test_fifo_matches_earliest_lot_first():
    trades = _df([
        ("2026-10-30", "09:31:00", "sz300001", "A", "buy", 10.0, 1000, 10000, None, None, None, None, 5, "", "首板", 9.5, 12.0, ""),
        ("2026-10-30", "10:00:00", "sz300001", "A", "buy", 11.0, 1000, 11000, None, None, None, None, 5, "", "首板", 9.5, 12.0, ""),
        ("2026-10-30", "14:00:00", "sz300001", "A", "sell", 12.0, 1500, 18000, None, None, None, None, 9, "", "", None, None, ""),
    ])
    trips, opens = match_round_trips(trades)
    assert len(trips) == 2
    first, second = trips[0], trips[1]
    assert first.entry_price == 10.0 and first.qty == 1000      # 先冲最早批次
    assert second.entry_price == 11.0 and second.qty == 500     # 剩余 500 股来自第二批
    # 第二批还剩 500 股未卖 → 必须作为持仓留出，不能凭空消失
    assert len(opens) == 1 and opens[0].qty == 500 and opens[0].entry_price == 11.0


def test_fees_are_allocated_not_duplicated():
    trades = _df([
        ("2026-10-30", "09:31:00", "sh600001", "B", "buy", 20.0, 2000, 40000, None, None, None, None, 10, "", "首板", 18.0, 24.0, ""),
        ("2026-10-30", "14:00:00", "sh600001", "B", "sell", 21.0, 1000, 21000, None, None, None, None, 6, "", "", None, None, ""),
        ("2026-10-31", "09:40:00", "sh600001", "B", "sell", 22.0, 1000, 22000, None, None, None, None, 7, "", "", None, None, ""),
    ])
    trips, _ = match_round_trips(trades)
    assert len(trips) == 2
    # 买入费用 10 按 1000/2000 摊到两笔，各 5；卖出费各计各的
    assert trips[0].fee == pytest.approx(5 + 6, abs=0.01)
    assert trips[1].fee == pytest.approx(5 + 7, abs=0.01)
    assert trips[0].gross == pytest.approx(1000, abs=0.01)


def test_open_position_is_not_counted_as_closed():
    trades = _df([
        ("2026-10-30", "09:31:00", "sz300002", "C", "buy", 9.0, 500, 4500, None, None, None, None, 5, "", "首板", 8.5, 11.0, ""),
    ])
    trips, opens = match_round_trips(trades)
    assert trips == [] and len(opens) == 1
    assert opens[0].status == "open" and opens[0].net == 0


def _trip(**kw):
    from scripts.review.build_review import RoundTrip
    base = dict(code="sz300001", name="A", entry_date="2026-10-30", entry_time="09:31:00",
                entry_price=10.0, exit_date="2026-10-30", exit_time="14:00:00",
                exit_price=11.0, qty=1000, buy_amount=10000, sell_amount=11000, fee=10,
                board_type="首板", plan_stop=None, plan_target=None)
    base.update(kw)
    return RoundTrip(**base)


def test_planned_but_not_triggered_is_not_labelled_unplanned():
    kind, _ = classify_exit(_trip(plan_stop=9.5, plan_target=12.0, exit_price=11.0))
    assert "区间离场" in kind
    assert "未设计划" not in kind


def test_stop_and_target_triggers():
    assert "止损（按计划触发）" in classify_exit(_trip(plan_stop=10.5, exit_price=10.2))[0]
    assert "止盈（按计划触发）" in classify_exit(_trip(plan_target=10.8, exit_price=11.0))[0]


def test_unplanned_exit_falls_back_to_result_rules():
    assert "被动止损（未设计划）" in classify_exit(_trip(exit_price=9.4))[0]   # -6%
    assert "止盈（未设计划）" in classify_exit(_trip(exit_price=11.0))[0]     # +10%
    assert "其他离场" in classify_exit(_trip(exit_price=10.1))[0]            # +1%
