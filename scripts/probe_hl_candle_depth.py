#!/usr/bin/env python3
"""Probe Hyperliquid candleSnapshot historical depth and write depth_probe.json."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from routines.lib.hl_candle_cache import DEFAULT_CACHE_DIR
from routines.lib.hl_candle_manifest import run_depth_probe_and_save


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe HL candleSnapshot retention. Default fast mode uses small anchor "
            "windows (~1 request each) plus binary search for earliest bar."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Candle cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--pairs",
        default="BTC-USD",
        help="Comma-separated trading pairs to probe (default: BTC-USD only)",
    )
    parser.add_argument(
        "--mode",
        choices=("fast", "full"),
        default="fast",
        help="fast: anchor + binary search (~15-40 requests/pair). full: download entire ranges (slow).",
    )
    parser.add_argument(
        "--no-earliest-search",
        action="store_true",
        help="Fast mode only: skip binary-search earliest-bar estimate",
    )
    parser.add_argument(
        "--no-1m-recent",
        action="store_true",
        help="Fast mode only: skip recent 1m sanity check",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=600,
        help="Minimum ms between HL REST requests",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="HL 429/5xx retries per request",
    )
    return parser.parse_args()


def _log_payload(payload: dict) -> None:
    mode = payload.get("mode", "full")
    if mode == "fast":
        for row in payload.get("anchor_results", []):
            if row.get("probe_type") == "recent_1m":
                logging.info(
                    "%s 1m recent -> has_data=%s bars=%s",
                    row["pair"],
                    row.get("has_data"),
                    row.get("bar_count"),
                )
                continue
            logging.info(
                "%s %s anchor %sy -> has_data=%s bars=%s earliest=%s",
                row["pair"],
                row["interval"],
                row.get("lookback_years"),
                row.get("has_data"),
                row.get("bar_count"),
                row.get("earliest_utc"),
            )
        for row in payload.get("earliest_results", []):
            logging.info(
                "%s %s earliest (~%d requests) -> %s",
                row["pair"],
                row["interval"],
                row.get("api_requests", 0),
                row.get("earliest_utc") or row.get("error", "no data"),
            )
        logging.info(
            "Estimated API requests: %s",
            payload.get("estimated_api_requests"),
        )
        return

    for row in payload.get("results", []):
        logging.info(
            "%s %s %sy -> bars=%s earliest=%s",
            row["pair"],
            row["interval"],
            row.get("lookback_years"),
            row.get("bar_count"),
            row.get("earliest_utc"),
        )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    pairs = [part.strip() for part in args.pairs.split(",") if part.strip()]
    payload = asyncio.run(
        run_depth_probe_and_save(
            cache_dir=args.cache_dir,
            pairs=pairs,
            request_interval_ms=args.request_interval_ms,
            max_retries=args.max_retries,
            mode=args.mode,
            find_earliest=not args.no_earliest_search,
            include_1m_recent=not args.no_1m_recent,
        )
    )
    _log_payload(payload)
    probe_path = args.cache_dir / "depth_probe.json"
    logging.info("Wrote %s", probe_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
