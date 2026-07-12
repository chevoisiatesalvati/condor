"""Tests for shared-memory candle storage."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from routines.macdbb_scanner_aggressive_hl_replay.candle_shared_store import (
    CandleSeriesView,
    SharedCandleStore,
    natr_from_candle_view,
    scanner_natr_from_candle_view,
)
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import scan_barriers_between


def _sample_candles(count: int = 10, start_ms: int = 1_700_000_000_000) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for index in range(count):
        ts = start_ms + index * 60_000
        price = 100.0 + index
        candles.append(
            {
                "timestamp_ms": float(ts),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
            }
        )
    return candles


def test_candle_series_view_from_candles_sorted():
    raw = _sample_candles(5)
    raw_rev = list(reversed(raw))
    view = CandleSeriesView.from_candles(raw_rev)
    assert np.all(np.diff(view.timestamp_ms) >= 0)
    assert len(view) == 5
    assert float(view.close[-1]) == 104.0


def test_shared_candle_store_pack_roundtrip():
    caches = {"BTC-USDT": _sample_candles(20)}
    store = SharedCandleStore.pack(caches, name_prefix="test_candles")
    try:
        view = store.get("BTC-USDT")
        assert view is not None
        assert len(view) == 20
        assert float(view.high[10]) == pytest.approx(111.0)
    finally:
        store.close_unlink()


def test_scan_barriers_view_matches_dict():
    candles = [
        {
            "timestamp_ms": 1_700_000_060_000,
            "open": 100.0,
            "high": 101.0,
            "low": 97.0,
            "close": 100.0,
        },
        {
            "timestamp_ms": 1_700_000_120_000,
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
        },
    ]
    view = CandleSeriesView.from_candles(candles)
    start = dt.datetime.fromtimestamp(1_700_000_000, tz=dt.timezone.utc)
    end = dt.datetime.fromtimestamp(1_700_000_180, tz=dt.timezone.utc)
    dict_hit = scan_barriers_between(candles, start, end, "long", 100.0, sl_pct=2.0, tp_pct=5.0)
    view_hit = scan_barriers_between(view, start, end, "long", 100.0, sl_pct=2.0, tp_pct=5.0)
    assert dict_hit == view_hit


def test_scanner_natr_view_matches_dict_helper():
    from condor.trading_agent.policies.macdbb_dynamic import (
        NATR_LOOKBACK_PERIODS,
        SCANNER_NATR_LOOKBACK_HOURS_DEFAULT,
        SCANNER_NATR_MIN_BARS,
        scanner_natr_mean_from_candles,
    )

    candles = _sample_candles(120)
    view = CandleSeriesView.from_candles(candles)
    entry_ms = int(candles[-1]["timestamp_ms"])
    entry = dt.datetime.fromtimestamp(entry_ms / 1000, tz=dt.timezone.utc)
    dict_val = scanner_natr_mean_from_candles(
        candles,
        entry,
        lookback_hours=SCANNER_NATR_LOOKBACK_HOURS_DEFAULT,
    )
    view_val = scanner_natr_from_candle_view(
        view,
        entry,
        lookback_hours=SCANNER_NATR_LOOKBACK_HOURS_DEFAULT,
        natr_period=NATR_LOOKBACK_PERIODS,
        min_bars=SCANNER_NATR_MIN_BARS,
    )
    assert dict_val == pytest.approx(view_val, rel=1e-9)
    natr_view = natr_from_candle_view(view, entry, lookback_periods=NATR_LOOKBACK_PERIODS)
    assert natr_view is not None
