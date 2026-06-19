"""Tests for parquet snapshot store."""

from __future__ import annotations

import datetime as dt

import numpy as np

from routines.macdbb_replay.models import ParsedReport
from routines.macdbb_replay.reports import (
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
from routines.macdbb_replay.snapshot_store import (
    append_states,
    configure_snapshot_dir,
    load_parsed_macdbb_snapshot,
    load_parsed_scanner_snapshot,
)
from routines.macdbb_replay.tick_market_state import (
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
