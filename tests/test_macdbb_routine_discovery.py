"""Tests for MACDBB routine discovery in the canonical agents layout."""

from condor.routine_store import get_routine_store
from routines.base import assistant_routines_dir


def test_agents_routines_discovered():
    store = get_routine_store()
    names = {r["name"] for r in store.list_routines()}
    assert "macdbb_scanner_aggressive_hl/macdbb_entry_policy" in names
    assert "macdbb_scanner_aggressive_hl/macdbb_signal_metrics" in names
    assert "macdbb_scanner_aggressive_hl/hyperliquid_market_scanner" in names
    assert "macdbb_scanner_aggressive_hl/macd_bb_analysis" in names


def test_assistant_routines_dir_uses_agents_layout():
    path = assistant_routines_dir("macdbb_scanner_aggressive_hl")
    assert path.name == "routines"
    assert path.parent.name == "macdbb_scanner_aggressive_hl"
    assert "agents" in str(path)
    assert path.is_dir()


def test_backtest_routine_fields_include_group_metadata():
    store = get_routine_store()
    routine = next(
        r for r in store.list_routines() if r["name"] == "macdbb_scanner_aggressive_hl_backtest"
    )
    assert routine.get("groups")
    fields = routine["fields"]
    assert fields["preset"]["group"] == "Preset & mode"
    assert sum(1 for meta in fields.values() if meta.get("group")) > 20
