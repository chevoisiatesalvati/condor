"""Tests for cross-venue trading pair format helpers."""

from routines.lib.pair_format import binance_pair_from_any, hl_pair_from_any


def test_binance_pair_from_hl_usd():
    assert binance_pair_from_any("BTC-USD") == "BTC-USDT"
    assert binance_pair_from_any("ETH-USD") == "ETH-USDT"


def test_binance_pair_from_usdt_unchanged():
    assert binance_pair_from_any("SOL-USDT") == "SOL-USDT"


def test_hl_pair_from_binance_usdt():
    assert hl_pair_from_any("BTC-USDT") == "BTC-USD"
