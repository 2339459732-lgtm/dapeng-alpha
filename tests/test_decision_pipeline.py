"""研判链路（decision-pipeline.v1）：降级必须可见、两类产出结构完整。

守三件事：
1. 每个环节都带 sample / output / scope / degraded / reason，不允许“少了一半还不说”；
2. 全市场广度缺失时标 degraded 并给出原因，而不是悄悄退回核心样本；
3. 盘后报告在 0 候选时必须解释“为什么空仓”，不能只返回一个空数组。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import decision_pipeline as dp  # noqa: E402


def test_stage_shape_is_explicit():
    s = dp._stage("x", "某环节", scope="全市场", sample=100, output=5, degraded=True, reason="样本不足")
    assert s["id"] == "x" and s["sample"] == 100 and s["output"] == 5
    assert s["degraded"] is True and s["reason"] == "样本不足" and s["scope"] == "全市场"


def test_breadth_stage_marks_degraded_without_full_market():
    packet = {"market": {"breadth": {"up": 100, "down": 200, "degraded": True,
                                     "reason": "全市场快照未就绪", "sample": 448,
                                     "scope": "核心样本"}}, "inputs_as_of": {"full_market": None}}
    s = dp._breadth_stage(packet, {"cross_section": {"available": True, "sample": 5655}})
    assert s["degraded"] is True
    assert "未就绪" in s["reason"]
    assert s["scope"] == "核心样本"


def test_breadth_stage_ok_when_full_market_present():
    packet = {"market": {"full_market": {"as_of": "2026-11-03"},
                         "breadth": {"up": 2100, "down": 3300, "limit_up": 60, "limit_down": 30,
                                     "sample": 5668, "scope": "沪深京 A 股（全市场）",
                                     "degraded": False}}, "inputs_as_of": {}}
    s = dp._breadth_stage(packet, {"cross_section": {"limit_up": 61, "limit_down": 30}})
    assert s["degraded"] is False and s["sample"] == 5668
    assert s["metrics"]["limit_up"] == 60


def test_sector_stage_degrades_without_industries():
    s = dp._sector_stage({"rotation": {"industries": []}})
    assert s["degraded"] is True and s["reason"]


def test_stock_stage_passes_funnel_through():
    profile = {"ok": True, "funnel": {"universe": 5780, "quoted": 5655, "prefiltered": 40,
                                      "scored": 40, "passed": 0}, "candidates": []}
    s = dp._stock_stage(profile)
    assert s["sample"] == 5655 and s["output"] == 0
    assert s["funnel"]["prefiltered"] == 40


def test_stock_stage_degrades_when_profile_unavailable():
    s = dp._stock_stage({"ok": False, "reason": "全市场快照尚未就绪"})
    assert s["degraded"] is True and "尚未就绪" in s["reason"]


def test_post_close_explains_zero_candidates():
    profile = {
        "ok": True, "date": "2026-11-03",
        "market": {"score": 0, "above_ma20": False, "limit_up": 65, "limit_down": 30},
        "funnel": {"universe": 5780, "prefiltered": 40, "scored": 40, "passed": 0},
        "candidates": [], "runtime": {"total_capital": 3200}, "caveats": ["风险为代理指标"],
    }
    pc = dp._post_close(profile, None)
    assert pc["available"] is True
    joined = " ".join(pc["deviations"])
    assert "0 只入选" in joined and "市场分 0/2" in joined
    assert "40" in joined  # 预筛/评分数量必须出现
    assert pc["performance"]["status"] == "pending"


def test_post_close_marks_unavailable():
    pc = dp._post_close({"ok": False, "reason": "候选池异常"}, None)
    assert pc["available"] is False and "异常" in pc["reason"]


def test_intraday_empty_candidates_is_explicit():
    import asyncio

    out = asyncio.run(dp._intraday_list([], market_open=False))
    assert out["items"] == [] and "空仓" in out["note"]
