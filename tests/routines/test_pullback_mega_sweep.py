"""Random 2k pullback mega-sweep grid."""

from routines.macdbb_pullback_hl_replay.mega_sweep import (
    HELD_OVERRIDES,
    MEGA_SWEEP_CONFIG_COUNT,
    MEGA_SWEEP_GRID,
    SWEPT_KEYS,
    WINNER_SWEEP_VALUES,
    mega_sweep_space_size,
    pullback_mega_cases,
    winner_baseline_case,
)


def test_space_is_much_larger_than_sample():
    assert mega_sweep_space_size() > MEGA_SWEEP_CONFIG_COUNT * 5


def test_two_thousand_unique_names_with_winner_first():
    cases = pullback_mega_cases()
    assert len(cases) == MEGA_SWEEP_CONFIG_COUNT
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    winner = winner_baseline_case()
    assert cases[0]["name"] == winner["name"]
    assert cases[0]["impulse_atr_mult"] == WINNER_SWEEP_VALUES["impulse_atr_mult"]
    assert cases[0]["pullback_epsilon_pct"] == WINNER_SWEEP_VALUES["pullback_epsilon_pct"]
    assert cases[0]["sl_pct"] == WINNER_SWEEP_VALUES["sl_pct"]
    assert cases[0]["tp_pct"] == WINNER_SWEEP_VALUES["tp_pct"]
    assert cases[0]["chase_long_bb_pos_max"] == WINNER_SWEEP_VALUES["chase_long_bb_pos_max"]
    assert cases[0]["chase_short_bb_pos_min"] == WINNER_SWEEP_VALUES["chase_short_bb_pos_min"]


def test_cases_include_held_and_swept_keys():
    cases = pullback_mega_cases(min_configs=8)
    for case in cases:
        assert set(SWEPT_KEYS) <= set(case)
        for key, value in HELD_OVERRIDES.items():
            assert case[key] == value
        for key in SWEPT_KEYS:
            assert case[key] in MEGA_SWEEP_GRID[key]


def test_seed_is_deterministic():
    first = [case["name"] for case in pullback_mega_cases(min_configs=20, seed=42)]
    second = [case["name"] for case in pullback_mega_cases(min_configs=20, seed=42)]
    other = [case["name"] for case in pullback_mega_cases(min_configs=20, seed=7)]
    assert first == second
    assert first != other


def test_load_completed_results_resumes_from_json(tmp_path):
    import json

    from routines.macdbb_pullback_hl_replay.mega_sweep_runner import (
        load_completed_results,
    )

    (tmp_path / "a.json").write_text(
        json.dumps({"name": "a", "stats": {"trades": 1}}),
        encoding="utf-8",
    )
    (tmp_path / "b.json").write_text("{not json", encoding="utf-8")
    cases = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    done, pending = load_completed_results(cases, tmp_path)
    assert [row["name"] for row in done] == ["a"]
    assert [case["name"] for case in pending] == ["b", "c"]
    done_forced, pending_forced = load_completed_results(cases, tmp_path, force=True)
    assert done_forced == []
    assert [case["name"] for case in pending_forced] == ["a", "b", "c"]


def test_verify_tape_is_lazy_until_screen_leader():
    from scripts.run_macdbb_pullback_mega_sweep import (
        include_probe_windows_for_grid,
        verify_tape_policy,
    )

    assert verify_tape_policy(verify_enabled=True, coverage_ok=True) == "lazy"
    assert verify_tape_policy(verify_enabled=False, coverage_ok=True) == "disabled"
    assert verify_tape_policy(verify_enabled=True, coverage_ok=False) == "refuse"
    assert include_probe_windows_for_grid("probe") is True
    assert include_probe_windows_for_grid("dynamics") is False
    assert include_probe_windows_for_grid("entry") is False
