"""Tests for live journal leg extraction."""

from __future__ import annotations

from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.live_ledger import (
    compare_legs,
    extract_live_legs,
    parse_journal_live_pnl,
)

SESSION_60 = (
    Path(__file__).resolve().parents[2]
    / "trading_agents/macdbb_scanner_aggressive_hl/sessions/session_60"
)


def test_parse_journal_live_pnl_session_60():
    pnl = parse_journal_live_pnl(SESSION_60 / "journal.md")
    assert pnl == -75.88


def test_extract_live_legs_session_60_core_legs():
    legs = extract_live_legs(SESSION_60 / "journal.md", SESSION_60)
    by_key = {(leg.pair, leg.entry_tick, leg.side): leg for leg in legs}
    assert len(legs) == 11
    assert by_key[("PURR-USD", 24, "long")].exit_tick == 26
    assert by_key[("PURR-USD", 26, "long")].exit_reason == "session_end_proxy"
    assert by_key[("XPL-USD", 23, "long")].pnl_quote == -5.43
    assert by_key[("ADA-USD", 1, "short")].exit_reason == "flip_confirmed"


def test_compare_legs_matches_by_entry_tick_not_index_order():
    from routines.macdbb_scanner_aggressive_hl_replay.live_ledger import LegRecord

    live = [
        LegRecord(1, 5, 7, "AERO-USD", "long", "adaptive_long", "x", "stop_loss_close_proxy", -1.0),
        LegRecord(2, 2, 4, "LIT-USD", "long", "adaptive_long", "x", "stop_loss_close_proxy", -2.0),
    ]
    sim = [
        LegRecord(1, 2, 4, "LIT-USD", "long", "adaptive_long", "x", "stop_loss_close_proxy", -2.0),
        LegRecord(2, 5, 7, "AERO-USD", "long", "adaptive_long", "x", "stop_loss_close_proxy", -1.0),
    ]
    rows = compare_legs(live, sim)
    assert len(rows) == 2
    assert all(row.entry_tick_match and row.exit_tick_match for row in rows if row.live and row.sim)


SESSION_58 = (
    Path(__file__).resolve().parents[2]
    / "trading_agents/macdbb_scanner_aggressive_hl/sessions/session_58"
)


def test_extract_live_legs_session_58_narrative_opens():
    legs = extract_live_legs(SESSION_58 / "journal.md", SESSION_58)
    by_key = {(leg.pair, leg.entry_tick): leg for leg in legs}
    assert ("FARTCOIN-USD", 4) in by_key
    assert ("ENA-USD", 6) in by_key
    assert ("ADA-USD", 7) in by_key
    assert ("FARTCOIN-USD", 8) in by_key
    assert ("LIT-USD", 77) in by_key
    assert ("LINK-USD", 88) in by_key
    assert by_key[("ADA-USD", 7)].exit_tick == 27
    assert by_key[("ADA-USD", 7)].exit_reason == "thesis_decay_exit"
    assert by_key[("ENA-USD", 6)].exit_tick == 86
