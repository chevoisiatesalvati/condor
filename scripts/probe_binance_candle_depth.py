#!/usr/bin/env python3
"""Probe Binance USDT-M perpetual kline historical depth."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from routines.lib.binance_candle_manifest import (
    DEFAULT_CACHE_DIR,
    run_depth_probe_and_save,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe Binance perpetual kline retention (fast mode: anchor windows + "
            "binary-search earliest bar)."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Output directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--pairs",
        default="BTC-USDT",
        help="Comma-separated trading pairs (default: BTC-USDT)",
    )
    parser.add_argument(
        "--binary-search-years",
        type=int,
        default=3,
        help="How many years back to search for earliest bar (default: 3)",
    )
    parser.add_argument(
        "--no-earliest-search",
        action="store_true",
        help="Skip binary-search earliest-bar estimate",
    )
    parser.add_argument(
        "--no-1m-recent",
        action="store_true",
        help="Skip recent 1m sanity check",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=100,
        help="Minimum ms between Binance REST requests",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=6,
        help="429/5xx retries per request",
    )
    return parser.parse_args()


def _log_payload(payload: dict) -> None:
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
    logging.info("Estimated API requests: %s", payload.get("estimated_api_requests"))


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
            find_earliest=not args.no_earliest_search,
            include_1m_recent=not args.no_1m_recent,
            binary_search_years=args.binary_search_years,
        )
    )
    _log_payload(payload)
    logging.info("Wrote %s", args.cache_dir / "depth_probe.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
