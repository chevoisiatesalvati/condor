"""Tests for replay data source configuration."""

from __future__ import annotations

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
    configure_replay_data_sources,
    is_report_driven_data_source,
    uses_snapshot_store,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import configure_snapshot_dir, get_snapshot_dir


def test_is_report_driven_includes_snapshots():
    assert is_report_driven_data_source("reports_only")
    assert is_report_driven_data_source("snapshots")
    assert not is_report_driven_data_source("journal_first")


def test_configure_replay_data_sources_sets_snapshot_dir(tmp_path):
    configure_snapshot_dir(None)
    config = DynamicStrategyReplayConfig(
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
    )
    assert uses_snapshot_store(config)
    configure_replay_data_sources(config)
    assert get_snapshot_dir() == tmp_path
    configure_snapshot_dir(None)
