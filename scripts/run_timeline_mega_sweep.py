#!/usr/bin/env python3
"""Run timeline mega sweep, validate top configs, apply winner to agent + preset."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from routines.macdbb_replay.config_sweep import _mega_dynamic_space_size
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.timeline_sweep import (
    DEFAULT_FREQUENCY_SEC,
    DEFAULT_TIME_WINDOW_MIN,
    TIMELINE_PRESET_NAME,
    apply_winner_to_agent,
    apply_winner_to_presets,
    build_timeline_preset_overrides,
    format_validation_log,
    full_replay_overrides,
    run_timeline_dynamic_sweep,
    timeline_range_from_reports,
    validate_top_configs_via_routine,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Timeline mega sweep pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="Run timeline mega sweep")
    sweep.add_argument("--dynamic-mode", default="both_on")
    sweep.add_argument("--min-configs", type=int, default=560)
    sweep.add_argument("--seed", type=int, default=42)
    sweep.add_argument("--top", type=int, default=40)
    sweep.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    sweep.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    sweep.add_argument("--time-window-min", type=int, default=DEFAULT_TIME_WINDOW_MIN)

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
    all_cmd.add_argument("--validate-top", type=int, default=5)
    all_cmd.add_argument("--skip-preset", action="store_true")
    all_cmd.add_argument("--skip-agent", action="store_true")

    return parser.parse_args()


async def _run_sweep(args: argparse.Namespace) -> Path:
    start, end = timeline_range_from_reports()
    print(
        f"Dynamic mega timeline sweep mode={args.dynamic_mode} | "
        f"space~{_mega_dynamic_space_size(args.dynamic_mode):,} | "
        f"min_configs={args.min_configs} | range {start} -> {end}"
    )
    results, _baseline, _benchmark, range_start, range_end = await run_timeline_dynamic_sweep(
        dynamic_mode=args.dynamic_mode,
        output_dir=args.output_dir,
        min_configs=args.min_configs,
        seed=args.seed,
        output_stem=f"strategy_replay_dynamic_{args.dynamic_mode}_mega_timeline",
        frequency_sec=args.frequency_sec,
        time_window_min=args.time_window_min,
        range_start_utc=start,
        range_end_utc=end,
        top_n=args.top,
    )
    csv_path = args.output_dir / f"strategy_replay_dynamic_{args.dynamic_mode}_mega_timeline.csv"
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
