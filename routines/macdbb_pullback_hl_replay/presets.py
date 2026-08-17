"""Preset resolution for macdbb_pullback_hl backtests."""

from __future__ import annotations

from typing import Any

from condor.strategy_runners.macdbb_pullback.presets import (
    DEFAULT_TIMELINE_60S_PRESET,
    PRESET_LABELS,
    PRESET_OVERRIDES,
    resolve_config_dict,
    strategy_params_from_preset,
)
from routines.macdbb_pullback_hl_replay.models import PullbackReplayConfig


_STRATEGY_OVERRIDE_KEYS = (
    "bb_proximity_epsilon_pct",
    "impulse_lookback_bars",
    "impulse_atr_mult",
    "atr_period",
    "chase_long_bb_pos_max",
    "chase_short_bb_pos_min",
    "pullback_epsilon_pct",
    "pullback_timeout_hours",
    "sl_pct",
    "tp_pct",
    "min_notional_quote",
    "max_notional_quote",
    "sl_symbol_cooldown_hours",
    "enable_flip_exit",
    "flip_confirm_ticks",
    "flip_cooldown_hours",
    "enable_thesis_decay_exit",
    "thesis_decay_exit_hours",
    "thesis_bb_drift_pts",
)


def resolve_pullback_config(config: PullbackReplayConfig) -> PullbackReplayConfig:
    # Only caller-set fields overlay the preset. model_dump(exclude_none=True)
    # would treat PullbackReplayConfig defaults (decay off / 28h) as overrides
    # and wipe pullback_decay_2h_60s.
    incoming = config.model_dump(exclude_unset=True)
    merged = resolve_config_dict(config.preset, overrides=incoming)
    for key in (
        "range_start_utc",
        "range_end_utc",
        "write_csv",
        "auto_update_snapshots",
        "total_amount_quote",
        "max_open_executors",
        "fee_bps",
        "slippage_bps",
        "snapshot_dir",
        "candle_source",
        "price_source",
        "hl_cache_dir",
        "live_equivalent_queue",
        "sessions",
    ):
        if key not in incoming:
            continue
        value = incoming[key]
        if value not in (None, ""):
            merged[key] = value
    freq = int(merged.get("frequency_sec") or config.frequency_sec or 60)
    params = strategy_params_from_preset(
        config.preset if config.preset != "custom" else DEFAULT_TIMELINE_60S_PRESET,
        frequency_sec=freq,
    )
    for key in _STRATEGY_OVERRIDE_KEYS:
        if key in incoming:
            params[key] = incoming[key]
        if key in params:
            merged[key] = params[key]
    params["pullback_timeout_ticks"] = max(
        1, int(round(float(params.get("pullback_timeout_hours") or 12) * 3600 / freq))
    )
    params["sl_symbol_cooldown_ticks"] = max(
        1, int(round(float(params.get("sl_symbol_cooldown_hours") or 5) * 3600 / freq))
    )
    params["thesis_decay_exit_ticks"] = max(
        0,
        int(round(float(params.get("thesis_decay_exit_hours") or 0) * 3600 / freq)),
    )
    params["flip_cooldown_ticks"] = max(
        0,
        int(round(float(params.get("flip_cooldown_hours") or 0) * 3600 / freq)),
    )
    merged["strategy_params"] = params
    merged["sl_cooldown_ticks"] = int(params.get("sl_symbol_cooldown_ticks") or 0)
    merged["pullback_timeout_ticks"] = int(params.get("pullback_timeout_ticks") or 0)
    merged["thesis_decay_exit_ticks"] = int(params.get("thesis_decay_exit_ticks") or 0)
    merged["flip_cooldown_ticks"] = int(params.get("flip_cooldown_ticks") or 0)
    return PullbackReplayConfig(**merged)


__all__ = [
    "DEFAULT_TIMELINE_60S_PRESET",
    "PRESET_LABELS",
    "PRESET_OVERRIDES",
    "resolve_pullback_config",
    "strategy_params_from_preset",
]
