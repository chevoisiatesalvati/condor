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


def test_configure_replay_data_sources_passes_timeline_range(tmp_path, monkeypatch):
    posted: dict[str, object] = {}

    def _warm(snapshot_dir, *, range_start_utc=None, range_end_utc=None):
        posted["dir"] = snapshot_dir
        posted["start"] = range_start_utc
        posted["end"] = range_end_utc

    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.replay_data.warm_snapshot_caches",
        _warm,
    )
    configure_snapshot_dir(None)
    config = DynamicStrategyReplayConfig(
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        range_start_utc="2026-07-18T00:00:00Z",
        range_end_utc="2026-08-17T10:20:00Z",
    )
    configure_replay_data_sources(config)
    assert posted["start"] == "2026-07-18T00:00:00Z"
    assert posted["end"] == "2026-08-17T10:20:00Z"
    configure_snapshot_dir(None)


def test_refresh_snapshot_caches_passes_timeline_range(tmp_path, monkeypatch):
    posted: dict[str, object] = {}

    def _reload(snapshot_dir, *, range_start_utc=None, range_end_utc=None):
        posted["dir"] = snapshot_dir
        posted["start"] = range_start_utc
        posted["end"] = range_end_utc

    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.replay_data.reload_snapshot_caches",
        _reload,
    )
    config = DynamicStrategyReplayConfig(
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        range_start_utc="2026-07-18T00:00:00Z",
        range_end_utc="2026-08-17T10:20:00Z",
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
        refresh_snapshot_caches,
    )

    refresh_snapshot_caches(config)
    assert posted["start"] == "2026-07-18T00:00:00Z"
    assert posted["end"] == "2026-08-17T10:20:00Z"
