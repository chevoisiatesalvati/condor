"""Tests for dynamic strategy replay mega sweep helpers."""

from routines.macdbb_replay.config_sweep import (
    BOTH_ON_V3_WINNER_OVERRIDES,
    DYNAMIC_MODE_PRESETS,
    _dynamic_grid_for_mode,
    _dynamic_sweep_base,
    build_dynamic_refine_sweep_configs,
    is_sensible_replay_config,
    iter_mega_dynamic_sweep_configs,
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

    barrier_grid = _dynamic_grid_for_mode("barriers_only")
    assert "sl_vol_exponent" in barrier_grid
    assert "strength_mult_per_unit" not in barrier_grid


def test_iter_mega_dynamic_sweep_configs_yields_enough_samples():
    configs = list(
        iter_mega_dynamic_sweep_configs("both_keep_journal", min_configs=20, seed=7)
    )
    assert len(configs) >= 23
    names = {name for name, _ in configs}
    assert len(names) == len(configs)
    assert any(name.startswith("dyn_both_keep_journal_baseline") for name in names)
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


def test_v2_grid_differs_from_v1():
    from routines.macdbb_replay.config_sweep import (
        MEGA_SWEEP_GRID,
        MEGA_SWEEP_GRID_V2,
        _dynamic_grid_for_mode,
    )

    assert MEGA_SWEEP_GRID_V2["sl_pct"] != MEGA_SWEEP_GRID["sl_pct"]
    v2_sizing = _dynamic_grid_for_mode("sizing_only", "v2")
    v1_sizing = _dynamic_grid_for_mode("sizing_only", "v1")
    assert v2_sizing["strength_mult_per_unit"] != v1_sizing["strength_mult_per_unit"]


def test_capital_normalized_pnl_scales_by_avg_notional():
    from routines.macdbb_replay.presets import capital_normalized_pnl

    # Dynamic: $190 raw at avg $200 vs fixed benchmark avg $300 → $285
    assert capital_normalized_pnl(190.0, 200.0, 300.0) == 285.0
    # Fixed at benchmark avg → unchanged
    assert capital_normalized_pnl(200.0, 300.0, 300.0) == 200.0


def test_sweep_and_routine_share_capital_benchmark_constant():
    from routines.macdbb_replay.config_sweep import FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL
    from routines.macdbb_replay.presets import (
        FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL as PRESET_BENCHMARK,
    )

    assert FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL == PRESET_BENCHMARK
    assert PRESET_BENCHMARK > 0


def test_is_sensible_replay_config_rejects_inverted_bounds():
    assert is_sensible_replay_config({"tp_min_pct": 3.0, "tp_max_pct": 15.0})
    assert not is_sensible_replay_config({"tp_min_pct": 7.0, "tp_max_pct": 6.0})
    assert not is_sensible_replay_config({"sl_min_pct": 5.0, "sl_max_pct": 3.0})


def test_both_on_v3_winner_anchor_has_sensible_barrier_bounds():
    assert is_sensible_replay_config(BOTH_ON_V3_WINNER_OVERRIDES)
    assert BOTH_ON_V3_WINNER_OVERRIDES["tp_min_pct"] < BOTH_ON_V3_WINNER_OVERRIDES["tp_max_pct"]


def test_build_dynamic_refine_sweep_configs_grid_size():
    configs = build_dynamic_refine_sweep_configs("both_on")
    assert len(configs) == 897
    names = {name for name, _ in configs}
    assert len(names) == len(configs)
    assert "refine_anchor_v3_winner" in names
    for _name, overrides in configs:
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True
        assert overrides["activation_ticks"] == 1
        assert is_sensible_replay_config(overrides)


def test_v3_grid_fixed_overrides_and_no_swept_noise():
    from routines.macdbb_replay.config_sweep import (
        MEGA_SWEEP_GRID_V3,
        _mega_grid_fixed_overrides,
        _dynamic_grid_for_mode,
    )

    assert "activation_ticks" not in MEGA_SWEEP_GRID_V3
    assert "ignore_adaptive_4h_filter" not in MEGA_SWEEP_GRID_V3
    assert 3.0 in MEGA_SWEEP_GRID_V3["sl_pct"]
    assert 8.0 in MEGA_SWEEP_GRID_V3["tp_pct"]
    assert 4 in MEGA_SWEEP_GRID_V3["thesis_decay_exit_ticks"]
    assert _mega_grid_fixed_overrides("v3") == {
        "activation_ticks": 0,
        "ignore_adaptive_4h_filter": True,
    }
    both_on = _dynamic_grid_for_mode("both_on", "v3")
    assert "sl_vol_exponent" in both_on
    assert "activation_ticks" not in both_on


def test_v4_grid_values_disjoint_from_v1_v2_v3():
    from routines.macdbb_replay.config_sweep import (
        MEGA_BARRIER_GRID_V4,
        MEGA_SIZING_GRID_V4,
        MEGA_SWEEP_GRID_V4,
        _prior_mega_grid_unions,
    )

    # Booleans / categorical toggles are intentionally re-swept each grid version.
    skip_keys = {
        "vol_inverse_sizing",
        "ignore_journal_barriers_when_dynamic",
        "volatility_source",
    }
    prior = _prior_mega_grid_unions()
    for grid in (MEGA_SWEEP_GRID_V4, MEGA_SIZING_GRID_V4, MEGA_BARRIER_GRID_V4):
        for key, values in grid.items():
            if key in skip_keys:
                continue
            overlap = prior.get(key, set()) & set(values)
            assert not overlap, f"v4 {key} overlaps prior grids: {overlap}"


def test_mega_dynamic_v4_both_on_samples():
    configs = list(
        iter_mega_dynamic_sweep_configs("both_on", min_configs=30, seed=19, grid_version="v4")
    )
    assert len(configs) >= 34
    assert any("anchor_v5_both_on_top" in name for name, _ in configs)
    for _name, overrides in configs:
        assert overrides["activation_ticks"] == 0
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True
        assert is_sensible_replay_config(overrides)


def test_hl_dynamic_mega_sweep_best_preset_matches_v6_winner():
    from routines.macdbb_replay.config_sweep import _dynamic_sweep_base, _merge
    from routines.macdbb_replay.models import DynamicStrategyReplayConfig
    from routines.macdbb_replay.presets import (
        DYNAMIC_PRESET_OVERRIDES,
        resolve_config_with_preset,
    )

    expected = _merge(
        _dynamic_sweep_base("both_on"),
        **DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"],
        preset="hl_dynamic_mega_sweep_best",
    )
    resolved = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_mega_sweep_best")
    ).model_dump()
    skip = {"write_csv", "report_label", "compare_journal_flags"}
    for key, value in expected.items():
        if key in resolved and key not in skip:
            assert resolved[key] == value, f"mismatch on {key}: {resolved[key]!r} != {value!r}"
