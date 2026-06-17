"""Tests for dynamic strategy replay mega sweep helpers."""

from routines.macdbb_replay.config_sweep import (
    DYNAMIC_MODE_PRESETS,
    _dynamic_grid_for_mode,
    _dynamic_sweep_base,
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


def test_hl_dynamic_mega_sweep_best_preset_matches_rank_one_base():
    from routines.macdbb_replay.config_sweep import _dynamic_sweep_base, _merge
    from routines.macdbb_replay.models import DynamicStrategyReplayConfig
    from routines.macdbb_replay.presets import (
        DYNAMIC_PRESET_OVERRIDES,
        resolve_config_with_preset,
    )

    expected = _merge(
        _dynamic_sweep_base("sizing_only"),
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
