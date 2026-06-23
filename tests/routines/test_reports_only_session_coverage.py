"""Report-index coverage vs session tick timestamps (reports_only replay)."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    build_reports_by_pair,
    load_reports_index,
    load_scanner_reports_index,
    nearest_report,
    nearest_scanner_report,
)
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import build_session_parity_ticks
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_tick_schedule_file

STRATEGY_SLUG = "macdbb_scanner_aggressive_hl"
SESSIONS_DIR = TRADING_AGENTS_DIR / STRATEGY_SLUG / "sessions"
TIME_WINDOW_MIN = 30


def _session_tick_range(session_num: int) -> tuple[dt.datetime, dt.datetime] | None:
    journal_path = SESSIONS_DIR / f"session_{session_num}" / "journal.md"
    if not journal_path.is_file():
        return None
    schedule = parse_tick_schedule_file(journal_path)
    if not schedule:
        return None
    ticks = sorted(schedule)
    return schedule[ticks[0]].timestamp, schedule[ticks[-1]].timestamp


def _scanner_hits_for_session(session_num: int, config: DynamicStrategyReplayConfig) -> tuple[int, int]:
    session_dir = SESSIONS_DIR / f"session_{session_num}"
    ticks, _, _ = build_session_parity_ticks(session_dir, STRATEGY_SLUG, config=config)
    if not ticks:
        return 0, 0
    scanner_reports = load_scanner_reports_index()
    macd_by_pair = build_reports_by_pair(load_reports_index())
    scanner_hits = 0
    macd_hits = 0
    for meta in ticks.values():
        if nearest_scanner_report(scanner_reports, meta.timestamp, TIME_WINDOW_MIN):
            scanner_hits += 1
        for pair in meta.macd_pairs[:3]:
            if nearest_report(
                macd_by_pair,
                pair,
                meta.timestamp,
                TIME_WINDOW_MIN,
                interval="1h",
            ):
                macd_hits += 1
                break
    return scanner_hits, len(ticks)


def _live_scanner_reports():
    return [
        row
        for row in load_scanner_reports_index()
        if "backfill" not in row.filename
    ]


def _live_macd_reports():
    return [
        row
        for row in load_reports_index()
        if "backfill" not in row.filename
    ]


def test_report_index_starts_after_early_sessions():
    """Saved live scanner/MACD HTML reports begin ~Jun 11; sessions 37-46 predate that."""
    scanner = _live_scanner_reports()
    macd = _live_macd_reports()
    assert scanner and macd

    earliest_scanner = min(r.created_at for r in scanner)
    earliest_macd = min(r.created_at for r in macd)
    assert earliest_scanner >= dt.datetime(2026, 6, 11, tzinfo=dt.timezone.utc)
    assert earliest_macd >= dt.datetime(2026, 6, 11, tzinfo=dt.timezone.utc)

    session_37 = _session_tick_range(37)
    assert session_37 is not None
    s37_end = session_37[1]
    assert s37_end < earliest_scanner, (
        "session 37 ends before first indexed scanner report — reports_only cannot "
        "resolve signals for that session"
    )


def test_session_37_live_reports_absent_before_backfill():
    """Without backfill files, session 37 has journal queue fields but no live HTML coverage."""
    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_session_parity",
        data_source="reports_only",
        config_source="preset",
    )
    session_dir = SESSIONS_DIR / "session_37"
    ticks, _, _ = build_session_parity_ticks(session_dir, STRATEGY_SLUG, config=config)
    assert ticks
    assert any(meta.tradeable_count and meta.tradeable_count > 0 for meta in ticks.values())
    assert any(meta.macd_pairs for meta in ticks.values())

    scanner_reports = _live_scanner_reports()
    macd_by_pair = build_reports_by_pair(_live_macd_reports())
    scanner_hits = 0
    macd_hits = 0
    for meta in ticks.values():
        if nearest_scanner_report(scanner_reports, meta.timestamp, TIME_WINDOW_MIN):
            scanner_hits += 1
        for pair in meta.macd_pairs[:3]:
            if nearest_report(
                macd_by_pair,
                pair,
                meta.timestamp,
                TIME_WINDOW_MIN,
                interval="1h",
            ):
                macd_hits += 1
                break
    assert scanner_hits == 0
    assert macd_hits == 0


def test_session_48_has_scanner_and_macd_report_coverage():
    """Later sessions overlap the saved report index — reports_only can compute signals."""
    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_session_parity",
        data_source="reports_only",
        config_source="preset",
    )
    scanner_hits, total = _scanner_hits_for_session(48, config)
    assert total > 0
    assert scanner_hits == total

    session_dir = SESSIONS_DIR / "session_48"
    ticks, _, _ = build_session_parity_ticks(session_dir, STRATEGY_SLUG, config=config)
    macd_by_pair = build_reports_by_pair(load_reports_index())
    macd_hits = 0
    for meta in ticks.values():
        for pair in meta.macd_pairs[:3]:
            if nearest_report(
                macd_by_pair,
                pair,
                meta.timestamp,
                TIME_WINDOW_MIN,
                interval="1h",
            ):
                macd_hits += 1
                break
    assert macd_hits == len(ticks)
