"""龙虎榜聚合层：标的/席位/题材三视角 + 合力判定 + 通道合并。

这一层的价值是「重排」，所以用例的重心是**语义正确**而不是跑通：

1. 合力 / 对峙的边界要可解释——它是给读者判断「这个方向靠不靠得住」用的；
2. 通道汇总（机构专用 / 北向资金）不能当成一个交易者算集中度——那是误读；
3. 按 |净额| 加权的主因，不能被大量小额记录带偏；
4. 数据缺失时给 None，不给 0。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import lhb_insight as ins  # noqa: E402


def rec(code, name, seat, seat_type="institution", net=1e7, motive="题材炒作",
        board=None, first_seal=None, opens=None, limit=False, industry=None):
    return {
        "code": code, "name": name, "seat": seat, "seat_type": seat_type,
        "seat_alias": "机构专用" if seat_type == "institution" else (
            "北向资金" if seat_type == "northbound" else seat),
        "seat_style": "", "alias_source": "交易所披露",
        "net": net, "net_wan": round(net / 1e4, 1),
        "side": "buy" if net >= 0 else "sell",
        "motive": {"primary": motive, "scores": {}, "evidence": {}},
        "price_context": {"board_count": board, "first_seal": first_seal,
                          "open_times": opens, "is_limit_up": limit,
                          "inference": "seal-time" if first_seal else "day-level"},
        "confidence": 0.6, "stock_chg": 5.0,
        "seat_role": "主导方", "seat_share": 0.4,
    }


def ctx(zt=None):
    return {"zt": zt or {}}


# ---------------------------------------------------------------------------
# 标的视角
# ---------------------------------------------------------------------------
def test_alignment_consensus_buy():
    recs = [rec("sz000001", "甲", "机构专用", net=5e7),
            rec("sz000001", "甲", "某营业部", "other", net=3e7)]
    out = ins.by_stock(recs, ctx())
    assert out[0]["alignment"] == "合力做多"
    assert out[0]["net"] == pytest.approx(8e7)


def test_alignment_consensus_sell():
    recs = [rec("sz000001", "甲", "机构专用", net=-5e7),
            rec("sz000001", "甲", "某营业部", "other", net=-3e7)]
    assert ins.by_stock(recs, ctx())[0]["alignment"] == "合力出货"


def test_alignment_conflict_needs_both_sides_material():
    """买 6 卖 4 是「对峙」，不是「合力做多」——双方都有实质金额。"""
    recs = [rec("sz000001", "甲", "机构专用", net=6e7),
            rec("sz000001", "甲", "某营业部", "other", net=-4e7)]
    out = ins.by_stock(recs, ctx())[0]
    assert out["alignment"] == "多空对峙"
    assert out["buy_share"] == pytest.approx(0.6)


def test_stock_aggregates_seat_types_and_seal_quality():
    recs = [rec("sz000001", "甲", "机构专用", "institution", net=5e7, board=3,
                first_seal="09:31", opens=0, limit=True),
            rec("sz000001", "甲", "深股通专用", "northbound", net=2e7)]
    out = ins.by_stock(recs, ctx({"sz000001": {"industry": "半导体"}}))[0]
    assert out["industry"] == "半导体"
    assert out["seat_types"] == {"机构": 1, "北向": 1}
    assert out["seal"]["level"] == "strong"
    assert out["board_count"] == 3
    assert "3 连板" in out["seal"]["label"]


def test_seal_quality_flags_weak_board():
    recs = [rec("sz000001", "甲", "机构专用", net=5e7, board=2, opens=4, limit=True)]
    out = ins.by_stock(recs, ctx())[0]
    assert out["seal"]["level"] == "weak"
    assert "封板弱" in out["seal"]["label"]


def test_seal_quality_none_when_not_limit_up():
    recs = [rec("sz000001", "甲", "机构专用", net=5e7, limit=False)]
    assert ins.by_stock(recs, ctx())[0]["seal"]["level"] == "none"


def test_stock_motive_is_weighted_by_amount_not_count():
    """大额记录的动因应该压过一堆小额记录。"""
    recs = [rec("sz000001", "甲", "某营业部", "other", net=9e7, motive="题材炒作")]
    recs += [rec("sz000001", "甲", f"营业部{i}", "other", net=1e6, motive="市场情绪")
             for i in range(5)]
    out = ins.by_stock(recs, ctx())[0]
    assert out["motive"]["primary"] == "题材炒作"
    assert out["motive"]["share"] > 0.9


# ---------------------------------------------------------------------------
# 席位视角
# ---------------------------------------------------------------------------
def test_channel_is_not_treated_as_single_trader():
    """机构专用 / 北向资金是通道汇总，不能算「集中狙击」。"""
    recs = [rec("sz000001", "甲", "机构专用", "institution", net=5e7),
            rec("sz000002", "乙", "机构专用", "institution", net=5e7)]
    out = ins.by_seat(recs)
    assert len(out) == 1
    assert out[0]["is_channel"] is True
    assert out[0]["focus"] == "通道汇总"
    assert out[0]["top_share"] is None


def test_northbound_merges_shanghai_and_shenzhen():
    """沪股通与深股通是同一个通道的两个入口，不能显示成两行「北向资金」。"""
    recs = [rec("sz000001", "甲", "深股通专用", "northbound", net=3e7),
            rec("sh600000", "乙", "沪股通专用", "northbound", net=2e7)]
    out = ins.by_seat(recs)
    assert len(out) == 1
    assert out[0]["seat"] == "北向资金"
    assert out[0]["trades"] == 2
    assert sorted(out[0]["seat_raw"]) == ["沪股通专用", "深股通专用"]


def test_named_trader_gets_focus_metric():
    recs = [rec("sz000001", "甲", "某证券营业部", "other", net=9e7),
            rec("sz000002", "乙", "某证券营业部", "other", net=1e7)]
    out = ins.by_seat(recs)[0]
    assert out["is_channel"] is False
    assert out["focus"] == "集中狙击"
    assert out["top_share"] == pytest.approx(0.9)
    assert out["stocks"] == 2


def test_trader_diversified_focus():
    recs = [rec(f"sz00000{i}", f"票{i}", "某证券营业部", "other", net=1e7) for i in range(4)]
    out = ins.by_seat(recs)[0]
    assert out["focus"] == "分散试仓"
    assert out["top_share"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# 题材视角
# ---------------------------------------------------------------------------
def test_industry_groups_unlisted_stocks_explicitly():
    """不在涨停池的票归入「未归入涨停池」——这是事实，不是缺失。"""
    recs = [rec("sz000001", "甲", "机构专用", net=5e7),
            rec("sz000002", "乙", "机构专用", net=-3e7)]
    stocks = ins.by_stock(recs, ctx({"sz000001": {"industry": "半导体"}}))
    out = ins.by_industry(recs, stocks)
    names = {x["industry"]: x for x in out}
    assert "半导体" in names
    assert "未归入涨停池" in names
    assert names["半导体"]["net_wan"] == pytest.approx(5000.0)


def test_industry_requires_raw_context():
    """传精简版 context（没有 zt）时不应把行业猜出来，全部落到未归入。"""
    recs = [rec("sz000001", "甲", "机构专用", net=5e7)]
    stocks = ins.by_stock(recs, ctx({"sz000001": {"industry": "半导体"}}))
    agg = ins.aggregate({"records": recs, "context": {"zt_count": 10}}, ctx=None)
    assert all(x["industry"] != "半导体" for x in agg["by_industry"])


# ---------------------------------------------------------------------------
# 矩阵与要点
# ---------------------------------------------------------------------------
def test_motive_matrix_splits_by_side():
    recs = [rec("sz000001", "甲", "机构专用", net=5e7, motive="题材炒作"),
            rec("sz000002", "乙", "机构专用", net=-2e7, motive="题材炒作")]
    cell = [m for m in ins.motive_matrix(recs) if m["motive"] == "题材炒作"][0]
    assert cell["buy"] == 1 and cell["sell"] == 1 and cell["total"] == 2


def test_highlights_are_ranked_statements_not_causal_claims():
    recs = [rec("sz000001", "甲", "机构专用", net=9e7),
            rec("sz000002", "乙", "机构专用", net=-3e7)]
    report = {"records": recs, "context": {"zt_count": 100, "zb_count": 20,
                                           "dt_count": 3, "seal_rate": 83.3, "max_board": 5}}
    stocks = ins.by_stock(recs, ctx())
    seats = ins.by_seat(recs)
    out = ins.highlights(report, stocks, seats)
    kinds = [h["kind"] for h in out]
    assert "largest" in kinds
    assert "context" in kinds
    assert all(h.get("title") for h in out)


def test_aggregate_shape_and_notes():
    recs = [rec("sz000001", "甲", "机构专用", net=5e7)]
    agg = ins.aggregate({"records": recs, "context": {"zt_count": 10}}, ctx({"sz000001": {"industry": "半导体"}}))
    for key in ("by_stock", "by_seat", "by_industry", "motive_matrix",
                "motive_by_seat_type", "highlights", "totals", "notes"):
        assert key in agg
    assert agg["totals"]["records"] == 1
    assert agg["notes"]


def test_empty_records_do_not_crash():
    agg = ins.aggregate({"records": [], "context": {}}, ctx())
    assert agg["totals"]["records"] == 0
    assert agg["by_stock"] == []
    assert agg["highlights"] == []
