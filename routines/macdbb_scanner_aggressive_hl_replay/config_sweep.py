"""Batch dynamic strategy-replay PnL sweep over session journals (CLI helper)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gc
import json
import logging
import math
import multiprocessing as mp
import os
import random
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from condor.trading_agent.policies.macdbb_dynamic import compute_dynamic_barriers
from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_scanner_aggressive_hl_replay.journal import parse_journal_ticks
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    StrategyReplayConfig,
    parse_session_selector,
)
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    capital_normalized_pnl,
    resolve_config_with_preset,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
    configure_replay_data_sources,
    is_report_driven_data_source,
)
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    ReportMeta,
    build_reports_by_pair,
    load_reports_index,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

logger = logging.getLogger(__name__)

# Timeline mega sweep anchor — refine v5 winner (``presets.py``).
CURRENT_WINNER_PRESET = "hl_dynamic_timeline_refine_v5_winner_binance_1y"

CURRENT_WINNER_OVERRIDES: dict[str, Any] = {
    **DYNAMIC_PRESET_OVERRIDES[CURRENT_WINNER_PRESET],
    "preset": "custom",
}

DYNAMIC_MODE_PRESETS: dict[str, dict[str, Any]] = {
    "sizing_only": {
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": False,
    },
    "barriers_only": {
        "enable_dynamic_sizing": False,
        "enable_dynamic_barriers": True,
    },
    "both_on": {
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": True,
        "ignore_journal_barriers_when_dynamic": True,
    },
    "both_keep_journal": {
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": True,
        "ignore_journal_barriers_when_dynamic": False,
    },
}

MEGA_GRID_VERSION = "v5"

# Typical 1h BB-width vol (%%) for HL alts — used to reject fully clamped barrier combos.
BARRIER_MEDIAN_VOL_PCT = 2.5

# ref_volatility_pct scales differ by vol source; stratified sampling uses these buckets.
NATR_REF_VOLATILITY_PCT: tuple[float, ...] = (0.08, 0.12, 0.18, 0.25, 0.35, 0.45)
BB_REF_VOLATILITY_PCT: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5)

# Live agent.md default_config strategy_params (Hyperliquid deployment target).
LIVE_AGENT_DEFAULT_OVERRIDES: dict[str, Any] = {
    "preset": "custom",
    "sl_pct": 4.5,
    "tp_pct": 6.2,
    "leverage": 30,
    "max_open_executors": 10,
    "adaptive_long_bb_pos_max": 82.0,
    "adaptive_short_bb_pos_min": 88.0,
    "adaptive_strong_long_bb_pos_max": 38.0,
    "adaptive_strong_short_bb_pos_min": 95.0,
    "adaptive_min_macd_gap_ratio": 0.15,
    "adaptive_min_hist_ratio": 0.07,
    "adaptive_score_open_min": 1.8,
    "adaptive_score_open_min_extreme": 1.8,
    "adaptive_hist_sign_bonus": 0.48,
    "adaptive_hist_sign_penalty": 0.28,
    "adaptive_momentum_bonus": 0.38,
    "adaptive_momentum_penalty": 0.06,
    "bb_proximity_epsilon_pct": 0.06,
    "thesis_decay_exit_ticks": 28,
    "thesis_bb_drift_pts": 18.0,
    "enable_dynamic_sizing": True,
    "enable_dynamic_barriers": True,
    "min_notional_quote": 200.0,
    "max_notional_quote": 1100.0,
    "min_conviction_mult": 0.7,
    "max_conviction_mult": 1.4,
    "strength_mult_per_unit": 0.16,
    "extreme_displacement_mult": 1.35,
    "thin_universe_mult": 0.82,
    "mature_tape_low_vol_mult": 1.12,
    "vol_inverse_sizing": True,
    "min_vol_mult": 0.82,
    "max_vol_mult": 1.75,
    "ref_volatility_pct": 0.68,
    "sl_vol_exponent": 1.05,
    "tp_vol_exponent": 0.75,
    "sl_min_pct": 2.2,
    "sl_max_pct": 7.5,
    "tp_min_pct": 5.5,
    "tp_max_pct": 11.0,
    "volatility_source": "bb_width",
}

# Mega sweep grid v5 — de-saturate dynamic barriers; trim redundant SL/TP grid values.
MEGA_SWEEP_GRID: dict[str, tuple[Any, ...]] = {
    "max_open_executors": (3, 5, 8, 10),
    "adaptive_long_bb_pos_max": (48.0, 58.0, 64.0, 68.0, 72.0, 76.0, 82.0),
    "adaptive_short_bb_pos_min": (80.0, 82.0, 88.0, 92.0, 94.0),
    "adaptive_strong_long_bb_pos_max": (20.0, 26.0, 36.0, 38.0),
    "adaptive_strong_short_bb_pos_min": (82.0, 88.0, 91.0, 95.0),
    "adaptive_min_macd_gap_ratio": (0.03, 0.07, 0.09, 0.11, 0.15, 0.20),
    "adaptive_min_hist_ratio": (0.07, 0.11, 0.17, 0.19, 0.26),
    "adaptive_score_open_min": (0.8, 1.8, 3.0),
    "adaptive_score_open_min_extreme": (0.6, 0.85, 1.8, 2.8),
    "adaptive_hist_sign_bonus": (0.20, 0.32, 0.38, 0.48),
    "adaptive_hist_sign_penalty": (0.20, 0.28, 0.42, 0.58),
    "adaptive_momentum_bonus": (0.11, 0.22, 0.38, 0.42),
    "adaptive_momentum_penalty": (0.06, 0.16, 0.18, 0.28),
    "sl_pct": (2.0, 3.2, 3.8, 4.5, 5.0),
    "tp_pct": (5.0, 5.5, 6.2, 8.0, 10.0, 13.0),
    "thesis_decay_exit_ticks": (6, 14, 20, 24, 28, 44, 64),
    "thesis_bb_drift_pts": (18.0, 28.0, 38.0, 55.0, 72.0, 78.0),
    "bb_proximity_epsilon_pct": (0.04, 0.06, 0.11, 0.18, 0.25, 0.28),
}

MEGA_SIZING_GRID: dict[str, tuple[Any, ...]] = {
    "min_conviction_mult": (0.58, 0.70, 0.92),
    "max_conviction_mult": (1.40, 1.65, 1.90, 2.15),
    "strength_mult_per_unit": (0.02, 0.16, 0.24, 0.32, 0.42),
    "extreme_displacement_mult": (0.95, 1.15, 1.35, 1.55, 1.65),
    "thin_universe_mult": (0.72, 0.82, 0.88, 0.96),
    "mature_tape_low_vol_mult": (0.78, 0.92, 0.99, 1.08, 1.12),
    "ref_volatility_pct": (0.45, 0.68, 0.95, 1.5, 2.0, 2.5),
    "min_vol_mult": (0.42, 0.58, 0.65, 0.82, 0.88),
    "max_vol_mult": (1.05, 1.22, 1.48, 1.55, 1.75),
    "vol_inverse_sizing": (True, False),
    "min_notional_quote": (40.0, 60.0, 125.0, 150.0, 200.0),
    "max_notional_quote": (550.0, 700.0, 850.0, 1100.0, 1400.0),
}

MEGA_BARRIER_GRID: dict[str, tuple[Any, ...]] = {
    "sl_vol_exponent": (0.55, 0.85, 1.05, 1.15),
    "tp_vol_exponent": (0.60, 0.75, 1.0, 1.35),
    "sl_min_pct": (1.0, 1.4, 2.0, 2.2),
    "sl_max_pct": (4.0, 5.5, 6.5, 7.5, 9.0),
    "tp_min_pct": (3.5, 4.5, 5.5, 6.5, 7.5),
    "tp_max_pct": (8.0, 10.0, 11.0, 15.0, 20.0, 22.0),
    "volatility_source": ("bb_width", "natr", "auto"),
    "ref_volatility_pct": BB_REF_VOLATILITY_PCT,
}

# Entry + SL/TP floor sweep (v6): adaptive entry gates + barrier bases/floors only.
# Sizing, barrier globals, tp_pct, and max_open_executors stay at v5 refine winner.
ENTRY_SLTP_SWEEP_VERSION = "v6_entry_sltp"

ENTRY_SLTP_SWEEP_GRID: dict[str, tuple[Any, ...]] = {
    # 3 values each: low / mid / high (mid ≈ winner where noted in v5 preset).
    "sl_pct": (2.0, 3.8, 5.0),
    "sl_min_pct": (1.0, 1.4, 2.6),
    "tp_min_pct": (5.5, 7.5, 10.0),
    "adaptive_long_bb_pos_max": (58.0, 76.0, 86.0),
    "adaptive_short_bb_pos_min": (78.0, 85.0, 92.0),
    "adaptive_strong_long_bb_pos_max": (20.0, 30.0, 40.0),
    "adaptive_strong_short_bb_pos_min": (78.0, 86.0, 95.0),
    "adaptive_min_macd_gap_ratio": (0.02, 0.08, 0.14),
    "adaptive_min_hist_ratio": (0.07, 0.17, 0.30),
    "adaptive_score_open_min": (1.0, 1.8, 3.5),
    "adaptive_score_open_min_extreme": (0.4, 0.6, 1.5),
    "adaptive_hist_sign_bonus": (0.25, 0.38, 0.50),
    "adaptive_hist_sign_penalty": (0.15, 0.28, 0.50),
    "adaptive_momentum_bonus": (0.15, 0.38, 0.45),
    "adaptive_momentum_penalty": (0.04, 0.06, 0.22),
    "bb_proximity_epsilon_pct": (0.04, 0.06, 0.22),
    "thesis_decay_exit_ticks": (16, 44, 72),
}

ENTRY_SLTP_SWEEP_MIN_CONFIGS = 600

SWEEP_GRID_CHOICES: tuple[str, ...] = ("mega_v5", "entry_sltp_v6")

# Per-mode default sample counts for phased sequential sweeps (A → B → C).
MEGA_SWEEP_MIN_CONFIGS_BY_MODE: dict[str, int] = {
    "sizing_only": 500,
    "barriers_only": 350,
    "both_on": 250,
    "both_keep_journal": 250,
}

STAGED_PHASE_MODES: dict[str, str] = {
    "A": "sizing_only",
    "B": "barriers_only",
    "C": "both_on",
}

REFINE_SWEEP_VERSION = "v5_winner"
REFINE_DYNAMIC_MODE = "both_on"

REFINE_STAGED_PHASES: tuple[str, ...] = ("A", "B", "C", "D")

# Narrow grids around staged v5 Phase C winner (trade-analysis driven).
REFINE_PHASE_A_GRID: dict[str, tuple[Any, ...]] = {
    "sl_min_pct": (1.4, 1.6, 1.8, 2.0, 2.2, 2.4),
    "tp_min_pct": (6.5, 7.5, 8.5, 9.5, 10.0),
    "ref_volatility_pct": (2.5, 3.0, 3.5, 4.0, 4.5, 5.0),
    "sl_vol_exponent": (0.85, 1.05, 1.25),
    "tp_vol_exponent": (1.0, 1.35, 1.6),
    "sl_pct": (3.2, 3.8, 4.2),
    "tp_pct": (5.0, 5.5, 6.5),
    "volatility_source": ("auto", "bb_width", "natr"),
}

REFINE_PHASE_B_GRID: dict[str, tuple[Any, ...]] = {
    "max_conviction_mult": (1.5, 1.7, 1.9, 2.0, 2.15),
    "strength_mult_per_unit": (0.20, 0.26, 0.32, 0.38),
    "min_conviction_mult": (0.85, 0.92, 1.0),
    "max_notional_quote": (850.0, 950.0, 1100.0, 1250.0),
    "min_notional_quote": (125.0, 150.0, 200.0),
    "extreme_displacement_mult": (1.35, 1.55, 1.65),
    "thin_universe_mult": (0.82, 0.88, 0.96),
}

REFINE_PHASE_C_GRID: dict[str, tuple[Any, ...]] = {
    "adaptive_long_bb_pos_max": (68.0, 72.0, 76.0, 80.0),
    "adaptive_score_open_min": (1.8, 2.2, 2.6, 3.0),
    "adaptive_min_hist_ratio": (0.17, 0.22, 0.26),
    "adaptive_score_open_min_extreme": (0.6, 0.85, 1.2),
    "adaptive_min_macd_gap_ratio": (0.03, 0.07, 0.11),
    "adaptive_momentum_penalty": (0.06, 0.12, 0.18),
    "sl_symbol_cooldown_ticks": (2, 4, 8, 12),
    "flip_cooldown_ticks": (4, 8, 16),
    "thesis_decay_exit_ticks": (16, 32, 48, 64),
}

REFINE_PHASE_D_GRID: dict[str, tuple[Any, ...]] = {
    "sl_min_pct": (1.6, 1.8, 2.0, 2.2),
    "tp_min_pct": (7.5, 8.5, 9.5),
    "ref_volatility_pct": (3.0, 3.5, 4.0),
    "max_conviction_mult": (1.7, 1.9, 2.15),
    "strength_mult_per_unit": (0.26, 0.32),
    "adaptive_long_bb_pos_max": (68.0, 72.0, 76.0),
    "adaptive_score_open_min": (2.2, 2.6),
    "sl_symbol_cooldown_ticks": (4, 8),
}

REFINE_PHASE_GRIDS: dict[str, dict[str, tuple[Any, ...]]] = {
    "A": REFINE_PHASE_A_GRID,
    "B": REFINE_PHASE_B_GRID,
    "C": REFINE_PHASE_C_GRID,
    "D": REFINE_PHASE_D_GRID,
}

REFINE_MIN_CONFIGS_BY_PHASE: dict[str, int] = {
    "A": 160,
    "B": 120,
    "C": 130,
    "D": 90,
}

_BARRIER_PARAM_KEYS: frozenset[str] = frozenset(
    {
        *MEGA_BARRIER_GRID.keys(),
        "sl_pct",
        "tp_pct",
    }
)

# Applied to every mega sample (not swept).
MEGA_GRID_FIXED_OVERRIDES: dict[str, Any] = {
    "activation_ticks": 0,
    "ignore_adaptive_4h_filter": True,
}

ENTRY_SLTP_SWEEP_FIXED_OVERRIDES: dict[str, Any] = {
    **MEGA_GRID_FIXED_OVERRIDES,
    "max_open_executors": 10,
    "tp_pct": 5.0,
}


@dataclass
class SweepResult:
    name: str
    pnl: float
    trades: int
    formal: int
    adaptive: int
    win_rate: float
    exits: dict[str, int] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    capital_normalized_pnl: float = 0.0
    pnl_per_exposure: float = 0.0
    total_exposure: float = 0.0
    avg_notional: float = 0.0
    avg_size_mult: float = 0.0
    avg_sl_pct: float = 0.0
    avg_tp_pct: float = 0.0
    sl_saturation_pct: float = 0.0
    tp_saturation_pct: float = 0.0
    dynamic_mode: str = ""
    snapshot_dir: str = ""


@dataclass
class SweepRunContext:
    dynamic_mode: str
    parsed_sessions: dict[int, dict[int, Any]]
    hl_caches_by_session: dict[int, dict[tuple[str, int], float]]
    hl_candle_cache: dict[str, list[dict[str, float]]]
    hl_barrier_candle_cache: dict[str, list[dict[str, float]]]
    hl_vol_candle_cache: dict[str, list[dict[str, float]]]
    reports_by_pair: dict[str, list[ReportMeta]]
    parent_overrides: dict[str, Any] | None
    benchmark_avg_notional: float


_SWEEP_CTX: SweepRunContext | None = None


def _available_ram_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except OSError:
        pass
    return float(os.cpu_count() or 1) * 2.0


def resolve_sweep_workers(
    requested: int,
    *,
    worker_ram_gb: float = 2.0,
    allow_non_fork: bool = False,
) -> int:
    """Resolve worker count capped by CPU and available RAM."""
    if requested <= 1:
        return 1
    start_method = mp.get_start_method(allow_none=True)
    if start_method not in (None, "fork") and not allow_non_fork:
        logger.warning(
            "Parallel sweep requires fork start method (got %s); using workers=1",
            start_method,
        )
        return 1
    cpu = os.cpu_count() or 1
    ram_cap = max(1, int(_available_ram_gb() // max(worker_ram_gb, 0.5)))
    resolved = max(1, min(requested, cpu, ram_cap))
    if resolved < requested:
        logger.info(
            "Capped sweep workers from %d to %d (cpu=%d ram_cap=%d)",
            requested,
            resolved,
            cpu,
            ram_cap,
        )
    return resolved


def _parallel_sweep_worker(item: tuple[str, dict[str, Any]]) -> SweepResult:
    ctx = _SWEEP_CTX
    if ctx is None:
        raise RuntimeError("Sweep worker context not initialized")
    name, overrides = item
    result = _run_dynamic_config(
        name,
        overrides,
        ctx.dynamic_mode,
        ctx.parsed_sessions,
        ctx.hl_caches_by_session,
        ctx.hl_candle_cache,
        ctx.hl_barrier_candle_cache,
        ctx.hl_vol_candle_cache,
        ctx.reports_by_pair,
        parent_overrides=ctx.parent_overrides,
    )
    return _apply_capital_metrics(result, ctx.benchmark_avg_notional)


def run_sweep_config_batch(
    config_items: list[tuple[str, dict[str, Any]]],
    ctx: SweepRunContext,
    *,
    workers: int = 1,
    worker_ram_gb: float = 2.0,
    allow_non_fork: bool = False,
    on_result: Callable[[int, SweepResult], None] | None = None,
) -> list[SweepResult]:
    """Run sweep configs sequentially or in a fork-based process pool."""
    resolved_workers = resolve_sweep_workers(
        workers,
        worker_ram_gb=worker_ram_gb,
        allow_non_fork=allow_non_fork,
    )
    if resolved_workers <= 1:
        results: list[SweepResult] = []
        for index, (name, overrides) in enumerate(config_items):
            result = _run_dynamic_config(
                name,
                overrides,
                ctx.dynamic_mode,
                ctx.parsed_sessions,
                ctx.hl_caches_by_session,
                ctx.hl_candle_cache,
                ctx.hl_barrier_candle_cache,
                ctx.hl_vol_candle_cache,
                ctx.reports_by_pair,
                parent_overrides=ctx.parent_overrides,
            )
            result = _apply_capital_metrics(result, ctx.benchmark_avg_notional)
            results.append(result)
            if on_result is not None:
                on_result(index + 1, result)
        return results

    global _SWEEP_CTX
    _SWEEP_CTX = ctx
    results = []
    completed = 0
    from routines.macdbb_scanner_aggressive_hl_replay import monitor_macdbb

    prior_persist = monitor_macdbb.persist_supplement_enabled()
    prior_inline = monitor_macdbb.inline_compute_enabled()
    monitor_macdbb.set_persist_supplement(False)
    monitor_macdbb.set_monitor_gap_recorder(None, inline_compute=False)
    try:
        pool_ctx = mp.get_context("fork")
        with pool_ctx.Pool(processes=resolved_workers) as pool:
            for result in pool.imap_unordered(
                _parallel_sweep_worker,
                config_items,
                chunksize=1,
            ):
                completed += 1
                results.append(result)
                if on_result is not None:
                    on_result(completed, result)
    finally:
        monitor_macdbb.set_persist_supplement(prior_persist)
        monitor_macdbb.set_monitor_gap_recorder(None, inline_compute=prior_inline)
        _SWEEP_CTX = None
    return results


def _merge(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    merged = dict(base)
    merged.update(overrides)
    return merged


_SENSIBLE_MIN_MAX_PAIRS: tuple[tuple[str, str], ...] = (
    ("sl_min_pct", "sl_max_pct"),
    ("tp_min_pct", "tp_max_pct"),
    ("min_notional_quote", "max_notional_quote"),
    ("min_conviction_mult", "max_conviction_mult"),
    ("min_vol_mult", "max_vol_mult"),
)


def _replay_config_from_overrides(overrides: dict[str, Any]) -> DynamicStrategyReplayConfig:
    allowed = set(DynamicStrategyReplayConfig.model_fields)
    payload = {key: value for key, value in overrides.items() if key in allowed}
    payload.setdefault("preset", "custom")
    payload.setdefault("formal_notional_quote", 500.0)
    return DynamicStrategyReplayConfig(**payload)


def _barriers_saturated_at_median_vol(overrides: dict[str, Any]) -> bool:
    """True when SL and TP both hit max clamp at typical alt BB-width vol."""
    if not overrides.get("enable_dynamic_barriers"):
        return False
    config = _replay_config_from_overrides(overrides)
    sl_pct, tp_pct = compute_dynamic_barriers(BARRIER_MEDIAN_VOL_PCT, config)
    return (
        sl_pct >= config.sl_max_pct - 1e-9
        and tp_pct >= config.tp_max_pct - 1e-9
    )


def is_sensible_replay_config(
    overrides: dict[str, Any],
    *,
    reject_saturated_barriers: bool = True,
) -> bool:
    """Reject invalid min/max pairs, adaptive ordering inversions, and clamped barriers."""
    for lo_key, hi_key in _SENSIBLE_MIN_MAX_PAIRS:
        lo = overrides.get(lo_key)
        hi = overrides.get(hi_key)
        if lo is None or hi is None:
            continue
        if float(lo) > float(hi):
            return False

    strong_long = overrides.get("adaptive_strong_long_bb_pos_max")
    long_max = overrides.get("adaptive_long_bb_pos_max")
    if strong_long is not None and long_max is not None:
        if float(strong_long) >= float(long_max):
            return False

    if reject_saturated_barriers and _barriers_saturated_at_median_vol(overrides):
        return False

    return True


def _dynamic_sweep_base(
    mode: str,
    *,
    parent_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in DYNAMIC_MODE_PRESETS:
        valid = ", ".join(sorted(DYNAMIC_MODE_PRESETS))
        raise ValueError(f"Unknown dynamic mode {mode!r}; choose one of: {valid}")
    root = dict(parent_overrides) if parent_overrides is not None else dict(CURRENT_WINNER_OVERRIDES)
    root["preset"] = "custom"
    return _merge(root, **DYNAMIC_MODE_PRESETS[mode])


def extract_barrier_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Pull SL/TP base + dynamic barrier keys from a full sweep config."""
    return {
        key: value
        for key, value in overrides.items()
        if key in _BARRIER_PARAM_KEYS
    }


