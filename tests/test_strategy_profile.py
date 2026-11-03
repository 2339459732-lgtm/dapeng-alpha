"""8 分制策略候选池：评分口径、仓位约束、数据缺失处理。

守的是三件事：
1. **评分严格按文档**：市场/板块/个股/量价/风险各 0~2，总分≥8 且无雷、盈亏比≥2 才入池；
2. **仓位约束取最小项**：首笔金额 = min(意图金额, 单票上限, 现金下限, 风险预算)，
   并且按 100 股整手；不是「想买多少就买多少」；
3. **没有数据就说没有**：全市场快照未就绪时返回 `available:false` + reason，
   不能用空候选列表冒充「今天没有机会」。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import strategy_profile as sp  # noqa: E402

CFG = sp.config(force=True)


# ---------------------------------------------------------------------------
# 基础口径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,name,expected", [
    ("sh600519", "贵州茅台", 9.5),
    ("sz000001", "平安银行", 9.5),
    ("sz300750", "宁德时代", 19.5),
    ("sh688981", "中芯国际", 19.5),
    ("bj430047", "诺思兰德", 29.5),
    ("sz000004", "ST国华", 4.8),
])
def test_limit_threshold(code, name, expected):
    assert sp.limit_threshold(code, name) == expected


def test_config_matches_document():
    """配置必须与 docx 的 config.json 对齐，不能被默认值悄悄覆盖。"""
    for key, value in {"total_capital": 3200, "max_loss_per_trade": 64,
                       "stop_loss_pct": 0.06, "target_pct_min": 0.12,
                       "min_rr": 2.0, "max_trades_per_day": 1}.items():
        assert CFG[key] == value, key
    assert CFG["confirm_start"] == "09:45" and CFG["confirm_end"] == "10:00"
    assert CFG["execution_mode"] == "paper"
    assert CFG["auto_execute"] is False  # 不接券商通道


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def test_market_score_three_tiers():
    rule = CFG["scoring"]["market"]
    assert sp.score_market(True, 40, 5, rule) == 2
    assert sp.score_market(True, 10, 5, rule) == 1      # 指数在上但涨停不足
    assert sp.score_market(False, 40, 5, rule) == 1     # 指数在下但涨停够多
    assert sp.score_market(False, 5, 30, rule) == 0     # 破位 + 跌停潮
    assert sp.score_market(None, 40, 5, rule) == 1      # 指数未知：按涨停条件给 1


def test_sector_score_needs_rank_and_leader():
    rule = CFG["scoring"]["sector"]
    assert sp.score_sector({"rank": 3, "leader_limit_up": True}, rule) == 2
    assert sp.score_sector({"rank": 3, "leader_limit_up": False}, rule) == 1
    assert sp.score_sector({"rank": 9, "leader_limit_up": True}, rule) == 1
    assert sp.score_sector({"rank": 9, "leader_limit_up": False}, rule) == 0
    assert sp.score_sector(None, rule) == 0


def test_stock_score_requires_ma20_and_breakout():
    assert sp.score_stock({"above_ma20": True, "breakout": True}) == 2
    assert sp.score_stock({"above_ma20": True, "breakout": False}) == 1
    assert sp.score_stock({"above_ma20": False, "breakout": True}) == 0
    assert sp.score_stock(None) == 0


def test_volume_score_full_and_partial():
    rule = CFG["scoring"]["volume"]
    row = {"chg": 5.0, "amount": 3e8, "turnover": 8.0}
    assert sp.score_volume(row, 1.8, rule) == 2
    # 量比不足 → 3/4 满足 → 1
    assert sp.score_volume(row, 1.1, rule) == 1
    # 只有 1 项满足 → 0
    assert sp.score_volume({"chg": 5.0, "amount": 1e7, "turnover": 1.0}, 1.1, rule) == 0
    # 边界：涨幅 3% 与 7% 都算满足（闭区间）
    assert sp.score_volume({"chg": 3.0, "amount": 3e8, "turnover": 5.0}, 1.6, rule) == 2


def test_has_risk_flags():
    rule = CFG["scoring"]["risk"]
    assert sp.has_risk({"name": "ST国华"}, rule)[0] is True
    assert sp.has_risk({"name": "某某退"}, rule)[0] is True
    assert sp.has_risk({"name": "正常股", "profit_yoy": -80}, rule)[0] is True
    assert sp.has_risk({"name": "正常股", "profit_yoy": 20}, rule)[0] is False


# ---------------------------------------------------------------------------
# 仓位与价格计划
# ---------------------------------------------------------------------------
def test_position_plan_respects_all_caps():
    plan = sp.position_plan(CFG, score=8, price=10.0)
    # 3200 本金：单票上限 1920、现金下限 3200-1280=1920、风险预算 64/0.06=1066.7、
    # 首笔 800 → 取最小 800；10 元 → 80 股（不足 100 股 → 0）
    assert plan["caps"]["risk_budget"] == pytest.approx(1066.67, rel=1e-3)
    assert plan["shares"] == 0  # 800 元买 10 元票只能买 80 股，整手约束下为 0
    assert plan["amount"] == 0


def test_position_plan_odd_lot():
    plan = sp.position_plan(CFG, score=8, price=7.5)
    # 800 / 7.5 = 106.7 → 100 股 → 750 元
    assert plan["shares"] == 100
    assert plan["amount"] == pytest.approx(750.0)
    assert plan["binding"] == "first_entry"


def test_position_plan_high_score_uses_max_entry():
    plan = sp.position_plan(CFG, score=9, price=9.0)
    assert plan["caps"]["want"] == 1000
    assert plan["shares"] == 100  # 1000/9=111 → 100 股


def test_price_plan_rr_two():
    plan = sp._price_plan(CFG, 10.0)
    assert plan["stop"] == 9.4 and plan["target"] == 11.2
    assert plan["rr"] == 2.0


# ---------------------------------------------------------------------------
# 数据缺失
# ---------------------------------------------------------------------------
def test_build_reports_unavailable_without_universe(monkeypatch):
    monkeypatch.setattr(sp, "_universe_rows", lambda: [])
    out = __import__("asyncio").run(sp.build(force=True))
    assert out["ok"] is False and out["available"] is False
    assert "尚未就绪" in out["reason"]


def test_sector_strength_ranks_and_flags_leader(monkeypatch):
    rows = [
        {"code": "sh600001", "name": "甲", "chg": 10.0},
        {"code": "sh600002", "name": "乙", "chg": 4.0},
        {"code": "sh600003", "name": "丙", "chg": -1.0},
    ]
    monkeypatch.setattr(sp, "_sector_lookup",
                        lambda: ({"sh600001": "测试行业", "sh600002": "测试行业", "sh600003": "测试行业"},
                                 {"测试行业": ["sh600001", "sh600002", "sh600003"]}))
    out = sp.sector_strength(rows)
    item = out["测试行业"]
    assert item["rank"] == 1
    assert item["leader"]["code"] == "sh600001"
    assert item["leader_limit_up"] is True
    assert item["avg_chg"] == pytest.approx(4.33, rel=1e-2)
