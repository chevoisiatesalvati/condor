"""Timeline mega sweep helpers, validation, and winner application."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml

from routines.macdbb_replay.config_sweep import (
    SweepResult,
    _apply_capital_metrics,
    _dynamic_sweep_base,
    _finalize_mega_dynamic_config,
    _load_sessions,
    _merge,
    _print_table,
    _run_dynamic_config,
    _write_sweep_csv,
    iter_mega_dynamic_sweep_configs,
)
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay.presets import (
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    _DRIVER_TIMELINE,
    _DYNAMIC_PRESET_INFRA,
    _STRATEGY_TIMELINE_MEGA_BEST,
    _merge_preset_layers,
    resolve_config_with_preset,
)
from routines.macdbb_replay.replay_range import timeline_range_from_reports
from routines.macdbb_replay.reports import (
    build_reports_by_pair,
    load_reports_index,
)
from routines.strategy_replay_backtest_dynamic_amount import run as run_dynamic_replay

DEFAULT_FREQUENCY_SEC = 1800
DEFAULT_TIME_WINDOW_MIN = 15
TIMELINE_PRESET_NAME = "hl_dynamic_timeline_mega_best"
AGENT_SLUG = "macdbb_scanner_aggressive_hl"
PRESET_STRIP_KEYS = frozenset(
    {"preset", "session_nums", "range_start_utc", "range_end_utc"}
)


def timeline_sweep_overrides(
    *,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> dict[str, Any]:
    start, end = timeline_range_from_reports()
    if range_start_utc:
        start = range_start_utc
    if range_end_utc:
        end = range_end_utc
    return {
        **_DRIVER_TIMELINE,
        "preset": "custom",
        "range_start_utc": start,
        "range_end_utc": end,
    }


def merge_timeline_config(
    overrides: dict[str, Any],
    *,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> dict[str, Any]:
    return _finalize_mega_dynamic_config(
        _merge(
            overrides,
            **timeline_sweep_overrides(
                range_start_utc=range_start_utc,
                range_end_utc=range_end_utc,
                frequency_sec=frequency_sec,
                time_window_min=time_window_min,
            ),
        )
    )


async def run_timeline_dynamic_sweep(
    dynamic_mode: str = "both_on",
    output_dir: Path | None = None,
    *,
    min_configs: int = 560,
    seed: int = 42,
    output_stem: str | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    top_n: int = 40,
) -> tuple[list[SweepResult], str, float, str, str]:
    timeline_fields = timeline_sweep_overrides(
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        frequency_sec=frequency_sec,
        time_window_min=time_window_min,
    )
    load_config = DynamicStrategyReplayConfig(
        **_finalize_mega_dynamic_config(_merge(_dynamic_sweep_base(dynamic_mode), **timeline_fields))
    )
    (
        parsed_sessions,
        hl_caches,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        _selected,
    ) = await _load_sessions(load_config)
    tick_count = sum(len(ticks) for ticks in parsed_sessions.values())
    reports_by_pair = build_reports_by_pair(load_reports_index())

    stem = output_stem or f"strategy_replay_dynamic_{dynamic_mode}_mega_timeline"
    baseline = f"dyn_{dynamic_mode}_timeline_baseline_winner"
    benchmark_avg_notional = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL

    print(
        f"Timeline sweep: {timeline_fields['range_start_utc']} -> "
        f"{timeline_fields['range_end_utc']} | ticks={tick_count} | "
        f"time_window={time_window_min}m | freq={frequency_sec}s"
    )

    results: list[SweepResult] = []
    for _index, (name, overrides) in enumerate(
        iter_mega_dynamic_sweep_configs(dynamic_mode, min_configs=min_configs, seed=seed)
    ):
        merged = merge_timeline_config(
            overrides,
            frequency_sec=frequency_sec,
            time_window_min=time_window_min,
            range_start_utc=timeline_fields["range_start_utc"],
            range_end_utc=timeline_fields["range_end_utc"],
        )
        result = _run_dynamic_config(
            name,
            merged,
            dynamic_mode,
            parsed_sessions,
            hl_caches,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
            reports_by_pair,
        )
        results.append(_apply_capital_metrics(result, benchmark_avg_notional))

    results.sort(key=lambda row: row.capital_normalized_pnl, reverse=True)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_sweep_csv(output_dir / f"{stem}.csv", results)

    baseline_row = next((row for row in results if row.name.endswith("baseline_winner")), results[-1])
    _print_table(
        results,
        baseline_row.pnl,
        top_n=top_n,
        dynamic=True,
        benchmark_avg_notional=benchmark_avg_notional,
        baseline_capital_normalized_pnl=baseline_row.capital_normalized_pnl,
        rank_by_normalized=True,
    )

    return (
        results,
        baseline,
        benchmark_avg_notional,
        timeline_fields["range_start_utc"],
        timeline_fields["range_end_utc"],
    )


def full_replay_overrides(
    sweep_delta: dict[str, Any],
    *,
    dynamic_mode: str = "both_on",
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> dict[str, Any]:
    base = _finalize_mega_dynamic_config(_dynamic_sweep_base(dynamic_mode))
    return merge_timeline_config(
        _merge(base, **sweep_delta),
        frequency_sec=frequency_sec,
        time_window_min=time_window_min,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
    )


def replay_config_to_agent_strategy_params(
    config: DynamicStrategyReplayConfig,
    *,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
) -> dict[str, Any]:
    hours_per_tick = frequency_sec / 3600.0

    def ticks_to_hours(ticks: int) -> float:
        return round(ticks * hours_per_tick, 4)

    return {
        "adaptive_activation_hours": ticks_to_hours(config.activation_ticks),
        "min_tradeable_for_adaptive": config.min_tradeable_count,
        "adaptive_skip_4h_filter": config.ignore_adaptive_4h_filter,
        "adaptive_requires_flat": config.adaptive_requires_flat,
        "sl_symbol_cooldown_hours": ticks_to_hours(config.sl_cooldown_ticks),
        "adaptive_long_bb_pos_max": config.adaptive_long_bb_pos_max,
        "adaptive_short_bb_pos_min": config.adaptive_short_bb_pos_min,
        "adaptive_strong_long_bb_pos_max": config.adaptive_strong_long_bb_pos_max,
        "adaptive_strong_short_bb_pos_min": config.adaptive_strong_short_bb_pos_min,
        "adaptive_min_macd_gap_ratio": config.adaptive_min_macd_gap_ratio,
        "adaptive_min_hist_ratio": config.adaptive_min_hist_ratio,
        "adaptive_score_open_min": config.adaptive_score_open_min,
        "adaptive_score_open_min_extreme": config.adaptive_score_open_min_extreme,
        "adaptive_hist_sign_bonus": config.adaptive_hist_sign_bonus,
        "adaptive_hist_sign_penalty": config.adaptive_hist_sign_penalty,
        "adaptive_momentum_bonus": config.adaptive_momentum_bonus,
        "adaptive_momentum_penalty": config.adaptive_momentum_penalty,
        "bb_proximity_epsilon_pct": config.bb_proximity_epsilon_pct,
        "sl_pct": config.sl_pct,
        "tp_pct": config.tp_pct,
        "thesis_decay_exit_hours": ticks_to_hours(config.thesis_decay_exit_ticks),
        "thesis_bb_drift_pts": config.thesis_bb_drift_pts,
        "flip_cooldown_hours": ticks_to_hours(config.flip_cooldown_ticks),
        "enable_dynamic_sizing": config.enable_dynamic_sizing,
        "enable_dynamic_barriers": config.enable_dynamic_barriers,
        "min_notional_quote": config.min_notional_quote,
        "max_notional_quote": config.max_notional_quote,
        "min_conviction_mult": config.min_conviction_mult,
        "max_conviction_mult": config.max_conviction_mult,
        "strength_mult_per_unit": config.strength_mult_per_unit,
        "extreme_displacement_mult": config.extreme_displacement_mult,
        "activation_streak_mult_per_tick": config.activation_streak_mult_per_tick,
        "thin_universe_mult": config.thin_universe_mult,
        "mature_tape_low_vol_mult": config.mature_tape_low_vol_mult,
        "vol_inverse_sizing": config.vol_inverse_sizing,
        "min_vol_mult": config.min_vol_mult,
        "max_vol_mult": config.max_vol_mult,
        "ref_volatility_pct": config.ref_volatility_pct,
        "sl_vol_exponent": config.sl_vol_exponent,
        "tp_vol_exponent": config.tp_vol_exponent,
        "sl_min_pct": config.sl_min_pct,
        "sl_max_pct": config.sl_max_pct,
        "tp_min_pct": config.tp_min_pct,
        "tp_max_pct": config.tp_max_pct,
        "volatility_source": config.volatility_source,
    }


def build_timeline_preset_overrides(
    sweep_delta: dict[str, Any],
    *,
    dynamic_mode: str = "both_on",
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> dict[str, Any]:
    del dynamic_mode, frequency_sec, time_window_min
    return _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _DRIVER_TIMELINE,
        _STRATEGY_TIMELINE_MEGA_BEST,
        sweep_delta,
    )


def load_top_sweep_rows(csv_path: Path, top_n: int = 5) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))[:top_n]


async def validate_top_configs_via_routine(
    csv_path: Path,
    *,
    top_n: int = 5,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> list[dict[str, Any]]:
    start, end = timeline_range_from_reports()
    rows: list[dict[str, Any]] = []
    for rank, row in enumerate(load_top_sweep_rows(csv_path, top_n=top_n), start=1):
        delta = json.loads(row["overrides_json"])
        overrides = full_replay_overrides(
            delta,
            frequency_sec=frequency_sec,
            time_window_min=time_window_min,
            range_start_utc=start,
            range_end_utc=end,
        )
        config = DynamicStrategyReplayConfig(**overrides)
        result = await run_dynamic_replay(config, None)
        text = result if isinstance(result, str) else getattr(result, "text", str(result))
        rows.append(
            {
                "rank": rank,
                "name": row["name"],
                "sweep_cap_norm": float(row["capital_normalized_pnl"]),
                "sweep_pnl": float(row["pnl"]),
                "sweep_trades": int(row["trades"]),
                "routine_output": text,
            }
        )
    return rows


def format_validation_log(rows: list[dict[str, Any]]) -> str:
    lines = ["Timeline top-N routine validation (strategy_replay_backtest_dynamic_amount)"]
    for row in rows:
        lines.append("")
        lines.append(f"=== Rank {row['rank']}: {row['name']} ===")
        lines.append(
            f"Sweep: cap-norm=${row['sweep_cap_norm']:+.2f} raw=${row['sweep_pnl']:+.2f} "
            f"trades={row['sweep_trades']}"
        )
        lines.append(str(row["routine_output"]))
    return "\n".join(lines) + "\n"


def _split_agent_front_matter(agent_path: Path) -> tuple[dict[str, Any], str]:
    text = agent_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*\Z)", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Could not parse YAML front matter in {agent_path}")
    front = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    return front, body


def apply_winner_to_agent(
    config: DynamicStrategyReplayConfig,
    *,
    agent_path: Path | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
) -> dict[str, Any]:
    agent_path = agent_path or (TRADING_AGENTS_DIR / AGENT_SLUG / "agent.md")
    front, body = _split_agent_front_matter(agent_path)
    default_config = front.setdefault("default_config", {})
    default_config.setdefault("frequency_sec", frequency_sec)
    default_config.setdefault("risk_limits", {})
    default_config["risk_limits"]["max_open_executors"] = config.max_open_executors
    strategy_params = replay_config_to_agent_strategy_params(config, frequency_sec=frequency_sec)
    existing = default_config.get("strategy_params", {})
    existing.update(strategy_params)
    default_config["strategy_params"] = existing
    front["default_config"] = default_config
    agent_path.write_text(
        "---\n" + yaml.safe_dump(front, sort_keys=False, default_flow_style=False) + "---\n" + body,
        encoding="utf-8",
    )
    return strategy_params


def render_preset_block(preset_name: str, overrides: dict[str, Any]) -> str:
    lines = [f'    "{preset_name}": {{']
    for key, value in overrides.items():
        if key == "preset":
            continue
        lines.append(f"        {json.dumps(key)}: {repr(value)},")
    lines.append("    },")
    return "\n".join(lines)


def apply_winner_to_presets(
    preset_overrides: dict[str, Any],
    *,
    presets_path: Path | None = None,
    models_path: Path | None = None,
) -> None:
    presets_path = presets_path or Path("routines/macdbb_replay/presets.py")
    models_path = models_path or Path("routines/macdbb_replay/models.py")
    preset_text = presets_path.read_text(encoding="utf-8")
    if TIMELINE_PRESET_NAME in preset_text:
        return
    marker = '    "hl_dynamic_session_parity": {'
    filtered = {
        key: value
        for key, value in preset_overrides.items()
        if key not in PRESET_STRIP_KEYS
    }
    block = render_preset_block(TIMELINE_PRESET_NAME, filtered)
    preset_text = preset_text.replace(marker, block + "\n" + marker, 1)
    presets_path.write_text(preset_text, encoding="utf-8")

    models_text = models_path.read_text(encoding="utf-8")
    if TIMELINE_PRESET_NAME not in models_text:
        models_text = models_text.replace(
            '"hl_dynamic_session_parity",',
            f'"hl_dynamic_session_parity",\n        "{TIMELINE_PRESET_NAME}",',
        )
    models_path.write_text(models_text, encoding="utf-8")
