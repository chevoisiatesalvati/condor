"""Tests for excluding stale_duplicate from Condor performance PnL."""

from __future__ import annotations

from condor.agents.performance import (
    _build_perf_from_rows,
    _executor_row,
    is_pnl_excluded_close_type,
)


def test_is_pnl_excluded_close_type():
    assert is_pnl_excluded_close_type("stale_duplicate")
    assert is_pnl_excluded_close_type("STALE_DUPLICATE")
    assert is_pnl_excluded_close_type("mistake")
    assert not is_pnl_excluded_close_type("take_profit")
    assert not is_pnl_excluded_close_type("early_stop")


def test_executor_row_zeros_stale_duplicate_pnl():
    ex = {
        "id": "stale1",
        "status": "TERMINATED",
        "close_type": "STALE_DUPLICATE",
        "net_pnl_quote": 217.84,
        "net_pnl_pct": 0.5,
        "filled_amount_quote": 100.0,
        "cum_fees_quote": 1.2,
        "trading_pair": "VVV-USD",
        "config": {
            "type": "position_executor",
            "trading_pair": "VVV-USD",
            "connector_name": "hyperliquid_perpetual",
            "entry_price": 1.0,
            "amount": 10,
        },
    }
    row = _executor_row(ex)
    assert row["pnl"] == 0.0
    assert row["fees"] == 0.0
    assert row["net_pnl_pct"] == 0.0
    assert row["close_type"] == "stale_duplicate"


def test_build_perf_ignores_stale_pnl_in_totals():
    rows = [
        {
            "id": "a",
            "status": "terminated",
            "close_type": "take_profit",
            "pnl": 10.0,
            "volume": 100.0,
            "fees": 0.5,
        },
        {
            "id": "b",
            "status": "terminated",
            "close_type": "stale_duplicate",
            "pnl": 0.0,  # already zeroed by _executor_row
            "volume": 50.0,
            "fees": 0.0,
        },
    ]
    perf = _build_perf_from_rows("agent_1", rows)
    assert perf.total_pnl == 10.0
    assert perf.realized_pnl == 10.0
    assert perf.fees == 0.5
    assert perf.win_rate == 1.0
