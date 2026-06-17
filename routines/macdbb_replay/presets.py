from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

PresetValue = float | int | bool | str

# Fixed-strategy avg position size on sessions 37-58 (HL_SWEEP_BEST baseline).
FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL = 278.41


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

# Dynamic replay only — includes strategy + sizing_only dynamic params (sweep rank #1, 37-58).
DYNAMIC_PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {
    "hl_dynamic_mega_sweep_best": {
        **PRESET_OVERRIDES["hl_mega_sweep_best"],
        "session_nums": (
            "37,38,39,40,41,42,43,44,45,46,47,48,49,50,"
            "51,52,53,54,55,56,57,58"
        ),
        "data_source": "journal_recompute",
        "formal_notional_quote": 500.0,
        "price_source": "auto",
        "hl_use_cache": True,
        "require_price_data": True,
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": False,
        "min_notional_quote": 75.0,
        "max_notional_quote": 750.0,
        "min_conviction_mult": 0.75,
        "max_conviction_mult": 1.35,
        "strength_mult_per_unit": 0.08,
        "extreme_displacement_mult": 1.10,
        "activation_streak_mult_per_tick": 0.0,
        "thin_universe_mult": 0.85,
        "mature_tape_low_vol_mult": 0.95,
        "vol_inverse_sizing": True,
        "min_vol_mult": 0.60,
        "max_vol_mult": 1.25,
        "ref_volatility_pct": 0.50,
        "sl_vol_exponent": 0.70,
        "tp_vol_exponent": 1.00,
        "sl_min_pct": 0.8,
        "sl_max_pct": 4.0,
        "tp_min_pct": 3.0,
        "tp_max_pct": 15.0,
        "volatility_source": "auto",
        "ignore_journal_barriers_when_dynamic": True,
    },
}

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def resolve_config_with_preset(config: ConfigT) -> ConfigT:
    """Apply a named preset profile on top of the submitted config.

    When preset is not ``custom``, keys defined in PRESET_OVERRIDES always win
    over form/default values (the UI sends every field, so exclude_unset would
    not help). Fields outside the preset dict — e.g. session_nums, sl_pct —
    still come from the form.
    """
    preset = getattr(config, "preset", "custom")
    if preset == "custom":
        return config
    overrides = PRESET_OVERRIDES.get(preset) or DYNAMIC_PRESET_OVERRIDES.get(preset)
    if not overrides:
        return config
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in overrides.items() if key in allowed}
    return config_type(**{**config.model_dump(), **filtered, "preset": preset})
