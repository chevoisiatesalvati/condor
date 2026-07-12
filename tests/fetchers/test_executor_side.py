"""Tests for executor side normalization."""

from __future__ import annotations

from condor.fetchers.executors import get_executor_side, normalize_executor_side


def test_normalize_side_from_numeric_string():
    assert normalize_executor_side("1") == "BUY"
    assert normalize_executor_side("2") == "SELL"


def test_normalize_side_from_custom_info():
    assert normalize_executor_side(None, None, "BUY") == "BUY"


def test_normalize_side_empty_when_unknown():
    assert normalize_executor_side("", None, {}) == ""


def test_get_executor_side_from_held_position_orders():
    ex = {
        "config": {},
        "custom_info": {
            "held_position_orders": [{"side": "BUY", "price": 100.0}],
        },
    }
    assert get_executor_side(ex) == "BUY"
