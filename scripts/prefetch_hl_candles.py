#!/usr/bin/env python3
"""Bulk-download Hyperliquid OHLCV candles into data/hl_candles with a coverage manifest."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

import aiohttp

from routines.lib.hl_candle_cache import DEFAULT_CACHE_DIR, fetch_hl_candles_between_cached
from routines.lib.hl_candle_manifest import build_manifest, write_manifest
from routines.lib.hl_candles import configure_hl_rate_limit
from routines.lib.shared_universe import (
    load_intersection_manifest,
    trading_pairs_for_exchange,
)
from routines.macdbb_scanner_aggressive_hl_replay.report_backfill import BackfillSettings, fetch_hl_universe


def _parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prefetch HL OHLCV candles into local parquet cache.",
    )
    parser.add_argument("--range-start", required=True, help="UTC start (ISO date or datetime)")
    parser.add_argument("--range-end", required=True, help="UTC end (ISO date or datetime)")
    parser.add_argument(
        "--intervals",
        default="5m,1h,4h",
        help="Comma-separated candle intervals (default: 5m,1h,4h)",
    )
    parser.add_argument(
        "--pairs",
        default="",
        help="Comma-separated trading pairs (default: fetch HL universe)",
    )
    parser.add_argument(
        "--universe",
        action="store_true",
        help="Fetch current HL metaAndAssetCtxs universe (default when --pairs unset)",
    )
    parser.add_argument(
        "--universe-top-n",
        type=int,
        default=60,
        help="Max pairs when using HL universe, ranked by 24h volume (default: 60)",
    )
    parser.add_argument(
        "--intersection-manifest",
        type=Path,
        default=None,
        help="Shared Binance∩HL universe JSON (prefetch only intersection pairs)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Output cache directory (default: {DEFAULT_CACHE_DIR})",
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
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        help="Max parallel pair downloads",
    )
    parser.add_argument(
        "--include-1m-days",
        type=int,
        default=4,
        help="Also fetch 1m candles for the last N days (0 = skip)",
    )
    return parser.parse_args()


async def _prefetch_pair_interval(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
    cache_dir: Path,
) -> tuple[str, str, int]:
    async with semaphore:
        candles = await fetch_hl_candles_between_cached(
            pair,
            interval,
            start,
            end,
            session=session,
            cache_dir=cache_dir,
            use_cache=True,
            refresh_cache=False,
            fill_gaps=True,
        )
        return pair, interval, len(candles)


async def _run_prefetch(args: argparse.Namespace) -> dict[str, int]:
    configure_hl_rate_limit(
        request_interval_ms=args.request_interval_ms,
        max_retries=args.max_retries,
    )
    range_start = _parse_iso(args.range_start)
    range_end = _parse_iso(args.range_end)
    if range_end <= range_start:
        raise ValueError("range-end must be after range-start")

    intervals = [part.strip() for part in args.intervals.split(",") if part.strip()]
    if args.include_1m_days > 0 and "1m" not in intervals:
        intervals.append("1m")

    pairs: list[str]
    universe_source = "explicit"
    async with aiohttp.ClientSession() as session:
        if args.intersection_manifest:
            intersection = load_intersection_manifest(args.intersection_manifest)
            pairs = trading_pairs_for_exchange(intersection, "hyperliquid")
            universe_source = "intersection_manifest"
        elif args.pairs:
            pairs = [part.strip() for part in args.pairs.split(",") if part.strip()]
        else:
            universe = await fetch_hl_universe(session, exclude_hip3=True)
            if args.universe_top_n > 0:
                universe = universe[: args.universe_top_n]
            pairs = [row["trading_pair"] for row in universe]
            universe_source = "metaAndAssetCtxs"

        semaphore = asyncio.Semaphore(max(1, args.max_concurrent))
        tasks: list[asyncio.Task[tuple[str, str, int]]] = []
        now = dt.datetime.now(dt.timezone.utc)
        one_m_start = now - dt.timedelta(days=args.include_1m_days)

        for pair in pairs:
            for interval in intervals:
                start = one_m_start if interval == "1m" else range_start
                end = range_end if interval != "1m" else now
                tasks.append(
                    asyncio.create_task(
                        _prefetch_pair_interval(
                            session,
                            semaphore,
                            pair,
                            interval,
                            start,
                            end,
                            args.cache_dir,
                        )
                    )
                )

        totals = {"pairs": len(pairs), "tasks": len(tasks), "bars": 0, "errors": 0}
        for index, task in enumerate(asyncio.as_completed(tasks), start=1):
            try:
                pair, interval, bar_count = await task
                totals["bars"] += bar_count
                if index % 25 == 0 or index == len(tasks):
                    logging.info(
                        "Progress %d/%d — last=%s %s bars=%d",
                        index,
                        len(tasks),
                        pair,
                        interval,
                        bar_count,
                    )
            except Exception:
                totals["errors"] += 1
                logging.exception("Prefetch task failed")

    manifest = build_manifest(
        cache_dir=args.cache_dir,
        pairs=pairs,
        intervals=intervals,
        range_start=range_start,
        range_end=range_end,
        universe_source=universe_source,
    )
    write_manifest(manifest, cache_dir=args.cache_dir)
    return totals


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    totals = asyncio.run(_run_prefetch(args))
    logging.info("Prefetch complete: %s", totals)
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
