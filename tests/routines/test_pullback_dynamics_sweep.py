"""Decay / flip / ATR-dynamics pullback sweep grid."""

from pathlib import Path

import pytest
import yaml

from condor.strategy_runners.macdbb_pullback.dynamic import (
    ANNUALIZATION_DAYS,
    annualized_cap_norm,
    capital_normalized_pnl,
    format_range_utc,
    window_days,
)
from condor.strategy_runners.macdbb_pullback.presets import invalidate_preset_cache
from routines.macdbb_pullback_hl_replay.dynamics_sweep import (
    CHASE_PAIRS,
    DECAY_HOURS_VALUES,
    DRIFT_VALUES,
    DYNAMICS_SWEEP_CONFIG_COUNT,
    EPSILON_VALUES,
    GRACE_VALUES,
    HELD_OVERRIDES,
    HELD_RANDOM_ENTRY,
    PROBE_ATR_PERIODS,
    PROBE_LOOKBACK_BARS,
    SL_VALUES,
    TIMEOUT_HOURS_VALUES,
    TP_VALUES,
    current_lead_baseline_case,
    current_lead_sweep_values,
    dynamics_sweep_out_dir,
    dynamics_sweep_preset,
    dynamics_sweep_seed,
    dynamics_sweep_space_size,
    gate_dry_run_cases,
    held_random_entry,
    lookback_atr_probe_cases,
    lookback_atr_probe_out_dir,
    lookback_atr_probe_space_size,
    pullback_dynamics_cases,
)
from routines.macdbb_pullback_hl_replay.mega_sweep_runner import trade_stats
from routines.macdbb_pullback_hl_replay.sweep_automation import PRESET_NAME_PREFIX


@pytest.fixture(autouse=True)
def _clear_preset_cache():
    invalidate_preset_cache()
    yield
    invalidate_preset_cache()


def _install_lead(tmp_path: Path, lead_n: int, **overrides) -> Path:
    dest = tmp_path / "strategies" / "macdbb_pullback_hl"
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{PRESET_NAME_PREFIX}{lead_n:03d}"
    payload = {
        "impulse_atr_mult": 1.75,
        "pullback_epsilon_pct": 0.9,
        "sl_pct": 4.4,
        "tp_pct": 7.5,
        "enable_dynamic_barriers": True,
        "ref_volatility_pct": 1.5,
        "sl_vol_exponent": 1.0,
        "tp_vol_exponent": 0.5,
        "sl_min_pct": 2.5,
        "sl_max_pct": 6.0,
        "tp_min_pct": 6.0,
        "tp_max_pct": 12.0,
        "enable_dynamic_sizing": False,
        "chase_long_bb_pos_max": 70.0,
        "chase_short_bb_pos_min": 40.0,
        "enable_flip_exit": False,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_hours": 4.0,
        "thesis_bb_drift_pts": 30.0,
    }
    payload.update(overrides)
    dest.joinpath("presets.yaml").write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "agent_strategy_preset_names": ["pullback_decay_2h_60s", name],
                "dynamic_preset_overrides": {
                    "pullback_decay_2h_60s": {"sl_pct": 3.0, "tp_pct": 6.0},
                    name: payload,
                },
            }
        ),
        encoding="utf-8",
    )
    invalidate_preset_cache()
    return dest / "presets.yaml"


def test_space_is_larger_than_sample():
    decay_on = len(DECAY_HOURS_VALUES) - 1
    expected = (
        (1 + decay_on * len(GRACE_VALUES) * len(DRIFT_VALUES))
        * len(EPSILON_VALUES)
        * len(TP_VALUES)
        * len(CHASE_PAIRS)
        * len(SL_VALUES)
        * len(TIMEOUT_HOURS_VALUES)
    )
    assert dynamics_sweep_space_size() == expected
    assert expected > DYNAMICS_SWEEP_CONFIG_COUNT


def test_latest_lead_drives_preset_seed_out_dir_and_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_STRATEGIES_DIR", str(tmp_path / "strategies"))
    presets_path = _install_lead(tmp_path, 12, impulse_atr_mult=1.75)
    assert dynamics_sweep_preset(presets_path=presets_path) == f"{PRESET_NAME_PREFIX}012"
    assert dynamics_sweep_seed(presets_path=presets_path) == 43 + (12 - 7)
    assert dynamics_sweep_out_dir(presets_path=presets_path).endswith("lead_012")
    values = current_lead_sweep_values(presets_path=presets_path)
    assert values["impulse_atr_mult"] == 1.75
    assert values["pullback_epsilon_pct"] == 0.9
    assert values["enable_dynamic_barriers"] is True
    case = current_lead_baseline_case(presets_path=presets_path)
    assert case["impulse_atr_mult"] == 1.75
    assert case["name"] == current_lead_baseline_case(presets_path=presets_path)["name"]
    held = held_random_entry(presets_path=presets_path)
    assert held["impulse_atr_mult"] == 1.75
    assert "sl_pct" not in held
    assert "pullback_epsilon_pct" not in held
    cases = pullback_dynamics_cases(
        min_configs=8,
        presets_path=presets_path,
    )
    assert cases[0]["impulse_atr_mult"] == 1.75
    assert cases[0]["name"] == case["name"]
    assert cases[0]["sl_pct"] == 4.4
    for row in cases[1:]:
        assert row["impulse_atr_mult"] == 1.75
        assert row["sl_pct"] in SL_VALUES
        assert row["enable_flip_exit"] is False
        assert row["enable_dynamic_sizing"] is False
        assert row["enable_dynamic_barriers"] is False
        assert row["pullback_epsilon_pct"] in EPSILON_VALUES


