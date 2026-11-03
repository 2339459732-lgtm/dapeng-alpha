"""快照钉住（pin_packet）：一轮决策只能依据同一份事实。

这条约束不是"优化"，是"结论可追溯"的前提：同一条链路挂在两三代快照上，
再漂亮的溯源表也说不清结论到底依据了什么。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import decision_snapshot as ds  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_pin():
    ds.unpin_packet()
    yield
    ds.unpin_packet()


def _packet(trace="mk_pin"):
    return {"ok": True, "version": "market-packet.v1", "trace_id": trace,
            "as_of": "2026-11-02T23:00:00+08:00"}


def test_pin_wins_over_force():
    ds.pin_packet(_packet(), ttl=60, owner="test")
    got = asyncio.run(ds.get_market_packet(force=True))
    assert got["trace_id"] == "mk_pin"


def test_unpin_restores_normal_path():
    ds.pin_packet(_packet(), ttl=60, owner="test")
    assert ds.pinned_packet() is not None
    ds.unpin_packet("test")
    assert ds.pinned_packet() is None


def test_unpin_by_other_owner_keeps_pin():
    """别人解不掉我的钉：避免两个并发闭环互相把对方的快照放掉。"""
    ds.pin_packet(_packet(), ttl=60, owner="cycle")
    ds.unpin_packet("other")
    assert ds.pinned_packet() is not None


def test_expired_pin_is_ignored(monkeypatch):
    ds.pin_packet(_packet(), ttl=30, owner="test")
    # 不真等：把「现在」拨到过期之后（先抓住原始函数，否则会自我递归）
    real = ds.time.time
    monkeypatch.setattr(ds.time, "time", lambda: real() + 60)
    assert ds.pinned_packet() is None


def test_failed_packet_is_not_pinned():
    """ok:false 的快照不许钉住 —— 钉住失败态等于把降级固化成事实。"""
    ds.pin_packet({"ok": False, "trace_id": "mk_bad"}, ttl=60, owner="test")
    assert ds.pinned_packet() is None
