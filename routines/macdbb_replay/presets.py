from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

PresetValue = float | int | bool | str

# Standard capital-at-risk reference for cap-norm PnL (sweep + routine).
# HL_SWEEP_BEST fixed replay, sessions 37-58: avg $ notional per trade.
# cap_norm_pnl = raw_pnl × (FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL / avg_notional).
# Re-measure after the session window or HL_SWEEP_BEST baseline changes.
FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL = 266.45


def capital_normalized_pnl(
    raw_pnl: float,
    avg_notional: float,
    benchmark_avg_notional: float = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
) -> float:
    """Scale raw PnL to fixed-strategy avg position size for capital-at-risk comparison."""
    if avg_notional <= 0 or benchmark_avg_notional <= 0:
        return raw_pnl
    return raw_pnl * (benchmark_avg_notional / avg_notional)


PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {
    "safe": {
        "activation_ticks": 8,
        "adaptive_long_bb_pos_max": 46.0,
        "adaptive_short_bb_pos_min": 74.0,
        "adaptive_strong_long_bb_pos_max": 33.0,
        "adaptive_strong_short_bb_pos_min": 87.0,
        "adaptive_min_macd_gap_ratio": 0.10,
        "adaptive_min_hist_ratio": 0.16,
        "adaptive_score_open_min": 2.55,
        "adaptive_score_open_min_extreme": 2.30,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.40,
        "adaptive_momentum_bonus": 0.15,
        "adaptive_momentum_penalty": 0.15,
    },
    "balanced": {
        "activation_ticks": 6,
        "adaptive_long_bb_pos_max": 48.0,
        "adaptive_short_bb_pos_min": 72.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 85.0,
        "adaptive_min_macd_gap_ratio": 0.08,
        "adaptive_min_hist_ratio": 0.12,
        "adaptive_score_open_min": 2.40,
        "adaptive_score_open_min_extreme": 2.15,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.35,
        "adaptive_momentum_bonus": 0.20,
        "adaptive_momentum_penalty": 0.10,
    },
    "opportunistic": {
        "activation_ticks": 4,
        "adaptive_long_bb_pos_max": 55.0,
        "adaptive_short_bb_pos_min": 65.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 2.10,
        "adaptive_score_open_min_extreme": 1.85,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
    "replay_probe": {
        "activation_ticks": 4,
        "time_window_min": 90,
        "adaptive_long_bb_pos_max": 90.0,
        "adaptive_short_bb_pos_min": 55.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 1.00,
        "adaptive_score_open_min_extreme": 0.75,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
    # Sessions 36-48 sweep winner (+$148.39): sl2.4/tp10/ne32 + tighter adaptive gates.
    "hl_sweep_best": {
        "activation_ticks": 1,
        "sl_pct": 2.4,
        "tp_pct": 10.0,
        "thesis_decay_exit_ticks": 32,
        "thesis_bb_drift_pts": 25.0,
        "adaptive_long_bb_pos_max": 65.0,
        "adaptive_short_bb_pos_min": 72.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 1.50,
        "adaptive_score_open_min_extreme": 1.00,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
        "bb_proximity_epsilon_pct": 0.10,
        "ignore_adaptive_4h_filter": True,
        "adaptive_requires_flat": False,
        "max_open_executors": 3,
        "min_tradeable_count": 1,
        "sl_cooldown_ticks": 2,
        "flip_cooldown_ticks": 8,
    },
    # Sessions 36-50 refine sweep winner (+$186.29): hl_sweep_best + loose adaptive BB gates.
    "hl_bb_loose_best": {
        "activation_ticks": 1,
        "sl_pct": 2.4,
        "tp_pct": 10.0,
        "thesis_decay_exit_ticks": 32,
        "thesis_bb_drift_pts": 25.0,
        "adaptive_long_bb_pos_max": 75.0,
        "adaptive_short_bb_pos_min": 65.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 85.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 1.50,
        "adaptive_score_open_min_extreme": 1.00,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
        "bb_proximity_epsilon_pct": 0.10,
        "ignore_adaptive_4h_filter": True,
        "adaptive_requires_flat": False,
        "max_open_executors": 3,
        "min_tradeable_count": 1,
        "sl_cooldown_ticks": 2,
        "flip_cooldown_ticks": 8,
    },
    # Sessions 36-50 mega sweep winner (+$199.33): sl1.8/tp10/td16 + wide BB gates.
    "hl_mega_sweep_best": {
        "activation_ticks": 1,
        "sl_pct": 1.8,
        "tp_pct": 10.0,
        "thesis_decay_exit_ticks": 16,
        "thesis_bb_drift_pts": 35.0,
        "adaptive_long_bb_pos_max": 80.0,
        "adaptive_short_bb_pos_min": 78.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 92.0,
        "adaptive_min_macd_gap_ratio": 0.08,
        "adaptive_min_hist_ratio": 0.16,
        "adaptive_score_open_min": 1.00,
        "adaptive_score_open_min_extreme": 0.75,
        "adaptive_hist_sign_bonus": 0.25,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.20,
        "adaptive_momentum_penalty": 0.05,
        "bb_proximity_epsilon_pct": 0.15,
        "ignore_adaptive_4h_filter": True,
        "adaptive_requires_flat": False,
        "max_open_executors": 3,
        "min_tradeable_count": 1,
        "sl_cooldown_ticks": 2,
        "flip_cooldown_ticks": 8,
    },
}

# Shared infra keys merged into dynamic presets (not mode-specific).
_DYNAMIC_PRESET_INFRA: dict[str, PresetValue] = {
    "formal_notional_quote": 500.0,
    "price_source": "auto",
    "hl_use_cache": True,
    "require_price_data": True,
}

_DRIVER_SESSION: dict[str, PresetValue] = {
    "replay_mode": "session_parity",
    "data_source": "reports_only",
    "config_source": "preset",
    "session_nums": "all",
    "time_window_min": 5,
    "use_journal_barriers": False,
    "write_csv": False,
}

_DRIVER_TIMELINE: dict[str, PresetValue] = {
    "replay_mode": "timeline_backtest",
    "data_source": "reports_only",
    "config_source": "preset",
    "frequency_sec": 1800,
    "time_window_min": 15,
    "use_journal_barriers": False,
    "write_csv": False,
}

_STRATEGY_SESSION_MEGA_BEST: dict[str, PresetValue] = {
    "enable_dynamic_sizing": True,
    "enable_dynamic_barriers": True,
    "ignore_journal_barriers_when_dynamic": True,
    "activation_ticks": 0,
    "sl_pct": 3.8,
    "tp_pct": 5.0,
    "thesis_decay_exit_ticks": 28,
    "thesis_bb_drift_pts": 78.0,
    "adaptive_long_bb_pos_max": 82.0,
    "adaptive_short_bb_pos_min": 88.0,
    "adaptive_strong_long_bb_pos_max": 26.0,
    "adaptive_strong_short_bb_pos_min": 82.0,
    "adaptive_min_macd_gap_ratio": 0.07,
    "adaptive_min_hist_ratio": 0.07,
    "adaptive_score_open_min": 0.8,
    "adaptive_score_open_min_extreme": 2.8,
    "adaptive_hist_sign_bonus": 0.48,
    "adaptive_hist_sign_penalty": 0.58,
    "adaptive_momentum_bonus": 0.22,
    "adaptive_momentum_penalty": 0.16,
    "bb_proximity_epsilon_pct": 0.11,
    "ignore_adaptive_4h_filter": True,
    "adaptive_requires_flat": False,
    "max_open_executors": 10,
    "min_tradeable_count": 1,
    "sl_cooldown_ticks": 2,
    "flip_cooldown_ticks": 8,
    "min_notional_quote": 200.0,
    "max_notional_quote": 1400.0,
    "min_conviction_mult": 0.7,
    "max_conviction_mult": 1.9,
    "strength_mult_per_unit": 0.42,
    "extreme_displacement_mult": 1.65,
    "activation_streak_mult_per_tick": 0.0,
    "thin_universe_mult": 0.96,
    "mature_tape_low_vol_mult": 0.99,
    "vol_inverse_sizing": True,
    "min_vol_mult": 0.58,
    "max_vol_mult": 1.05,
    "ref_volatility_pct": 0.75,
    "sl_vol_exponent": 1.05,
    "tp_vol_exponent": 1.35,
    "sl_min_pct": 1.4,
    "sl_max_pct": 7.0,
    "tp_min_pct": 3.5,
    "tp_max_pct": 11.0,
    "volatility_source": "bb_width",
}

_STRATEGY_TIMELINE_MEGA_BEST: dict[str, PresetValue] = {
    "enable_dynamic_sizing": True,
    "enable_dynamic_barriers": True,
    "ignore_journal_barriers_when_dynamic": False,
    "activation_ticks": 0,
    "sl_pct": 4.5,
    "tp_pct": 6.2,
    "thesis_decay_exit_ticks": 28,
    "thesis_bb_drift_pts": 18.0,
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
    "ignore_adaptive_4h_filter": True,
    "adaptive_requires_flat": False,
    "max_open_executors": 10,
    "min_tradeable_count": 1,
    "sl_cooldown_ticks": 2,
    "flip_cooldown_ticks": 8,
    "min_notional_quote": 200.0,
    "max_notional_quote": 1100.0,
    "min_conviction_mult": 0.7,
    "max_conviction_mult": 1.4,
    "strength_mult_per_unit": 0.16,
    "extreme_displacement_mult": 1.35,
    "activation_streak_mult_per_tick": 0.0,
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


def _merge_preset_layers(*layers: dict[str, PresetValue]) -> dict[str, PresetValue]:
    merged: dict[str, PresetValue] = {}
    for layer in layers:
        merged.update(layer)
    return merged


# Dynamic replay only — mega sweep v4 top1 (sessions 37-60 routine validation, cap-norm +$342).
DYNAMIC_PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {
    "hl_dynamic_mega_sweep_best": _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _DRIVER_SESSION,
        _STRATEGY_SESSION_MEGA_BEST,
    ),
    "hl_dynamic_timeline_mega_best": _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _DRIVER_TIMELINE,
        _STRATEGY_TIMELINE_MEGA_BEST,
    ),
    "hl_dynamic_session_parity": _merge_preset_layers(
        {
            "price_source": "auto",
            "hl_use_cache": True,
            "require_price_data": True,
        },
        {
            **_DRIVER_SESSION,
            "config_source": "session",
            "compare_journal_flags": False,
        },
    ),
}

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def resolve_timeline_range(config: ConfigT) -> ConfigT:
    """Fill missing timeline range from scanner report index."""
    replay_mode = getattr(config, "replay_mode", None)
    if replay_mode != "timeline_backtest":
        return config
    start = getattr(config, "range_start_utc", None)
    end = getattr(config, "range_end_utc", None)
    if start and end:
        return config
    from routines.macdbb_replay.replay_range import timeline_range_from_reports

    range_start, range_end = timeline_range_from_reports()
    updates: dict[str, PresetValue] = {}
    if not start:
        updates["range_start_utc"] = range_start
    if not end:
        updates["range_end_utc"] = range_end
    if not updates:
        return config
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in updates.items() if key in allowed}
    return config_type(**{**config.model_dump(), **filtered})


def resolve_config_with_preset(config: ConfigT) -> ConfigT:
    """Apply a named preset profile on top of the submitted config.

    When preset is not ``custom``, keys defined in PRESET_OVERRIDES always win
    over form/default values (the UI sends every field, so exclude_unset would
    not help). Fields outside the preset dict — e.g. session_nums, sl_pct —
    still come from the form.
    """
    preset = getattr(config, "preset", "custom")
    if preset == "custom":
        return resolve_timeline_range(config)
    overrides = PRESET_OVERRIDES.get(preset) or DYNAMIC_PRESET_OVERRIDES.get(preset)
    if not overrides:
        return resolve_timeline_range(config)
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in overrides.items() if key in allowed}
    merged = config_type(**{**config.model_dump(), **filtered, "preset": preset})
    return resolve_timeline_range(merged)
