"""Unit tests for tiered HL vol candle prefetch helpers."""

from __future__ import annotations

import datetime as dt

from condor.trading_agent.policies.macdbb_dynamic import SCANNER_NATR_MIN_BARS
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
    HL_1M_API_MAX_AGE_DAYS,
    _vol_bars_in_lookback,
    _vol_series_usable,
    _within_1m_api_window,
)


def _make_candles(
    start: dt.datetime,
    count: int,
    *,
    interval_minutes: int = 1,
) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for index in range(count):
        timestamp = start + dt.timedelta(minutes=interval_minutes * index)
        price = 100.0 + index * 0.01
        candles.append(
            {
                "timestamp_ms": int(timestamp.timestamp() * 1000),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
            }
        )
    return candles


def test_vol_bars_in_lookback_counts_bars_in_window() -> None:
    end = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)
    start = end - dt.timedelta(hours=6)
    candles = _make_candles(start, 361, interval_minutes=1)
    tick_times = [end]

    assert _vol_bars_in_lookback(candles, tick_times, lookback_hours=6) == 361


def test_vol_series_usable_requires_min_bars() -> None:
    end = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)
    start = end - dt.timedelta(hours=6)
    tick_times = [end]

    enough_1m = _make_candles(start, SCANNER_NATR_MIN_BARS, interval_minutes=1)
    assert _vol_series_usable(enough_1m, tick_times, lookback_hours=6) is True

    too_few_1m = _make_candles(start, SCANNER_NATR_MIN_BARS - 1, interval_minutes=1)
    assert _vol_series_usable(too_few_1m, tick_times, lookback_hours=6) is False

    enough_5m = _make_candles(start, SCANNER_NATR_MIN_BARS, interval_minutes=5)
    assert _vol_series_usable(enough_5m, tick_times, lookback_hours=6) is True


def test_within_1m_api_window_respects_max_age() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    recent = [now - dt.timedelta(days=HL_1M_API_MAX_AGE_DAYS - 1)]
    old = [now - dt.timedelta(days=HL_1M_API_MAX_AGE_DAYS + 1)]

    assert _within_1m_api_window(recent, candle_source="hyperliquid") is True
    assert _within_1m_api_window(old, candle_source="hyperliquid") is False
    assert _within_1m_api_window(old, candle_source="binance_perpetual") is True
