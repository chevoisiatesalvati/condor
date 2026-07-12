"""Tests for snapshot coverage gap detection."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.replay_range import (
    requested_range_exceeds_coverage,
    snapshot_coverage,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import compute_snapshot_gap
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import write_manifest
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc


def test_compute_snapshot_gap_forward_extension():
    coverage_end = parse_iso_utc("2026-06-19T21:00:00Z")
    requested_start = parse_iso_utc("2026-07-01T00:00:00Z")
    requested_end = parse_iso_utc("2026-07-08T23:59:59Z")

    gap = compute_snapshot_gap(
        requested_start,
        requested_end,
        coverage_start=parse_iso_utc("2025-07-07T00:00:00Z"),
        coverage_end=coverage_end,
    )

    assert gap is not None
    assert gap.gap_start_utc == "2026-07-01T00:00:00Z"
    assert gap.gap_end_utc == "2026-07-08T23:59:59Z"
    assert 7 <= gap.gap_days <= 9


def test_compute_snapshot_gap_returns_none_when_covered():
    coverage_start = parse_iso_utc("2026-06-01T00:00:00Z")
    coverage_end = parse_iso_utc("2026-06-30T23:59:59Z")
    requested_start = parse_iso_utc("2026-06-10T00:00:00Z")
    requested_end = parse_iso_utc("2026-06-15T23:59:59Z")

    gap = compute_snapshot_gap(
        requested_start,
        requested_end,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    assert gap is None


def test_requested_range_exceeds_coverage_from_manifest(tmp_path):
    write_manifest(
        {
            "version": 1,
            "updated_at": "2026-06-20T14:09:17+00:00",
            "range_start_utc": "2025-07-07T00:00:00Z",
            "range_end_utc": "2026-06-19T21:00:00Z",
            "tick_count": 100,
        },
        snapshot_dir=tmp_path,
    )
    config = DynamicStrategyReplayConfig(
        replay_mode="timeline_backtest",
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-08T23:59:59Z",
    )

    gap = requested_range_exceeds_coverage(config)
    assert gap is not None
    assert gap.gap_start_utc.startswith("2026-07-01")
    coverage = snapshot_coverage(tmp_path)
    assert coverage.end_utc == "2026-06-19T21:00:00Z"
