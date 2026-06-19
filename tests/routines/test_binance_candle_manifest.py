"""Tests for Binance candle depth probe helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from routines.lib.binance_candles import trading_pair_to_symbol
from routines.lib.candle_depth_probe import find_earliest_bar, summarize_candles


def test_trading_pair_to_symbol():
    assert trading_pair_to_symbol("BTC-USDT") == "BTCUSDT"
    assert trading_pair_to_symbol("1000PEPE-USDT") == "1000PEPEUSDT"


def test_summarize_candles_empty():
    summary = summarize_candles([])
    assert summary["has_data"] is False
    assert summary["bar_count"] == 0


@pytest.mark.asyncio
async def test_find_earliest_bar_binary_search():
    hour_ms = 3_600_000
    search_start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    search_end = search_start + dt.timedelta(days=30)
    data_start_ms = int(search_start.timestamp() * 1000) + 100 * hour_ms

    async def _fake_window(_session, _pair, _interval, start, end):
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        if end_ms <= data_start_ms or start_ms >= int(search_end.timestamp() * 1000):
            return []
        ts = max(start_ms, data_start_ms)
        return [
            {
                "timestamp_ms": float(ts),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            }
        ]

    class _Session:
        pass

    result = await find_earliest_bar(
        _Session(),
        "BTC-USDT",
        "1h",
        search_start,
        search_end,
        hour_ms,
        _fake_window,
        probe_bars=24,
    )
    assert result["has_data"] is True
    assert result["earliest_ts_ms"] == data_start_ms
