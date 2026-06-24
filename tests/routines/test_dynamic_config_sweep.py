"""Tests for dynamic strategy replay mega sweep helpers."""

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    CURRENT_WINNER_OVERRIDES,
    CURRENT_WINNER_PRESET,
    DYNAMIC_MODE_PRESETS,
    ENTRY_SLTP_SWEEP_FIXED_OVERRIDES,
    ENTRY_SLTP_SWEEP_GRID,
    ENTRY_SLTP_SWEEP_MIN_CONFIGS,
    ENTRY_SLTP_SWEEP_VERSION,
    LIVE_AGENT_DEFAULT_OVERRIDES,
    MEGA_GRID_FIXED_OVERRIDES,
    MEGA_GRID_VERSION,
    MEGA_SWEEP_GRID,
    MEGA_SWEEP_MIN_CONFIGS_BY_MODE,
    REFINE_MIN_CONFIGS_BY_PHASE,
    REFINE_PHASE_A_GRID,
    REFINE_SWEEP_VERSION,
    SWEEP_GRID_CHOICES,
    _barriers_saturated_at_median_vol,
    _dynamic_grid_for_mode,
    _dynamic_sweep_base,
    _entry_sltp_space_size,
    default_min_configs_for_mode,
    default_min_configs_for_refine_phase,
    default_min_configs_for_sweep_grid,
    is_sensible_replay_config,
    iter_entry_sltp_sweep_configs,
    iter_mega_dynamic_sweep_configs,
    iter_refine_sweep_configs,
    sweep_space_size,
)
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    capital_normalized_pnl,
    resolve_config_with_preset,
)


def test_dynamic_sweep_base_respects_mode():
    sizing = _dynamic_sweep_base("sizing_only")
    assert sizing["enable_dynamic_sizing"] is True
    assert sizing["enable_dynamic_barriers"] is False

    barriers = _dynamic_sweep_base("barriers_only")
    assert barriers["enable_dynamic_sizing"] is False
    assert barriers["enable_dynamic_barriers"] is True


def test_dynamic_grid_includes_barrier_keys_only_when_needed():
    sizing_grid = _dynamic_grid_for_mode("sizing_only")
    assert "sl_vol_exponent" not in sizing_grid
    assert "strength_mult_per_unit" in sizing_grid
    assert "max_open_executors" in sizing_grid

    barrier_grid = _dynamic_grid_for_mode("barriers_only")
    assert "sl_vol_exponent" in barrier_grid
    assert "strength_mult_per_unit" not in barrier_grid


def test_iter_mega_dynamic_sweep_configs_yields_enough_samples():
    configs = list(
        iter_mega_dynamic_sweep_configs("both_keep_journal", min_configs=20, seed=7)
    )
    assert len(configs) >= 22
    names = {name for name, _ in configs}
    assert len(names) == len(configs)
    assert any(name.startswith("dyn_both_keep_journal_baseline_winner") for name in names)
    for _name, overrides in configs:
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True


def test_all_dynamic_modes_defined():
    assert set(DYNAMIC_MODE_PRESETS) == {
        "sizing_only",
        "barriers_only",
        "both_on",
        "both_keep_journal",
    }


def test_capital_normalized_pnl_scales_by_avg_notional():
    assert capital_normalized_pnl(190.0, 200.0, 300.0) == 285.0
    assert capital_normalized_pnl(200.0, 300.0, 300.0) == 200.0


def test_sweep_and_routine_share_capital_benchmark_constant():
    from routines.macdbb_scanner_aggressive_hl_replay.presets import (
        FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL as PRESET_BENCHMARK,
    )

    assert FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL == PRESET_BENCHMARK
    assert PRESET_BENCHMARK > 0


def test_is_sensible_replay_config_rejects_inverted_bounds():
    assert is_sensible_replay_config({"tp_min_pct": 3.0, "tp_max_pct": 15.0})
    assert not is_sensible_replay_config({"tp_min_pct": 7.0, "tp_max_pct": 6.0})
    assert not is_sensible_replay_config({"sl_min_pct": 5.0, "sl_max_pct": 3.0})


def test_is_sensible_replay_config_rejects_adaptive_ordering_inversions():
    assert not is_sensible_replay_config(
        {
            "adaptive_strong_long_bb_pos_max": 82.0,
            "adaptive_long_bb_pos_max": 68.0,
            "enable_dynamic_barriers": False,
        }
    )


