#!/usr/bin/env python3
"""Run timeline mega sweep, validate top configs, apply winner to agent + preset."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    ENTRY_SLTP_SWEEP_VERSION,
    SWEEP_GRID_CHOICES,
    default_min_configs_for_sweep_grid,
    sweep_space_size,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.presets import resolve_config_with_preset
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import configure_replay_data_sources
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
    DEFAULT_CHECKPOINT_EVERY,
    DEFAULT_FREQUENCY_SEC,
    DEFAULT_TIME_WINDOW_MIN,
    TIMELINE_PRESET_NAME,
    apply_winner_to_agent,
    apply_winner_to_presets,
    build_timeline_preset_overrides,
    discover_replay_snapshot_dirs,
    estimate_timeline_sweep_seconds,
    format_validation_log,
    full_replay_overrides,
    run_multi_snapshot_timeline_sweep,
    run_timeline_dynamic_sweep,
    timeline_range_from_reports,
    validate_top_configs_via_routine,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Timeline mega sweep pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="Run timeline mega sweep")
    sweep.add_argument("--dynamic-mode", default="both_on")
    sweep.add_argument(
        "--sweep-grid",
        choices=SWEEP_GRID_CHOICES,
        default="entry_sltp_v6",
        help="Config grid: entry_sltp_v6 (adaptive+SL floors) or mega_v5 (full mega)",
    )
    sweep.add_argument(
        "--min-configs",
        type=int,
        default=0,
        help="Random sample count (0 = grid default)",
    )
    sweep.add_argument("--seed", type=int, default=42)
    sweep.add_argument("--top", type=int, default=40)
    sweep.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    sweep.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    sweep.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)
    sweep.add_argument(
        "--output-stem",
        default="",
        help="CSV filename stem (default: macdbb_scanner_aggressive_hl_backtest_{mode}_mega_timeline)",
    )
    sweep.add_argument(
        "--parent-csv",
        type=Path,
        default=None,
        help="Prior phase winner CSV; merged config becomes staged sweep base",
    )
    sweep.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots_binance_1y"),
        help="Parquet snapshot directory for timeline replay",
    )
    sweep.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help="Write checkpoint CSV every N configs (0 disables)",
    )
    sweep.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel config workers (Linux fork; default 1)",
    )
    sweep.add_argument(
        "--worker-ram-gb",
        type=float,
        default=2.0,
        help="Estimated RAM per worker for worker cap (default 2.0)",
    )

    sweep_all = sub.add_parser(
        "sweep-all",
        help="Run mega sweep across every replay_snapshots* directory",
    )
    sweep_all.add_argument("--dynamic-mode", default="both_on")
    sweep_all.add_argument(
        "--sweep-grid",
        choices=SWEEP_GRID_CHOICES,
        default="entry_sltp_v6",
    )
    sweep_all.add_argument("--min-configs", type=int, default=0)
    sweep_all.add_argument("--seed", type=int, default=42)
    sweep_all.add_argument("--top", type=int, default=40)
    sweep_all.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    sweep_all.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    sweep_all.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)
    sweep_all.add_argument(
        "--output-stem",
        default="macdbb_scanner_aggressive_hl_backtest_both_on_mega_timeline_all_snapshots",
    )

    validate = sub.add_parser("validate", help="Validate top-N via dynamic replay routine")
    validate.add_argument(
        "--csv",
        type=Path,
        default=Path(
            "data/strategy_replay_sweeps/strategy_replay_dynamic_both_on_mega_timeline.csv"
        ),
    )
    validate.add_argument("--top", type=int, default=5)
    validate.add_argument(
        "--output",
        type=Path,
        default=Path("data/strategy_replay_sweeps/timeline_top5_routine_validation.log"),
    )
    validate.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    validate.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)

    apply = sub.add_parser("apply-winner", help="Write rank #1 to agent.md and presets.py")
    apply.add_argument(
        "--csv",
        type=Path,
        default=Path(
            "data/strategy_replay_sweeps/strategy_replay_dynamic_both_on_mega_timeline.csv"
        ),
    )
    apply.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    apply.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)
    apply.add_argument("--skip-preset", action="store_true")
    apply.add_argument("--skip-agent", action="store_true")

    all_cmd = sub.add_parser("all", help="sweep -> validate top 5 -> apply winner")
    all_cmd.add_argument("--dynamic-mode", default="both_on")
    all_cmd.add_argument("--min-configs", type=int, default=560)
    all_cmd.add_argument("--seed", type=int, default=42)
    all_cmd.add_argument("--top", type=int, default=40)
    all_cmd.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    all_cmd.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    all_cmd.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)
    all_cmd.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots"),
        help="Parquet snapshot directory for timeline replay",
    )
    all_cmd.add_argument("--validate-top", type=int, default=5)
    all_cmd.add_argument("--skip-preset", action="store_true")
    all_cmd.add_argument("--skip-agent", action="store_true")

    return parser.parse_args()


async def _run_sweep(args: argparse.Namespace) -> Path:
    parent_overrides = None
    if args.parent_csv is not None:
        from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import load_sweep_winner_from_csv

        _name, _diff, parent_overrides = load_sweep_winner_from_csv(args.parent_csv)
        print(f"Staged parent from {args.parent_csv}: {_name}")

    min_configs = args.min_configs or default_min_configs_for_sweep_grid(
        args.sweep_grid,
        args.dynamic_mode,
    )

    configure_replay_data_sources(
        DynamicStrategyReplayConfig(
            data_source="snapshots",
            snapshot_dir=str(args.snapshot_dir),
            replay_mode="timeline_backtest",
        )
    )
    manifest = load_manifest(snapshot_dir=args.snapshot_dir)
    if manifest and manifest.get("range_start_utc") and manifest.get("range_end_utc"):
        start = manifest["range_start_utc"]
        end = manifest["range_end_utc"]
    else:
        start, end = timeline_range_from_reports()
    grid_tag = (
        ENTRY_SLTP_SWEEP_VERSION
        if args.sweep_grid == "entry_sltp_v6"
        else args.sweep_grid
    )
    output_stem = (
        args.output_stem
        or f"macdbb_scanner_aggressive_hl_backtest_{args.dynamic_mode}_{grid_tag}_timeline"
    )
    progress_path = args.output_dir / f"{output_stem}.progress.json"
    checkpoint_path = args.output_dir / f"{output_stem}.checkpoint.csv"
    print(
        f"Timeline sweep grid={args.sweep_grid} mode={args.dynamic_mode} | "
        f"space~{sweep_space_size(args.sweep_grid, args.dynamic_mode):,} | "
        f"min_configs={min_configs} | range {start} -> {end} | snapshots={args.snapshot_dir}"
    )
    print(f"Checkpoint CSV (every {args.checkpoint_every} configs): {checkpoint_path}")
    results, _baseline, _benchmark, range_start, range_end = await run_timeline_dynamic_sweep(
        dynamic_mode=args.dynamic_mode,
        output_dir=args.output_dir,
        min_configs=min_configs,
        seed=args.seed,
        output_stem=output_stem,
        frequency_sec=args.frequency_sec,
        time_window_min=args.time_window_min,
        range_start_utc=start,
        range_end_utc=end,
        snapshot_dir=str(args.snapshot_dir),
        top_n=args.top,
        progress_path=progress_path,
        parent_overrides=parent_overrides,
        checkpoint_every=args.checkpoint_every,
        workers=args.workers,
        worker_ram_gb=args.worker_ram_gb,
        sweep_grid=args.sweep_grid,
    )
    csv_path = args.output_dir / f"{output_stem}.csv"
    if results:
        winner = results[0]
        print(
            f"\nTop config: {winner.name}  CapNorm=${winner.capital_normalized_pnl:+.2f}  "
            f"RawPnL=${winner.pnl:+.2f}  trades={winner.trades}  "
            f"avg_notional=${winner.avg_notional:.0f}  "
            f"overrides={json.dumps(winner.overrides, sort_keys=True)}"
        )
        print(f"Range: {range_start} -> {range_end}")
    print(f"\nWrote {csv_path}")
    print(f"Progress: {progress_path}")
    return csv_path


async def _run_sweep_all(args: argparse.Namespace) -> Path:
    from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
        resolve_sweep_config_iterator,
    )

    min_configs = args.min_configs or default_min_configs_for_sweep_grid(
        args.sweep_grid,
        args.dynamic_mode,
    )
    snapshot_dirs = discover_replay_snapshot_dirs()
    config_count = len(
        list(
            resolve_sweep_config_iterator(
                args.sweep_grid,
                args.dynamic_mode,
                min_configs=min_configs,
                seed=args.seed,
            )
        )
    )
    est_sec = estimate_timeline_sweep_seconds(snapshot_dirs, config_count=config_count)
    est_hours = est_sec / 3600
    progress_path = args.output_dir / f"{args.output_stem}.progress.json"
    csv_path = args.output_dir / f"{args.output_stem}.csv"

    print("=== Multi-snapshot mega sweep plan ===")
    print(f"  Snapshot dirs : {len(snapshot_dirs)}")
    for path in snapshot_dirs:
        manifest = load_manifest(snapshot_dir=path)
        ticks = (manifest or {}).get("tick_count", "?")
        print(f"    - {path.as_posix()} ({ticks} ticks)")
    print(f"  Configs/dir   : {config_count} (min_configs={min_configs})")
    print(f"  Total runs    : {config_count * len(snapshot_dirs):,}")
    print(f"  Est. runtime  : ~{est_hours:.1f} hours ({est_sec / 60:.0f} min)")
    print(f"  Output CSV    : {csv_path}")
    print(f"  Progress file : {progress_path}")
    print(f"  Checkpoint    : {args.output_dir / f'{args.output_stem}.checkpoint.csv'}")
    print("======================================\n")

    results, range_start, range_end = await run_multi_snapshot_timeline_sweep(
        dynamic_mode=args.dynamic_mode,
        output_dir=args.output_dir,
        min_configs=min_configs,
        seed=args.seed,
        output_stem=args.output_stem,
        frequency_sec=args.frequency_sec,
        time_window_min=args.time_window_min,
        snapshot_dirs=snapshot_dirs,
        top_n=args.top,
        progress_path=progress_path,
        sweep_grid=args.sweep_grid,
    )
    if results:
        winner = results[0]
        print(
            f"\nTop overall: {winner.name} @ {winner.snapshot_dir}  "
            f"CapNorm=${winner.capital_normalized_pnl:+.2f}  RawPnL=${winner.pnl:+.2f}  "
            f"trades={winner.trades}"
        )
        print(f"Range: {range_start} -> {range_end}")
    print(f"\nWrote {csv_path}")
    return csv_path


async def _run_validate(args: argparse.Namespace) -> None:
    rows = await validate_top_configs_via_routine(
        args.csv,
        top_n=args.top,
        frequency_sec=args.frequency_sec,
        time_window_min=args.time_window_min,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(format_validation_log(rows), encoding="utf-8")
    print(f"Wrote {args.output}")


def _apply_winner(args: argparse.Namespace) -> None:
    import csv

    with args.csv.open(encoding="utf-8") as handle:
        winner_row = next(csv.DictReader(handle))
    delta = json.loads(winner_row["overrides_json"])
    preset_overrides = build_timeline_preset_overrides(
        delta,
        frequency_sec=args.frequency_sec,
        time_window_min=args.time_window_min,
    )
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(**{**preset_overrides, "preset": "custom"})
    )
    if not args.skip_agent:
        params = apply_winner_to_agent(config, frequency_sec=args.frequency_sec)
        print(f"Updated agent.md strategy_params ({len(params)} keys)")
    if not args.skip_preset:
        apply_winner_to_presets(preset_overrides)
        print(f"Added preset {TIMELINE_PRESET_NAME} to presets.py and models.py")
    print(f"Winner: {winner_row['name']}")


async def main() -> int:
    args = _parse_args()
    if args.command == "sweep":
        await _run_sweep(args)
        return 0
    if args.command == "sweep-all":
        await _run_sweep_all(args)
        return 0
    if args.command == "validate":
        await _run_validate(args)
        return 0
    if args.command == "apply-winner":
        _apply_winner(args)
        return 0
    if args.command == "all":
        csv_path = await _run_sweep(args)
        validate_args = argparse.Namespace(
            csv=csv_path,
            top=args.validate_top,
            output=args.output_dir / "timeline_top5_routine_validation.log",
            frequency_sec=args.frequency_sec,
            time_window_min=args.time_window_min,
        )
        await _run_validate(validate_args)
        apply_args = argparse.Namespace(
            csv=csv_path,
            frequency_sec=args.frequency_sec,
            time_window_min=args.time_window_min,
            skip_preset=args.skip_preset,
            skip_agent=args.skip_agent,
        )
        _apply_winner(apply_args)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
