"""Tests for shared Binance/HL intersection universe helpers."""

from routines.lib.shared_universe import (
    canonical_base,
    hl_canonical_base,
    universe_rows_for_exchange,
)


def test_canonical_base_maps_kpepe():
    assert canonical_base("1000PEPE-USDT") == "1000PEPE"
    assert hl_canonical_base("kPEPE-USD") == "1000PEPE"
    assert hl_canonical_base("BTC-USD") == "BTC"


def test_universe_rows_for_exchange_uses_exchange_specific_volume():
    intersection = [
        {
            "canonical_base": "BTC",
            "binance_pair": "BTC-USDT",
            "hl_pair": "BTC-USD",
            "binance_volume_24h_usd": 1_000_000_000.0,
            "hl_volume_24h_usd": 500_000_000.0,
            "binance_price": 100.0,
            "hl_price": 100.1,
        }
    ]
    b_rows = universe_rows_for_exchange(intersection, "binance_perpetual")
    h_rows = universe_rows_for_exchange(intersection, "hyperliquid")
    assert b_rows[0]["trading_pair"] == "BTC-USDT"
    assert b_rows[0]["volume_24h_usd"] == 1_000_000_000.0
    assert h_rows[0]["trading_pair"] == "BTC-USD"
    assert h_rows[0]["volume_24h_usd"] == 500_000_000.0
