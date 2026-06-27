#!/usr/bin/env python3
"""Prefetch one year of Binance USDT-M klines for the full liquid universe."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

import aiohttp

from routines.lib.binance_candle_cache import DEFAULT_CACHE_DIR
from routines.macdbb_scanner_aggressive_hl_replay.report_backfill import fetch_binance_universe


def _parse_args() -> argparse.Namespace:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    default_start = (now - dt.timedelta(days=365)).isoformat().replace("+00:00", "Z")
    default_end = now.isoformat().replace("+00:00", "Z")

    parser = argparse.ArgumentParser(
        description="Backfill ~1 year of Binance perpetual klines for mega timeline sweeps.",
    )
    parser.add_argument("--range-start", default=default_start)
    parser.add_argument("--range-end", default=default_end)
    parser.add_argument(
        "--intervals",
        default="5m,1h,4h",
        help="Kline intervals for the full range (default: 5m,1h,4h)",
    )
    parser.add_argument(
        "--min-volume-usd",
        type=float,
        default=2_000_000.0,
        help="Include pairs with at least this 24h quote volume (default: 2M)",
    )
    parser.add_argument(
        "--include-all-pairs",
        action="store_true",
        help="Set min volume to 0 and fetch every USDT-M pair (slow, large cache)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
    )
    parser.add_argument("--request-interval-ms", type=int, default=250)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument(
        "--include-1m-days",
        type=int,
        default=4,
        help="Also fetch 1m klines for the last N days (default: 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pair count and task estimate without downloading",
    )
    return parser.parse_args()


async def _count_universe(min_volume_usd: float) -> int:
    async with aiohttp.ClientSession() as session:
        rows = await fetch_binance_universe(
            session,
            top_n=0,
            min_volume_usd=min_volume_usd,
        )
    return len(rows)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    min_volume = 0.0 if args.include_all_pairs else args.min_volume_usd
    pair_count = asyncio.run(_count_universe(min_volume))
    intervals = [part.strip() for part in args.intervals.split(",") if part.strip()]
    if args.include_1m_days > 0 and "1m" not in intervals:
        intervals.append("1m")

    logging.info(
        "Backfill plan: %s -> %s | pairs=%d | intervals=%s | cache=%s",
        args.range_start,
        args.range_end,
        pair_count,
        ",".join(intervals),
        args.cache_dir,
    )
    task_count = pair_count * len(intervals)
    logging.info("Tasks: %d pair-interval downloads", task_count)
    if args.dry_run:
        return 0

    repo_root = Path(__file__).resolve().parent.parent
    prefetch = repo_root / "scripts" / "prefetch_binance_candles.py"
    cmd = [
        sys.executable,
        str(prefetch),
        "--range-start",
        args.range_start,
        "--range-end",
        args.range_end,
        "--intervals",
        args.intervals,
        "--universe-all",
        "--min-volume-usd",
        str(min_volume),
        "--cache-dir",
        str(args.cache_dir),
        "--request-interval-ms",
        str(args.request_interval_ms),
        "--max-retries",
        str(args.max_retries),
        "--max-concurrent",
        str(args.max_concurrent),
        "--include-1m-days",
        str(args.include_1m_days),
    ]
    env = {**dict(**__import__("os").environ), "PYTHONPATH": str(repo_root)}
    completed = subprocess.run(cmd, cwd=repo_root, env=env, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    sys.exit(main())