def test_dynamics_cli_defaults_follow_latest_lead(monkeypatch):
    import sys

    from scripts.run_macdbb_pullback_mega_sweep import (
        _parse_args,
        _resolve_grid_defaults,
    )

    monkeypatch.setattr(sys, "argv", ["prog", "--grid", "dynamics"])
    args = _resolve_grid_defaults(_parse_args())
    assert args.seed == dynamics_sweep_seed()
    assert args.out_dir == dynamics_sweep_out_dir()
    assert args.min_configs == DYNAMICS_SWEEP_CONFIG_COUNT


def test_dynamics_cli_overrides_configs_and_seed(monkeypatch):
    import sys

    from scripts.run_macdbb_pullback_mega_sweep import (
        _parse_args,
        _resolve_grid_defaults,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--grid", "dynamics", "--min-configs", "10000", "--seed", "46"],
    )
    args = _resolve_grid_defaults(_parse_args())
    assert args.min_configs == 10000
    assert args.seed == 46
    assert args.out_dir == dynamics_sweep_out_dir()


def test_dynamics_cli_configs_alias_overrides_default(monkeypatch):
    import sys

    from scripts.run_macdbb_pullback_mega_sweep import (
        _parse_args,
        _resolve_grid_defaults,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--grid", "dynamics", "--configs", "10000"],
    )
    args = _resolve_grid_defaults(_parse_args())
    assert args.min_configs == 10000
    assert args.seed == dynamics_sweep_seed()


def test_probe_cli_defaults_follow_latest_lead(monkeypatch):
    import sys

    from scripts.run_macdbb_pullback_mega_sweep import (
        _parse_args,
        _resolve_grid_defaults,
    )

    monkeypatch.setattr(sys, "argv", ["prog", "--grid", "probe"])
    args = _resolve_grid_defaults(_parse_args())
    assert args.seed == dynamics_sweep_seed()
    assert args.out_dir == lookback_atr_probe_out_dir()
    assert args.min_configs == lookback_atr_probe_space_size()


def test_current_lead_baseline_uses_generated_case_name():
    generated = current_lead_baseline_case()
    values = current_lead_sweep_values()
    assert generated["impulse_atr_mult"] == values["impulse_atr_mult"]
    assert generated["enable_dynamic_barriers"] == values["enable_dynamic_barriers"]
    assert generated["name"]


def test_default_grid_unique_names_start_from_current_lead():
    cases = pullback_dynamics_cases()
    assert len(cases) == DYNAMICS_SWEEP_CONFIG_COUNT
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    current = current_lead_baseline_case()
    assert cases[0]["name"] == current["name"]
    for key, value in current_lead_sweep_values().items():
        if key in cases[0]:
            assert cases[0][key] == value


def test_oversize_config_count_stops_at_unique_space():
    space = dynamics_sweep_space_size()
    cases = pullback_dynamics_cases(min_configs=space + 2000, seed=46)
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    assert len(cases) <= space + 1
    assert len(cases) >= space


def test_random_cases_hold_lead_impulse_and_v2_axes():
    cases = pullback_dynamics_cases(min_configs=12)
    held = held_random_entry()
    assert set(held) <= set(HELD_RANDOM_ENTRY)
    for case in cases[1:]:
        for key, value in HELD_OVERRIDES.items():
            assert case[key] == value
        for key, value in held.items():
            assert case[key] == value
        assert case["enable_flip_exit"] is False
        assert case["enable_dynamic_sizing"] is False
        assert case["enable_dynamic_barriers"] is False
        assert case["pullback_epsilon_pct"] in EPSILON_VALUES
        assert case["tp_pct"] in TP_VALUES
        assert case["sl_pct"] in SL_VALUES
        assert case["pullback_timeout_hours"] in TIMEOUT_HOURS_VALUES
        assert (
            case["chase_long_bb_pos_max"],
            case["chase_short_bb_pos_min"],
        ) in CHASE_PAIRS
        assert case["thesis_decay_exit_hours"] in DECAY_HOURS_VALUES
        if case["thesis_decay_exit_hours"] == 0.0:
            assert case["enable_thesis_decay_exit"] is False
            assert case["thesis_decay_negative_grace_minutes"] == 30.0
            assert case["thesis_bb_drift_pts"] == 20.0
        else:
            assert case["enable_thesis_decay_exit"] is True
            assert case["thesis_decay_negative_grace_minutes"] in GRACE_VALUES
            assert case["thesis_bb_drift_pts"] in DRIFT_VALUES


