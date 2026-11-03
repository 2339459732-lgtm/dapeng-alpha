"""core.numbers / core.timeutil 的行为契约。"""
from __future__ import annotations

from datetime import datetime

import pytest

from server.core.numbers import clamp, safe_float, to_float, to_int
from server.core.timeutil import iso_of, now_iso


@pytest.mark.parametrize("value", [None, "", "  ", "-", "--", "N/A", True, False, "abc", float("nan"), float("inf")])
def test_to_float_rejects_missing_and_bool(value):
    assert to_float(value) is None


@pytest.mark.parametrize("value,expected", [("3.14", 3.14), (2, 2.0), ("  -1.5 ", -1.5), (0, 0.0)])
def test_to_float_parses_valid(value, expected):
    assert to_float(value) == expected


def test_safe_float_uses_business_default():
    assert safe_float(None) == 0.0
    assert safe_float("bad", 12.5) == 12.5
    assert safe_float("7") == 7.0


def test_to_int_truncates_and_defaults():
    assert to_int("3.9") == 3
    assert to_int(None) is None
    assert to_int("bad", -1) == -1


def test_clamp_bounds():
    assert clamp(120) == 100
    assert clamp(-5) == 0
    assert clamp(42, 0, 50) == 42


def test_now_iso_has_timezone_and_second_precision():
    text = now_iso()
    assert len(text) >= 19
    assert ("+" in text[10:] or text.endswith("Z"))  # 带时区
    assert "." not in text.split("T")[1]  # 默认不带微秒


def test_iso_of_none_and_value():
    assert iso_of(None) is None
    text = iso_of(datetime(2026, 11, 2, 15, 0, 0))
    assert text.startswith("2026-11-02T15:00:00")
