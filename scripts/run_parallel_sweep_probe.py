#!/usr/bin/env python3
"""Probe parallel sweep scaling with a small config batch (default 10).

Loads timeline replay data once, then times ``run_sweep_config_batch`` at several
worker counts. Use this before a 500+ config mega sweep to pick ``--workers``.

Example::

    PYTHONPATH=. .venv/bin/python -u scripts/run_parallel_sweep_probe.py \\
        --configs 10 --workers 1,2,4,8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    SweepRunContext,
    _dynamic_sweep_base,
    _load_sessions,
    iter_mega_dynamic_sweep_configs,
    prepare_shared_candle_stores,
    resolve_sweep_workers,
    run_sweep_config_batch,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.presets import FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import configure_replay_data_sources
from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest, warm_snapshot_caches
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
    _finalize_mega_dynamic_config,
    _merge,
    merge_timeline_config,
    timeline_sweep_overrides,
)


def _parse_workers(raw: str) -> list[int]:
    return [max(1, int(part.strip())) for part in raw.split(",") if part.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe parallel sweep worker scaling")
    parser.add_argument("--configs", type=int, default=10, help="Number of configs to run per trial")
    parser.add_argument(
        "--workers",
        default="1,2,4,8",
        help="Comma-separated worker counts to try (default: 1,2,4,8)",
    )
    parser.add_argument("--dynamic-mode", default="both_on")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots_binance_1y"),
    )
    parser.add_argument("--worker-ram-gb", type=float, default=3.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_replay_sweeps/parallel_sweep_probe.json"),
    )
    return parser.parse_args()


async def _preload(args: argparse.Namespace) -> tuple[SweepRunContext, list[tuple[str, dict]]]:
    manifest = load_manifest(snapshot_dir=args.snapshot_dir)
    if manifest and manifest.get("range_start_utc") and manifest.get("range_end_utc"):
        range_start = str(manifest["range_start_utc"])
        range_end = str(manifest["range_end_utc"])
        frequency_sec = int(manifest.get("frequency_sec") or 1800)
    else:
        timeline_fields = timeline_sweep_overrides()
        range_start = timeline_fields["range_start_utc"]
        range_end = timeline_fields["range_end_utc"]
        frequency_sec = 1800

    timeline_fields = timeline_sweep_overrides(
        range_start_utc=range_start,
        range_end_utc=range_end,
        frequency_sec=frequency_sec,
    )
    timeline_fields = {**timeline_fields, "snapshot_dir": str(args.snapshot_dir)}
    load_config = DynamicStrategyReplayConfig(
        **_finalize_mega_dynamic_config(
            _merge(
                _dynamic_sweep_base(args.dynamic_mode),
                **timeline_fields,
            )
        )
    )
    configure_replay_data_sources(load_config)
    warm_snapshot_caches(args.snapshot_dir)

    t0 = time.perf_counter()
    (
        parsed_sessions,
        hl_caches,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        _selected,
    ) = await _load_sessions(load_config)
    reports_by_pair = build_reports_by_pair(load_reports_index())
    load_sec = time.perf_counter() - t0

    raw_items = list(
        iter_mega_dynamic_sweep_configs(
            args.dynamic_mode,
            min_configs=args.configs,
            seed=args.seed,
        )
    )[: args.configs]
    merged_items: list[tuple[str, dict]] = []
    for name, overrides in raw_items:
        merged = merge_timeline_config(
            overrides,
            range_start_utc=timeline_fields["range_start_utc"],
            range_end_utc=timeline_fields["range_end_utc"],
        )
        merged["snapshot_dir"] = str(args.snapshot_dir)
        merged_items.append((name, merged))

    tick_count = sum(len(ticks) for ticks in parsed_sessions.values())
    print(
        f"Preloaded {len(merged_items)} configs | ticks={tick_count} | "
        f"load={load_sec:.1f}s | snapshot={args.snapshot_dir}",
        flush=True,
    )

    (
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        hl_candle_store,
        hl_barrier_candle_store,
        hl_vol_candle_store,
    ) = prepare_shared_candle_stores(
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        enabled=True,
    )
    if hl_candle_store is not None:
        print(
            f"Shared candle stores: price={len(hl_candle_store)} "
            f"barrier={len(hl_barrier_candle_store or [])} "
            f"vol={len(hl_vol_candle_store or [])}",
            flush=True,
        )

    ctx = SweepRunContext(
        dynamic_mode=args.dynamic_mode,
        parsed_sessions=parsed_sessions,
        hl_caches_by_session=hl_caches,
        hl_candle_cache=hl_candle_cache,
        hl_barrier_candle_cache=hl_barrier_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
        hl_candle_store=hl_candle_store,
        hl_barrier_candle_store=hl_barrier_candle_store,
        hl_vol_candle_store=hl_vol_candle_store,
        reports_by_pair=reports_by_pair,
        parent_overrides=None,
        benchmark_avg_notional=FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    )
    return ctx, merged_items


def _process_rss_kb() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def main() -> int:
    args = _parse_args()
    if not (args.snapshot_dir / "macdbb.parquet").is_file():
        print(f"Snapshot not found: {args.snapshot_dir}", file=sys.stderr)
        return 1

    worker_counts = _parse_workers(args.workers)
    # Fastest trials first so partial results are useful if interrupted.
    worker_counts = sorted(set(worker_counts), reverse=True)
    ctx, merged_items = asyncio.run(_preload(args))

    print(f"\n{'workers':>8} {'resolved':>9} {'wall_s':>8} {'s/config':>10} {'speedup':>8} {'rss_mb':>8}")
    print("-" * 60)

    baseline_sec: float | None = None
    rows: list[dict] = []

    try:
        for requested in worker_counts:
            resolved = resolve_sweep_workers(
                requested,
                worker_ram_gb=args.worker_ram_gb,
            )
            t0 = time.perf_counter()
            results = run_sweep_config_batch(
                merged_items,
                ctx,
                workers=requested,
                worker_ram_gb=args.worker_ram_gb,
            )
            wall = time.perf_counter() - t0
            rss_mb = (_process_rss_kb() or 0) / 1024.0
            if baseline_sec is None:
                baseline_sec = wall
            speedup = baseline_sec / wall if wall > 0 else 0.0
            per_config = wall / len(merged_items)
            print(
                f"{requested:>8} {resolved:>9} {wall:>8.1f} {per_config:>10.1f} {speedup:>7.2f}x {rss_mb:>8.0f}",
                flush=True,
            )
            rows.append(
                {
                    "requested_workers": requested,
                    "resolved_workers": resolved,
                    "config_count": len(merged_items),
                    "wall_sec": round(wall, 2),
                    "sec_per_config": round(per_config, 2),
                    "speedup_vs_baseline": round(speedup, 3),
                    "parent_rss_mb": round(rss_mb, 1),
                    "result_count": len(results),
                }
            )
    finally:
        ctx.close_shared_stores()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {args.output}")
    best = max(rows, key=lambda row: row["speedup_vs_baseline"] / max(row["resolved_workers"], 1))
    print(
        f"Suggested starting point: --workers {best['resolved_workers']} "
        f"({best['sec_per_config']:.0f}s/config in this probe)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
