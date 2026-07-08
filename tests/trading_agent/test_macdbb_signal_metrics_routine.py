"""Tests for macdbb_signal_metrics agent-local routine."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.presets import DYNAMIC_PRESET_OVERRIDES


def _load_routine_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "agents/macdbb_scanner_aggressive_hl/routines/macdbb_signal_metrics.py"
    )
    spec = importlib.util.spec_from_file_location("macdbb_signal_metrics_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strategy_params() -> dict:
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_session_parity"]
    keys = (
        "bb_proximity_epsilon_pct",
        "adaptive_long_bb_pos_max",
        "adaptive_short_bb_pos_min",
        "adaptive_strong_long_bb_pos_max",
        "adaptive_strong_short_bb_pos_min",
        "adaptive_min_macd_gap_ratio",
        "adaptive_min_hist_ratio",
        "adaptive_score_open_min",
        "adaptive_score_open_min_extreme",
        "adaptive_hist_sign_bonus",
        "adaptive_hist_sign_penalty",
        "adaptive_momentum_bonus",
        "adaptive_momentum_penalty",
    )
    return {key: preset[key] for key in keys}


def test_macdbb_signal_metrics_routine_run():
    module = _load_routine_module()
    config = module.Config(
        pair="ETH-USD",
        price=3200.0,
        bb_pos_pct=25.0,
        bb_mid=3180.0,
        bb_upper=3250.0,
        macd=12.0,
        signal_line=8.0,
        histogram=4.0,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
        strategy_params=_strategy_params(),
    )
    output = asyncio.run(module.run(config, context=None))

    assert "signal=LONG" in output
    assert "formal_long=true" in output
    assert "adaptive_strength_long=" in output
    assert "adaptive_long_open=" in output
    assert "macd_gap_ratio=" in output
