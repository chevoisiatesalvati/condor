"""Tests for snapshot build helpers."""

from __future__ import annotations

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import (
    merge_manifest,
    settings_from_replay_config,
)


def test_merge_manifest_extends_range_and_tick_count():
    existing = {
        "version": 1,
        "range_start_utc": "2025-07-07T00:00:00Z",
        "range_end_utc": "2026-06-19T21:00:00Z",
        "tick_count": 13719,
        "monitor_macdbb_rows": 149785,
    }
    merged = merge_manifest(
        existing,
        built=42,
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-08T23:59:59Z",
        snapshot_dir=__import__("pathlib").Path("data/replay_snapshots_binance_1y"),
        cache_dir=__import__("pathlib").Path("data/binance_candles"),
        candle_source="binance_perpetual",
        volume_source="binance_perpetual",
        frequency_sec=1800,
        intersection_manifest=None,
        sessions="",
    )

    assert merged["range_start_utc"] == "2025-07-07T00:00:00Z"
    assert merged["range_end_utc"] == "2026-07-08T23:59:59Z"
    assert merged["tick_count"] == 13761
    assert merged["monitor_macdbb_rows"] == 149785


def test_merge_manifest_creates_new_when_empty():
    merged = merge_manifest(
        None,
        built=10,
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-08T23:59:59Z",
        snapshot_dir=__import__("pathlib").Path("data/replay_snapshots"),
        cache_dir=__import__("pathlib").Path("data/binance_candles"),
        candle_source="binance_perpetual",
        volume_source="binance_perpetual",
        frequency_sec=1800,
        intersection_manifest=None,
        sessions="",
    )

    assert merged["tick_count"] == 10
    assert merged["range_start_utc"] == "2026-07-01T00:00:00Z"
    assert merged["range_end_utc"] == "2026-07-08T23:59:59Z"


def test_live_equivalent_snapshot_settings_match_live_scanner():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        live_equivalent_queue=True,
        snapshot_dir="data/replay_snapshots_hl_60s",
    )
    settings = settings_from_replay_config(config)
    assert settings.live_equivalent_queue is True
    assert settings.universe_top_n == 0
    assert settings.candidate_pool == 80
    assert settings.exclude_hip3 is False
    assert settings.macd_review_count == 8
    assert settings.snapshot_dir.name == "replay_snapshots_hl_60s"
