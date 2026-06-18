"""Tests for tick schedule helpers."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_replay.tick_schedule import (
    build_range_tick_schedule,
    parse_tick_schedule,
)


def test_parse_tick_schedule_journal_ticks_only():
    journal = """
## Ticks
- tick#1 | 2026-06-10 12:00 | actions=0 | hold
- tick#2 | 2026-06-10 12:30 | actions=1 | opened
## Decisions
- **#1** tick=1 entry_class=hold signals_1h=BTC-USD:bb=50
"""
    ticks = parse_tick_schedule(journal)
    assert set(ticks) == {1, 2}
    assert ticks[1].timestamp == dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)
    assert ticks[1].signals_1h == {}
    assert ticks[1].macd_pairs == []


def test_build_range_tick_schedule():
    start = dt.datetime(2026, 6, 10, 0, 0, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 6, 10, 1, 0, tzinfo=dt.timezone.utc)
    ticks = build_range_tick_schedule(start, end, frequency_sec=1800)
    assert list(ticks.keys()) == [1, 2, 3]
    assert ticks[1].timestamp == start
    assert ticks[3].timestamp == end
