"""Tests for timeline tick chunking."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import iter_timeline_tick_chunks


def _tick_map(days: int, *, step_hours: int = 12) -> dict[int, TickMeta]:
    start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    tick_map: dict[int, TickMeta] = {}
    tick = 1
    current = start
    end = start + dt.timedelta(days=days)
    while current <= end:
        tick_map[tick] = TickMeta(tick=tick, timestamp=current, macd_pairs=[])
        tick += 1
        current += dt.timedelta(hours=step_hours)
    return tick_map


def test_iter_timeline_tick_chunks_splits_long_range():
    tick_map = _tick_map(60)
    chunks = iter_timeline_tick_chunks(tick_map, chunk_days=28, overlap_days=7)
    assert len(chunks) >= 2
    covered = {tick for chunk in chunks for tick in chunk}
    assert covered == set(tick_map)


def test_iter_timeline_tick_chunks_disabled_returns_full_map():
    tick_map = _tick_map(10)
    chunks = iter_timeline_tick_chunks(tick_map, chunk_days=0)
    assert chunks == [tick_map]