def test_is_sensible_replay_config_rejects_saturated_barriers():
    saturated = {
        "enable_dynamic_barriers": True,
        "sl_pct": 4.5,
        "tp_pct": 6.2,
        "ref_volatility_pct": 0.68,
        "sl_vol_exponent": 1.05,
        "tp_vol_exponent": 0.75,
        "sl_min_pct": 2.2,
        "sl_max_pct": 7.5,
        "tp_min_pct": 5.5,
        "tp_max_pct": 11.0,
    }
    assert _barriers_saturated_at_median_vol(saturated)
    assert not is_sensible_replay_config(saturated)
    assert is_sensible_replay_config(saturated, reject_saturated_barriers=False)


def test_live_agent_anchor_is_included():
    configs = list(iter_mega_dynamic_sweep_configs("both_on", min_configs=5, seed=1))
    assert any("anchor_live_agent_default" in name for name, _ in configs)
    live = next(overrides for name, overrides in configs if "anchor_live_agent_default" in name)
    assert live["ref_volatility_pct"] == LIVE_AGENT_DEFAULT_OVERRIDES["ref_volatility_pct"]


def test_mega_grid_version_and_mode_defaults():
    assert MEGA_GRID_VERSION == "v5"
    assert default_min_configs_for_mode("sizing_only") == 500
    assert default_min_configs_for_mode("barriers_only") == 350
    assert default_min_configs_for_mode("both_on") == 250
    assert "ignore_journal_barriers_when_dynamic" not in _dynamic_grid_for_mode("both_on")


def test_staged_parent_overrides_replace_winner_base():
    parent = dict(LIVE_AGENT_DEFAULT_OVERRIDES)
    parent["adaptive_long_bb_pos_max"] = 72.0
    configs = list(
        iter_mega_dynamic_sweep_configs(
            "barriers_only",
            min_configs=5,
            seed=11,
            parent_overrides=parent,
        )
    )
    assert len(configs) >= 6
    assert not any(f"anchor_{CURRENT_WINNER_PRESET}" in name for name, _ in configs)
    baseline = next(overrides for name, overrides in configs if name.endswith("baseline_winner"))
    assert baseline["adaptive_long_bb_pos_max"] == 72.0
    assert baseline["enable_dynamic_sizing"] is False
    assert baseline["enable_dynamic_barriers"] is True


def test_current_winner_anchor_has_sensible_barrier_bounds():
    assert is_sensible_replay_config(
        CURRENT_WINNER_OVERRIDES, reject_saturated_barriers=False
    )
    assert CURRENT_WINNER_OVERRIDES["tp_min_pct"] < CURRENT_WINNER_OVERRIDES["tp_max_pct"]


def test_mega_grid_sweeps_max_open_executors():
    assert MEGA_SWEEP_GRID["max_open_executors"] == (3, 5, 8, 10)
    assert CURRENT_WINNER_OVERRIDES["max_open_executors"] == 10


def test_mega_grid_fixed_overrides():
    assert MEGA_GRID_FIXED_OVERRIDES == {
        "activation_ticks": 0,
        "ignore_adaptive_4h_filter": True,
    }
    both_on = _dynamic_grid_for_mode("both_on")
    assert "activation_ticks" not in both_on
    assert "sl_vol_exponent" in both_on


def test_mega_dynamic_both_on_samples():
    configs = list(
        iter_mega_dynamic_sweep_configs("both_on", min_configs=30, seed=19)
    )
    assert len(configs) >= 33
    assert any(f"anchor_{CURRENT_WINNER_PRESET}" in name for name, _ in configs)
    assert any("anchor_live_agent_default" in name for name, _ in configs)
    executor_values = {
        overrides["max_open_executors"]
        for _name, overrides in configs
        if _name.startswith("dyn_both_on_mega_")
    }
    assert len(executor_values) > 1
    for name, overrides in configs:
        assert overrides["activation_ticks"] == 0
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True
        is_anchor = "anchor" in name or name.endswith("baseline_winner")
        assert is_sensible_replay_config(
            overrides, reject_saturated_barriers=not is_anchor
        )


def test_refine_v5_winner_preset_matches_current_winner_base():
    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig

    expected = {**CURRENT_WINNER_OVERRIDES, **DYNAMIC_MODE_PRESETS["both_on"]}
    resolved = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset=CURRENT_WINNER_PRESET)
    ).model_dump()
    skip = {"write_csv", "report_label", "compare_journal_flags", "preset"}
    for key, value in expected.items():
        if key in resolved and key not in skip:
            assert resolved[key] == value, f"mismatch on {key}: {resolved[key]!r} != {value!r}"

    assert (
        DYNAMIC_PRESET_OVERRIDES[CURRENT_WINNER_PRESET]["max_open_executors"] == 10
    )
    assert CURRENT_WINNER_PRESET == "hl_dynamic_timeline_refine_v5_winner_binance_1y"


