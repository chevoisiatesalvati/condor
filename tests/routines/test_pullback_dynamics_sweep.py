"""Decay / flip / ATR-dynamics pullback sweep grid."""

from condor.strategy_runners.macdbb_pullback.dynamic import capital_normalized_pnl
from routines.macdbb_pullback_hl_replay.dynamics_sweep import (
    DYNAMICS_SWEEP_CONFIG_COUNT,
    DYNAMICS_SWEEP_SEED,
    HELD_OVERRIDES,
    HELD_RANDOM_ENTRY,
    LEADER_CASE_NAME,
    LIVE_CASE_NAME,
    dynamics_sweep_space_size,
    gate_dry_run_cases,
    leader_baseline_case,
    live_baseline_case,
    pullback_dynamics_cases,
)
from routines.macdbb_pullback_hl_replay.mega_sweep_runner import trade_stats


def test_space_is_several_times_larger_than_sample():
    assert dynamics_sweep_space_size() > DYNAMICS_SWEEP_CONFIG_COUNT * 2


def test_three_thousand_unique_names_live_then_leader():
    cases = pullback_dynamics_cases()
    assert len(cases) == DYNAMICS_SWEEP_CONFIG_COUNT
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    assert cases[0]["name"] == LIVE_CASE_NAME
    assert cases[1]["name"] == LEADER_CASE_NAME
    live = live_baseline_case()
    leader = leader_baseline_case()
    assert cases[0]["impulse_atr_mult"] == live["impulse_atr_mult"]
    assert cases[0]["pullback_epsilon_pct"] == 0.35
    assert cases[0]["sl_pct"] == 3.0
    assert cases[0]["tp_pct"] == 6.0
    assert cases[0]["enable_dynamic_barriers"] is False
    assert cases[0]["enable_dynamic_sizing"] is False
    assert cases[0]["enable_thesis_decay_exit"] is True
    assert cases[0]["thesis_decay_exit_hours"] == 2.0
    assert cases[1]["impulse_atr_mult"] == leader["impulse_atr_mult"]
    assert cases[1]["pullback_epsilon_pct"] == 1.25
    assert cases[1]["sl_pct"] == 3.8
    assert cases[1]["tp_pct"] == 9.0
    assert cases[1]["chase_long_bb_pos_max"] == 80.0
    assert cases[1]["enable_dynamic_barriers"] is False
    assert cases[1]["enable_dynamic_sizing"] is False


def test_random_cases_hold_leader_entry_gates():
    cases = pullback_dynamics_cases(min_configs=12, seed=DYNAMICS_SWEEP_SEED)
    for case in cases[2:]:
        for key, value in HELD_OVERRIDES.items():
            assert case[key] == value
        for key, value in HELD_RANDOM_ENTRY.items():
            assert case[key] == value
        assert case["impulse_atr_mult"] in (0.75, 1.0, 1.25)
        assert case["thesis_decay_exit_hours"] in (0.0, 1.0, 2.0, 4.0, 8.0)
        if case["thesis_decay_exit_hours"] == 0.0:
            assert case["enable_thesis_decay_exit"] is False
        else:
            assert case["enable_thesis_decay_exit"] is True


def test_seed_43_is_deterministic():
    first = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=43)
    ]
    second = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=43)
    ]
    other = [
        case["name"]
        for case in pullback_dynamics_cases(min_configs=20, seed=7)
    ]
    assert first == second
    assert first != other


def test_gate_dry_run_is_decay_off_vs_2h_vs_dynamic_on():
    cases = gate_dry_run_cases()
    assert len(cases) == 3
    decay_off, decay_2h, dynamic_on = cases
    assert decay_off["enable_thesis_decay_exit"] is False
    assert decay_off["thesis_decay_exit_hours"] == 0.0
    assert decay_off["enable_dynamic_barriers"] is False
    assert decay_2h["name"] == LEADER_CASE_NAME
    assert decay_2h["enable_thesis_decay_exit"] is True
    assert decay_2h["thesis_decay_exit_hours"] == 2.0
    assert decay_2h["enable_dynamic_barriers"] is False
    assert dynamic_on["enable_dynamic_barriers"] is True
    assert dynamic_on["enable_dynamic_sizing"] is True
    assert dynamic_on["enable_thesis_decay_exit"] is True
    assert {case["name"] for case in cases} == {
        decay_off["name"],
        LEADER_CASE_NAME,
        dynamic_on["name"],
    }


def test_capital_normalized_pnl_scales_to_100_budget():
    assert capital_normalized_pnl(40.0, 100.0) == 40.0
    assert capital_normalized_pnl(40.0, 50.0) == 80.0
    assert capital_normalized_pnl(10.0, 0.0) == 10.0


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
