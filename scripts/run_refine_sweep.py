#!/usr/bin/env python3
"""Refine sweep around any named winner preset (parent-relative grids).

Phases (sequential, parent chaining):
  A — barrier floors / vol curve (SL slippage leak)
  B — conviction sizing cap (high-mult loss bucket)
  C — adaptive long quality + cooldown churn
  D — combined narrow grid on A+B+C winners

Each phase samples ±%%/±abs steps around the current parent baseline (not fixed
absolute grids). Use ``BCD`` to run B→C→D when phase A CSV already exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    REFINE_DYNAMIC_MODE,
    REFINE_MIN_CONFIGS_BY_PHASE,
    REFINE_STAGED_PHASES,
    default_min_configs_for_refine_phase,
    iter_refine_sweep_configs,
    load_sweep_winner_from_csv,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import configure_replay_data_sources
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
    DEFAULT_CHECKPOINT_EVERY,
    run_timeline_dynamic_sweep,
    timeline_range_from_reports,
)
from condor.strategy_runners.macdbb.presets import (
    DEFAULT_AGENT_STRATEGY_PRESET,
    DYNAMIC_PRESET_OVERRIDES,
)


def refine_output_slug(parent_label: str) -> str:
    """Filesystem-safe slug from a preset id or sweep config name."""
    slug = parent_label.strip()
    for prefix in ("hl_dynamic_timeline_", "dyn_both_on_"):
        if slug.startswith(prefix):
            slug = slug.removeprefix(prefix)
    if slug.endswith("_binance_1y"):
        slug = slug.removesuffix("_binance_1y")
    slug = slug.replace("/", "_").strip("_")
    return slug or "parent"


def resolve_refine_timeline_range(
    *,
    parent_overrides: dict[str, Any],
    snapshot_dir: Path,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> tuple[str, str]:
    """Pick the refine backtest window (CLI > parent JSON > manifest > reports)."""
    if range_start_utc and range_end_utc:
        return range_start_utc, range_end_utc
    parent_start = parent_overrides.get("range_start_utc")
    parent_end = parent_overrides.get("range_end_utc")
    if parent_start and parent_end:
        return str(parent_start), str(parent_end)
    manifest = load_manifest(snapshot_dir=snapshot_dir)
    if manifest and manifest.get("range_start_utc") and manifest.get("range_end_utc"):
        return str(manifest["range_start_utc"]), str(manifest["range_end_utc"])
    return timeline_range_from_reports()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refine timeline sweep: A→B→C→D around a winner preset"
    )
    parser.add_argument(
        "phase",
        choices=[*REFINE_STAGED_PHASES, "BCD", "all"],
        help="Run one phase, BCD (B→C→D from existing A CSV), or all four",
    )
    parser.add_argument(
        "--parent-preset",
        default=DEFAULT_AGENT_STRATEGY_PRESET,
        help="Named preset as refine root parent (default: agent default preset)",
    )
    parser.add_argument(
        "--output-tag",
        default="",
        help="Optional short tag for output filenames (default: derived from parent preset)",
    )
    parser.add_argument(
        "--parent-overrides-json",
        type=Path,
        default=None,
        help="JSON file with full merged parent config (alternative to --parent-preset)",
    )
    parser.add_argument(
        "--automation-state-path",
        type=Path,
        default=None,
        help="Automation state JSON for cancel-between-phases checks",
    )
    parser.add_argument(
        "--legacy-staged-parent",
        action="store_true",
        help="Use staged mega Phase A/B/C CSV chain instead of --parent-preset",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots_binance_1y"),
    )
    parser.add_argument(
        "--range-start-utc",
        default="",
        help="Timeline start UTC (ISO). Default: parent JSON, else snapshot manifest",
    )
    parser.add_argument(
        "--range-end-utc",
        default="",
        help="Timeline end UTC (ISO). Default: parent JSON, else snapshot manifest",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    parser.add_argument(
        "--phase-a-csv",
        type=Path,
        default=None,
        help="Staged mega Phase A CSV (only with --legacy-staged-parent)",
    )
    parser.add_argument(
        "--phase-b-csv",
        type=Path,
        default=None,
        help="Staged mega Phase B CSV (only with --legacy-staged-parent)",
    )
    parser.add_argument(
        "--phase-c-csv",
        type=Path,
        default=None,
        help="Staged mega Phase C CSV (only with --legacy-staged-parent)",
    )
    parser.add_argument(
        "--refine-a-csv",
        type=Path,
        default=None,
        help="Refine phase A CSV (required for B/C/D unless running all)",
    )
    parser.add_argument(
        "--refine-b-csv",
        type=Path,
        default=None,
        help="Refine phase B CSV (required for C/D unless running all or BCD)",
    )
    parser.add_argument(
        "--refine-c-csv",
        type=Path,
        default=None,
        help="Refine phase C CSV (required for D unless running all or BCD)",
    )
    parser.add_argument("--min-configs", type=int, default=0, help="Override phase budget")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help="Write checkpoint CSV every N configs (0 disables)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel config workers (default 1)",
    )
    parser.add_argument(
        "--worker-ram-gb",
        type=float,
        default=2.0,
        help="Estimated RAM per worker for worker cap (default 2.0)",
    )
    return parser.parse_args()


def _snapshot_slug(snapshot_dir: Path) -> str:
    name = snapshot_dir.name
    if name.startswith("replay_snapshots_"):
        return name.removeprefix("replay_snapshots_")
    return name


def _refine_stem(args: argparse.Namespace, phase: str) -> str:
    slug = _snapshot_slug(args.snapshot_dir)
    parent_slug = args.output_tag or refine_output_slug(args.root_parent_label)
    return (
        f"macdbb_scanner_aggressive_hl_backtest_{REFINE_DYNAMIC_MODE}_refine_{parent_slug}_"
        f"phase{phase}_{slug}"
    )


def _refine_paths(args: argparse.Namespace, phase: str) -> tuple[Path, Path, Path]:
    stem = _refine_stem(args, phase)
    csv_path = args.output_dir / f"{stem}.csv"
    progress_path = args.output_dir / f"{stem}.progress.json"
    checkpoint_path = args.output_dir / f"{stem}.checkpoint.csv"
    return csv_path, progress_path, checkpoint_path


def _load_preset_parent(preset: str) -> tuple[str, dict]:
    overrides = DYNAMIC_PRESET_OVERRIDES.get(preset)
    if not overrides:
        valid = ", ".join(sorted(DYNAMIC_PRESET_OVERRIDES))
        raise ValueError(f"Unknown parent preset {preset!r}; choose one of: {valid}")
    full = dict(overrides)
    full["preset"] = "custom"
    print(f"Refine root parent preset: {preset}")
    return preset, full


def _load_legacy_staged_parent(args: argparse.Namespace) -> tuple[str, dict]:
    from scripts.apply_staged_v5_winner import (
        DEFAULT_PHASE_A,
        DEFAULT_PHASE_B,
        DEFAULT_PHASE_C,
        load_staged_phase_c_winner,
    )

    phase_a = args.phase_a_csv or Path(DEFAULT_PHASE_A)
    phase_b = args.phase_b_csv or Path(DEFAULT_PHASE_B)
    phase_c = args.phase_c_csv or Path(DEFAULT_PHASE_C)
    for label, path in (
        ("Phase A", phase_a),
        ("Phase B", phase_b),
        ("Phase C", phase_c),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} CSV not found: {path}")
    name, _diff, config = load_staged_phase_c_winner(phase_a, phase_b, phase_c)
    full = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    print(f"Legacy staged mega parent: {name}")
    return name, full


def _load_json_parent(path: Path) -> tuple[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Parent overrides JSON must be an object: {path}")
    full = dict(raw)
    full["preset"] = "custom"
    label = path.stem
    print(f"Refine root parent from JSON: {path}")
    return label, full


def _check_refine_cancel(args: argparse.Namespace) -> bool:
    if args.automation_state_path is None:
        return False
    from routines.macdbb_scanner_aggressive_hl_replay.sweep_automation import (
        is_refine_cancel_requested,
    )

    if is_refine_cancel_requested(args.automation_state_path):
        print("Refine cancel requested — exiting between phases")
        return True
    return False


def _load_root_parent(args: argparse.Namespace) -> tuple[str, dict]:
    if args.parent_overrides_json is not None:
        if not args.parent_overrides_json.is_file():
            raise FileNotFoundError(
                f"Parent overrides JSON not found: {args.parent_overrides_json}"
            )
        return _load_json_parent(args.parent_overrides_json)
    if args.legacy_staged_parent:
        return _load_legacy_staged_parent(args)
    return _load_preset_parent(args.parent_preset)


def _load_refine_parent(csv_path: Path, *, parent_overrides: dict) -> tuple[str, dict]:
    name, _diff, full = load_sweep_winner_from_csv(
        csv_path,
        mode=REFINE_DYNAMIC_MODE,
        parent_overrides=parent_overrides,
    )
    print(f"Refine parent loaded: {name} from {csv_path}")
    return name, full


async def _run_refine_phase(
    args: argparse.Namespace,
    phase: str,
    *,
    parent_overrides: dict,
    root_parent_label: str = "",
) -> Path:
    min_configs = args.min_configs or default_min_configs_for_refine_phase(phase)
    stem = _refine_stem(args, phase)
    csv_path, progress_path, checkpoint_path = _refine_paths(args, phase)

    configure_replay_data_sources(
        DynamicStrategyReplayConfig(
            data_source="snapshots",
            snapshot_dir=str(args.snapshot_dir),
            replay_mode="timeline_backtest",
        )
    )
    start, end = resolve_refine_timeline_range(
        parent_overrides=parent_overrides,
        snapshot_dir=args.snapshot_dir,
        range_start_utc=getattr(args, "range_start_utc", "") or None,
        range_end_utc=getattr(args, "range_end_utc", "") or None,
    )
    print(f"Refine timeline range: {start} -> {end}")

    config_items = list(
        iter_refine_sweep_configs(
            phase,
            min_configs=min_configs,
            seed=args.seed + ord(phase) - ord("A"),
            parent_overrides=parent_overrides,
        )
    )

    parent_note = root_parent_label or "prior refine phase"
    print(
        f"\n=== Refine phase {phase} | configs={len(config_items)} | "
        f"parent={parent_note} ==="
    )
    print(f"Checkpoint CSV (every {args.checkpoint_every} configs): {checkpoint_path}")

    results, _baseline, _benchmark, range_start, range_end = await run_timeline_dynamic_sweep(
        dynamic_mode=REFINE_DYNAMIC_MODE,
        output_dir=args.output_dir,
        min_configs=min_configs,
        seed=args.seed + ord(phase) - ord("A"),
        output_stem=stem,
        snapshot_dir=str(args.snapshot_dir),
        top_n=args.top,
        progress_path=progress_path,
        parent_overrides=parent_overrides,
        checkpoint_every=args.checkpoint_every,
        config_items=config_items,
        workers=args.workers,
        worker_ram_gb=args.worker_ram_gb,
        range_start_utc=start,
        range_end_utc=end,
    )
    if results:
        winner = results[0]
        print(
            f"Refine {phase} top: {winner.name}  CapNorm=${winner.capital_normalized_pnl:+.2f}  "
            f"avg_sl={winner.avg_sl_pct:.2f}%  sl_sat={winner.sl_saturation_pct:.0f}%  "
            f"avg_mult={winner.avg_size_mult:.2f}"
        )
    print(f"Refine phase {phase} wrote {csv_path}  range {range_start} -> {range_end}")
    return csv_path


async def _run_bcd(args: argparse.Namespace, root_parent: dict) -> None:
    refine_a_csv = args.refine_a_csv or _refine_paths(args, "A")[0]
    if not refine_a_csv.is_file():
        raise FileNotFoundError(f"Refine phase A CSV not found: {refine_a_csv}")
    _name_a, parent_a = _load_refine_parent(refine_a_csv, parent_overrides=root_parent)

    if _check_refine_cancel(args):
        return
    refine_b_csv = await _run_refine_phase(args, "B", parent_overrides=parent_a)
    _name_b, parent_b = _load_refine_parent(refine_b_csv, parent_overrides=parent_a)

    if _check_refine_cancel(args):
        return
    refine_c_csv = await _run_refine_phase(args, "C", parent_overrides=parent_b)
    _name_c, parent_c = _load_refine_parent(refine_c_csv, parent_overrides=parent_b)

    if _check_refine_cancel(args):
        return
    await _run_refine_phase(args, "D", parent_overrides=parent_c)


async def _run_all(args: argparse.Namespace, root_parent: dict, root_label: str) -> None:
    refine_a_csv = await _run_refine_phase(
        args,
        "A",
        parent_overrides=root_parent,
        root_parent_label=root_label,
    )
    if _check_refine_cancel(args):
        return
    args.refine_a_csv = refine_a_csv
    await _run_bcd(args, root_parent)


async def main() -> int:
    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.parent_overrides_json and args.legacy_staged_parent:
        print("Use only one of --parent-overrides-json or --legacy-staged-parent", file=sys.stderr)
        return 1

    try:
        root_label, root_parent = _load_root_parent(args)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.root_parent_label = root_label
    output_tag = args.output_tag or refine_output_slug(root_label)
    total_default = sum(REFINE_MIN_CONFIGS_BY_PHASE[p] for p in REFINE_STAGED_PHASES) + len(
        REFINE_STAGED_PHASES
    )
    print(
        f"Refine sweep parent={output_tag} | default budget ≈{total_default} configs "
        f"({', '.join(f'{p}={REFINE_MIN_CONFIGS_BY_PHASE[p]}' for p in REFINE_STAGED_PHASES)} "
        f"+ {len(REFINE_STAGED_PHASES)} anchors) | workers={args.workers}"
    )

    if args.phase == "all":
        await _run_all(args, root_parent, root_label)
        return 0

    if args.phase == "BCD":
        try:
            await _run_bcd(args, root_parent)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.phase == "A":
        await _run_refine_phase(
            args,
            "A",
            parent_overrides=root_parent,
            root_parent_label=root_label,
        )
        return 0

    if args.phase == "B":
        refine_a_csv = args.refine_a_csv or _refine_paths(args, "A")[0]
        if not refine_a_csv.is_file():
            print(f"Refine phase A CSV not found: {refine_a_csv}", file=sys.stderr)
            return 1
        _name_a, parent_a = _load_refine_parent(refine_a_csv, parent_overrides=root_parent)
        await _run_refine_phase(args, "B", parent_overrides=parent_a)
        return 0

    if args.phase == "C":
        refine_a_csv = args.refine_a_csv or _refine_paths(args, "A")[0]
        refine_b_csv = args.refine_b_csv or _refine_paths(args, "B")[0]
        if not refine_a_csv.is_file() or not refine_b_csv.is_file():
            print(
                f"Refine phase C requires A and B CSVs:\n  A: {refine_a_csv}\n  B: {refine_b_csv}",
                file=sys.stderr,
            )
            return 1
        _name_a, parent_a = _load_refine_parent(refine_a_csv, parent_overrides=root_parent)
        _name_b, parent_b = _load_refine_parent(refine_b_csv, parent_overrides=parent_a)
        await _run_refine_phase(args, "C", parent_overrides=parent_b)
        return 0

    refine_a_csv = args.refine_a_csv or _refine_paths(args, "A")[0]
    refine_b_csv = args.refine_b_csv or _refine_paths(args, "B")[0]
    refine_c_csv = args.refine_c_csv or _refine_paths(args, "C")[0]
    if not all(path.is_file() for path in (refine_a_csv, refine_b_csv, refine_c_csv)):
        print(
            "Refine phase D requires A, B, and C CSVs:\n"
            f"  A: {refine_a_csv}\n  B: {refine_b_csv}\n  C: {refine_c_csv}",
            file=sys.stderr,
        )
        return 1
    _name_a, parent_a = _load_refine_parent(refine_a_csv, parent_overrides=root_parent)
    _name_b, parent_b = _load_refine_parent(refine_b_csv, parent_overrides=parent_a)
    _name_c, parent_c = _load_refine_parent(refine_c_csv, parent_overrides=parent_b)
    await _run_refine_phase(args, "D", parent_overrides=parent_c)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
