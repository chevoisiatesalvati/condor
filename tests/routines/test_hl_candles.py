"""Tests for Hyperliquid candle helpers."""

from __future__ import annotations

from routines.lib.hl_candles import trading_pair_to_hl_coin


def test_trading_pair_to_hl_coin_pepe_alias():
    assert trading_pair_to_hl_coin("PEPE-USD") == "kPEPE"
    assert trading_pair_to_hl_coin("BTC-USD") == "BTC"
    assert trading_pair_to_hl_coin("ABCD:FOO-USD") == "ABCD:FOO"
