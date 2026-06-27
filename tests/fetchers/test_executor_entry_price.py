"""Tests for executor entry price resolution."""

from __future__ import annotations

from condor.fetchers.executors import get_executor_entry_price


def test_entry_price_prefers_config():
    ex = {"config": {"entry_price": 1.5}, "entry_price": 2.0}
    assert get_executor_entry_price(ex) == 1.5


def test_entry_price_falls_back_to_custom_info_average():
    ex = {
        "config": {},
        "custom_info": {"current_position_average_price": 0.42},
    }
    assert get_executor_entry_price(ex) == 0.42


def test_entry_price_uses_held_position_order_fill():
    ex = {
        "config": {},
        "custom_info": {
            "held_position_orders": [{"price": 0.00123}],
        },
    }
    assert get_executor_entry_price(ex) == 0.00123


def test_entry_price_uses_buy_breakeven_price():
    ex = {
        "config": {},
        "custom_info": {"buy_breakeven_price": 99.5},
    }
    assert get_executor_entry_price(ex) == 99.5
