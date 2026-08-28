"""Tests for parquet snapshot store."""

from __future__ import annotations

import datetime as dt

import numpy as np

from routines.macdbb_scanner_aggressive_hl_replay.models import ParsedReport
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    ParsedScannerReport,
    ScannerPairRow,
    build_reports_by_pair,
    load_parsed_report,
    load_parsed_scanner_report,
    load_reports_index,
    load_scanner_reports_index,
    nearest_report,
    nearest_scanner_report,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    append_states,
    configure_snapshot_dir,
    is_snapshot_store_active,
    load_macdbb_index,
    load_parsed_macdbb_snapshot,
    load_parsed_scanner_snapshot,
    reload_snapshot_caches,
    warm_snapshot_caches,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import (
    TickMarketState,
    compute_macdbb_from_closes,
    metrics_to_parsed_report,
)


def _sample_state(tick_time: dt.datetime) -> TickMarketState:
    closes = np.cumsum(np.random.default_rng(1).normal(0.001, 0.02, size=250)) + 100.0
    metrics = compute_macdbb_from_closes(closes)
    assert metrics is not None
    parsed = ParsedScannerReport(
        total_analyzed=2,
        mature=[
            ScannerPairRow(
                pair="BTC-USD",
                volume_24h_usd=5_000_000.0,
                price_change_24h=1.2,
                natr_mean=0.5,
                natr_cv=0.2,
                bucket_cv=0.3,
                price_range_pct=4.0,
            )
        ],
        degen=[
            ScannerPairRow(
                pair="DOGE-USD",
                volume_24h_usd=3_000_000.0,
                price_change_24h=-2.0,
                natr_mean=1.1,
                natr_cv=0.8,
                bucket_cv=0.9,
                price_range_pct=12.0,
            )
        ],
        lookback_hours=6,
    )
    return TickMarketState(
        tick_time=tick_time,
        scanner_interval="5m",
        parsed_scanner=parsed,
        macdbb_reports=[metrics_to_parsed_report("BTC-USD", "1h", metrics)],
        macd_pairs=["BTC-USD"],
    )


def test_snapshot_store_batch_append(tmp_path):
    tick_a = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc)
    tick_b = dt.datetime(2026, 6, 1, 12, 30, tzinfo=dt.timezone.utc)
    append_states([_sample_state(tick_a), _sample_state(tick_b)], snapshot_dir=tmp_path)
    configure_snapshot_dir(tmp_path)

    scanner_index = load_scanner_reports_index()
    assert len(scanner_index) == 2


def test_snapshot_store_round_trip_and_report_loader(tmp_path):
    tick_time = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc)
    append_states([_sample_state(tick_time)], snapshot_dir=tmp_path)
    configure_snapshot_dir(tmp_path)

    scanner_index = load_scanner_reports_index()
    assert len(scanner_index) == 1
    scanner_meta = nearest_scanner_report(scanner_index, tick_time, max_window_minutes=15)
    assert scanner_meta is not None

    parsed_scanner = load_parsed_scanner_report(scanner_meta)
    assert parsed_scanner is not None
    assert parsed_scanner.total_analyzed == 2
    assert parsed_scanner.mature[0].pair == "BTC-USD"

    reports = load_reports_index()
    by_pair = build_reports_by_pair(reports)
    report_meta = nearest_report(by_pair, "BTC-USD", tick_time, max_window_minutes=15, interval="1h")
    assert report_meta is not None

    parsed = load_parsed_report(report_meta)
    assert parsed is not None
    assert parsed.pair == "BTC-USD"
    assert parsed.interval == "1h"
    assert parsed.signal in {"LONG", "SHORT", "NEUTRAL"}


def test_reload_snapshot_caches_picks_up_incremental_append(tmp_path):
    """Simulate auto-update: warm on empty dir, append, reload must see macdbb rows."""
    configure_snapshot_dir(tmp_path)
    warm_snapshot_caches(tmp_path)
    assert load_macdbb_index(snapshot_dir=tmp_path) == []

    tick_time = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    append_states([_sample_state(tick_time)], snapshot_dir=tmp_path)

    reload_snapshot_caches(tmp_path)
    configure_snapshot_dir(tmp_path)

    assert is_snapshot_store_active()
    reports = load_macdbb_index(snapshot_dir=tmp_path)
    assert len(reports) == 1
    assert reports[0].pair == "BTC-USD"
    assert len(load_reports_index()) == 1


