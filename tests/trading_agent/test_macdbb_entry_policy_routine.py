"""Tests for macdbb_entry_policy agent-local routine."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from routines.macdbb_replay.presets import DYNAMIC_PRESET_OVERRIDES


def _load_routine_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "trading_agents/macdbb_scanner_aggressive_hl/routines/macdbb_entry_policy.py"
    )
    spec = importlib.util.spec_from_file_location("macdbb_entry_policy_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strategy_params() -> dict:
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_session_parity"]
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
        "thin_universe_mult",
        "mature_tape_low_vol_mult",
        "vol_inverse_sizing",
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


def test_macdbb_entry_policy_routine_run():
    module = _load_routine_module()
    config = module.Config(
        pair="ETH-USD",
        side="long",
        entry_class="regime_adaptive_half_size",
        formal_notional_quote=500.0,
        adaptive_activation_streak=0,
        scanner_regime="degen",
        tradeable_count=4,
        natr_mean_pct=1.1,
        adaptive_strength_long=3.2,
        adaptive_strength_short=0.4,
        long_open_threshold=0.8,
        short_open_threshold=0.8,
        extreme_long_candidate=True,
        extreme_short_candidate=False,
        strategy_params=_strategy_params(),
    )
    output = asyncio.run(module.run(config, context=None))

    assert "notional_usd=" in output
    assert "sl_pct=" in output
    assert "tp_pct=" in output
    assert "stop_loss=" in output
    assert "take_profit=" in output
    assert "sizing_multiplier=" in output
