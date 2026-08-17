#!/usr/bin/env python3
"""Build config-independent market snapshots from local candle cache."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.report_backfill import collect_session_tick_times
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import (
    SnapshotBuildSettings,
    build_snapshots_for_range,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import DEFAULT_SNAPSHOT_DIR
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build scanner + MACD BB parquet snapshots for replay/sweeps.",
    )
    parser.add_argument("--range-start", help="UTC start for timeline ticks (ISO)")
    parser.add_argument("--range-end", help="UTC end for timeline ticks (ISO)")
    parser.add_argument(
        "--frequency-sec",
        type=int,
        default=1800,
        help="Synthetic tick interval for timeline mode (default 1800)",
    )
    parser.add_argument(
        "--sessions",
        default="",
        help="Session selector for journal tick times (e.g. 37-47)",
    )
    parser.add_argument(
        "--strategy-slug",
        default="macdbb_scanner_aggressive_hl",
        help="Trading agent slug for session tick collection",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help=f"Output snapshot directory (default: {DEFAULT_SNAPSHOT_DIR})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Candle cache directory (default depends on --candle-source)",
    )
    parser.add_argument(
        "--candle-source",
        choices=("hyperliquid", "binance_perpetual"),
        default="binance_perpetual",
        help="Exchange candle cache to read/fill (default: binance_perpetual)",
    )
    parser.add_argument(
        "--universe-top-n",
        type=int,
        default=100,
        help="Top pairs per venue when not using --intersection-manifest (0 = no limit)",
    )
    parser.add_argument(
        "--intersection-manifest",
        type=Path,
        default=None,
        help="Shared Binance∩HL universe JSON (same pair set on both venues)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild ticks even if already present in snapshot parquet",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N ticks (0 = all)",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=600,
        help="Minimum ms between HL REST requests for cache gap fill",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="HL 429/5xx retries per request",
    )
    parser.add_argument(
        "--candidate-pool",
        type=int,
        default=45,
        help="Top universe pairs to rank per tick",
    )
    parser.add_argument(
        "--volume-source",
        choices=("same", "binance_perpetual", "hyperliquid"),
        default="same",
        help="Candle cache for 24h volume ranking (default: same as --candle-source)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Append snapshot parquet every N built ticks (default: 50)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=30,
        help="Max concurrent candle fetches per tick (default: 30)",
    )
    parser.add_argument(
        "--file-cache-entries",
        type=int,
        default=80,
        help="Max pair-interval candle files kept in memory (default: 80)",
    )
    parser.add_argument(
        "--live-equivalent-queue",
        action="store_true",
        help="No NATR floor; store >=8 MACD review pairs (live Strategies queue)",
    )
    parser.add_argument(
        "--macd-review-count",
        type=int,
        default=5,
        help="MACD pairs stored per tick (raised to >=8 with --live-equivalent-queue)",
    )
    return parser.parse_args()


def _expand_session_selector(raw: str) -> list[int]:
    numbers: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start_num = int(start_text.strip())
            end_num = int(end_text.strip())
            if end_num < start_num:
                start_num, end_num = end_num, start_num
            numbers.extend(range(start_num, end_num + 1))
            continue
        numbers.append(int(part))
    return sorted(set(numbers))


async def _run_build(args: argparse.Namespace) -> dict[str, int]:
    live_eq = bool(args.live_equivalent_queue)
    settings = SnapshotBuildSettings(
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        cache_dir=args.cache_dir,
        frequency_sec=args.frequency_sec,
        volume_source=args.volume_source,
        intersection_manifest=args.intersection_manifest,
        universe_top_n=args.universe_top_n,
        candidate_pool=args.candidate_pool,
        batch_size=args.batch_size,
        max_concurrent=args.max_concurrent,
        file_cache_entries=args.file_cache_entries,
        request_interval_ms=args.request_interval_ms,
        max_retries=args.max_retries,
        force=args.force,
        limit=args.limit,
        sessions=args.sessions,
        live_equivalent_queue=live_eq,
        macd_review_count=int(args.macd_review_count),
        exclude_hip3=False if live_eq else True,
    )
    if args.sessions:
        session_nums = _expand_session_selector(args.sessions)
        tick_times = collect_session_tick_times(
            session_nums,
            strategy_slug=args.strategy_slug,
        )
        if not tick_times:
            logging.info("No session ticks to build.")
            return {"ticks": 0, "built": 0, "skipped": 0, "errors": 0}
        range_start = tick_times[0]
        range_end = tick_times[-1]
        result = await build_snapshots_for_range(
            range_start,
            range_end,
            settings,
            tick_times=tick_times,
        )
    else:
        if not args.range_start or not args.range_end:
            raise ValueError("Provide --range-start/--range-end or --sessions")
        range_start = parse_iso_utc(args.range_start)
        range_end = parse_iso_utc(args.range_end)
        result = await build_snapshots_for_range(range_start, range_end, settings)
    return {
        "ticks": result.ticks,
        "built": result.built,
        "skipped": result.skipped,
        "errors": result.errors,
    }


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    totals = asyncio.run(_run_build(args))
    logging.info("Snapshot build complete: %s", totals)
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