def test_warm_and_refresh_keep_range_limited_parsed_cache(tmp_path):
    from routines.macdbb_scanner_aggressive_hl_replay.models import (
        DynamicStrategyReplayConfig,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
        refresh_snapshot_caches,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
        load_parsed_scanner_snapshot,
        load_scanner_index,
        parsed_snapshot_cache_range,
    )

    tick_a = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc)
    tick_b = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
    append_states([_sample_state(tick_a), _sample_state(tick_b)], snapshot_dir=tmp_path)
    configure_snapshot_dir(tmp_path)
    warm_snapshot_caches(
        tmp_path,
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-31T23:59:59Z",
    )
    assert len(load_scanner_index(snapshot_dir=tmp_path)) == 1
    start_ms, end_ms = parsed_snapshot_cache_range()
    assert start_ms is not None and end_ms is not None
    assert start_ms < tick_b.timestamp() * 1000 < end_ms

    config = DynamicStrategyReplayConfig(
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        range_start_utc="2026-07-01T00:00:00Z",
        range_end_utc="2026-07-31T23:59:59Z",
    )
    refresh_snapshot_caches(config)
    assert parsed_snapshot_cache_range() == (start_ms, end_ms)
    assert len(load_scanner_index(snapshot_dir=tmp_path)) == 1

    meta = load_scanner_index(snapshot_dir=tmp_path)[0]
    parsed = load_parsed_scanner_snapshot(meta, snapshot_dir=tmp_path)
    assert parsed is not None
    assert parsed.mature[0].pair == "BTC-USD"

    reload_snapshot_caches(tmp_path)
    assert len(load_scanner_index(snapshot_dir=tmp_path)) == 2
    assert parsed_snapshot_cache_range() == (None, None)


def test_metrics_to_parsed_report_matches_compute_fields():
    closes = np.cumsum(np.random.default_rng(2).normal(0.001, 0.02, size=250)) + 50.0
    metrics = compute_macdbb_from_closes(closes)
    assert metrics is not None
    parsed = metrics_to_parsed_report("ETH-USD", "4h", metrics)
    assert isinstance(parsed, ParsedReport)
    assert parsed.pair == "ETH-USD"
    assert parsed.interval == "4h"
    assert parsed.price == metrics["close"]
    assert parsed.bullish_cross == metrics["c_long_cross"]


def test_merge_snapshot_stores_dedupes_and_extends_range(tmp_path):
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
        load_manifest,
        merge_snapshot_stores,
        write_manifest,
    )

    source = tmp_path / "backfill"
    dest = tmp_path / "live"
    dest_tick = dt.datetime(2026, 5, 6, 0, 0, tzinfo=dt.timezone.utc)
    source_tick = dt.datetime(2025, 8, 17, 0, 0, tzinfo=dt.timezone.utc)
    overlap_tick = dest_tick
    append_states([_sample_state(dest_tick)], snapshot_dir=dest)
    write_manifest(
        {
            "range_start_utc": "2026-05-06T00:00:00Z",
            "range_end_utc": "2026-08-17T10:20:00Z",
            "tick_count": 1,
            "frequency_sec": 60,
        },
        snapshot_dir=dest,
    )
    append_states(
        [_sample_state(source_tick), _sample_state(overlap_tick)],
        snapshot_dir=source,
    )
    write_manifest(
        {
            "range_start_utc": "2025-08-17T00:00:00Z",
            "range_end_utc": "2026-05-05T23:59:59Z",
            "tick_count": 2,
            "frequency_sec": 60,
        },
        snapshot_dir=source,
    )
    merged = merge_snapshot_stores(source, dest)
    assert merged["range_start_utc"] == "2025-08-17T00:00:00Z"
    assert merged["range_end_utc"] == "2026-08-17T10:20:00Z"
    assert merged["tick_count"] == 2
    assert str(source) in merged["merged_from"]
    reloaded = load_manifest(snapshot_dir=dest)
    assert reloaded is not None
    assert reloaded["tick_count"] == 2
