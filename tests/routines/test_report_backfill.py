"""Unit tests for report backfill helpers."""

from __future__ import annotations

import datetime as dt

import numpy as np

from routines.macdbb_replay.report_backfill import (
    CandleCache,
    collect_session_tick_times,
)
from routines.macdbb_replay.tick_market_state import (
    compute_macdbb_from_closes,
    quote_volume_24h,
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
    assert quote_volume_24h(candles) == 53.0


def test_candle_cache_delegates_to_disk_cache(tmp_path, monkeypatch):
    import asyncio

    fetched: list[tuple[str, str]] = []

    async def _fake_fetch(*args, **kwargs):
        fetched.append((args[0], args[1]))
        return [{"timestamp_ms": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]

    monkeypatch.setattr(
        "routines.macdbb_replay.report_backfill.fetch_hl_candles_between_cached",
        _fake_fetch,
    )

    async def _run():
        import aiohttp

        async with aiohttp.ClientSession() as session:
            cache = CandleCache(session=session, cache_dir=tmp_path)
            end = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
            await cache.get_interval_window("BTC-USD", "5m", end, 30)
            await cache.get_interval_window("BTC-USD", "5m", end, 30)

    asyncio.run(_run())
    assert len(fetched) == 1


def test_collect_session_tick_times_respects_until_cutoff():
    times = collect_session_tick_times([37], until=dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc))
    assert times
    assert all(ts < dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc) for ts in times)
