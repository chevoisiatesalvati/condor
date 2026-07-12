"""Tests for executor display config resolution."""

from __future__ import annotations

from condor.fetchers.executors import get_executor_display_config


def test_display_config_merges_top_level_when_nested_empty():
    ex = {
        "config": {},
        "leverage": 10,
        "total_amount_quote": 250,
        "triple_barrier_config": {"stop_loss": 0.02, "take_profit": 0.04},
    }
    cfg = get_executor_display_config(ex)
    assert cfg["leverage"] == 10
    assert cfg["total_amount_quote"] == 250
    assert cfg["stop_loss"] == 0.02
    assert cfg["take_profit"] == 0.04


def test_display_config_prefers_nested_config():
    ex = {
        "config": {
            "leverage": 5,
            "triple_barrier_config": {"stop_loss": 0.01},
        },
        "leverage": 20,
    }
    cfg = get_executor_display_config(ex)
    assert cfg["leverage"] == 5
    assert cfg["stop_loss"] == 0.01
