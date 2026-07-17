"""Tests for executor notional quote helper and row normalization."""

from __future__ import annotations

import pytest

from condor.trading_agent.performance import _executor_notional_quote, _executor_row


def test_notional_prefers_total_amount_quote():
    assert _executor_notional_quote({"total_amount_quote": 500.0, "amount": 339000}, 0.001) == 500.0


def test_notional_falls_back_to_amount_times_entry_price():
    assert _executor_notional_quote({"amount": 1000.0}, 0.0015) == 1.5


def test_executor_row_amount_uses_quote_notional_not_base_size():
    """Position executors store base `amount`; UI Amount must be quote notional."""
    row = _executor_row(
        {
            "id": "5duEWZtp7Z8SHZqzUTYN5XMtWJs6v2SRkkhEP3WM19mo",
            "status": "TERMINATED",
            "close_type": "TAKE_PROFIT",
            "net_pnl_quote": 9.36292,
            "net_pnl_pct": 0.024876,
            "filled_amount_quote": 376.389384,
            "config": {
                "type": "position_executor",
                "connector_name": "hyperliquid_perpetual",
                "trading_pair": "kBONK-USD",
                "side": 2,
                "amount": 110152.0,
                "leverage": 10,
                "triple_barrier_config": {"take_profit": 0.070986, "stop_loss": 0.065},
            },
            "custom_info": {
                "current_position_average_price": 0.003417,
                "close_price": 0.003332,
                "side": "SELL",
            },
        }
    )
    assert row["notional_quote"] == pytest.approx(376.389384)
    assert row["amount"] == pytest.approx(376.389384)
    assert row["amount"] != 110152.0
    assert row["current_price"] == pytest.approx(0.003332)
    assert row["entry_price"] == pytest.approx(0.003417)


def test_executor_row_normalizes_type_and_status():
    row = _executor_row(
        {
            "id": "abc123",
            "status": "RUNNING",
            "config": {
                "type": "position_executor",
                "connector_name": "hyperliquid_perpetual",
                "trading_pair": "BTC-USD",
                "side": "BUY",
            },
        }
    )
    assert row["type"] == "position"
    assert row["status"] == "running"
