"""Tests for backtest snapshot auto-update integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from routines.macdbb_scanner_aggressive_hl_backtest import _ensure_snapshot_coverage
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.replay_range import CoverageGap
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import BuildResult
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import write_manifest


@pytest.mark.asyncio
async def test_ensure_snapshot_coverage_skips_when_no_gap(tmp_path):
    write_manifest(
        {
            "version": 1,
            "range_start_utc": "2026-06-01T00:00:00Z",
            "range_end_utc": "2026-07-30T23:59:59Z",
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

    with patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.build_snapshots_for_range",
        new_callable=AsyncMock,
    ) as build_mock:
        result = await _ensure_snapshot_coverage(config)

    assert result is None
    build_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_snapshot_coverage_builds_when_gap_exists(tmp_path):
    write_manifest(
        {
            "version": 1,
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
        range_end_utc="2026-07-02T23:59:59Z",
        auto_update_snapshots=True,
        max_auto_snapshot_days=14,
    )

    with patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.build_snapshots_for_range",
        new_callable=AsyncMock,
        return_value=BuildResult(ticks=10, built=10, skipped=0, errors=0),
    ) as build_mock, patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.refresh_snapshot_caches",
    ) as refresh_mock:
        result = await _ensure_snapshot_coverage(config)

    assert result is None
    build_mock.assert_called_once()
    refresh_mock.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_snapshot_coverage_builds_july_window_within_cap(tmp_path):
    """Jul 1–8 vs manifest ending Jun 19: build ~8 days, not blocked by 14-day cap."""
    write_manifest(
        {
            "version": 1,
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
        auto_update_snapshots=True,
        max_auto_snapshot_days=14,
    )

    with patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.build_snapshots_for_range",
        new_callable=AsyncMock,
        return_value=BuildResult(ticks=384, built=384, skipped=0, errors=0),
    ) as build_mock, patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.refresh_snapshot_caches",
    ) as refresh_mock:
        result = await _ensure_snapshot_coverage(config)

    assert result is None
    build_mock.assert_called_once()
    refresh_mock.assert_called_once()
    args = build_mock.call_args[0]
    assert args[0].startswith("2026-07-01")
    assert args[1].startswith("2026-07-08")


@pytest.mark.asyncio
async def test_ensure_snapshot_coverage_returns_error_when_capped(tmp_path):
    write_manifest(
        {
            "version": 1,
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
        auto_update_snapshots=True,
        max_auto_snapshot_days=14,
    )

    with patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.requested_range_exceeds_coverage",
        return_value=CoverageGap(
            gap_start_utc="2026-07-01T00:00:00Z",
            gap_end_utc="2026-07-30T23:59:59Z",
            gap_days=30.0,
            coverage_end_utc="2026-06-19T21:00:00Z",
            coverage_start_utc="2025-07-07T00:00:00Z",
        ),
    ):
        result = await _ensure_snapshot_coverage(config)

    assert result is not None
    assert "Auto-update cap" in result
    assert "build_replay_snapshots.py" in result


@pytest.mark.asyncio
async def test_ensure_snapshot_coverage_disabled_returns_manual_hint(tmp_path):
    config = DynamicStrategyReplayConfig(
        replay_mode="timeline_backtest",
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-08T23:59:59Z",
        auto_update_snapshots=False,
    )

    with patch(
        "routines.macdbb_scanner_aggressive_hl_backtest.requested_range_exceeds_coverage",
        return_value=CoverageGap(
            gap_start_utc="2026-07-01T00:00:00Z",
            gap_end_utc="2026-07-08T23:59:59Z",
            gap_days=8.0,
            coverage_end_utc="2026-06-19T21:00:00Z",
            coverage_start_utc=None,
        ),
    ):
        result = await _ensure_snapshot_coverage(config)

    assert result is not None
    assert "Auto-update is disabled" in result