def reconstruct_sweep_overrides(
    mode: str,
    diff: dict[str, Any],
    *,
    parent_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild full config from sweep CSV diff + optional staged parent."""
    base = _dynamic_sweep_base(mode, parent_overrides=parent_overrides)
    return _finalize_mega_dynamic_config(_merge(base, **diff))


def load_sweep_winner_from_csv(
    csv_path: Path,
    *,
    mode: str | None = None,
    parent_overrides: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Load rank-1 row; return (name, diff, full merged overrides)."""
    with csv_path.open(encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    winner_mode = mode or row.get("dynamic_mode") or "both_on"
    diff = json.loads(row["overrides_json"])
    full = reconstruct_sweep_overrides(
        winner_mode,
        diff,
        parent_overrides=parent_overrides,
    )
    return row["name"], diff, full


def _sweep_result_from_csv_row(row: dict[str, str]) -> SweepResult:
    exits = {
        "take_profit_close_proxy": int(float(row["exit_tp"])),
        "stop_loss_close_proxy": int(float(row["exit_sl"])),
        "thesis_decay_exit": int(float(row["exit_thesis_decay"])),
        "session_end_proxy": int(float(row["exit_session_end"])),
        "flip_confirmed": int(float(row["exit_flip"])),
    }
    return SweepResult(
        name=row["name"],
        pnl=float(row["pnl"]),
        trades=int(row["trades"]),
        formal=int(row["formal"]),
        adaptive=int(row["adaptive"]),
        win_rate=float(row["win_rate_pct"]) / 100.0,
        exits=exits,
        overrides=json.loads(row["overrides_json"]),
        capital_normalized_pnl=float(row["capital_normalized_pnl"]),
        pnl_per_exposure=float(row["pnl_per_exposure"]),
        total_exposure=float(row["total_exposure"]),
        avg_notional=float(row["avg_notional"]),
        avg_size_mult=float(row["avg_size_mult"]),
        avg_sl_pct=float(row["avg_sl_pct"]),
        avg_tp_pct=float(row["avg_tp_pct"]),
        sl_saturation_pct=float(row["sl_saturation_pct"]),
        tp_saturation_pct=float(row["tp_saturation_pct"]),
        dynamic_mode=row.get("dynamic_mode", ""),
        snapshot_dir=row.get("snapshot_dir", ""),
    )


def load_sweep_results_from_csv(csv_path: Path) -> list[SweepResult]:
    """Load all sweep rows from a checkpoint or final results CSV."""
    with csv_path.open(encoding="utf-8") as handle:
        return [_sweep_result_from_csv_row(row) for row in csv.DictReader(handle)]


def _dynamic_grid_for_mode(mode: str) -> dict[str, tuple[Any, ...]]:
    preset = DYNAMIC_MODE_PRESETS[mode]
    grid = dict(MEGA_SWEEP_GRID)
    if preset.get("enable_dynamic_sizing"):
        grid.update(MEGA_SIZING_GRID)
    if preset.get("enable_dynamic_barriers"):
        grid.update(MEGA_BARRIER_GRID)
    return grid


def _mega_dynamic_space_size(mode: str) -> int:
    return math.prod(len(values) for values in _dynamic_grid_for_mode(mode).values())


def _mega_config_name(overrides: dict[str, Any]) -> str:
    parts = [
        f"exec{overrides.get('max_open_executors', 3)}",
        f"L{int(overrides['adaptive_long_bb_pos_max'])}",
        f"S{int(overrides['adaptive_short_bb_pos_min'])}",
        f"sl{overrides['sl_pct']}",
        f"tp{int(overrides['tp_pct'])}",
        f"td{overrides['thesis_decay_exit_ticks']}",
        f"dr{int(overrides['thesis_bb_drift_pts'])}",
        f"eps{overrides['bb_proximity_epsilon_pct']}",
    ]
    return "mega_" + "_".join(parts)


def _mega_dynamic_config_name(overrides: dict[str, Any], mode: str) -> str:
    strategy_name = _mega_config_name(overrides)
    dynamic_bits: list[str] = []
    if overrides.get("enable_dynamic_sizing"):
        dynamic_bits.append(
            f"cm{overrides.get('min_conviction_mult', 0)}-{overrides.get('max_conviction_mult', 0)}"
        )
        dynamic_bits.append(f"str{overrides.get('strength_mult_per_unit', 0)}")
        dynamic_bits.append(f"rv{overrides.get('ref_volatility_pct', 0)}")
    if overrides.get("enable_dynamic_barriers"):
        dynamic_bits.append(
            f"sle{overrides.get('sl_vol_exponent', 0)}_tle{overrides.get('tp_vol_exponent', 0)}"
        )
        dynamic_bits.append(f"slmax{overrides.get('sl_max_pct', 0)}")
        dynamic_bits.append(f"tpmax{overrides.get('tp_max_pct', 0)}")
        dynamic_bits.append(f"vs{overrides.get('volatility_source', 'bb')}")
        dynamic_bits.append(
            f"j{int(not overrides.get('ignore_journal_barriers_when_dynamic', True))}"
        )
    suffix = "_".join(dynamic_bits) if dynamic_bits else mode
    return f"dyn_{mode}_{strategy_name}_{suffix}"


def _finalize_mega_dynamic_config(overrides: dict[str, Any]) -> dict[str, Any]:
    """Re-apply fixed keys last so preset anchors cannot override them."""
    return _merge(overrides, **MEGA_GRID_FIXED_OVERRIDES)


def _sample_ref_volatility_pct(
    rng: random.Random,
    volatility_source: str | None,
) -> float | None:
    if volatility_source == "natr":
        return rng.choice(NATR_REF_VOLATILITY_PCT)
    if volatility_source in ("bb_width", "auto"):
        return rng.choice(BB_REF_VOLATILITY_PCT)
    return None


def _random_dynamic_mega_combo(rng: random.Random, mode: str) -> dict[str, Any]:
    grid = _dynamic_grid_for_mode(mode)
    combo = {key: rng.choice(values) for key, values in grid.items()}
    if "ref_volatility_pct" in combo:
        source = combo.get("volatility_source")
        resampled = _sample_ref_volatility_pct(rng, source)
        if resampled is not None:
            combo["ref_volatility_pct"] = resampled
    return combo


def default_min_configs_for_mode(mode: str) -> int:
    return MEGA_SWEEP_MIN_CONFIGS_BY_MODE.get(mode, 560)


def default_min_configs_for_refine_phase(phase: str) -> int:
    if phase not in REFINE_MIN_CONFIGS_BY_PHASE:
        valid = ", ".join(REFINE_STAGED_PHASES)
        raise ValueError(f"Unknown refine phase {phase!r}; choose one of: {valid}")
    return REFINE_MIN_CONFIGS_BY_PHASE[phase]


def _refine_grid_for_phase(phase: str) -> dict[str, tuple[Any, ...]]:
    if phase not in REFINE_PHASE_GRIDS:
        valid = ", ".join(REFINE_STAGED_PHASES)
        raise ValueError(f"Unknown refine phase {phase!r}; choose one of: {valid}")
    return REFINE_PHASE_GRIDS[phase]


def _random_refine_combo(rng: random.Random, phase: str) -> dict[str, Any]:
    grid = _refine_grid_for_phase(phase)
    combo = {key: rng.choice(values) for key, values in grid.items()}
    if "ref_volatility_pct" in combo:
        source = combo.get("volatility_source", "auto")
        resampled = _sample_ref_volatility_pct(rng, source)
        if resampled is not None and "volatility_source" in grid:
            combo["ref_volatility_pct"] = resampled
    return combo


def _refine_config_name(overrides: dict[str, Any], phase: str) -> str:
    bits: list[str] = [f"refine_{phase}"]
    if "sl_min_pct" in overrides:
        bits.append(f"slmin{overrides['sl_min_pct']}")
    if "tp_min_pct" in overrides:
        bits.append(f"tpmin{overrides['tp_min_pct']}")
    if "ref_volatility_pct" in overrides:
        bits.append(f"rv{overrides['ref_volatility_pct']}")
    if "max_conviction_mult" in overrides:
        bits.append(f"cm{overrides['max_conviction_mult']}")
    if "strength_mult_per_unit" in overrides:
        bits.append(f"str{overrides['strength_mult_per_unit']}")
    if "adaptive_long_bb_pos_max" in overrides:
        bits.append(f"L{int(overrides['adaptive_long_bb_pos_max'])}")
    if "adaptive_score_open_min" in overrides:
        bits.append(f"sc{overrides['adaptive_score_open_min']}")
    if "sl_symbol_cooldown_ticks" in overrides:
        bits.append(f"slcd{overrides['sl_symbol_cooldown_ticks']}")
    if "thesis_decay_exit_ticks" in overrides:
        bits.append(f"td{overrides['thesis_decay_exit_ticks']}")
    return "_".join(bits)


def iter_refine_sweep_configs(
    phase: str,
    *,
    min_configs: int | None = None,
    seed: int = 42,
    parent_overrides: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield refine configs around a parent winner (staged v5 Phase C by default).

    Each phase sweeps a focused parameter subset while keeping ``both_on`` dynamic
    mode. Random samples are merged onto ``parent_overrides`` (prior phase winner
    or the v5 staged ABC winner for phase A).
    """
    if phase not in REFINE_PHASE_GRIDS:
        valid = ", ".join(REFINE_STAGED_PHASES)
        raise ValueError(f"Unknown refine phase {phase!r}; choose one of: {valid}")

    mode = REFINE_DYNAMIC_MODE
    base = _finalize_mega_dynamic_config(
        _dynamic_sweep_base(mode, parent_overrides=parent_overrides)
    )
    anchor_name = f"refine_{phase}_baseline_winner"
    if not is_sensible_replay_config(base, reject_saturated_barriers=False):
        raise ValueError(f"refine sweep anchor failed sanity check: {anchor_name}")
    yield anchor_name, dict(base)

    target = max(min_configs or default_min_configs_for_refine_phase(phase), 1)
    rng = random.Random(seed)
    seen_names: set[str] = {anchor_name}
    emitted = 0
    attempts = 0
    max_attempts = target * 50

    while emitted < target and attempts < max_attempts:
        attempts += 1
        combo = _random_refine_combo(rng, phase)
        merged = _finalize_mega_dynamic_config(_merge(base, **combo))
        if not is_sensible_replay_config(merged):
            continue
        name = _refine_config_name(merged, phase)
        if name in seen_names:
            name = f"{name}_n{emitted}"
        if name in seen_names:
            continue
        seen_names.add(name)
        yield name, merged
        emitted += 1


def _finalize_entry_sltp_config(overrides: dict[str, Any]) -> dict[str, Any]:
    """Re-apply entry/SLTP fixed keys last so random samples cannot override them."""
    return _merge(overrides, **ENTRY_SLTP_SWEEP_FIXED_OVERRIDES)


def _entry_sltp_space_size() -> int:
    return math.prod(len(values) for values in ENTRY_SLTP_SWEEP_GRID.values())


def _random_entry_sltp_combo(rng: random.Random) -> dict[str, Any]:
    return {key: rng.choice(values) for key, values in ENTRY_SLTP_SWEEP_GRID.items()}


def _entry_sltp_config_name(overrides: dict[str, Any], mode: str) -> str:
    parts = [
        f"sl{overrides['sl_pct']}",
        f"slmin{overrides['sl_min_pct']}",
        f"tpmin{overrides['tp_min_pct']}",
        f"L{int(overrides['adaptive_long_bb_pos_max'])}",
        f"S{int(overrides['adaptive_short_bb_pos_min'])}",
        f"sc{overrides['adaptive_score_open_min']}",
        f"td{overrides['thesis_decay_exit_ticks']}",
        f"eps{overrides['bb_proximity_epsilon_pct']}",
    ]
    return f"dyn_{mode}_entry_sltp_" + "_".join(parts)


def iter_entry_sltp_sweep_configs(
    mode: str = "both_on",
    *,
    min_configs: int | None = None,
    seed: int = 42,
    parent_overrides: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield entry/SLTP sweep configs (v6 grid) merged onto the v5 refine winner."""
    if mode != "both_on":
        raise ValueError(
            f"entry_sltp sweep requires mode 'both_on'; got {mode!r}"
        )
    if parent_overrides is not None:
        raise ValueError("entry_sltp sweep does not support parent_overrides")

    if min_configs is None:
        min_configs = ENTRY_SLTP_SWEEP_MIN_CONFIGS

    base = _finalize_entry_sltp_config(_dynamic_sweep_base(mode))
    anchors: list[tuple[str, dict[str, Any]]] = [
        (f"dyn_{mode}_entry_sltp_baseline_winner", dict(base)),
        (
            f"dyn_{mode}_entry_sltp_anchor_{CURRENT_WINNER_PRESET}",
            _finalize_entry_sltp_config(
                _merge(_dynamic_sweep_base(mode), **CURRENT_WINNER_OVERRIDES)
            ),
        ),
    ]
    for name, overrides in anchors:
        if not is_sensible_replay_config(overrides, reject_saturated_barriers=False):
            raise ValueError(f"entry_sltp sweep anchor failed sanity check: {name}")
        yield name, overrides

    rng = random.Random(seed)
    seen_names: set[str] = {name for name, _ in anchors}
    target = max(min_configs, 1)
    emitted = 0
    attempts = 0
    max_attempts = target * 50

    while emitted < target and attempts < max_attempts:
        attempts += 1
        combo = _random_entry_sltp_combo(rng)
        merged = _finalize_entry_sltp_config(_merge(base, **combo))
        if not is_sensible_replay_config(merged):
            continue
        name = _entry_sltp_config_name(merged, mode)
        if name in seen_names:
            name = f"{name}_n{emitted}"
        if name in seen_names:
            continue
        seen_names.add(name)
        yield name, merged
        emitted += 1


def resolve_sweep_config_iterator(
    sweep_grid: str,
    mode: str,
    *,
    min_configs: int | None = None,
    seed: int = 42,
    parent_overrides: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    if sweep_grid == "entry_sltp_v6":
        return iter_entry_sltp_sweep_configs(
            mode,
            min_configs=min_configs,
            seed=seed,
            parent_overrides=parent_overrides,
        )
    if sweep_grid == "mega_v5":
        return iter_mega_dynamic_sweep_configs(
            mode,
            min_configs=min_configs,
            seed=seed,
            parent_overrides=parent_overrides,
        )
    valid = ", ".join(SWEEP_GRID_CHOICES)
    raise ValueError(f"Unknown sweep_grid {sweep_grid!r}; choose one of: {valid}")


def sweep_space_size(sweep_grid: str, mode: str) -> int:
    if sweep_grid == "entry_sltp_v6":
        return _entry_sltp_space_size()
    if sweep_grid == "mega_v5":
        return _mega_dynamic_space_size(mode)
    valid = ", ".join(SWEEP_GRID_CHOICES)
    raise ValueError(f"Unknown sweep_grid {sweep_grid!r}; choose one of: {valid}")


def default_min_configs_for_sweep_grid(sweep_grid: str, mode: str) -> int:
    if sweep_grid == "entry_sltp_v6":
        return ENTRY_SLTP_SWEEP_MIN_CONFIGS
    if sweep_grid == "mega_v5":
        return default_min_configs_for_mode(mode)
    valid = ", ".join(SWEEP_GRID_CHOICES)
    raise ValueError(f"Unknown sweep_grid {sweep_grid!r}; choose one of: {valid}")


def finalize_sweep_config(
    overrides: dict[str, Any],
    *,
    sweep_grid: str = "mega_v5",
) -> dict[str, Any]:
    if sweep_grid == "entry_sltp_v6":
        return _finalize_entry_sltp_config(overrides)
    if sweep_grid == "mega_v5":
        return _finalize_mega_dynamic_config(overrides)
    valid = ", ".join(SWEEP_GRID_CHOICES)
    raise ValueError(f"Unknown sweep_grid {sweep_grid!r}; choose one of: {valid}")


def iter_mega_dynamic_sweep_configs(
    mode: str = "both_on",
    *,
    min_configs: int | None = None,
    seed: int = 42,
    parent_overrides: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield dynamic mega configs; strategy grid + mode-specific dynamic params.

    When ``parent_overrides`` is set (staged phases B/C), the parent full config
    replaces CURRENT_WINNER_OVERRIDES as the merge root before mode flags apply.
    """
    if min_configs is None:
        min_configs = default_min_configs_for_mode(mode)
    base = _finalize_mega_dynamic_config(
        _dynamic_sweep_base(mode, parent_overrides=parent_overrides)
    )
    staged = parent_overrides is not None
    anchors: list[tuple[str, dict[str, Any]]] = [
        (f"dyn_{mode}_baseline_winner", dict(base)),
    ]
    if not staged:
        live_anchor = _finalize_mega_dynamic_config(
            _merge(_dynamic_sweep_base(mode), **LIVE_AGENT_DEFAULT_OVERRIDES)
        )
        anchors.extend(
            [
                (
                    f"dyn_{mode}_anchor_{CURRENT_WINNER_PRESET}",
                    _finalize_mega_dynamic_config(
                        _merge(_dynamic_sweep_base(mode), **CURRENT_WINNER_OVERRIDES)
                    ),
                ),
                (f"dyn_{mode}_anchor_live_agent_default", live_anchor),
            ]
        )
    for name, overrides in anchors:
        if not is_sensible_replay_config(overrides, reject_saturated_barriers=False):
            raise ValueError(f"mega sweep anchor failed sanity check: {name}")
        yield name, overrides

    rng = random.Random(seed)
    seen_names: set[str] = {name for name, _ in anchors}
    target = max(min_configs, 1)
    emitted = 0
    attempts = 0
    max_attempts = target * 50

    while emitted < target and attempts < max_attempts:
        attempts += 1
        combo = _random_dynamic_mega_combo(rng, mode)
        merged = _finalize_mega_dynamic_config(_merge(base, **combo))
        if not is_sensible_replay_config(merged):
            continue
        name = _mega_dynamic_config_name(merged, mode)
        if name in seen_names:
            name = f"{name}_n{emitted}"
        if name in seen_names:
            continue
        seen_names.add(name)
        yield name, merged
        emitted += 1


def _apply_capital_metrics(
    result: SweepResult,
    benchmark_avg_notional: float,
) -> SweepResult:
    result.capital_normalized_pnl = capital_normalized_pnl(
        result.pnl,
        result.avg_notional,
        benchmark_avg_notional,
    )
    result.pnl_per_exposure = (
        result.pnl / result.total_exposure if result.total_exposure > 0 else 0.0
    )
    return result


async def _load_sessions(
    config: StrategyReplayConfig,
) -> tuple[
    dict[int, dict[int, Any]],
    dict[int, dict[tuple[str, int], float]],
    dict[str, list[dict[str, float]]],
    dict[str, list[dict[str, float]]],
    dict[str, list[dict[str, float]]],
    list[int],
]:
    strategy_dir = TRADING_AGENTS_DIR / config.strategy_slug
    sessions_dir = strategy_dir / "sessions"
    selected_sessions = parse_session_selector(config.session_nums, sessions_dir)

    if is_report_driven_data_source(config.data_source):
        parsed_sessions, _session_configs, selected_sessions = load_replay_sessions(
            config  # type: ignore[arg-type]
        )
    else:
        parsed_sessions: dict[int, dict[int, Any]] = {}
        for session_num in selected_sessions:
            journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
            if not journal_path.is_file():
                continue
            tick_meta_map = parse_journal_ticks(
                journal_path.read_text(encoding="utf-8"),
                session_dir=sessions_dir / f"session_{session_num}",
            )
            if tick_meta_map:
                parsed_sessions[session_num] = tick_meta_map

    hl_caches_by_session: dict[int, dict[tuple[str, int], float]] = {}
    hl_candle_cache: dict[str, list[dict[str, float]]] = {}
    hl_barrier_candle_cache: dict[str, list[dict[str, float]]] = {}
    hl_vol_candle_cache: dict[str, list[dict[str, float]]] = {}
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import should_prefetch_replay_candles

    if parsed_sessions and should_prefetch_replay_candles(config):
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    return (
        parsed_sessions,
        hl_caches_by_session,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        selected_sessions,
    )


def _run_dynamic_config(
    name: str,
    overrides: dict[str, Any],
    dynamic_mode: str,
    parsed_sessions: dict[int, dict[int, Any]],
    hl_caches_by_session: dict[int, dict[tuple[str, int], float]],
    hl_candle_cache: dict[str, list[dict[str, float]]],
    hl_barrier_candle_cache: dict[str, list[dict[str, float]]],
    hl_vol_candle_cache: dict[str, list[dict[str, float]]],
    reports_by_pair: dict[str, list[ReportMeta]],
    *,
    parent_overrides: dict[str, Any] | None = None,
) -> SweepResult:
    config = resolve_config_with_preset(DynamicStrategyReplayConfig(**overrides))
    policy = DynamicReplayPolicy(config)
    total_trades = 0
    wins = 0
    pnl = 0.0
    formal = 0
    adaptive = 0
    exit_counts: Counter[str] = Counter()
    notional_sum = 0.0
    size_mult_sum = 0.0
    sl_sum = 0.0
    tp_sum = 0.0
    sl_at_max = 0
    tp_at_max = 0

    for session_num, tick_meta_map in parsed_sessions.items():
        hl_price_cache = hl_caches_by_session.get(session_num)
        _, _, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=config,
            hl_price_cache=hl_price_cache,
            hl_candle_cache=hl_candle_cache,
            hl_barrier_candle_cache=hl_barrier_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            replay_policy=policy,
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        for trade in trades:
            total_trades += 1
            pnl += trade.pnl_quote
            notional_sum += trade.notional_quote
            size_mult_sum += trade.sizing_multiplier
            sl_sum += trade.sl_pct_used
            tp_sum += trade.tp_pct_used
            if config.sl_max_pct and trade.sl_pct_used >= config.sl_max_pct - 1e-6:
                sl_at_max += 1
            if config.tp_max_pct and trade.tp_pct_used >= config.tp_max_pct - 1e-6:
                tp_at_max += 1
            if trade.pnl_quote > 0:
                wins += 1
            if trade.entry_class == "formal":
                formal += 1
            elif trade.entry_class == "regime_adaptive_half_size":
                adaptive += 1
            exit_counts[trade.exit_reason] += 1

    win_rate = (wins / total_trades) if total_trades else 0.0
    base = _dynamic_sweep_base(dynamic_mode, parent_overrides=parent_overrides)
    diff_keys = {
        key: value
        for key, value in overrides.items()
        if key not in base or base[key] != value
    }
    avg_notional = (notional_sum / total_trades) if total_trades else 0.0

    return SweepResult(
        name=name,
        pnl=pnl,
        trades=total_trades,
        formal=formal,
        adaptive=adaptive,
        win_rate=win_rate,
        exits=dict(exit_counts),
        overrides=diff_keys,
        total_exposure=notional_sum,
        avg_notional=avg_notional,
        avg_size_mult=(size_mult_sum / total_trades) if total_trades else 0.0,
        avg_sl_pct=(sl_sum / total_trades) if total_trades else 0.0,
        avg_tp_pct=(tp_sum / total_trades) if total_trades else 0.0,
        sl_saturation_pct=(sl_at_max / total_trades * 100.0) if total_trades else 0.0,
        tp_saturation_pct=(tp_at_max / total_trades * 100.0) if total_trades else 0.0,
        dynamic_mode=dynamic_mode,
    )


def _exit_bucket(exits: dict[str, int], *reasons: str) -> int:
    return sum(exits.get(reason, 0) for reason in reasons)


SWEEP_CSV_FIELDS = [
    "rank",
    "name",
    "pnl",
    "capital_normalized_pnl",
    "pnl_per_exposure",
    "trades",
    "formal",
    "adaptive",
    "win_rate_pct",
    "exit_tp",
    "exit_sl",
    "exit_thesis_decay",
    "exit_session_end",
    "exit_flip",
    "exit_other",
    "total_exposure",
    "avg_notional",
    "avg_size_mult",
    "avg_sl_pct",
    "avg_tp_pct",
    "sl_saturation_pct",
    "tp_saturation_pct",
    "dynamic_mode",
    "snapshot_dir",
    "overrides_json",
]


def _result_to_csv_row(rank: int, row: SweepResult) -> dict[str, Any]:
    return {
        "rank": rank,
        "name": row.name,
        "pnl": round(row.pnl, 2),
        "capital_normalized_pnl": round(row.capital_normalized_pnl, 2),
        "pnl_per_exposure": round(row.pnl_per_exposure, 6),
        "trades": row.trades,
        "formal": row.formal,
        "adaptive": row.adaptive,
        "win_rate_pct": round(row.win_rate * 100, 1),
        "exit_tp": _exit_bucket(row.exits, "take_profit_close_proxy"),
        "exit_sl": _exit_bucket(row.exits, "stop_loss_close_proxy"),
        "exit_thesis_decay": _exit_bucket(row.exits, "thesis_decay_exit"),
        "exit_session_end": _exit_bucket(row.exits, "session_end_proxy"),
        "exit_flip": _exit_bucket(row.exits, "flip_confirmed"),
        "exit_other": sum(
            count
            for reason, count in row.exits.items()
            if reason
            not in (
                "take_profit_close_proxy",
                "stop_loss_close_proxy",
                "thesis_decay_exit",
                "session_end_proxy",
                "flip_confirmed",
            )
        ),
        "total_exposure": round(row.total_exposure, 2),
        "avg_notional": round(row.avg_notional, 2),
        "avg_size_mult": round(row.avg_size_mult, 4),
        "avg_sl_pct": round(row.avg_sl_pct, 3),
        "avg_tp_pct": round(row.avg_tp_pct, 3),
        "sl_saturation_pct": round(row.sl_saturation_pct, 1),
        "tp_saturation_pct": round(row.tp_saturation_pct, 1),
        "dynamic_mode": row.dynamic_mode,
        "snapshot_dir": row.snapshot_dir,
        "overrides_json": json.dumps(row.overrides, sort_keys=True),
    }


def _write_sweep_csv(path: Path, results: list[SweepResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=SWEEP_CSV_FIELDS)
        writer.writeheader()
        for rank, row in enumerate(results, start=1):
            writer.writerow(_result_to_csv_row(rank, row))


def _write_sweep_json(path: Path, results: list[SweepResult]) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "name": row.name,
                    "pnl": round(row.pnl, 2),
                    "capital_normalized_pnl": round(row.capital_normalized_pnl, 2),
                    "pnl_per_exposure": round(row.pnl_per_exposure, 6),
                    "trades": row.trades,
                    "formal": row.formal,
                    "adaptive": row.adaptive,
                    "win_rate_pct": round(row.win_rate * 100, 1),
                    "exits": row.exits,
                    "overrides": row.overrides,
                }
                for row in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


async def run_dynamic_sweep(
    dynamic_mode: str = "both_on",
    output_dir: Path | None = None,
    *,
    min_configs: int = 560,
    seed: int = 42,
    output_stem: str | None = None,
    baseline_name: str | None = None,
    gc_every: int = 25,
    write_json: bool = False,
    rank_by_normalized: bool = True,
    config_builder: Callable[[], list[tuple[str, dict[str, Any]]]] | None = None,
    workers: int = 1,
    worker_ram_gb: float = 2.0,
    allow_non_fork: bool = False,
) -> tuple[list[SweepResult], str, float]:
    load_config = DynamicStrategyReplayConfig(**_dynamic_sweep_base(dynamic_mode))
    configure_replay_data_sources(load_config)
    (
        parsed_sessions,
        hl_caches,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        _selected,
    ) = await _load_sessions(load_config)
    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    stem = output_stem or f"macdbb_scanner_aggressive_hl_backtest_{dynamic_mode}_mega_all_sessions"
    baseline = baseline_name or f"dyn_{dynamic_mode}_baseline_winner"
    benchmark_avg_notional = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL

    if config_builder is None:
        config_iter: Iterator[tuple[str, dict[str, Any]]] = (
            iter_mega_dynamic_sweep_configs(
                dynamic_mode,
                min_configs=min_configs,
                seed=seed,
            )
        )
    else:
        config_iter = iter(config_builder())

    config_items = list(config_iter)
    sweep_ctx = SweepRunContext(
        dynamic_mode=dynamic_mode,
        parsed_sessions=parsed_sessions,
        hl_caches_by_session=hl_caches,
        hl_candle_cache=hl_candle_cache,
        hl_barrier_candle_cache=hl_barrier_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
        reports_by_pair=reports_by_pair,
        parent_overrides=None,
        benchmark_avg_notional=benchmark_avg_notional,
    )

    def _on_result(done: int, _result: SweepResult) -> None:
        if gc_every and done % gc_every == 0:
            gc.collect()

    results = run_sweep_config_batch(
        config_items,
        sweep_ctx,
        workers=workers,
        worker_ram_gb=worker_ram_gb,
        allow_non_fork=allow_non_fork,
        on_result=_on_result if gc_every else None,
    )

    sort_key = (
        (lambda row: row.capital_normalized_pnl)
        if rank_by_normalized
        else (lambda row: row.pnl)
    )
    results.sort(key=sort_key, reverse=True)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_sweep_csv(output_dir / f"{stem}.csv", results)
        if write_json:
            _write_sweep_json(output_dir / f"{stem}.json", results)

    return results, baseline, benchmark_avg_notional


def _print_table(
    results: list[SweepResult],
    baseline_pnl: float,
    *,
    top_n: int = 40,
    dynamic: bool = False,
    benchmark_avg_notional: float | None = None,
    baseline_capital_normalized_pnl: float | None = None,
    rank_by_normalized: bool = False,
) -> None:
    print(f"Sweep: all sessions | configs={len(results)}")
    if dynamic and results and results[0].dynamic_mode:
        print(f"Dynamic mode: {results[0].dynamic_mode}")
    if rank_by_normalized and benchmark_avg_notional:
        print(
            "Ranking by capital-normalized PnL "
            f"(raw × fixed_avg_notional/avg_notional; benchmark avg=${benchmark_avg_notional:.0f})"
        )
    header = (
        f"{'Rank':<5} {'Name':<32} {'CapNorm':>9} {'RawPnL':>9} {'Δ base':>9} "
        f"{'Trades':>7} {'Win%':>6} {'TP':>4} {'SL':>4} {'Decay':>5} {'End':>4}"
    )
    if dynamic:
        header += f" {'Avg$':>7} {'$/exp':>7} {'Mult':>5}"
    print(header)
    print("-" * (len(header) + 5))
    display = results[:top_n]
    if len(results) > top_n:
        print(f"(showing top {top_n} of {len(results)})")
    baseline_cap_norm = (
        baseline_capital_normalized_pnl
        if baseline_capital_normalized_pnl is not None
        else baseline_pnl
    )
    for rank, row in enumerate(display, start=1):
        delta = (
            row.capital_normalized_pnl - baseline_cap_norm
            if rank_by_normalized
            else row.pnl - baseline_pnl
        )
        line = (
            f"{rank:<5} {row.name[:32]:<32} ${row.capital_normalized_pnl:+8.2f} "
            f"${row.pnl:+8.2f} ${delta:+8.2f} "
            f"{row.trades:>7} {row.win_rate * 100:5.1f}% "
            f"{_exit_bucket(row.exits, 'take_profit_close_proxy'):>4} "
            f"{_exit_bucket(row.exits, 'stop_loss_close_proxy'):>4} "
            f"{_exit_bucket(row.exits, 'thesis_decay_exit'):>5} "
            f"{_exit_bucket(row.exits, 'session_end_proxy'):>4}"
        )
        if dynamic:
            line += (
                f" {row.avg_notional:>7.0f} {row.pnl_per_exposure:>7.4f} "
                f"{row.avg_size_mult:>5.2f}"
            )
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MACD+BB dynamic strategy replay mega sweep (grid v5, all sessions)"
    )
    parser.add_argument(
        "--all-dynamic-modes",
        action="store_true",
        help="Run mega sweep for every dynamic mode sequentially",
    )
    parser.add_argument(
        "--dynamic-mode",
        choices=sorted(DYNAMIC_MODE_PRESETS),
        default="both_on",
        help="Dynamic replay mode (default: both_on)",
    )
    parser.add_argument(
        "--min-configs",
        type=int,
        default=560,
        help="Minimum random configs to sample (default 560)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for mega sampling",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="Number of top rows to print",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
        help="Directory for CSV/JSON sweep output",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel config workers (Linux fork; default 1)",
    )
    parser.add_argument(
        "--worker-ram-gb",
        type=float,
        default=2.0,
        help="Estimated RAM per worker for worker cap (default 2.0)",
    )
    args = parser.parse_args()

    if args.all_dynamic_modes:
        all_results: list[tuple[str, list[SweepResult], str, Path]] = []
        for mode in sorted(DYNAMIC_MODE_PRESETS):
            print(f"\n{'=' * 72}\nDynamic mega sweep — mode={mode}\n{'=' * 72}")
            stem = f"macdbb_scanner_aggressive_hl_backtest_{mode}_mega_all_sessions"
            results, baseline_name, benchmark_avg = asyncio.run(
                run_dynamic_sweep(
                    dynamic_mode=mode,
                    output_dir=args.output_dir,
                    min_configs=args.min_configs,
                    seed=args.seed,
                    output_stem=stem,
                    gc_every=25,
                    write_json=False,
                    rank_by_normalized=True,
                    workers=args.workers,
                    worker_ram_gb=args.worker_ram_gb,
                )
            )
            output_file = args.output_dir / f"{stem}.csv"
            baseline = next(
                (row for row in results if row.name == baseline_name),
                results[-1],
            )
            _print_table(
                results,
                baseline.pnl,
                top_n=args.top,
                dynamic=True,
                benchmark_avg_notional=benchmark_avg,
                baseline_capital_normalized_pnl=baseline.capital_normalized_pnl,
                rank_by_normalized=True,
            )
            print(f"\nWrote {output_file}")
            if results:
                winner = results[0]
                print(
                    f"Top: {winner.name}  CapNorm=${winner.capital_normalized_pnl:+.2f}  "
                    f"RawPnL=${winner.pnl:+.2f}  avg_notional=${winner.avg_notional:.0f}  "
                    f"pnl/exp={winner.pnl_per_exposure:.4f}  "
                    f"overrides={json.dumps(winner.overrides, sort_keys=True)}"
                )
            all_results.append((mode, results, baseline_name, output_file))

        print(
            f"\n{'=' * 72}\nCross-mode summary (best capital-normalized PnL per mode)\n{'=' * 72}"
        )
        for mode, results, _baseline_name, output_file in all_results:
            if not results:
                continue
            winner = results[0]
            print(
                f"  {mode:<20} cap=${winner.capital_normalized_pnl:+9.2f}  "
                f"raw=${winner.pnl:+9.2f}  {winner.trades:>3} trades  "
                f"avg$={winner.avg_notional:>6.0f}  top={winner.name[:40]}  -> {output_file.name}"
            )
        return

    stem = f"macdbb_scanner_aggressive_hl_backtest_{args.dynamic_mode}_mega_all_sessions"
    results, baseline_name, benchmark_avg = asyncio.run(
        run_dynamic_sweep(
            dynamic_mode=args.dynamic_mode,
            output_dir=args.output_dir,
            min_configs=args.min_configs,
            seed=args.seed,
            output_stem=stem,
            gc_every=25,
            write_json=False,
            rank_by_normalized=True,
            workers=args.workers,
            worker_ram_gb=args.worker_ram_gb,
        )
    )
    output_file = args.output_dir / f"{stem}.csv"
    print(
        f"Dynamic mega sweep mode={args.dynamic_mode} | "
        f"space~{_mega_dynamic_space_size(args.dynamic_mode):,} | "
        f"sampled: {len(results)} | fixed benchmark avg=${benchmark_avg:.0f}"
    )
    baseline = next(
        (row for row in results if row.name == baseline_name),
        results[-1],
    )
    _print_table(
        results,
        baseline.pnl,
        top_n=args.top,
        dynamic=True,
        benchmark_avg_notional=benchmark_avg,
        baseline_capital_normalized_pnl=baseline.capital_normalized_pnl,
        rank_by_normalized=True,
    )
    print(f"\nWrote {output_file}")
    if results:
        winner = results[0]
        print(
            f"\nTop config: {winner.name}  CapNorm=${winner.capital_normalized_pnl:+.2f}  "
            f"RawPnL=${winner.pnl:+.2f}  avg_notional=${winner.avg_notional:.0f}  "
            f"pnl/exp={winner.pnl_per_exposure:.4f}  "
            f"overrides={json.dumps(winner.overrides, sort_keys=True)}"
        )


if __name__ == "__main__":
    main()
