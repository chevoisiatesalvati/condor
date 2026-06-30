"""Tests for executor notional quote helper and row normalization."""

from __future__ import annotations

from condor.trading_agent.performance import _executor_notional_quote, _executor_row


def test_notional_prefers_total_amount_quote():
    assert _executor_notional_quote({"total_amount_quote": 500.0, "amount": 339000}, 0.001) == 500.0


def test_notional_falls_back_to_amount_times_entry_price():
    assert _executor_notional_quote({"amount": 1000.0}, 0.0015) == 1.5


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
