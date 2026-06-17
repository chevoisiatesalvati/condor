"""Tests for live MACDBB signal metrics helpers."""

from __future__ import annotations

from condor.trading_agent.policies.macdbb_metrics import (
    LiveSignalInput,
    compute_live_signal_metrics,
    compute_metrics,
    infer_signal_label,
    parsed_report_from_live_input,
)
from routines.macdbb_replay.models import StrategyReplayConfig
from routines.macdbb_replay.presets import DYNAMIC_PRESET_OVERRIDES


def _v6_metrics_params() -> dict:
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"]
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


def _sample_input(**overrides) -> LiveSignalInput:
    base = dict(
        pair="BTC-USD",
        price=100.0,
        bb_pos_pct=30.0,
        bb_mid=98.0,
        bb_upper=102.0,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        trend="bullish",
        momentum="increasing",
        bullish_cross=False,
        bearish_cross=False,
    )
    base.update(overrides)
    return LiveSignalInput(**base)


def test_compute_live_signal_metrics_matches_compute_metrics():
    params = _v6_metrics_params()
    signal_input = _sample_input()
    live = compute_live_signal_metrics(signal_input, params)
    replay = compute_metrics(
        parsed_report_from_live_input(signal_input),
        StrategyReplayConfig(preset="custom", **_v6_metrics_params()),
    )
    for key in live:
        assert live[key] == replay[key], f"mismatch on {key}"


def test_formal_long_via_bullish_cross():
    params = _v6_metrics_params()
    metrics = compute_live_signal_metrics(
        _sample_input(bullish_cross=True),
        params,
    )
    assert metrics["formal_long"] is True
    assert infer_signal_label(metrics) == "LONG"


def test_adaptive_strength_higher_at_extreme_displacement():
    params = _v6_metrics_params()
    normal = compute_live_signal_metrics(_sample_input(bb_pos_pct=40.0), params)
    extreme = compute_live_signal_metrics(_sample_input(bb_pos_pct=10.0), params)
    assert float(extreme["adaptive_strength_long"]) > float(normal["adaptive_strength_long"])
    assert bool(extreme["extreme_long_candidate"]) is True


def test_adaptive_long_open_requires_mode_gates_in_metrics():
    params = _v6_metrics_params()
    metrics = compute_live_signal_metrics(
        _sample_input(bb_pos_pct=10.0, macd=2.0, signal_line=0.2, histogram=1.0),
        params,
    )
    assert metrics["strength_gate"] is True
    assert float(metrics["adaptive_strength_long"]) >= float(metrics["long_open_threshold"])