def test_refine_sweep_defaults_and_grids():
    assert REFINE_SWEEP_VERSION == "v5_winner"
    assert default_min_configs_for_refine_phase("A") == REFINE_MIN_CONFIGS_BY_PHASE["A"]
    assert sum(REFINE_MIN_CONFIGS_BY_PHASE[p] for p in "ABCD") == 500
    assert "sl_min_pct" in REFINE_PHASE_A_GRID
    assert "max_conviction_mult" not in REFINE_PHASE_A_GRID


def test_iter_refine_sweep_configs_yields_anchor_and_samples():
    parent = dict(LIVE_AGENT_DEFAULT_OVERRIDES)
    parent["sl_min_pct"] = 1.4
    parent["tp_min_pct"] = 7.5
    configs = list(
        iter_refine_sweep_configs(
            "A",
            min_configs=15,
            seed=3,
            parent_overrides=parent,
        )
    )
    assert len(configs) >= 16
    names = {name for name, _ in configs}
    assert len(names) == len(configs)
    assert any(name == "refine_A_baseline_winner" for name in names)
    baseline = next(cfg for name, cfg in configs if name == "refine_A_baseline_winner")
    assert baseline["enable_dynamic_sizing"] is True
    assert baseline["enable_dynamic_barriers"] is True
    assert baseline["sl_min_pct"] == 1.4
    sampled = [cfg for name, cfg in configs if name != "refine_A_baseline_winner"]
    assert any(cfg["sl_min_pct"] != parent["sl_min_pct"] for cfg in sampled)
    for _name, overrides in configs:
        assert is_sensible_replay_config(
            overrides, reject_saturated_barriers=False
        )


def test_entry_sltp_grid_version_and_space():
    assert ENTRY_SLTP_SWEEP_VERSION == "v6_entry_sltp"
    assert "entry_sltp_v6" in SWEEP_GRID_CHOICES
    assert len(ENTRY_SLTP_SWEEP_GRID) == 17
    assert "tp_pct" not in ENTRY_SLTP_SWEEP_GRID
    assert "max_open_executors" not in ENTRY_SLTP_SWEEP_GRID
    assert sweep_space_size("entry_sltp_v6", "both_on") == _entry_sltp_space_size()
    assert default_min_configs_for_sweep_grid("entry_sltp_v6", "both_on") == 600


def test_iter_entry_sltp_sweep_configs():
    configs = list(iter_entry_sltp_sweep_configs("both_on", min_configs=25, seed=11))
    assert len(configs) >= 27
    names = {name for name, _ in configs}
    assert len(names) == len(configs)
    assert any("entry_sltp_baseline_winner" in name for name in names)
    assert any(f"entry_sltp_anchor_{CURRENT_WINNER_PRESET}" in name for name in names)
    for name, overrides in configs:
        assert overrides["max_open_executors"] == 10
        assert overrides["tp_pct"] == 5.0
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True
        assert overrides["activation_ticks"] == ENTRY_SLTP_SWEEP_FIXED_OVERRIDES["activation_ticks"]
        is_anchor = "anchor" in name or name.endswith("baseline_winner")
        assert is_sensible_replay_config(
            overrides, reject_saturated_barriers=not is_anchor
        )
    sampled = [
        cfg
        for name, cfg in configs
        if "baseline_winner" not in name and "anchor" not in name
    ]
    assert any(cfg["sl_pct"] != CURRENT_WINNER_OVERRIDES["sl_pct"] for cfg in sampled)
    adaptive_keys = [
        k for k in CURRENT_WINNER_OVERRIDES
        if k.startswith("adaptive_") and k != "adaptive_requires_flat"
    ]
    assert all(k in ENTRY_SLTP_SWEEP_GRID for k in adaptive_keys)
    executor_values = {cfg["max_open_executors"] for cfg in sampled}
    assert executor_values == {10}


def test_entry_sltp_rejects_parent_overrides():
    import pytest

    with pytest.raises(ValueError, match="parent_overrides"):
        list(
            iter_entry_sltp_sweep_configs(
                "both_on",
                min_configs=5,
                parent_overrides={"sl_pct": 2.0},
            )
        )
