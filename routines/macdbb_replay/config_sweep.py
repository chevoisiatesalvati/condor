"""Batch dynamic strategy-replay PnL sweep over session journals (CLI helper)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gc
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from routines.macdbb_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_replay.journal import parse_journal_ticks
from routines.macdbb_replay.models import (
    DynamicStrategyReplayConfig,
    StrategyReplayConfig,
    parse_session_selector,
)
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay.presets import (
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    capital_normalized_pnl,
    resolve_config_with_preset,
)
from routines.macdbb_replay.replay_data import (
    configure_replay_data_sources,
    is_report_driven_data_source,
)
from routines.macdbb_replay.reports import (
    ReportMeta,
    build_reports_by_pair,
    load_reports_index,
)
from routines.macdbb_replay.replay_loader import load_replay_sessions
from routines.macdbb_replay.simulator import simulate_strategy_session

# Current live winner — hl_dynamic_mega_sweep_best (mega sweep v4 top1, sessions 37-60).
CURRENT_WINNER_OVERRIDES: dict[str, Any] = {
    **DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"],
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

# Mega sweep grid v4 — both_on exploration around the current winner neighborhood.
# max_open_executors is swept (was fixed at 3 in earlier sweeps).
MEGA_SWEEP_GRID: dict[str, tuple[Any, ...]] = {
    "max_open_executors": (3, 5, 8, 10),
    "adaptive_long_bb_pos_max": (48.0, 58.0, 64.0, 68.0, 82.0),
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
    "sl_pct": (1.6, 2.5, 3.2, 3.4, 3.8, 4.5, 5.0),
    "tp_pct": (5.0, 5.5, 6.2, 6.8, 7.8, 8.2, 8.8, 9.2, 10.5, 13.0, 17.0),
    "thesis_decay_exit_ticks": (6, 14, 28, 44, 64),
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
    "ref_volatility_pct": (0.28, 0.45, 0.48, 0.68, 0.75, 0.95),
    "min_vol_mult": (0.42, 0.58, 0.65, 0.82, 0.88),
    "max_vol_mult": (1.05, 1.22, 1.48, 1.55, 1.75),
    "vol_inverse_sizing": (True, False),
    "min_notional_quote": (40.0, 60.0, 125.0, 150.0, 200.0),
    "max_notional_quote": (550.0, 700.0, 850.0, 1100.0, 1400.0),
}

MEGA_BARRIER_GRID: dict[str, tuple[Any, ...]] = {
    "sl_vol_exponent": (0.40, 0.65, 0.95, 1.05, 1.15),
    "tp_vol_exponent": (0.60, 0.75, 1.35, 1.45),
    "sl_min_pct": (0.6, 1.4, 2.0, 2.2),
    "sl_max_pct": (3.2, 4.5, 5.5, 7.0, 7.5),
    "tp_min_pct": (3.5, 5.5, 6.5, 7.5, 7.8),
    "tp_max_pct": (7.5, 11.0, 20.0, 22.0),
    "ignore_journal_barriers_when_dynamic": (True, False),
    "volatility_source": ("auto", "bb_width", "natr"),
}

# Applied to every mega sample (not swept).
MEGA_GRID_FIXED_OVERRIDES: dict[str, Any] = {
    "activation_ticks": 0,
    "ignore_adaptive_4h_filter": True,
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
    dynamic_mode: str = ""


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


def is_sensible_replay_config(overrides: dict[str, Any]) -> bool:
    """Reject parameter combos where a min bound exceeds its max (e.g. tp_min > tp_max)."""
    for lo_key, hi_key in _SENSIBLE_MIN_MAX_PAIRS:
        lo = overrides.get(lo_key)
        hi = overrides.get(hi_key)
        if lo is None or hi is None:
            continue
        if float(lo) > float(hi):
            return False
    return True


def _dynamic_sweep_base(mode: str) -> dict[str, Any]:
    if mode not in DYNAMIC_MODE_PRESETS:
        valid = ", ".join(sorted(DYNAMIC_MODE_PRESETS))
        raise ValueError(f"Unknown dynamic mode {mode!r}; choose one of: {valid}")
    return _merge(dict(CURRENT_WINNER_OVERRIDES), **DYNAMIC_MODE_PRESETS[mode])


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
        dynamic_bits.append(f"tpmax{int(overrides.get('tp_max_pct', 0))}")
        dynamic_bits.append(
            f"j{int(not overrides.get('ignore_journal_barriers_when_dynamic', True))}"
        )
    suffix = "_".join(dynamic_bits) if dynamic_bits else mode
    return f"dyn_{mode}_{strategy_name}_{suffix}"


def _finalize_mega_dynamic_config(overrides: dict[str, Any]) -> dict[str, Any]:
    """Re-apply fixed keys last so preset anchors cannot override them."""
    return _merge(overrides, **MEGA_GRID_FIXED_OVERRIDES)


def _random_dynamic_mega_combo(rng: random.Random, mode: str) -> dict[str, Any]:
    grid = _dynamic_grid_for_mode(mode)
    return {key: rng.choice(values) for key, values in grid.items()}


def iter_mega_dynamic_sweep_configs(
    mode: str = "both_on",
    *,
    min_configs: int = 560,
    seed: int = 42,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield dynamic mega configs; strategy grid + mode-specific dynamic params."""
    base = _finalize_mega_dynamic_config(_dynamic_sweep_base(mode))
    anchors: list[tuple[str, dict[str, Any]]] = [
        (f"dyn_{mode}_baseline_winner", dict(base)),
        (
            f"dyn_{mode}_anchor_hl_dynamic_mega_sweep_best",
            _finalize_mega_dynamic_config(
                _merge(_dynamic_sweep_base(mode), **CURRENT_WINNER_OVERRIDES)
            ),
        ),
    ]
    for name, overrides in anchors:
        if not is_sensible_replay_config(overrides):
            raise ValueError(f"mega sweep anchor failed sanity check: {name}")
        yield name, overrides

    rng = random.Random(seed)
    seen_names: set[str] = {name for name, _ in anchors}
    target = max(min_configs, 560)
    emitted = 0
    attempts = 0
    max_attempts = target * 25

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
    if parsed_sessions:
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
            if trade.pnl_quote > 0:
                wins += 1
            if trade.entry_class == "formal":
                formal += 1
            elif trade.entry_class == "regime_adaptive_half_size":
                adaptive += 1
            exit_counts[trade.exit_reason] += 1

    win_rate = (wins / total_trades) if total_trades else 0.0
    base = _dynamic_sweep_base(dynamic_mode)
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
    "dynamic_mode",
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
        "dynamic_mode": row.dynamic_mode,
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

    stem = output_stem or f"strategy_replay_dynamic_{dynamic_mode}_mega_all_sessions"
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

    results: list[SweepResult] = []
    for index, (name, overrides) in enumerate(config_iter):
        result = _run_dynamic_config(
            name,
            overrides,
            dynamic_mode,
            parsed_sessions,
            hl_caches,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
            reports_by_pair,
        )
        results.append(_apply_capital_metrics(result, benchmark_avg_notional))
        if gc_every and (index + 1) % gc_every == 0:
            gc.collect()

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
        description="MACD+BB dynamic strategy replay mega sweep (grid v4, all sessions)"
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
    args = parser.parse_args()

    if args.all_dynamic_modes:
        all_results: list[tuple[str, list[SweepResult], str, Path]] = []
        for mode in sorted(DYNAMIC_MODE_PRESETS):
            print(f"\n{'=' * 72}\nDynamic mega sweep — mode={mode}\n{'=' * 72}")
            stem = f"strategy_replay_dynamic_{mode}_mega_all_sessions"
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

    stem = f"strategy_replay_dynamic_{args.dynamic_mode}_mega_all_sessions"
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
