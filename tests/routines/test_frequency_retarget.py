"""Duration-preserving frequency retarget for MACDBB timeline backtests."""

from __future__ import annotations

from condor.strategy_runners.macdbb.presets import (
    DEFAULT_60S_TIMELINE_SNAPSHOT_DIR,
    DEFAULT_TIMELINE_SNAPSHOT_DIR,
    REFINE_LEAD_013_60S_PRESET,
    REFINE_LEAD_013_PRESET,
    backtest_preset_names,
    get_dynamic_preset_overrides,
    rescale_duration_tick_value,
    resolve_config_with_preset,
    strategy_params_from_preset,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig


def test_rescale_duration_tick_value_preserves_wall_clock():
    # 56 ticks @ 1800s = 28 hours = 1680 ticks @ 60s
    assert rescale_duration_tick_value(56, 1800, 60) == 1680
    assert rescale_duration_tick_value(1680, 60, 1800) == 56
    assert rescale_duration_tick_value(0, 1800, 60) == 0
    assert rescale_duration_tick_value(10, 1800, 1800) == 10


def test_60s_alias_preset_is_registered():
    overrides = get_dynamic_preset_overrides()
    if REFINE_LEAD_013_PRESET not in overrides:
        return  # private presets unavailable in this checkout
    assert REFINE_LEAD_013_60S_PRESET in overrides
    assert REFINE_LEAD_013_60S_PRESET in backtest_preset_names()
    alias = overrides[REFINE_LEAD_013_60S_PRESET]
    parent = overrides[REFINE_LEAD_013_PRESET]
    assert alias["frequency_sec"] == 60
    assert alias["snapshot_dir"] == DEFAULT_60S_TIMELINE_SNAPSHOT_DIR
    assert alias["thesis_decay_exit_ticks"] == rescale_duration_tick_value(
        parent["thesis_decay_exit_ticks"], 1800, 60
    )
    assert alias["sl_cooldown_ticks"] == rescale_duration_tick_value(
        parent["sl_cooldown_ticks"], 1800, 60
    )
    assert alias["time_window_min"] == 1


def test_resolve_retargets_frequency_override_on_1800_preset():
    overrides = get_dynamic_preset_overrides()
    if REFINE_LEAD_013_PRESET not in overrides:
        return
    parent = overrides[REFINE_LEAD_013_PRESET]
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset=REFINE_LEAD_013_PRESET,
            frequency_sec=60,
            range_start_utc="2026-05-06T00:00:00Z",
            range_end_utc="2026-08-06T23:59:59Z",
        )
    )
    assert config.frequency_sec == 60
    assert config.snapshot_dir == DEFAULT_60S_TIMELINE_SNAPSHOT_DIR
    assert config.thesis_decay_exit_ticks == rescale_duration_tick_value(
        parent["thesis_decay_exit_ticks"], 1800, 60
    )
    assert config.sl_cooldown_ticks == rescale_duration_tick_value(
        parent["sl_cooldown_ticks"], 1800, 60
    )
    assert config.flip_cooldown_ticks == rescale_duration_tick_value(
        parent["flip_cooldown_ticks"], 1800, 60
    )
    assert config.time_window_min == 1


def test_resolve_60s_preset_without_explicit_frequency_keeps_60():
    overrides = get_dynamic_preset_overrides()
    if REFINE_LEAD_013_60S_PRESET not in overrides:
        return
    # Model default frequency_sec=1800 must not clobber the 60s preset.
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset=REFINE_LEAD_013_60S_PRESET)
    )
    assert config.frequency_sec == 60
    assert config.snapshot_dir == DEFAULT_60S_TIMELINE_SNAPSHOT_DIR


def test_resolve_keeps_1800_when_unchanged():
    overrides = get_dynamic_preset_overrides()
    if REFINE_LEAD_013_PRESET not in overrides:
        return
    parent = overrides[REFINE_LEAD_013_PRESET]
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset=REFINE_LEAD_013_PRESET)
    )
    assert config.frequency_sec == 1800
    assert config.snapshot_dir == DEFAULT_TIMELINE_SNAPSHOT_DIR
    assert config.thesis_decay_exit_ticks == parent["thesis_decay_exit_ticks"]


def test_strategy_params_from_preset_hours_stable_across_frequencies():
    overrides = get_dynamic_preset_overrides()
    if REFINE_LEAD_013_PRESET not in overrides:
        return
    hours_1800 = strategy_params_from_preset(REFINE_LEAD_013_PRESET, frequency_sec=1800)
    hours_60 = strategy_params_from_preset(REFINE_LEAD_013_PRESET, frequency_sec=60)
    assert hours_1800["thesis_decay_exit_hours"] == hours_60["thesis_decay_exit_hours"]
    assert hours_1800["sl_symbol_cooldown_hours"] == hours_60["sl_symbol_cooldown_hours"]
    assert hours_1800["flip_cooldown_hours"] == hours_60["flip_cooldown_hours"]
    # 56 ticks @ 1800s → 28h
    assert hours_60["thesis_decay_exit_hours"] == 28.0