def test_default_seed_is_deterministic_and_avoids_prior_draws():
    seed = dynamics_sweep_seed()
    first = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=seed)
    ]
    second = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=seed)
    ]
    prior = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=43)
    ]
    assert first == second
    if seed != 43:
        assert first[1:] != prior[1:]


def test_gate_dry_run_is_decay_off_vs_current_lead_vs_dynamic_on():
    cases = gate_dry_run_cases()
    assert len(cases) == 3
    decay_off, current, dynamic_on = cases
    lead = current_lead_baseline_case()
    assert decay_off["enable_thesis_decay_exit"] is False
    assert decay_off["thesis_decay_exit_hours"] == 0.0
    assert current["name"] == lead["name"]
    assert current["enable_thesis_decay_exit"] == lead["enable_thesis_decay_exit"]
    assert dynamic_on["enable_dynamic_barriers"] is True
    assert dynamic_on["enable_dynamic_sizing"] is False
    assert {case["name"] for case in cases} == {
        decay_off["name"],
        lead["name"],
        dynamic_on["name"],
    }


def test_lookback_atr_probe_is_nine_cells_on_current_lead():
    cases = lookback_atr_probe_cases()
    lead = current_lead_baseline_case()
    assert len(cases) == lookback_atr_probe_space_size()
    assert cases[0]["name"] == lead["name"]
    assert cases[0]["impulse_lookback_bars"] == 2
    assert cases[0]["atr_period"] == 14
    expected_other = {
        f"lb{lookback}_atr{atr_period}"
        for lookback in PROBE_LOOKBACK_BARS
        for atr_period in PROBE_ATR_PERIODS
        if not (lookback == 2 and atr_period == 14)
    }
    others = cases[1:]
    assert {case["name"] for case in others} == expected_other
    skip_keys = {"name", "impulse_lookback_bars", "atr_period"}
    for case in others:
        assert case["impulse_lookback_bars"] in PROBE_LOOKBACK_BARS
        assert case["atr_period"] in PROBE_ATR_PERIODS
        for key, value in lead.items():
            if key in skip_keys:
                continue
            assert case[key] == value


def test_capital_normalized_pnl_scales_to_100_budget():
    assert capital_normalized_pnl(40.0, 100.0) == 40.0
    assert capital_normalized_pnl(40.0, 50.0) == 80.0
    assert capital_normalized_pnl(10.0, 0.0) == 10.0


def test_annualized_cap_norm_scales_by_window_days():
    assert window_days("2026-07-18T00:00:00Z", "2026-08-17T00:00:00Z") == 30.0
    assert annualized_cap_norm(10.0, window_days=30.0) == 10.0 * (ANNUALIZATION_DAYS / 30.0)
    assert annualized_cap_norm(10.0, window_days=ANNUALIZATION_DAYS) == 10.0
    assert annualized_cap_norm(10.0, window_days=0.0) == 10.0
    assert window_days("", "2026-08-17T00:00:00Z") == 0.0


def test_format_range_utc_drops_calendar_day_times():
    assert (
        format_range_utc("2026-07-18T00:00:00Z", "2026-08-17T23:59:59Z")
        == "18 Jul → 17 Aug 2026"
    )
    assert (
        format_range_utc("2025-08-17T00:00:00Z", "2026-08-17T23:59:59Z")
        == "17 Aug 2025 → 17 Aug 2026"
    )
    assert (
        format_range_utc("2026-07-18T14:30:00Z", "2026-08-17T09:05:00Z")
        == "18 Jul 2026 14:30 UTC → 17 Aug 2026 09:05 UTC"
    )
    assert format_range_utc("", "") == ""


def test_trade_stats_include_avg_notional_and_cap_norm():
    class _Trade:
        def __init__(self, pnl, notional, sl=3.8, tp=9.0):
            self.pnl_quote = pnl
            self.notional_quote = notional
            self.exit_reason = "thesis_decay"
            self.entry_class = "immediate"
            self.hold_ticks = 10
            self.return_pct = pnl / notional * 100.0
            self.sl_pct_used = sl
            self.tp_pct_used = tp

    stats = trade_stats([_Trade(10.0, 50.0), _Trade(-2.0, 150.0)])
    assert stats["avg_notional"] == 100.0
    assert stats["net_pnl_quote"] == 8.0
    assert stats["capital_normalized_pnl"] == 8.0
    assert stats["thesis_decay"] == 2
