"""Tests for timeline mega sweep helpers."""

from routines.macdbb_replay.timeline_sweep import (
    merge_timeline_config,
    replay_config_to_agent_strategy_params,
    timeline_sweep_overrides,
)
from routines.macdbb_replay.config_sweep import _dynamic_sweep_base
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import resolve_config_with_preset


def test_timeline_sweep_overrides_sets_backtest_mode():
    overrides = timeline_sweep_overrides()
    assert overrides["replay_mode"] == "timeline_backtest"
    assert overrides["data_source"] == "reports_only"
    assert overrides["range_start_utc"].endswith("Z")
    assert overrides["range_end_utc"].endswith("Z")


def test_merge_timeline_config_includes_fixed_activation():
    merged = merge_timeline_config(_dynamic_sweep_base("both_on"))
    assert merged["activation_ticks"] == 0
    assert merged["replay_mode"] == "timeline_backtest"


def test_timeline_preset_loads():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_timeline_mega_best")
    )
    assert config.replay_mode == "timeline_backtest"
    assert config.sl_pct == 4.5
    assert config.tp_pct == 6.2


def test_replay_config_to_agent_strategy_params_hours():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_timeline_mega_best")
    )
    params = replay_config_to_agent_strategy_params(config, frequency_sec=1800)
    assert params["thesis_decay_exit_hours"] == 14.0
    assert params["sl_pct"] == 4.5
