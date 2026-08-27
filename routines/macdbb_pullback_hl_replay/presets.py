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
    "enable_dynamic_barriers",
    "ref_volatility_pct",
    "sl_vol_exponent",
    "tp_vol_exponent",
    "sl_min_pct",
    "sl_max_pct",
    "tp_min_pct",
    "tp_max_pct",
    "min_notional_quote",
    "max_notional_quote",
    "enable_dynamic_sizing",
    "min_vol_mult",
    "max_vol_mult",
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
    # #region agent log
    try:
        import json as _json
        import time as _time

        from condor.strategy_runners.macdbb_pullback.presets import (
            get_dynamic_preset_overrides,
        )

        yaml_over = get_dynamic_preset_overrides().get(str(config.preset)) or {}
        with open(
            "/home/saul/projects/Hummingbot/.cursor/debug-f59e1a.log",
            "a",
            encoding="utf-8",
        ) as _dbg:
            _dbg.write(
                _json.dumps(
                    {
                        "sessionId": "f59e1a",
                        "hypothesisId": "H1",
                        "location": "presets.py:resolve_pullback_config",
                        "message": "incoming vs yaml vs merged strategy params",
                        "data": {
                            "preset": config.preset,
                            "incoming": {
                                "impulse_atr_mult": incoming.get("impulse_atr_mult"),
                                "pullback_epsilon_pct": incoming.get(
                                    "pullback_epsilon_pct"
                                ),
                                "sl_pct": incoming.get("sl_pct"),
                                "tp_pct": incoming.get("tp_pct"),
                                "enable_dynamic_barriers": incoming.get(
                                    "enable_dynamic_barriers"
                                ),
                            },
                            "yaml": {
                                "impulse_atr_mult": yaml_over.get("impulse_atr_mult"),
                                "pullback_epsilon_pct": yaml_over.get(
                                    "pullback_epsilon_pct"
                                ),
                                "sl_pct": yaml_over.get("sl_pct"),
                                "tp_pct": yaml_over.get("tp_pct"),
                                "enable_dynamic_barriers": yaml_over.get(
                                    "enable_dynamic_barriers"
                                ),
                            },
                            "merged_after_dict": {
                                "impulse_atr_mult": merged.get("impulse_atr_mult"),
                                "pullback_epsilon_pct": merged.get(
                                    "pullback_epsilon_pct"
                                ),
                                "sl_pct": merged.get("sl_pct"),
                                "tp_pct": merged.get("tp_pct"),
                                "enable_dynamic_barriers": merged.get(
                                    "enable_dynamic_barriers"
                                ),
                            },
                            "incoming_has_impulse": "impulse_atr_mult" in incoming,
                            "incoming_has_epsilon": "pullback_epsilon_pct" in incoming,
                            "incoming_has_sl": "sl_pct" in incoming,
                            "incoming_has_dyn_barriers": (
                                "enable_dynamic_barriers" in incoming
                            ),
                            "incoming_live_eq": incoming.get(
                                "live_equivalent_queue"
                            ),
                            "incoming_has_live_eq": (
                                "live_equivalent_queue" in incoming
                            ),
                        },
                        "timestamp": int(_time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
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
