#!/usr/bin/env python3
"""Proactive backfill of monitor MACD-BB rows for open-leg timeline replay.

Run once before a sweep marathon (or after strategy changes that alter which
pairs stay open). Discovers monitor snapshot gaps via a baseline simulation,
batch-computes MACD from cached 1h Binance candles, and appends rows to
``macdbb_monitor.parquet`` (merged at read time with base ``macdbb.parquet``).

Progress is logged to stdout (one line per stage / ~5 pairs during prefetch /
~4% of ticks during sim). Do **not** pipe through ``tail`` until the run
finishes — that hides live progress.

Example::

    PYTHONPATH=. .venv/bin/python -u scripts/backfill_replay_monitor_macdbb.py \\
        --snapshot-dir data/replay_snapshots_binance_1y \\
        --preset hl_dynamic_timeline_refine_v5_winner_binance_1y
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from routines.lib.binance_candle_cache import DEFAULT_CACHE_DIR as BINANCE_DEFAULT_CACHE_DIR
from routines.macdbb_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_replay.hl_prices import hl_prefetch_settings_from_config, prefetch_replay_hl_prices
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.monitor_macdbb import (
    batch_compute_macdbb_gaps,
    set_monitor_gap_recorder,
    update_monitor_manifest,
)
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.replay_data import configure_replay_data_sources, should_prefetch_replay_candles
from routines.macdbb_replay.replay_loader import load_replay_sessions
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_replay.simulator import simulate_strategy_session
from routines.macdbb_replay.snapshot_store import (
    DEFAULT_SNAPSHOT_DIR,
    MONITOR_MACDBB_FILENAME,
    append_monitor_macdbb_rows,
    configure_snapshot_dir,
    load_manifest,
    nearest_macdbb_snapshot,
)
from routines.macdbb_replay.timeline_sweep import merge_timeline_config

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def _stage(label: str) -> float:
    logger.info("=== %s ===", label)
    return time.monotonic()


def _stage_done(label: str, started: float) -> None:
    elapsed = time.monotonic() - started
    logger.info("=== %s done (%.1fs) ===", label, elapsed)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill monitor MACD-BB parquet supplement")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path(DEFAULT_SNAPSHOT_DIR),
        help="Replay snapshot directory (scanner + macdbb parquet)",
    )
    parser.add_argument(
        "--preset",
        default="hl_dynamic_timeline_refine_v5_winner_binance_1y",
        help="Timeline replay preset for baseline gap discovery sim",
    )
    parser.add_argument(
        "--strategy-slug",
        default="macdbb_scanner_aggressive_hl",
        help="Trading agent slug",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=BINANCE_DEFAULT_CACHE_DIR,
        help="Binance candle cache directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover gaps only; do not write macdbb_monitor.parquet",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="Parallel workers for batch MACD compute",
    )
    parser.add_argument(
        "--coverage-pair",
        default="WLD-USD",
        help="Pair to print before/after macdbb index coverage for",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging (per-pair cache misses, etc.)",
    )
    return parser.parse_args()


def _resolve_config(args: argparse.Namespace) -> DynamicStrategyReplayConfig:
    manifest = load_manifest(snapshot_dir=args.snapshot_dir) or {}
    overrides = merge_timeline_config(
        {"preset": args.preset, "strategy_slug": args.strategy_slug},
        range_start_utc=manifest.get("range_start_utc"),
        range_end_utc=manifest.get("range_end_utc"),
    )
    overrides["snapshot_dir"] = str(args.snapshot_dir)
    overrides["hl_cache_dir"] = str(args.cache_dir)
    overrides["candle_source"] = "binance_perpetual"
    overrides["replay_mode"] = "timeline_backtest"
    overrides["data_source"] = "snapshots"
    return resolve_config_with_preset(DynamicStrategyReplayConfig(**overrides))


def _pair_tick_coverage(
    pair: str,
    tick_map: dict[int, object],
    *,
    snapshot_dir: Path,
    window_min: int,
) -> tuple[int, int]:
    covered = 0
    total = len(tick_map)
    step = max(1, total // 10) if total >= 1000 else 0
    for index, meta in enumerate(tick_map.values(), start=1):
        hit = nearest_macdbb_snapshot(
            pair,
            meta.timestamp,
            window_min,
            interval="1h",
            snapshot_dir=snapshot_dir,
        )
        if hit is not None:
            covered += 1
        if step and (index == 1 or index % step == 0 or index == total):
            logger.info(
                "Coverage scan %s: %d/%d ticks checked (%d hits so far)",
                pair,
                index,
                total,
                covered,
            )
    return covered, total


async def _prefetch(config: DynamicStrategyReplayConfig, tick_maps: dict) -> tuple:
    if not should_prefetch_replay_candles(config):
        logger.info("Candle prefetch skipped (not required for this config)")
        return {}, {}, {}, {}
    return await prefetch_replay_hl_prices(
        tick_maps,
        settings=hl_prefetch_settings_from_config(config),
    )


async def _run(args: argparse.Namespace) -> int:
    if not args.snapshot_dir.is_dir():
        logger.error("Snapshot dir not found: %s", args.snapshot_dir)
        return 1

    t0 = _stage("Load config + hydrate timeline ticks")
    config = _resolve_config(args)
    configure_snapshot_dir(args.snapshot_dir)
    configure_replay_data_sources(config)
    logger.info(
        "Timeline %s → %s, frequency=%ds, snapshot=%s",
        config.range_start_utc,
        config.range_end_utc,
        config.frequency_sec,
        args.snapshot_dir,
    )

    tick_maps, session_configs, selected = load_replay_sessions(config)
    if not selected:
        logger.error("No timeline ticks loaded")
        return 1

    tick_map = tick_maps[selected[0]]
    unique_pairs = {
        pair
        for meta in tick_map.values()
        for pair in (meta.macd_pairs or []) + (meta.queue_total or [])
    }
    logger.info(
        "Hydrated %d ticks, %d unique queue pairs across timeline",
        len(tick_map),
        len(unique_pairs),
    )
    _stage_done("Load config + hydrate timeline ticks", t0)

    t1 = _stage(f"Coverage check ({args.coverage_pair})")
    before_cov, total_ticks = _pair_tick_coverage(
        args.coverage_pair,
        tick_map,
        snapshot_dir=args.snapshot_dir,
        window_min=config.time_window_min,
    )
    logger.info(
        "%s base macdbb coverage: %d/%d ticks (%.1f%%)",
        args.coverage_pair,
        before_cov,
        total_ticks,
        100.0 * before_cov / max(total_ticks, 1),
    )
    _stage_done(f"Coverage check ({args.coverage_pair})", t1)

    t2 = _stage("Prefetch 1m/5m barrier + price candles (sequential per pair)")
    hl_caches, hl_candle, hl_barrier, hl_vol = await _prefetch(config, tick_maps)
    logger.info(
        "Prefetch loaded: %d price series, %d barrier series, %d vol series",
        len(hl_candle),
        len(hl_barrier),
        len(hl_vol),
    )
    _stage_done("Prefetch 1m/5m barrier + price candles (sequential per pair)", t2)

    t3 = _stage("Baseline sim — discover monitor MACD gaps (no inline compute)")
    gaps: list[tuple[str, str, object]] = []
    set_monitor_gap_recorder(gaps, inline_compute=False)
    reports_by_pair = build_reports_by_pair(load_reports_index())
    session_num = selected[0]
    simulate_strategy_session(
        session_num=session_num,
        tick_meta_map=tick_map,
        reports_by_pair=reports_by_pair,
        config=session_configs[session_num],
        hl_price_cache=hl_caches.get(session_num),
        hl_candle_cache=hl_candle,
        hl_barrier_candle_cache=hl_barrier,
        hl_vol_candle_cache=hl_vol,
        replay_policy=DynamicReplayPolicy(session_configs[session_num]),
    )
    set_monitor_gap_recorder(None)
    unique_gaps = len({(g[0], g[1], g[2]) for g in gaps})
    logger.info("Monitor gaps discovered: %d raw, %d unique", len(gaps), unique_gaps)
    _stage_done("Baseline sim — discover monitor MACD gaps (no inline compute)", t3)

    if args.dry_run:
        logger.info("Dry run — skipping batch compute and persist")
        return 0

    t4 = _stage("Batch compute monitor MACD rows from 1h cache")
    rows = batch_compute_macdbb_gaps(
        gaps,
        cache_dir=args.cache_dir,
        candle_source=config.candle_source,
        max_workers=args.max_workers,
    )
    logger.info("Computed monitor rows: %d / %d gaps", len(rows), len(gaps))
    _stage_done("Batch compute monitor MACD rows from 1h cache", t4)
    if not rows:
        logger.warning("Nothing to persist")
        return 0

    t5 = _stage("Persist macdbb_monitor.parquet supplement")
    written = append_monitor_macdbb_rows(rows, snapshot_dir=args.snapshot_dir)
    update_monitor_manifest(snapshot_dir=args.snapshot_dir, rows_added=written)
    monitor_path = args.snapshot_dir / MONITOR_MACDBB_FILENAME
    logger.info("Wrote %d rows to %s", written, monitor_path)
    _stage_done("Persist macdbb_monitor.parquet supplement", t5)

    after_cov, _ = _pair_tick_coverage(
        args.coverage_pair,
        tick_map,
        snapshot_dir=args.snapshot_dir,
        window_min=config.time_window_min,
    )
    logger.info(
        "%s merged macdbb coverage: %d/%d ticks (%.1f%%)",
        args.coverage_pair,
        after_cov,
        total_ticks,
        100.0 * after_cov / max(total_ticks, 1),
    )
    return 0


def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
