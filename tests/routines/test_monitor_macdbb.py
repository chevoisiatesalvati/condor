"""Tests for monitor MACD-BB supplement persistence and resolve fallback."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from routines.lib.binance_candle_cache import save_candles
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import _aggregate_pair_requests
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig, TickMeta
from routines.macdbb_scanner_aggressive_hl_replay.monitor_macdbb import (
    batch_compute_macdbb_gaps,
    compute_macdbb_at_tick,
    flush_monitor_macdbb_buffer,
    parsed_report_to_macdbb_row,
    set_monitor_gap_recorder,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import configure_replay_data_sources
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index, nearest_report
from routines.macdbb_scanner_aggressive_hl_replay.signals import resolve_snapshot
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    append_monitor_macdbb_rows,
    configure_snapshot_dir,
    load_macdbb_index,
    load_manifest,
)
from routines.macdbb_scanner_aggressive_hl_replay.presets import resolve_config_with_preset
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import merge_timeline_config


def _write_1h_candles(
    tmp_path,
    pair: str,
    *,
    end: dt.datetime,
    count: int = 260,
    base_price: float = 2.5,
) -> None:
    end_ms = int(end.timestamp() * 1000)
    hour_ms = 3_600_000
    closes = np.cumsum(np.random.default_rng(42).normal(0.001, 0.02, size=count)) + base_price
    candles = [
        {
            "timestamp_ms": float(end_ms - (count - index - 1) * hour_ms),
            "open": float(closes[index]),
            "high": float(closes[index]) * 1.01,
            "low": float(closes[index]) * 0.99,
            "close": float(closes[index]),
            "volume": 1000.0,
        }
        for index in range(count)
    ]
    save_candles(pair, "1h", candles, cache_dir=tmp_path)


def test_compute_macdbb_at_tick_from_cached_candles(tmp_path):
    tick_time = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    _write_1h_candles(tmp_path, "WLD-USDT", end=tick_time)
    report = compute_macdbb_at_tick(
        "WLD-USD",
        tick_time,
        cache_dir=tmp_path,
        candle_source="binance_perpetual",
    )
    assert report is not None
    assert report.pair == "WLD-USD"
    assert report.interval == "1h"
    assert report.price > 0


def test_append_monitor_macdbb_rows_dedupes_and_merges_index(tmp_path):
    tick_time = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    _write_1h_candles(tmp_path / "cache", "WLD-USDT", end=tick_time)
    report = compute_macdbb_at_tick(
        "WLD-USD",
        tick_time,
        cache_dir=tmp_path / "cache",
        candle_source="binance_perpetual",
    )
    assert report is not None
    row = parsed_report_to_macdbb_row(report, tick_time)
    append_monitor_macdbb_rows([row], snapshot_dir=tmp_path)
    append_monitor_macdbb_rows([row], snapshot_dir=tmp_path)
    configure_snapshot_dir(tmp_path)
    index = load_macdbb_index(snapshot_dir=tmp_path)
    assert len(index) == 1
    assert index[0].pair == "WLD-USD"


def test_resolve_snapshot_monitor_fallback(tmp_path):
    tick_time = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    cache_dir = tmp_path / "cache"
    _write_1h_candles(cache_dir, "WLD-USDT", end=tick_time)
    configure_snapshot_dir(tmp_path)

    meta = TickMeta(tick=100, timestamp=tick_time, macd_pairs=[])
    config = DynamicStrategyReplayConfig(
        preset="custom",
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        hl_cache_dir=str(cache_dir),
        candle_source="binance_perpetual",
        time_window_min=15,
    )
    reports_by_pair = build_reports_by_pair(load_reports_index())
    assert nearest_report(reports_by_pair, "WLD-USD", tick_time, 15, interval="1h") is None

    snapshot = resolve_snapshot(
        "WLD-USD",
        meta,
        reports_by_pair,
        config,
        {},
        monitor_pair=True,
    )
    assert snapshot is not None
    assert snapshot.price > 0
    assert snapshot.source.startswith("monitor")
    assert snapshot.parsed is not None


def _timeline_config() -> DynamicStrategyReplayConfig:
    from pathlib import Path

    snapshot_dir = "data/replay_snapshots_binance_1y"
    configure_snapshot_dir(Path(snapshot_dir))
    manifest = load_manifest(snapshot_dir=snapshot_dir) or {}
    overrides = merge_timeline_config(
        {
            "preset": "hl_dynamic_timeline_refine_v5_winner_binance_1y",
            "snapshot_dir": snapshot_dir,
            "replay_mode": "timeline_backtest",
            "data_source": "snapshots",
        },
        range_start_utc=manifest.get("range_start_utc"),
        range_end_utc=manifest.get("range_end_utc"),
    )
    return resolve_config_with_preset(DynamicStrategyReplayConfig(**overrides))


def test_hydrated_timeline_ticks_have_queue_pairs():
    config = _timeline_config()
    configure_replay_data_sources(config)
    tick_maps, _, selected = load_replay_sessions(config)
    if not selected:
        pytest.skip("timeline ticks unavailable")
    tick_map = tick_maps[selected[0]]
    with_queue = sum(1 for meta in tick_map.values() if meta.macd_pairs or meta.queue_total)
    assert with_queue > len(tick_map) * 0.5


def test_aggregate_pair_requests_discovers_hydrated_pairs():
    config = _timeline_config()
    configure_replay_data_sources(config)
    tick_maps, _, selected = load_replay_sessions(config)
    if not selected:
        pytest.skip("timeline ticks unavailable")
    pair_requests = _aggregate_pair_requests({selected[0]: tick_maps[selected[0]]})
    assert len(pair_requests) > 30


def test_gap_recorder_and_batch_compute(tmp_path):
    tick_time = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    cache_dir = tmp_path / "cache"
    _write_1h_candles(cache_dir, "WLD-USDT", end=tick_time)
    gaps: list[tuple[str, str, dt.datetime]] = []
    set_monitor_gap_recorder(gaps, inline_compute=False)
    gaps.append(("20260301_120000", "WLD-USD", tick_time))
    set_monitor_gap_recorder(None)
    rows = batch_compute_macdbb_gaps(
        gaps,
        cache_dir=cache_dir,
        candle_source="binance_perpetual",
        max_workers=2,
    )
    assert len(rows) == 1
    assert rows[0]["pair"] == "WLD-USD"


def test_wld_supplement_row_visible_in_merged_index(tmp_path):
    tick_time = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    cache_dir = tmp_path / "cache"
    _write_1h_candles(cache_dir, "WLD-USDT", end=tick_time)
    configure_snapshot_dir(tmp_path)
    config = DynamicStrategyReplayConfig(
        preset="custom",
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        hl_cache_dir=str(cache_dir),
        candle_source="binance_perpetual",
        time_window_min=15,
    )
    meta = TickMeta(tick=1, timestamp=tick_time, macd_pairs=[])
    reports_by_pair = build_reports_by_pair(load_reports_index())
    snapshot = resolve_snapshot(
        "WLD-USD",
        meta,
        reports_by_pair,
        config,
        {},
        monitor_pair=True,
    )
    assert snapshot is not None
    flush_monitor_macdbb_buffer(snapshot_dir=tmp_path)
    configure_snapshot_dir(tmp_path)
    reports_by_pair = build_reports_by_pair(load_reports_index())
    after = nearest_report(reports_by_pair, "WLD-USD", tick_time, 15, interval="1h")
    assert after is not None
