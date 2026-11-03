"""买卖因素库：规则命中、事实组装、归因接线。

这些用例守的是「为什么买/为什么卖」能不能被信任：

1. **判定式必须严格**——事实缺失不命中（「不知道」≠「不满足」），
   配置引用了不存在的事实也不允许静默通过；
2. **因素排序按权重**——同一只票可同时具备买入理由与兑现条件，
   主因素选择必须是确定性的（weight 降序），否则页面每次刷新顺序都会变；
3. **接线完整性**——attribute() 返回 factors 字段、卖出触发来自因素命中、
   买入侧时机文案带因素链；insight 层能按因素聚合统计。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import lhb_factors as fx  # noqa: E402
from server import lhb_review as lhb  # noqa: E402
from server import lhb_insight as insight  # noqa: E402

CFG = lhb.config(force=True)


# ---------------------------------------------------------------------------
# 判定式
# ---------------------------------------------------------------------------
def test_match_numeric_thresholds():
    rule = {"id": "t", "label": "换手高", "weight": 0.5, "when": {"turnover_gte": 20}}
    ok, ev = fx.match(rule, {"turnover": 25.0})
    assert ok
    assert any("换手" in e for e in ev)
    assert not fx.match(rule, {"turnover": 12.0})[0]


def test_match_time_string_comparison():
    """时间是零填充 HH:MM，字典序必须等价于时间序。"""
    rule = {"id": "t", "label": "早封", "weight": 0.8, "when": {"first_seal_lte": "10:00"}}
    assert fx.match(rule, {"first_seal": "09:32"})[0]
    assert not fx.match(rule, {"first_seal": "13:45"})[0]


def test_match_bool_requires_exact():
    rule = {"id": "t", "label": "涨停", "weight": 0.5, "when": {"is_limit_up": True}}
    assert fx.match(rule, {"is_limit_up": True})[0]
    assert not fx.match(rule, {"is_limit_up": False})[0]


def test_match_multiple_keys_are_and():
    rule = {"id": "t", "label": "主线", "weight": 0.7,
            "when": {"peers_gte": 3, "industry_rank_lte": 3}}
    assert fx.match(rule, {"peers": 5, "industry_rank": 2})[0]
    assert not fx.match(rule, {"peers": 5, "industry_rank": 7})[0]
    assert not fx.match(rule, {"peers": 1, "industry_rank": 2})[0]


def test_match_missing_fact_never_hits():
    """事实缺失（None / 不存在）不命中——「不知道」不等于「不满足」。"""
    rule = {"id": "t", "label": "x", "weight": 0.5, "when": {"turnover_gte": 10}}
    assert not fx.match(rule, {})[0]
    assert not fx.match(rule, {"turnover": None})[0]


def test_match_unknown_fact_key_fails_closed():
    """配置引用了不存在的事实名：不命中，绝不静默通过。"""
    rule = {"id": "t", "label": "x", "weight": 0.5, "when": {"no_such_fact_gte": 1}}
    assert not fx.match(rule, {"turnover": 99})[0]


def test_match_empty_when_never_hits():
    assert not fx.match({"id": "t", "label": "x", "weight": 1, "when": {}}, {"turnover": 99})[0]


def test_match_incomparable_values_fail_closed():
    rule = {"id": "t", "label": "x", "weight": 0.5, "when": {"turnover_gte": 10}}
    assert not fx.match(rule, {"turnover": "abc"})[0]


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------
def test_evaluate_sorts_by_weight_and_picks_primary():
    facts = {"is_limit_up": True, "board_count": 3, "turnover": 25.0}
    out = fx.evaluate(facts, CFG)
    weights = [f["weight"] for f in out["buy"]]
    assert weights == sorted(weights, reverse=True)
    assert out["primary_buy"] is not None
    assert out["primary_buy"]["id"] == out["buy"][0]["id"]


def test_evaluate_both_sides_can_hit_simultaneously():
    """涨停 + 高换手：买入理由与兑现条件同时成立——这正是分歧的来源。"""
    facts = {"is_limit_up": True, "open_times": 2, "turnover": 25.0}
    out = fx.evaluate(facts, CFG)
    assert out["buy"] and out["sell"]
    # balance = 最强买入因素 - 最强卖出因素
    assert out["balance"] == round(out["primary_buy"]["weight"] - out["primary_sell"]["weight"], 3)


def test_evaluate_empty_facts_hits_nothing():
    out = fx.evaluate({}, CFG)
    assert out["buy"] == [] and out["sell"] == []
    assert out["primary_buy"] is None and out["primary_sell"] is None
    assert out["balance"] == 0.0


def test_default_factors_cover_buy_and_sell():
    """默认规则库两侧都必须非空，且每条都有 when/label/weight。"""
    for side in ("buy", "sell"):
        rules = fx.DEFAULT_FACTORS[side]
        assert rules, f"默认 {side} 因素为空"
        for rule in rules:
            assert rule["when"], f"{rule['id']} 缺 when"
            assert rule["label"]
            assert 0 < rule["weight"] <= 1


def test_config_file_factors_are_loaded_and_override():
    """data/lhb_config.json 的 factors 段要能读到（深合并后仍保留自定义规则）。"""
    factors_cfg = CFG.get("factors") or {}
    assert factors_cfg.get("buy") and factors_cfg.get("sell")
    ids = {r["id"] for r in factors_cfg["sell"]}
    assert "open_window" in ids and "seal_cash" in ids


# ---------------------------------------------------------------------------
# 事实组装
# ---------------------------------------------------------------------------
def test_facts_from_computes_vwap_gap():
    price_ctx = {"close": 11.0, "vwap": 10.0, "is_limit_up": True}
    facts = fx.facts_from(price_ctx, {"chg": 10.0, "turnover": 12.0},
                          {"news": 0.5}, {}, "sz000001", net_ratio=6.0)
    assert facts["vwap_gap_pct"] == pytest.approx(10.0)
    assert facts["net_ratio"] == 6.0
    assert facts["news_score"] == 0.5
    assert facts["is_limit_up"] is True


def test_facts_from_peers_and_rank_from_context():
    ctx = {"zt": {"sz000001": {"industry": "软件开发", "turnover": 15.0}},
           "industries": {"软件开发": 8, "广告营销": 9, "出版": 7}}
    facts = fx.facts_from({}, {}, {}, ctx, "sz000001")
    assert facts["peers"] == 8
    assert facts["industry_rank"] == 2  # 广告营销 9 > 软件开发 8
    # 涨停池换手优先于 stock_meta
    assert facts["turnover"] == 15.0


def test_facts_from_missing_everything_is_none_not_zero():
    facts = fx.facts_from({}, {}, {}, {}, "sz000001")
    assert facts["turnover"] is None
    assert facts["peers"] == 0  # 无行业 → 0 家同行业涨停是事实
    assert facts["industry_rank"] is None


# ---------------------------------------------------------------------------
# attribute() 接线
# ---------------------------------------------------------------------------
def _row(seat, buy=0.0, sell=0.0):
    return {"OPERATEDEPT_NAME": seat, "BUY": buy, "SELL": sell, "NET": buy - sell}


def _meta(**over):
    base = {"code": "sz000001", "name": "测试股", "price": 10.0, "chg": 10.0,
            "accum_amount": 1e9, "accum_volume": 1e8, "turnover": 12.0,
            "reason": "日涨幅偏离值达到7%的前5只证券"}
    base.update(over)
    return base


def _ctx(**over):
    base = {
        "date": "2026-11-02", "available": True, "reason": None,
        "zt_count": 120, "zb_count": 30, "dt_count": 5, "seal_rate": 80.0,
        "max_board": 6, "board_distribution": {1: 90, 2: 20, 6: 1},
        "industries": {"软件开发": 8, "广告营销": 9, "出版": 7},
        "zt": {
            "sz000001": {"board_count": 6, "industry": "软件开发", "open_times": 0,
                         "first_seal": "093015", "last_seal": "093015", "turnover": 12.0},
            "sz000002": {"board_count": 1, "industry": "软件开发", "open_times": 2,
                         "first_seal": "104500", "last_seal": "140000", "turnover": 25.0},
        },
        "zb": {},
    }
    base.update(over)
    return base


def test_attribute_returns_factors_contract():
    out = lhb.attribute("sz000001", "测试股", [_row("机构专用", 5e7)],
                        _meta(), _ctx(), CFG, side="buy", seat_net=5e7)
    fac = out["factors"]
    assert fac["version"] == fx.VERSION
    assert "buy" in fac and "sell" in fac
    assert fac["primary_buy"] is not None  # 早盘封板抢筹必命中（09:30 封板 + 涨停）
    assert any(f["id"] == "seal_early" for f in fac["buy"])
    assert fac["primary_buy"]["evidence"]  # 证据链非空


def test_attribute_buy_timing_mentions_factor_chain():
    out = lhb.attribute("sz000001", "测试股", [_row("机构专用", 5e7)],
                        _meta(), _ctx(), CFG, side="buy", seat_net=5e7)
    assert "买入侧因素链" in out["timing"]
    assert "「" in out["timing"]  # 因素以「label」形式出现在链里


def test_attribute_sell_trigger_comes_from_factor_hits():
    """卖出触发来自因素命中：每条因素要带自己的证据，而不是一句笼统总结。"""
    out = lhb.attribute("sz000002", "测试股", [_row("机构专用", 0, 5e7)],
                        _meta(code="sz000002"), _ctx(), CFG,
                        side="sell", seat_net=-5e7)
    trigger = out["sell_trigger"]
    assert trigger and trigger != "无显著触发特征（证据不足）"
    # sz000002 炸板 2 次 + 换手 25% → 开板兑现窗口 / 换手过高出货 必命中
    assert "开板兑现窗口" in trigger or "板上兑现" in trigger
    assert "（" in trigger  # 证据以括号形式跟在因素名后


def test_attribute_logic_mentions_factor_summary():
    out = lhb.attribute("sz000001", "测试股", [_row("机构专用", 5e7)],
                        _meta(), _ctx(), CFG, side="buy", seat_net=5e7)
    assert "买入侧最强因素" in out["logic"]


# ---------------------------------------------------------------------------
# insight 聚合
# ---------------------------------------------------------------------------
def test_factor_stats_counts_hits_and_amounts():
    recs = [
        {"net": 5e7, "factors": {"buy": [{"id": "seal_early", "label": "早盘封板抢筹",
                                          "weight": 0.8, "evidence": []}]}},
        {"net": -3e7, "factors": {"sell": [{"id": "open_window", "label": "开板兑现窗口",
                                            "weight": 0.7, "evidence": []}]}},
        {"net": 2e7, "factors": {"buy": [{"id": "seal_early", "label": "早盘封板抢筹",
                                          "weight": 0.8, "evidence": []}]}},
    ]
    stats = insight.factor_stats(recs)
    assert stats["buy"][0]["count"] == 2
    assert stats["buy"][0]["net_wan"] == pytest.approx(7000.0)
    assert stats["sell"][0]["id"] == "open_window"


def test_factor_stats_filters_by_record_side():
    """统计口径按方向过滤：买入记录上命中的卖出因素不能混进「为什么卖」。"""
    recs = [{
        "net": 5e7,  # 买入记录
        "factors": {
            "buy": [{"id": "seal_early", "label": "早盘封板抢筹", "weight": 0.8, "evidence": []}],
            "sell": [{"id": "seal_cash", "label": "板上兑现", "weight": 0.6, "evidence": []}],
        },
    }]
    stats = insight.factor_stats(recs)
    assert [x["id"] for x in stats["buy"]] == ["seal_early"]
    assert stats["sell"] == []


def test_factor_stats_empty_records():
    assert insight.factor_stats([]) == {"buy": [], "sell": []}


def test_aggregate_includes_factor_stats():
    report = {"records": [], "context": {}}
    agg = insight.aggregate(report)
    assert "factor_stats" in agg
