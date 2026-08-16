"""MACDBB no longer ships agent-local LLM routine wrappers."""

from condor.routine_store import get_routine_store
from routines.base import assistant_routines_dir


def test_macdbb_agent_local_routines_removed():
    store = get_routine_store()
    names = {r["name"] for r in store.list_routines()}
    assert "macdbb_scanner_aggressive_hl/macdbb_entry_policy" not in names
    assert "macdbb_scanner_aggressive_hl/macdbb_signal_metrics" not in names
    assert "macdbb_scanner_aggressive_hl_backtest" in names


def test_assistant_routines_dir_has_no_macdbb_agents_tree():
    path = assistant_routines_dir("macdbb_scanner_aggressive_hl")
    assert not path.is_dir()


def test_backtest_routine_fields_include_group_metadata():
    store = get_routine_store()
    routine = next(
        r for r in store.list_routines() if r["name"] == "macdbb_scanner_aggressive_hl_backtest"
    )
    assert routine.get("groups")
    fields = routine["fields"]
    assert fields["preset"]["group"] == "Preset & mode"
    assert sum(1 for meta in fields.values() if meta.get("group")) > 20


def test_pullback_backtest_routine_fields_include_group_metadata():
    store = get_routine_store()
    routine = next(
        r for r in store.list_routines() if r["name"] == "macdbb_pullback_hl_backtest"
    )
    assert routine["groups"][0] == "Preset & data"
    assert routine["expanded_groups"] == ["Preset & data", "Timeline"]
    fields = routine["fields"]
    assert fields["preset"]["widget"] == "select"
    assert fields["snapshot_dir"]["options_from"] == "replay_snapshot_dirs"
    assert fields["strategy_params"]["hidden"] is True
