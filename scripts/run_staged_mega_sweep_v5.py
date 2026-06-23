#!/usr/bin/env python3
"""Sequential staged mega sweep v5: Phase A → B → C with winner chaining.

Use ``BC`` to run only phases B and C when phase A CSV already exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from routines.macdbb_replay.config_sweep import (
    MEGA_GRID_VERSION,
    MEGA_SWEEP_MIN_CONFIGS_BY_MODE,
    STAGED_PHASE_MODES,
    _merge,
    default_min_configs_for_mode,
    extract_barrier_overrides,
    load_sweep_winner_from_csv,
)
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.replay_data import configure_replay_data_sources
from routines.macdbb_replay.snapshot_store import load_manifest
from routines.macdbb_replay.timeline_sweep import (
    DEFAULT_CHECKPOINT_EVERY,
    run_timeline_dynamic_sweep,
    timeline_range_from_reports,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            f"Staged timeline mega sweep ({MEGA_GRID_VERSION}): "
            "A→B→C, BC (B→C from existing A CSV), or a single phase"
        )
    )
    parser.add_argument(
        "phase",
        choices=["A", "B", "C", "BC", "all"],
        help="Run one phase, BC (B then C), or all three in sequence",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots_binance_1y"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    parser.add_argument("--min-configs", type=int, default=0, help="Override per-phase budget")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument(
        "--phase-a-csv",
        type=Path,
        default=None,
        help="Phase A winner CSV (required for B/C unless running all)",
    )
    parser.add_argument(
        "--phase-b-csv",
        type=Path,
        default=None,
        help="Phase B winner CSV (required for C unless running all)",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help="Write checkpoint CSV every N configs (0 disables)",
    )
    return parser.parse_args()


def _snapshot_slug(snapshot_dir: Path) -> str:
    name = snapshot_dir.name
    if name.startswith("replay_snapshots_"):
        return name.removeprefix("replay_snapshots_")
    return name


def _phase_paths(args: argparse.Namespace, phase: str) -> tuple[Path, Path, Path, Path]:
    slug = _snapshot_slug(args.snapshot_dir)
    mode = STAGED_PHASE_MODES[phase]
    stem = f"strategy_replay_dynamic_{mode}_mega_timeline_{MEGA_GRID_VERSION}_phase{phase}_{slug}"
    csv_path = args.output_dir / f"{stem}.csv"
    progress_path = args.output_dir / f"{stem}.progress.json"
    checkpoint_path = args.output_dir / f"{stem}.checkpoint.csv"
    log_path = args.output_dir / f"mega_sweep_{MEGA_GRID_VERSION}_phase{phase}_{slug}.log"
    return csv_path, progress_path, checkpoint_path, log_path


async def _run_phase(
    args: argparse.Namespace,
    phase: str,
    *,
    parent_overrides: dict | None = None,
) -> Path:
    mode = STAGED_PHASE_MODES[phase]
    min_configs = args.min_configs or default_min_configs_for_mode(mode)
    slug = _snapshot_slug(args.snapshot_dir)
    stem = f"strategy_replay_dynamic_{mode}_mega_timeline_{MEGA_GRID_VERSION}_phase{phase}_{slug}"
    csv_path, progress_path, checkpoint_path, _log_path = _phase_paths(args, phase)

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

    print(
        f"\n=== Phase {phase} ({mode}) | min_configs={min_configs} | "
        f"parent={'yes' if parent_overrides else 'no'} ==="
    )
    print(
        f"Checkpoint CSV (every {args.checkpoint_every} configs): {checkpoint_path}"
    )
    results, _baseline, _benchmark, range_start, range_end = await run_timeline_dynamic_sweep(
        dynamic_mode=mode,
        output_dir=args.output_dir,
        min_configs=min_configs,
        seed=args.seed + ord(phase) - ord("A"),
        output_stem=stem,
        snapshot_dir=str(args.snapshot_dir),
        top_n=args.top,
        progress_path=progress_path,
        parent_overrides=parent_overrides,
        checkpoint_every=args.checkpoint_every,
    )
    if results:
        winner = results[0]
        print(
            f"Phase {phase} top: {winner.name}  CapNorm=${winner.capital_normalized_pnl:+.2f}  "
            f"avg_sl={winner.avg_sl_pct:.2f}%  sl_sat={winner.sl_saturation_pct:.0f}%"
        )
    print(f"Phase {phase} wrote {csv_path}  range {range_start} -> {range_end}")
    return csv_path


def _load_phase_a_winner(args: argparse.Namespace) -> tuple[Path, str, dict]:
    phase_a_csv = args.phase_a_csv or _phase_paths(args, "A")[0]
    if not phase_a_csv.is_file():
        raise FileNotFoundError(f"Phase A CSV not found: {phase_a_csv}")
    name, _diff, full_a = load_sweep_winner_from_csv(phase_a_csv, mode="sizing_only")
    print(f"Using Phase A winner: {name} from {phase_a_csv}")
    return phase_a_csv, name, full_a


async def _run_bc(args: argparse.Namespace) -> None:
    _phase_a_csv, _name_a, full_a = _load_phase_a_winner(args)

    phase_b_csv = await _run_phase(args, "B", parent_overrides=full_a)
    _name_b, _diff_b, full_b = load_sweep_winner_from_csv(
        phase_b_csv,
        mode="barriers_only",
        parent_overrides=full_a,
    )
    print(f"Phase B winner loaded: {_name_b}")

    phase_c_parent = _merge(full_a, **extract_barrier_overrides(full_b))
    await _run_phase(args, "C", parent_overrides=phase_c_parent)


async def _run_all(args: argparse.Namespace) -> None:
    phase_a_csv = await _run_phase(args, "A", parent_overrides=None)
    _name_a, _diff_a, full_a = load_sweep_winner_from_csv(phase_a_csv, mode="sizing_only")
    print(f"Phase A winner loaded: {_name_a}")

    args.phase_a_csv = phase_a_csv
    await _run_bc(args)


async def main() -> int:
    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.phase == "all":
        await _run_all(args)
        return 0

    if args.phase == "BC":
        try:
            await _run_bc(args)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.phase == "A":
        await _run_phase(args, "A", parent_overrides=None)
        return 0

    if args.phase == "B":
        try:
            _phase_a_csv, _name, full_a = _load_phase_a_winner(args)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        await _run_phase(args, "B", parent_overrides=full_a)
        return 0

    phase_a_csv = args.phase_a_csv or _phase_paths(args, "A")[0]
    phase_b_csv = args.phase_b_csv or _phase_paths(args, "B")[0]
    if not phase_a_csv.is_file() or not phase_b_csv.is_file():
        print(
            f"Phase C requires both prior CSVs:\n  A: {phase_a_csv}\n  B: {phase_b_csv}",
            file=sys.stderr,
        )
        return 1
    _name_a, _diff_a, full_a = load_sweep_winner_from_csv(phase_a_csv, mode="sizing_only")
    _name_b, _diff_b, full_b = load_sweep_winner_from_csv(
        phase_b_csv,
        mode="barriers_only",
        parent_overrides=full_a,
    )
    phase_c_parent = _merge(full_a, **extract_barrier_overrides(full_b))
    print(f"Phase C parent from A={_name_a} + B barrier keys from {_name_b}")
    await _run_phase(args, "C", parent_overrides=phase_c_parent)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
