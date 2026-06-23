#!/usr/bin/env python3
"""Validate refine v5 winner (A→B→C→D chain), apply to agent.md, register preset."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from routines.macdbb_replay.config_sweep import (
    REFINE_DYNAMIC_MODE,
    load_sweep_winner_from_csv,
)
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.snapshot_store import load_manifest
from routines.macdbb_replay.timeline_sweep import (
    DEFAULT_FREQUENCY_SEC,
    apply_winner_to_agent,
    apply_winner_to_presets,
    merge_timeline_config,
)
from routines.strategy_replay_backtest_dynamic_amount import run as run_dynamic_replay
from scripts.apply_staged_v5_winner import (
    DEFAULT_PHASE_A,
    DEFAULT_PHASE_B,
    DEFAULT_PHASE_C,
    build_staged_winner_preset_overrides,
    load_staged_phase_c_winner,
)

REFINE_V5_WINNER_PRESET = "hl_dynamic_timeline_refine_v5_winner_binance_1y"
DEFAULT_REFINE_A = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_both_on_refine_v5_winner_phaseA_binance_1y.csv"
)
DEFAULT_REFINE_B = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_both_on_refine_v5_winner_phaseB_binance_1y.csv"
)
DEFAULT_REFINE_C = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_both_on_refine_v5_winner_phaseC_binance_1y.csv"
)
DEFAULT_REFINE_D = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_both_on_refine_v5_winner_phaseD_binance_1y.csv"
)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply refine v5 sweep winner (A→B→C→D)")
    parser.add_argument("--phase-a-csv", type=Path, default=Path(DEFAULT_PHASE_A))
    parser.add_argument("--phase-b-csv", type=Path, default=Path(DEFAULT_PHASE_B))
    parser.add_argument("--phase-c-csv", type=Path, default=Path(DEFAULT_PHASE_C))
    parser.add_argument("--refine-a-csv", type=Path, default=Path(DEFAULT_REFINE_A))
    parser.add_argument("--refine-b-csv", type=Path, default=Path(DEFAULT_REFINE_B))
    parser.add_argument("--refine-c-csv", type=Path, default=Path(DEFAULT_REFINE_C))
    parser.add_argument("--refine-d-csv", type=Path, default=Path(DEFAULT_REFINE_D))
    parser.add_argument(
        "--preset-name",
        default=REFINE_V5_WINNER_PRESET,
        help="New backtest preset name to register in presets.py",
    )
    parser.add_argument("--frequency-sec", type=int, default=DEFAULT_FREQUENCY_SEC)
    parser.add_argument("--skip-routine", action="store_true")
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--skip-preset", action="store_true")
    parser.add_argument(
        "--validation-log",
        type=Path,
        default=Path(
            "data/strategy_replay_sweeps/"
            "refine_v5_winner_routine_validation.log"
        ),
    )
    return parser.parse_args()


def load_refine_winner(
    phase_a_csv: Path,
    phase_b_csv: Path,
    phase_c_csv: Path,
    refine_a_csv: Path,
    refine_b_csv: Path,
    refine_c_csv: Path,
    refine_d_csv: Path,
) -> tuple[str, dict, DynamicStrategyReplayConfig]:
    """Chain v5 staged winner → refine A→B→C→D; return final resolved config."""
    _v5_name, _v5_diff, v5_config = load_staged_phase_c_winner(
        phase_a_csv, phase_b_csv, phase_c_csv
    )
    v5_parent = v5_config.model_dump()

    _name_a, _diff_a, full_a = load_sweep_winner_from_csv(
        refine_a_csv,
        mode=REFINE_DYNAMIC_MODE,
        parent_overrides=v5_parent,
    )
    _name_b, diff_b, full_b = load_sweep_winner_from_csv(
        refine_b_csv,
        mode=REFINE_DYNAMIC_MODE,
        parent_overrides=full_a,
    )
    _name_c, _diff_c, full_c = load_sweep_winner_from_csv(
        refine_c_csv,
        mode=REFINE_DYNAMIC_MODE,
        parent_overrides=full_b,
    )
    name, diff_d, full_d = load_sweep_winner_from_csv(
        refine_d_csv,
        mode=REFINE_DYNAMIC_MODE,
        parent_overrides=full_c,
    )
    final_diff = {**diff_b, **diff_d} if diff_d else diff_b

    snapshot_dir = full_d.get("snapshot_dir") or "data/replay_snapshots_binance_1y"
    manifest = load_manifest(snapshot_dir=snapshot_dir) or {}
    range_start = manifest.get("range_start_utc")
    range_end = manifest.get("range_end_utc")
    replay_overrides = merge_timeline_config(
        full_d,
        range_start_utc=str(range_start) if range_start else None,
        range_end_utc=str(range_end) if range_end else None,
    )
    replay_overrides["snapshot_dir"] = snapshot_dir
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(**replay_overrides)
    )
    return name, final_diff, config


async def _main() -> int:
    _configure_logging()
    args = _parse_args()
    for label, path in (
        ("v5 Phase A", args.phase_a_csv),
        ("v5 Phase B", args.phase_b_csv),
        ("v5 Phase C", args.phase_c_csv),
        ("Refine A", args.refine_a_csv),
        ("Refine B", args.refine_b_csv),
        ("Refine C", args.refine_c_csv),
        ("Refine D", args.refine_d_csv),
    ):
        if not path.is_file():
            print(f"{label} CSV not found: {path}", file=sys.stderr)
            return 1

    name, diff, config = load_refine_winner(
        args.phase_a_csv,
        args.phase_b_csv,
        args.phase_c_csv,
        args.refine_a_csv,
        args.refine_b_csv,
        args.refine_c_csv,
        args.refine_d_csv,
    )
    print(f"Refine v5 winner: {name}")
    print(f"  overrides vs v5 parent: {len(diff)} keys")
    print(f"  max_open_executors={config.max_open_executors}")
    print(f"  sl/tp base={config.sl_pct}/{config.tp_pct}%")
    print(
        f"  barriers: {config.volatility_source} ref_vol={config.ref_volatility_pct} "
        f"sl_exp={config.sl_vol_exponent} tp_exp={config.tp_vol_exponent} "
        f"sl_min/max={config.sl_min_pct}/{config.sl_max_pct} "
        f"tp_min/max={config.tp_min_pct}/{config.tp_max_pct}"
    )
    print(
        f"  sizing: conv {config.min_conviction_mult}-{config.max_conviction_mult} "
        f"str={config.strength_mult_per_unit} notional {config.min_notional_quote}-"
        f"{config.max_notional_quote}"
    )

    if not args.skip_routine:
        print(
            "\nRunning strategy_replay_backtest_dynamic_amount "
            "(prefetch + full timeline sim — expect several minutes)...",
            flush=True,
        )
        t0 = time.monotonic()
        result = await run_dynamic_replay(config, None)
        elapsed = time.monotonic() - t0
        print(f"Routine finished in {elapsed:.0f}s", flush=True)
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        args.validation_log.parent.mkdir(parents=True, exist_ok=True)
        args.validation_log.write_text(
            f"Refine v5 winner routine validation\n"
            f"Config: {name}\n\n{text}\n",
            encoding="utf-8",
        )
        print(f"Wrote {args.validation_log}")
        print(text[:2000] if len(text) > 2000 else text)

    preset_overrides = build_staged_winner_preset_overrides(config)
    if not args.skip_agent:
        params = apply_winner_to_agent(config, frequency_sec=args.frequency_sec)
        print(
            f"\nUpdated trading_agents/macdbb_scanner_aggressive_hl/agent.md "
            f"({len(params)} params)"
        )

    if not args.skip_preset:
        apply_winner_to_presets(preset_overrides, preset_name=args.preset_name)
        print(f"Added preset {args.preset_name!r} to presets.py and models.py")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
