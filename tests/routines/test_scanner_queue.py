"""Tests for scanner queue builder."""

from __future__ import annotations

from routines.macdbb_replay.reports import ParsedScannerReport, ScannerPairRow
from routines.macdbb_replay.scanner_queue import build_scanner_queue


def _sample_report() -> ParsedScannerReport:
    return ParsedScannerReport(
        total_analyzed=30,
        mature=[
            ScannerPairRow("BTC-USD", 2e9, 1.0, 0.30, 0.1, 0.2, 1.0),
            ScannerPairRow("ETH-USD", 9e8, 0.5, 0.40, 0.1, 0.2, 1.5),
        ],
        degen=[
            ScannerPairRow("PUMP-USD", 5e7, 10.0, 1.20, 0.5, 0.8, 8.0),
            ScannerPairRow("SOL-USD", 4e8, 2.0, 0.55, 0.2, 0.3, 3.0),
        ],
    )


def test_infer_regime_degen_when_degen_natr_cv_higher():
    parsed = ParsedScannerReport(
        total_analyzed=4,
        mature=[
            ScannerPairRow("BTC-USD", 2e9, 1.0, 0.30, 0.10, 0.2, 1.0),
        ],
        degen=[
            ScannerPairRow("PUMP-USD", 5e7, 10.0, 1.20, 0.50, 0.8, 8.0),
        ],
    )
    from routines.macdbb_replay.scanner_queue import _infer_regime

    assert _infer_regime(parsed) == "degen"


def test_build_scanner_queue_primary_and_review():
    params = {
        "natr_floor_mature_pct": 0.08,
        "natr_floor_degen_pct": 0.1,
        "macd_queue_primary_size": 2,
        "macd_primary_review_count": 2,
        "macd_queue_pass2_min": 3,
        "macd_queue_pass2_max": 4,
        "macd_queue_total_cap": 6,
        "min_tradeable_for_adaptive": 1,
    }
    result = build_scanner_queue(_sample_report(), params)
    assert result.regime in ("mature", "degen")
    assert len(result.queue_primary) <= 2
    assert len(result.macd_pairs) <= 2
    assert result.tradeable_count >= 1
