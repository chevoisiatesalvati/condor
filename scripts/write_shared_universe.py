#!/usr/bin/env python3
"""Write Binance ∩ Hyperliquid shared universe manifest for aligned backtests."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import aiohttp

from routines.lib.shared_universe import (
    DEFAULT_INTERSECTION_MANIFEST,
    fetch_intersection_universe,
    write_intersection_manifest,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build shared intersection universe manifest (Binance ∩ HL).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_INTERSECTION_MANIFEST),
        help=f"Output JSON path (default: {DEFAULT_INTERSECTION_MANIFEST})",
    )
    parser.add_argument(
        "--top-n-per-exchange",
        type=int,
        default=100,
        help="How many top-volume pairs to consider per venue before intersecting",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=60,
        help="Max shared pairs to keep after intersection (default: 60)",
    )
    parser.add_argument(
        "--min-volume-usd",
        type=float,
        default=2_000_000.0,
        help="Minimum 24h quote volume on each venue",
    )
    parser.add_argument(
        "--rank-by",
        choices=("min", "binance", "hl"),
        default="min",
        help="Rank intersection by min/binance/hl 24h volume",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        return await fetch_intersection_universe(
            session,
            top_n_per_exchange=args.top_n_per_exchange,
            max_pairs=args.max_pairs,
            min_volume_usd=args.min_volume_usd,
            rank_by=args.rank_by,
        )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    intersection = asyncio.run(_run(args))
    path = write_intersection_manifest(
        intersection,
        path=args.output,
        metadata={
            "top_n_per_exchange": args.top_n_per_exchange,
            "max_pairs": args.max_pairs,
            "min_volume_usd": args.min_volume_usd,
            "rank_by": args.rank_by,
        },
    )
    bases = [row["canonical_base"] for row in intersection]
    logging.info("Shared universe: %d pairs", len(intersection))
    logging.info("Bases: %s", ", ".join(bases))
    logging.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
