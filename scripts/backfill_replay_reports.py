#!/usr/bin/env python3
"""Backfill scanner + MACD BB HTML reports for pre-index session tick times."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys

from routines.macdbb_scanner_aggressive_hl_replay.models import parse_session_selector
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.report_backfill import (
    BackfillSettings,
    collect_session_tick_times,
    run_backfill,
)
from routines.macdbb_scanner_aggressive_hl_replay.reports import load_scanner_reports_index
def _first_live_scanner_time() -> dt.datetime | None:
    live = [
        row.created_at
        for row in load_scanner_reports_index()
        if "backfill" not in row.filename
    ]
    return min(live) if live else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill hyperliquid_market_scanner + macd_bb_analysis HTML reports "
            "for historical session tick timestamps (reports_only replay)."
        )
    )
    parser.add_argument(
        "--sessions",
        default="37-47",
        help="Session selector (default: 37-47, pre-index window)",
    )
    parser.add_argument(
        "--strategy-slug",
        default="macdbb_scanner_aggressive_hl",
        help="Trading agent slug",
    )
    parser.add_argument(
        "--include-all-session-ticks",
        action="store_true",
        help="Backfill all session ticks (default: only before first indexed scanner report)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N tick times (0 = all)",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=600,
        help="Minimum ms between HL REST requests (default 600)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="HL 429/5xx retries per request (default 8)",
    )
    parser.add_argument(
        "--candidate-pool",
        type=int,
        default=45,
        help="Top meta-universe pairs to fetch 1m candles for per tick",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List tick times only; do not fetch or write reports",
    )
    return parser.parse_args()


def _expand_session_selector(raw: str, sessions_dir) -> list[int]:
    """Support comma lists and inclusive ranges like 37-47."""
    text = raw.strip()
    if not text:
        return []
    if text.lower() == "all":
        return parse_session_selector("all", sessions_dir)
    numbers: list[int] = []
    for part in text.split(","):
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


def _session_nums(args: argparse.Namespace) -> list[int]:
    sessions_dir = TRADING_AGENTS_DIR / args.strategy_slug / "sessions"
    return _expand_session_selector(args.sessions, sessions_dir)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    session_nums = _session_nums(args)
    if not session_nums:
        logging.error("No sessions matched selector %r", args.sessions)
        return 1

    until: dt.datetime | None = None
    if not args.include_all_session_ticks:
        until = _first_live_scanner_time()
        if until is not None:
            logging.info("Cutoff before first live scanner report: %s", until)

    tick_times = collect_session_tick_times(
        session_nums,
        strategy_slug=args.strategy_slug,
        until=until,
    )
    if args.limit > 0:
        tick_times = tick_times[: args.limit]

    logging.info(
        "Sessions %s -> %d unique tick timestamps to backfill",
        session_nums,
        len(tick_times),
    )
    if not tick_times:
        logging.info("Nothing to backfill.")
        return 0

    if args.dry_run:
        for tick_time in tick_times[:20]:
            print(tick_time.isoformat())
        if len(tick_times) > 20:
            print(f"... and {len(tick_times) - 20} more")
        return 0

    settings = BackfillSettings(
        request_interval_ms=args.request_interval_ms,
        max_retries=args.max_retries,
        candidate_pool=args.candidate_pool,
    )
    totals = asyncio.run(run_backfill(tick_times, settings))
    logging.info("Backfill complete: %s", totals)
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
