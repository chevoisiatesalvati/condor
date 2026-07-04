"""Tests for fork routine discovery bridging trading_agents/."""

from condor.routine_store import get_routine_store
from routines.base import assistant_routines_dir


def test_trading_agents_routines_discovered():
    store = get_routine_store()
    names = {r["name"] for r in store.list_routines()}
    assert "macdbb_scanner_aggressive_hl/macdbb_entry_policy" in names
    assert "macdbb_scanner_aggressive_hl/macdbb_signal_metrics" in names


def test_assistant_routines_dir_falls_back_to_trading_agents():
    path = assistant_routines_dir("macdbb_scanner_aggressive_hl")
    assert path.name == "routines"
    assert path.parent.name == "macdbb_scanner_aggressive_hl"
    assert "trading_agents" in str(path)
    assert path.is_dir()
