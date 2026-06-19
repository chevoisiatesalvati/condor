"""Unit tests for report backfill helpers."""

from __future__ import annotations

import datetime as dt

import numpy as np

from routines.macdbb_replay.report_backfill import (
    _quote_volume_24h,
    collect_session_tick_times,
    compute_macdbb_from_closes,
)


def test_compute_macdbb_from_closes_returns_signal_fields():
    rng = np.random.default_rng(0)
    closes = np.cumsum(rng.normal(0.001, 0.02, size=250)) + 100.0
    metrics = compute_macdbb_from_closes(closes)
    assert metrics is not None
    assert metrics["signal"] in {"LONG", "SHORT", "NEUTRAL"}
    assert 0.0 <= metrics["bb_pos"] <= 1.0


def test_quote_volume_24h_sums_close_times_volume():
    candles = [
        {"close": 10.0, "volume": 2.0},
        {"close": 11.0, "volume": 3.0},
    ]
    assert _quote_volume_24h(candles) == 53.0


def test_collect_session_tick_times_respects_until_cutoff():
    times = collect_session_tick_times([37], until=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc))
    assert times
    assert all(ts < dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc) for ts in times)
