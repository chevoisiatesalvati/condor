"""Compact pullback entry-gate + SL/TP sweep grid."""

from routines.macdbb_pullback_hl_replay.entry_sltp_sweep import (
    BARRIER_PAIRS,
    ENTRY_SLTP_SWEEP_CONFIG_COUNT,
    IMPULSE_ATR_MULTS,
    PULLBACK_EPSILON_PCTS,
    PULLBACK_WINNER_PRESET,
    SWEPT_KEYS,
    pullback_entry_sltp_cases,
)


def test_grid_is_24_unique_named_cases():
    cases = pullback_entry_sltp_cases()
    assert len(cases) == 24
    assert len(cases) == ENTRY_SLTP_SWEEP_CONFIG_COUNT
    assert len({case["name"] for case in cases}) == 24
    for case in cases:
        assert set(SWEPT_KEYS) <= set(case)
        assert case["impulse_atr_mult"] in IMPULSE_ATR_MULTS
        assert case["pullback_epsilon_pct"] in PULLBACK_EPSILON_PCTS
        assert (case["sl_pct"], case["tp_pct"]) in BARRIER_PAIRS


def test_first_case_is_current_pullback_winner():
    first = pullback_entry_sltp_cases()[0]
    assert PULLBACK_WINNER_PRESET == "pullback_decay_2h_60s"
    assert first["impulse_atr_mult"] == 1.25
    assert first["pullback_epsilon_pct"] == 0.35
    assert first["sl_pct"] == 3.0
    assert first["tp_pct"] == 6.0
    assert first["name"] == "imp1.25_pb0.35_sl3_tp6"


def test_grid_includes_scanner_winner_barrier_pairs():
    pairs = {(case["sl_pct"], case["tp_pct"]) for case in pullback_entry_sltp_cases()}
    assert (3.8, 5.0) in pairs
    assert (3.8, 7.5) in pairs
    assert (4.4, 6.8) in pairs
