#!/usr/bin/env python3
"""Build config-independent market snapshots from local candle cache."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import aiohttp

from routines.lib.binance_candle_cache import DEFAULT_CACHE_DIR as BINANCE_DEFAULT_CACHE_DIR
from routines.lib.hl_candle_cache import DEFAULT_CACHE_DIR as HL_DEFAULT_CACHE_DIR
from routines.lib.binance_candles import configure_binance_rate_limit
from routines.lib.hl_candles import configure_hl_rate_limit
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.lib.shared_universe import (
    load_intersection_manifest,
    universe_rows_for_exchange,
)
from routines.macdbb_replay.report_backfill import (
    BackfillSettings,
    CandleCache,
    collect_session_tick_times,
    fetch_binance_universe,
    fetch_hl_universe,
)
from routines.macdbb_replay.session_builder import _default_strategy_params
from routines.macdbb_replay.snapshot_store import (
    DEFAULT_SNAPSHOT_DIR,
    append_states,
    existing_tick_ids,
    write_manifest,
)
from routines.macdbb_replay.tick_market_state import (
    TickMarketSettings,
    TickMarketState,
    compute_tick_market_state,
)
from routines.macdbb_replay.tick_schedule import build_range_tick_schedule, parse_iso_utc


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


def _collect_tick_times(args: argparse.Namespace) -> list[dt.datetime]:
    if args.sessions:
        session_nums = _expand_session_selector(args.sessions)
        return collect_session_tick_times(
            session_nums,
            strategy_slug=args.strategy_slug,
        )
    if not args.range_start or not args.range_end:
        raise ValueError("Provide --range-start/--range-end or --sessions")
    start = parse_iso_utc(args.range_start)
    end = parse_iso_utc(args.range_end)
    schedule = build_range_tick_schedule(start, end, args.frequency_sec)
    return [meta.timestamp for meta in schedule.values()]


async def _run_build(args: argparse.Namespace) -> dict[str, int]:
    cache_dir = args.cache_dir
    if cache_dir is None:
        cache_dir = (
            BINANCE_DEFAULT_CACHE_DIR
            if args.candle_source == "binance_perpetual"
            else HL_DEFAULT_CACHE_DIR
        )

    if args.candle_source == "binance_perpetual":
        configure_binance_rate_limit(
            request_interval_ms=args.request_interval_ms,
            max_retries=args.max_retries,
        )
    else:
        configure_hl_rate_limit(
            request_interval_ms=args.request_interval_ms,
            max_retries=args.max_retries,
        )
    volume_source = (
        args.candle_source if args.volume_source == "same" else args.volume_source
    )
    tick_times = _collect_tick_times(args)
    if args.limit > 0:
        tick_times = tick_times[: args.limit]

    known_ticks = existing_tick_ids(snapshot_dir=args.snapshot_dir)
    if not args.force:
        tick_times = [
            tick_time
            for tick_time in tick_times
            if tick_time.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M%S") not in known_ticks
        ]

    settings = BackfillSettings(
        candidate_pool=args.candidate_pool,
        cache_dir=cache_dir,
        candle_source=args.candle_source,
    )
    market_settings = TickMarketSettings(
        lookback_hours=settings.lookback_hours,
        top_n=settings.top_n,
        min_volume_usd=settings.min_volume_usd,
        mature_count=settings.mature_count,
        degen_count=settings.degen_count,
        candidate_pool=settings.candidate_pool,
        macd_review_count=settings.macd_review_count,
        macd_pairs_superset=settings.macd_review_count,
        max_concurrent=args.max_concurrent,
    )
    strategy_params = _default_strategy_params(DynamicStrategyReplayConfig())

    totals = {"ticks": len(tick_times), "built": 0, "skipped": 0, "errors": 0}
    pending_states: list[TickMarketState] = []
    stop_requested = False

    def _request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        logging.info("Stop requested (signal %s); finishing current tick then flushing batch", signum)

    def _flush_pending() -> None:
        if not pending_states:
            return
        append_states(pending_states, snapshot_dir=args.snapshot_dir)
        pending_states.clear()

    previous_handlers: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _request_stop)
        except (AttributeError, ValueError, OSError):
            pass

    if not tick_times:
        logging.info("No ticks to build.")
        return totals

    try:
        async with aiohttp.ClientSession() as session:
            cache = CandleCache(
                session=session,
                cache_dir=cache_dir,
                candle_source=args.candle_source,
                max_file_cache_entries=args.file_cache_entries,
            )
            volume_cache: CandleCache | None = None
            if volume_source != args.candle_source:
                volume_cache_dir = (
                    BINANCE_DEFAULT_CACHE_DIR
                    if volume_source == "binance_perpetual"
                    else HL_DEFAULT_CACHE_DIR
                )
                volume_cache = CandleCache(
                    session=session,
                    cache_dir=volume_cache_dir,
                    candle_source=volume_source,
                    max_file_cache_entries=args.file_cache_entries,
                )
                logging.info(
                    "Volume ranking from %s candles; OHLC/MACD from %s",
                    volume_source,
                    args.candle_source,
                )
            if args.intersection_manifest:
                intersection = load_intersection_manifest(args.intersection_manifest)
                universe = universe_rows_for_exchange(intersection, args.candle_source)
                logging.info(
                    "Using shared intersection universe: %d pairs from %s",
                    len(universe),
                    args.intersection_manifest,
                )
            elif args.candle_source == "binance_perpetual":
                universe = await fetch_binance_universe(
                    session,
                    top_n=args.universe_top_n,
                    min_volume_usd=settings.min_volume_usd,
                )
            else:
                universe = await fetch_hl_universe(session, exclude_hip3=settings.exclude_hip3)
                if args.universe_top_n > 0:
                    universe = universe[: args.universe_top_n]
            for index, tick_time in enumerate(tick_times, start=1):
                if stop_requested:
                    logging.info("Stopping build after %d ticks in this run", index - 1)
                    break
                try:
                    state = await compute_tick_market_state(
                        tick_time,
                        universe=universe,
                        loader=cache,
                        settings=market_settings,
                        strategy_params=strategy_params,
                        volume_loader=volume_cache,
                    )
                    if state.parsed_scanner is None:
                        totals["skipped"] += 1
                        continue
                    pending_states.append(state)
                    totals["built"] += 1
                    if len(pending_states) >= args.batch_size:
                        _flush_pending()
                except Exception:
                    totals["errors"] += 1
                    logging.exception("Snapshot build failed at %s", tick_time)
                    continue
                if index % 10 == 0 or index == len(tick_times):
                    logging.info(
                        "Progress %d/%d — built=%d skipped=%d errors=%d pending=%d",
                        index,
                        len(tick_times),
                        totals["built"],
                        totals["skipped"],
                        totals["errors"],
                        len(pending_states),
                    )
    finally:
        _flush_pending()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (AttributeError, ValueError, OSError):
                pass

    write_manifest(
        {
            "version": 1,
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "snapshot_dir": str(args.snapshot_dir),
            "cache_dir": str(cache_dir),
            "candle_source": args.candle_source,
            "volume_source": volume_source,
            "intersection_manifest": str(args.intersection_manifest) if args.intersection_manifest else None,
            "tick_count": totals["built"],
            "frequency_sec": args.frequency_sec,
            "range_start_utc": args.range_start,
            "range_end_utc": args.range_end,
            "sessions": args.sessions,
        },
        snapshot_dir=args.snapshot_dir,
    )
    return totals


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
