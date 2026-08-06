#!/usr/bin/env python3
"""Validate staged v5 Phase C winner, apply to agent.md, and register backtest preset."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    _merge,
    extract_barrier_overrides,
    load_sweep_winner_from_csv,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
    _DYNAMIC_PRESET_INFRA,
    _STRATEGY_TIMELINE_MEGA_BEST,
    _build_timeline_driver,
    _merge_preset_layers,
    resolve_config_with_preset,
)
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
    DEFAULT_FREQUENCY_SEC,
    PRESET_STRIP_KEYS,
    apply_winner_to_agent,
    apply_winner_to_presets,
    merge_timeline_config,
)
from routines.macdbb_scanner_aggressive_hl_backtest import run as run_dynamic_replay


def build_staged_winner_preset_overrides(config: DynamicStrategyReplayConfig) -> dict:
    """Full timeline preset from resolved winner (includes A+B+C chained params)."""
    strategy_layer = {
        key: getattr(config, key)
        for key in _STRATEGY_TIMELINE_MEGA_BEST
        if hasattr(config, key)
    }
    driver_keys = set(_build_timeline_driver())
    for key in DynamicStrategyReplayConfig.model_fields:
        if key in strategy_layer or key in PRESET_STRIP_KEYS:
            continue
        if key in _DYNAMIC_PRESET_INFRA or key in driver_keys:
            continue
        strategy_layer[key] = getattr(config, key)
    return _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _build_timeline_driver(),
        strategy_layer,
    )

STAGED_V5_WINNER_PRESET = "hl_dynamic_timeline_v5_staged_abc_winner_binance_1y"
DEFAULT_PHASE_A = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_sizing_only_mega_timeline_v5_phaseA_binance_1y.csv"
)
DEFAULT_PHASE_B = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_barriers_only_mega_timeline_v5_phaseB_binance_1y.csv"
)
DEFAULT_PHASE_C = (
    "data/strategy_replay_sweeps/"
    "strategy_replay_dynamic_both_on_mega_timeline_v5_phaseC_binance_1y.csv"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply staged v5 Phase C sweep winner")
    parser.add_argument("--phase-a-csv", type=Path, default=Path(DEFAULT_PHASE_A))
    parser.add_argument("--phase-b-csv", type=Path, default=Path(DEFAULT_PHASE_B))
    parser.add_argument("--phase-c-csv", type=Path, default=Path(DEFAULT_PHASE_C))
    parser.add_argument(
        "--preset-name",
        default=STAGED_V5_WINNER_PRESET,
        help="New backtest preset name to register in strategies/ presets.yaml",
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
            "staged_v5_phaseC_winner_routine_validation.log"
        ),
    )
    return parser.parse_args()


def load_staged_phase_c_winner(
    phase_a_csv: Path,
    phase_b_csv: Path,
    phase_c_csv: Path,
) -> tuple[str, dict, DynamicStrategyReplayConfig]:
    _, _, full_a = load_sweep_winner_from_csv(phase_a_csv, mode="sizing_only")
    _, _, full_b = load_sweep_winner_from_csv(
        phase_b_csv,
        mode="barriers_only",
        parent_overrides=full_a,
    )
    phase_c_parent = _merge(full_a, **extract_barrier_overrides(full_b))
    name, diff_c, full_c = load_sweep_winner_from_csv(
        phase_c_csv,
        mode="both_on",
        parent_overrides=phase_c_parent,
    )
    snapshot_dir = full_c.get("snapshot_dir") or "data/replay_snapshots_binance_1y"
    manifest = load_manifest(snapshot_dir=snapshot_dir) or {}
    range_start = manifest.get("range_start_utc")
    range_end = manifest.get("range_end_utc")
    replay_overrides = merge_timeline_config(
        full_c,
        range_start_utc=str(range_start) if range_start else None,
        range_end_utc=str(range_end) if range_end else None,
    )
    replay_overrides["snapshot_dir"] = snapshot_dir
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(**replay_overrides)
    )
    return name, diff_c, config


async def _main() -> int:
    args = _parse_args()
    for label, path in (
        ("Phase A", args.phase_a_csv),
        ("Phase B", args.phase_b_csv),
        ("Phase C", args.phase_c_csv),
    ):
        if not path.is_file():
            print(f"{label} CSV not found: {path}", file=sys.stderr)
            return 1

    name, diff_c, config = load_staged_phase_c_winner(
        args.phase_a_csv,
        args.phase_b_csv,
        args.phase_c_csv,
    )
    print(f"Staged v5 winner: {name}")
    print(f"  max_open_executors={config.max_open_executors}")
    print(f"  sl/tp base={config.sl_pct}/{config.tp_pct}%")
    print(
        f"  barriers: {config.volatility_source} ref_vol={config.ref_volatility_pct} "
        f"sl_exp={config.sl_vol_exponent} tp_exp={config.tp_vol_exponent} "
        f"sl_max={config.sl_max_pct} tp_max={config.tp_max_pct}"
    )
    print(
        f"  sizing: conv {config.min_conviction_mult}-{config.max_conviction_mult} "
        f"str={config.strength_mult_per_unit} notional {config.min_notional_quote}-"
        f"{config.max_notional_quote}"
    )

    if not args.skip_routine:
        print("\nRunning macdbb_scanner_aggressive_hl_backtest...")
        result = await run_dynamic_replay(config, None)
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        args.validation_log.parent.mkdir(parents=True, exist_ok=True)
        args.validation_log.write_text(
            f"Staged v5 Phase C winner routine validation\n"
            f"Config: {name}\n\n{text}\n",
            encoding="utf-8",
        )
        print(f"Wrote {args.validation_log}")
        print(text[:2000] if len(text) > 2000 else text)

    preset_overrides = build_staged_winner_preset_overrides(config)
    if not args.skip_agent:
        params = apply_winner_to_agent(config, frequency_sec=args.frequency_sec)
        print(
            "\nUpdated strategies/macdbb_scanner_aggressive_hl/strategy.yaml "
            f"({len(params)} params)"
        )

    if not args.skip_preset:
        apply_winner_to_presets(preset_overrides, preset_name=args.preset_name)
        print(f"Added preset {args.preset_name!r} to strategies/ presets.yaml")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
