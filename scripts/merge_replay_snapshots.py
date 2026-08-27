#!/usr/bin/env python3
"""Merge a snapshot backfill directory into an existing 60s (or other) store.

After the year backfill finishes:

    PYTHONPATH=. .venv/bin/python scripts/merge_replay_snapshots.py --require-complete

Then confirm 1y coverage:

    PYTHONPATH=. .venv/bin/python scripts/merge_replay_snapshots.py --check-year

Smoke one config on the merged 1y 60s range:

    PYTHONPATH=. .venv/bin/python scripts/run_macdbb_pullback_backtest.py \\
      --preset pullback_decay_2h_60s \\
      --range-start 2025-08-17T00:00:00Z \\
      --range-end 2026-08-17T10:20:00Z
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Concat parquet snapshots from --source into --dest (deduped)."
    )
    parser.add_argument(
        "--source",
        default="data/replay_snapshots_binance_60s_year_backfill",
        help="Backfill directory to merge from",
    )
    parser.add_argument(
        "--dest",
        default="data/replay_snapshots_binance_60s",
        help="Live snapshot store to merge into",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Refuse to merge if source manifest range_end is more than 1 day before dest range_start",
    )
    parser.add_argument(
        "--check-year",
        action="store_true",
        help="Do not merge; exit 0 if --dest already covers the 1y verify window",
    )
    parser.add_argument(
        "--verify-start",
        default="2025-08-17T00:00:00Z",
        help="UTC start used by --check-year",
    )
    parser.add_argument(
        "--verify-end",
        default="2026-08-17T10:20:00Z",
        help="UTC end used by --check-year",
    )
    return parser.parse_args()


def main() -> int:
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
        load_manifest,
        merge_snapshot_stores,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc

    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    source = Path(args.source)
    dest = Path(args.dest)
    if args.check_year:
        from routines.macdbb_pullback_hl_replay.sweep_automation import (
            verify_range_coverage_gap,
        )

        gap = verify_range_coverage_gap(
            str(dest),
            args.verify_start,
            args.verify_end,
        )
        if gap is not None:
            logging.error(
                "1y coverage missing: gap %s → %s (%.1fd). Manifest %s → %s",
                gap.gap_start_utc,
                gap.gap_end_utc,
                gap.gap_days,
                gap.coverage_start_utc,
                gap.coverage_end_utc,
            )
            return 1
        logging.info("1y 60s coverage OK: %s → %s", args.verify_start, args.verify_end)
        return 0
    source_manifest = load_manifest(snapshot_dir=source) or {}
    dest_manifest = load_manifest(snapshot_dir=dest) or {}
    if args.require_complete:
        source_end = source_manifest.get("range_end_utc")
        dest_start = dest_manifest.get("range_start_utc")
        if not source_end:
            logging.error("Source manifest missing range_end_utc; build is not complete")
            return 1
        if dest_start:
            gap_seconds = (
                parse_iso_utc(str(dest_start)) - parse_iso_utc(str(source_end))
            ).total_seconds()
            if gap_seconds > 86400:
                logging.error(
                    "Source ends at %s, dest starts at %s (%.1fd gap) — build is incomplete",
                    source_end,
                    dest_start,
                    gap_seconds / 86400.0,
                )
                return 1
    merged = merge_snapshot_stores(source, dest)
    print(json.dumps(merged, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
