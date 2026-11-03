"""补缺模块的解析与失败语义。

补缺是「第二源兜底」，它自己必须先做到不说谎：
解析错一位就会把涨停价当收盘价；全批失败时必须报 ok=False，而不是返回 0 条假装成功。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import market_coverage_gapfill as gap  # noqa: E402


def _line(price="1451.80", prev="1439.10", pct="0.88", amount_wan="363420", turnover="0.20"):
    fields = [""] * 88
    fields[0], fields[1], fields[2] = "1", "贵州茅台", "600519"
    fields[3], fields[4], fields[5], fields[6] = price, prev, "1450.00", "25102"
    fields[30], fields[31], fields[32] = "20261103103641", "12.70", pct
    fields[33], fields[34] = "1458.00", "1435.10"
    fields[37], fields[38], fields[39] = amount_wan, turnover, "22.13"
    fields[44], fields[45], fields[46] = "18148.68", "18148.68", "6.70"
    return fields


def test_row_parses_units_and_fields():
    row = gap._row("sh600519", _line())
    assert row["price"] == 1451.80
    assert row["chg"] == 0.88
    assert row["volume"] == 25102 * 100          # 手 → 股
    assert row["amount"] == 363420 * 10000       # 万元 → 元
    assert row["market_cap_yi"] == 18148.68      # 已是亿元
    assert row["quote_source"] == "tencent_gapfill"


def test_row_rejects_suspended_or_invalid():
    assert gap._row("sh600519", _line(price="0")) is None      # 停牌/无成交
    assert gap._row("sh600519", [""] * 10) is None             # 字段不足


def test_empty_input_is_ok_without_requests():
    result = asyncio.run(gap.get_gapfill_quotes([]))
    assert result["ok"] is True
    assert result["meta"]["requested"] == 0 and result["rows"] == {}


def test_failed_upstream_reports_not_ok(monkeypatch):
    """全部失败时必须 ok=False：否则上层会把「没补到」当成「不需要补」。"""
    def boom(url, timeout=10):   # _fetch_url 是同步函数（外层用 to_thread 调）
        raise RuntimeError("network down")

    monkeypatch.setattr(gap, "_fetch_url", boom)
    result = asyncio.run(gap.get_gapfill_quotes(["sh600519"], force=True))
    assert result["ok"] is False
    assert result["rows"] == {}
    assert result["meta"]["unresolved_count"] == 1
