"""Tests for live MACDBB dynamic policy helpers."""

from __future__ import annotations

from condor.trading_agent.policies.macdbb_dynamic import (
    LivePolicyMeta,
    estimate_pair_volatility,
    live_policy_config_from_params,
    resolve_live_entry_policy,
)
from condor.trading_agent.strategy_configs.registry import duration_to_ticks
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import DYNAMIC_PRESET_OVERRIDES


def _preset_strategy_params() -> dict:
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"]
    keys = (
        "enable_dynamic_sizing",
        "enable_dynamic_barriers",
        "sl_pct",
        "tp_pct",
        "min_notional_quote",
        "max_notional_quote",
        "min_conviction_mult",
        "max_conviction_mult",
        "strength_mult_per_unit",
        "extreme_displacement_mult",
        "activation_streak_mult_per_tick",
        "thin_universe_mult",
        "mature_tape_low_vol_mult",
        "vol_inverse_sizing",
        "min_vol_mult",
        "max_vol_mult",
        "ref_volatility_pct",
        "sl_vol_exponent",
        "tp_vol_exponent",
        "sl_min_pct",
        "sl_max_pct",
        "tp_min_pct",
        "tp_max_pct",
        "volatility_source",
    )
    params = {key: preset[key] for key in keys}
    params["adaptive_activation_ticks"] = preset["activation_ticks"]
    return params


def test_duration_to_ticks_zero_hours():
    assert duration_to_ticks(0, 1800) == 0


def test_live_policy_config_from_params_defaults_fixed_when_flags_absent():
    config = live_policy_config_from_params(
        {"sl_pct": 1.8, "tp_pct": 10.0},
        formal_notional_quote=500.0,
    )
    assert config.enable_dynamic_sizing is False
    assert config.enable_dynamic_barriers is False
    assert config.sl_pct == 1.8


def test_live_policy_config_from_params_matches_preset():
    params = _preset_strategy_params()
    config = live_policy_config_from_params(params, formal_notional_quote=500.0)
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"]

    assert config.formal_notional_quote == 500.0
    assert config.activation_ticks == preset["activation_ticks"]
    assert config.enable_dynamic_sizing is True
    assert config.enable_dynamic_barriers is True
    assert config.min_notional_quote == preset["min_notional_quote"]
    assert config.max_notional_quote == preset["max_notional_quote"]
    assert config.tp_min_pct == preset["tp_min_pct"]
    assert config.tp_max_pct == preset["tp_max_pct"]
    assert config.volatility_source == preset["volatility_source"]


def test_pair_vol_override_used_for_natr_source():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        volatility_source="natr",
        ref_volatility_pct=0.75,
    )
    vol = estimate_pair_volatility(
        pair="SOL-USD",
        journal_signal=None,
        config=config,
        pair_vol_override=1.25,
    )
    assert vol == 1.25


def test_resolve_live_entry_policy_returns_clamped_barriers():
    params = _preset_strategy_params()
    metrics = {
        "adaptive_strength_long": 2.5,
        "adaptive_strength_short": 0.5,
        "long_open_threshold": 0.8,
        "short_open_threshold": 0.8,
        "extreme_long_candidate": True,
        "extreme_short_candidate": False,
    }
    result = resolve_live_entry_policy(
        pair="BTC-USD",
        side="long",
        entry_class="formal",
        metrics=metrics,
        meta=LivePolicyMeta(tradeable_count=5, scanner_regime="mature"),
        entry_streak=0,
        strategy_params=params,
        formal_notional_quote=500.0,
        natr_mean_pct=0.9,
    )

    assert params["min_notional_quote"] <= result.notional_quote <= params["max_notional_quote"]
    assert params["sl_min_pct"] <= result.sl_pct <= params["sl_max_pct"]
    assert params["tp_min_pct"] <= result.tp_pct <= params["tp_max_pct"]
    assert result.volatility_proxy_pct == params["ref_volatility_pct"]
    assert result.sizing_multiplier > 0
    assert result.stop_loss_decimal == result.sl_pct / 100.0
    assert result.take_profit_decimal == result.tp_pct / 100.0
