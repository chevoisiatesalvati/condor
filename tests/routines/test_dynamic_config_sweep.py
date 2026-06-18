"""Tests for dynamic strategy replay mega sweep helpers."""

from routines.macdbb_replay.config_sweep import (
    CURRENT_WINNER_OVERRIDES,
    DYNAMIC_MODE_PRESETS,
    MEGA_GRID_FIXED_OVERRIDES,
    MEGA_SWEEP_GRID,
    _dynamic_grid_for_mode,
    _dynamic_sweep_base,
    is_sensible_replay_config,
    iter_mega_dynamic_sweep_configs,
)
from routines.macdbb_replay.presets import (
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
    from routines.macdbb_replay.presets import (
        FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL as PRESET_BENCHMARK,
    )

    assert FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL == PRESET_BENCHMARK
    assert PRESET_BENCHMARK > 0


def test_is_sensible_replay_config_rejects_inverted_bounds():
    assert is_sensible_replay_config({"tp_min_pct": 3.0, "tp_max_pct": 15.0})
    assert not is_sensible_replay_config({"tp_min_pct": 7.0, "tp_max_pct": 6.0})
    assert not is_sensible_replay_config({"sl_min_pct": 5.0, "sl_max_pct": 3.0})


def test_current_winner_anchor_has_sensible_barrier_bounds():
    assert is_sensible_replay_config(CURRENT_WINNER_OVERRIDES)
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
    assert len(configs) >= 32
    assert any("anchor_hl_dynamic_mega_sweep_best" in name for name, _ in configs)
    executor_values = {
        overrides["max_open_executors"]
        for _name, overrides in configs
        if _name.startswith("dyn_both_on_mega_")
    }
    assert len(executor_values) > 1
    for _name, overrides in configs:
        assert overrides["activation_ticks"] == 0
        assert overrides["enable_dynamic_sizing"] is True
        assert overrides["enable_dynamic_barriers"] is True
        assert is_sensible_replay_config(overrides)


def test_hl_dynamic_mega_sweep_best_preset_matches_winner_base():
    from routines.macdbb_replay.models import DynamicStrategyReplayConfig

    expected = {**CURRENT_WINNER_OVERRIDES, **DYNAMIC_MODE_PRESETS["both_on"]}
    resolved = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_mega_sweep_best")
    ).model_dump()
    skip = {"write_csv", "report_label", "compare_journal_flags", "preset"}
    for key, value in expected.items():
        if key in resolved and key not in skip:
            assert resolved[key] == value, f"mismatch on {key}: {resolved[key]!r} != {value!r}"

    assert (
        DYNAMIC_PRESET_OVERRIDES["hl_dynamic_mega_sweep_best"]["max_open_executors"] == 10
    )
