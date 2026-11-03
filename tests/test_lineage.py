"""decision-lineage.v1 的端到端约束：链路要能建、能验、能回放。

这些用例守的是「结论可追溯」的实质，而不是「有个 trace_id 就算追溯」：
节点缺失必须如实记 gap、时点倒挂必须被发现、指纹必须随依据变化而变化。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import lineage  # noqa: E402


def _nodes(**over):
    base = {
        "coverage": {"version": "market-coverage.v1", "as_of": "2026-11-02 14:49:30", "generated_at": "2026-11-02 14:50:00",
                     "ashare": {"total_universe": 5782,
                                "quotes": {"valid": 5581, "limit_up": 141, "amount": 1.79e12},
                                "quality": {"level": "partial"}}},
        "packet": {"version": "market-packet.v1", "as_of": "2026-11-02 14:50:10", "generated_at": "2026-11-02 14:50:12",
                   "trace_id": "mk_1",
                   "market": {"breadth": {"up_ratio": 83.9, "limit_up": 141, "valid": 5581},
                              "turnover": {"amount": 1.79e12}},
                   "chain": {"build_ms": 3120.0},
                   "evidence": [{"metric": "sample_coverage", "value": 96.5}]},
        "sentiment": {"version": "sentiment.v1", "as_of": "2026-11-02 14:50:12", "generated_at": "2026-11-02 14:50:14",
                      "trace_id": "mk_1", "score": 80.5, "band": "hot", "coverage": 0.86,
                      "dimensions": [{"score": 88}, {"score": 76}]},
        "sentiment_flow": {"version": "sentiment-flow.v1", "as_of": "2026-11-02 14:50:40", "generated_at": "2026-11-02 14:50:42",
                           "trace_id": "mk_1", "level": 80.5, "band": "hot",
                           "delta_short": 0.6, "slope_per_hour": 1.2,
                           "turning_point": {"detected": False}},
        "survey": {"version": "survey-pipeline.v1", "as_of": "2026-11-02 14:51:00", "generated_at": "2026-11-02 14:51:02",
                   "trace_id": "mk_1", "regime": {"status": "偏热", "score": 78},
                   "divergence": {"detected": False}, "facts": {"up_ratio": 83.9},
                   "alerts": [{"type": "放量"}]},
        "experts": {"version": "expert-panel.v1", "as_of": "2026-11-02 14:51:20", "generated_at": "2026-11-02 14:51:22",
                    "trace_id": "mk_1",
                    "consensus": {"stance": "attack", "score": 72, "disagreement": 0.21}},
        "crosscheck": {"version": "layer-crosscheck.v1", "generated_at": "2026-11-02 14:51:24", "ok": True, "score": 1.0,
                       "counts": {"pass": 7, "incomparable": 2}, "comparable_ratio": 1.0},
        "memo": {"version": "brain-memo.v1", "as_of": "2026-11-02 14:51:25", "generated_at": "2026-11-02 14:51:26",
                 "trace_id": "mk_1", "stance": "attack", "position_cap": 40,
                 "confidence": 0.58},
    }
    base.update(over)
    return base


def test_chain_is_complete_and_ordered():
    record = lineage.build("mk_1", _nodes())
    assert record["ok"] is True
    assert record["gaps"] == []
    assert record["checks"]["time_ordered"] is True
    assert [n["key"] for n in record["nodes"]] == lineage.CHAIN
    assert all(n["digest"] for n in record["nodes"])


def test_missing_node_becomes_gap_not_fabrication():
    nodes = _nodes()
    nodes["experts"] = None
    record = lineage.build("mk_1", nodes)
    assert record["ok"] is False
    assert record["gaps"] == ["experts"]
    missing = [n for n in record["nodes"] if n["key"] == "experts"][0]
    assert missing["present"] is False
    assert missing["digest"] is None and missing["facts"] == {}


def test_time_regression_is_detected():
    nodes = _nodes()
    nodes["survey"] = dict(nodes["survey"], generated_at="2026-11-02 14:49:00")
    record = lineage.build("mk_1", nodes)
    assert record["ok"] is False
    assert record["checks"]["time_ordered"] is False
    assert record["checks"]["out_of_order"][0]["to"] == "survey"


def test_trace_mismatch_is_detected():
    nodes = _nodes()
    nodes["experts"] = dict(nodes["experts"], trace_id="mk_OTHER")
    record = lineage.build("mk_1", nodes)
    # 结构仍然可用；不一致单独表达为 consistent=False（证据来自另一份快照）
    assert record["ok"] is True
    assert record["consistent"] is False
    assert record["checks"]["trace_consistent"] is False
    assert record["checks"]["trace_mismatch"][0]["key"] == "experts"
    cites = {c["node"]: c["same_trace"] for c in lineage.citations(record)}
    assert cites["experts"] is False and cites["packet"] is True


def test_digest_tracks_underlying_facts():
    """依据变了，指纹必须变；否则「指纹」只是装饰。"""
    a = lineage.build("mk_1", _nodes())
    changed = _nodes()
    changed["coverage"] = json.loads(json.dumps(_nodes()["coverage"]))
    changed["coverage"]["ashare"]["quotes"]["limit_up"] = 152
    b = lineage.build("mk_1", changed)
    da = {n["key"]: n["digest"] for n in a["nodes"]}
    db = {n["key"]: n["digest"] for n in b["nodes"]}
    assert da["coverage"] != db["coverage"]
    assert da["memo"] == db["memo"]  # 结论字段没变，指纹不该跟着动


def test_citations_exclude_memo_and_keep_evidence_only():
    record = lineage.build("mk_1", _nodes())
    cites = lineage.citations(record)
    assert [c["node"] for c in cites] == lineage.CHAIN[:-1]
    # 指纹是硬要求；时点允许缺（派生节点如对证只有 generated_at）
    assert all(c["digest"] for c in cites)


def test_record_and_get_roundtrip(tmp_path, monkeypatch):
    ring = tmp_path / "lineage.json"
    monkeypatch.setattr(lineage, "RING_PATH", str(ring))
    record = lineage.build("mk_ring", _nodes())
    lineage.record(record)
    got = lineage.get("mk_ring")
    assert got is not None and got["trace_id"] == "mk_ring"
    assert lineage.get("nope") is None
    assert lineage.recent(5)[0]["trace_id"] == "mk_ring"


def test_record_is_idempotent_per_trace(tmp_path, monkeypatch):
    ring = tmp_path / "lineage.json"
    monkeypatch.setattr(lineage, "RING_PATH", str(ring))
    lineage.record(lineage.build("mk_dup", _nodes()))
    lineage.record(lineage.build("mk_dup", _nodes()))
    rows = json.loads(ring.read_text(encoding="utf-8"))
    assert len(rows) == 1


def test_ring_is_capped(tmp_path, monkeypatch):
    ring = tmp_path / "lineage.json"
    monkeypatch.setattr(lineage, "RING_PATH", str(ring))
    monkeypatch.setattr(lineage, "_KEEP", 3)
    for idx in range(5):
        lineage.record(lineage.build(f"mk_{idx}", _nodes()))
    rows = json.loads(ring.read_text(encoding="utf-8"))
    assert [r["trace_id"] for r in rows] == ["mk_2", "mk_3", "mk_4"]


def test_persist_failure_does_not_raise(tmp_path, monkeypatch):
    """留痕失败不能把主链路带崩 —— 结论比日志重要。"""
    monkeypatch.setattr(lineage, "RING_PATH", str(tmp_path / "no_such_dir" / "x.json"))
    record = lineage.build("mk_fail", _nodes())
    assert lineage.record(record)["trace_id"] == "mk_fail"


def test_mixed_timestamp_formats_do_not_break_ordering():
    """带时区与不带时区混用曾把闭环打成 500：必须统一到本地时间再比。"""
    nodes = _nodes()
    nodes["crosscheck"] = dict(nodes["crosscheck"], generated_at="2026-11-02T14:51:24+08:00")
    nodes["memo"] = dict(nodes["memo"], generated_at="2026-11-02 14:51:26")
    record = lineage.build("mk_1", nodes)
    assert record["checks"]["check_error"] is None
    assert record["checks"]["time_ordered"] is True


def test_coverage_is_exempt_from_ordering():
    """coverage 由后台 worker 生产，晚于本轮快照重建属正常，不该报时间倒挂。"""
    nodes = _nodes()
    nodes["coverage"] = dict(nodes["coverage"], generated_at="2026-11-02 15:10:00")
    record = lineage.build("mk_1", nodes)
    assert record["checks"]["time_ordered"] is True


def test_packet_digest_reads_nested_market_paths():
    """快照的市场数据在 market.* 下；路径取错会让指纹变成常量（等于没指纹）。"""
    base = lineage.build("mk_1", _nodes())
    moved = _nodes()
    moved["packet"] = json.loads(json.dumps(_nodes()["packet"]))
    moved["packet"]["market"]["breadth"]["up_ratio"] = 61.2
    other = lineage.build("mk_1", moved)
    digests = {n["key"]: n["digest"] for n in base["nodes"]}
    moved_digests = {n["key"]: n["digest"] for n in other["nodes"]}
    assert digests["packet"] != moved_digests["packet"]
    facts = next(n["facts"] for n in other["nodes"] if n["key"] == "packet")
    assert facts["up_ratio"] == 61.2 and facts["amount"] == 1.79e12
