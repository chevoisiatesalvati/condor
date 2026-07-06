"""Replay parity fixes: streak timing, journal ratios, barriers, open-leg carry."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import scan_barriers_between
from routines.macdbb_scanner_aggressive_hl_replay.journal import (
    _parse_barrier_events,
    _parse_decision_line,
    _parse_signals_1h,
    parse_dt,
)
from routines.macdbb_scanner_aggressive_hl_replay.metrics import compute_metrics, parsed_report_from_journal
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    JournalCreatePlan,
    JournalSignal1h,
    OpenPosition,
    StrategyReplayConfig,
    TickMeta,
)
from routines.macdbb_scanner_aggressive_hl_replay.simulator import (
    _advance_simulated_streak,
    _adaptive_entry_allowed,
    _thesis_decay_reasons,
    _update_thesis_decay_streak,
    barrier_exit_price,
)


def _snapshot(signal: str):
    return type("Snap", (), {"signal": signal})()


def _position(**overrides) -> OpenPosition:
    base = dict(
        entry_tick=1,
        entry_time=dt.datetime(2026, 6, 12, tzinfo=dt.timezone.utc),
        pair="HYPE-USD",
        side="long",
        entry_price=60.0,
        entry_class="regime_adaptive_half_size",
        entry_trigger="adaptive_long",
        notional_quote=250.0,
        entry_score_long=2.0,
        entry_score_short=0.0,
        entry_adaptive_activation_streak=0,
        entry_bb_pos_pct=8.9,
    )
    base.update(overrides)
    return OpenPosition(**base)


def test_strength_gate_uses_journal_gap_when_macd_rounds_to_zero():
    raw = (
        "HMSTR-USD:bb=39.90,macd=0.0000,sig=0.0000,hist=-0.0000,gap=0.3659,hr=0.5772,"
        "tr=bull,mom=inc,fL=0,fS=0,aL=1,aS=0,sL=1.7348,sS=0,p=0.000267"
    )
    journal_signal = _parse_signals_1h(raw)["HMSTR-USD"]
    parsed = parsed_report_from_journal(journal_signal, price=0.000267)
    config = StrategyReplayConfig(
        preset="custom",
        adaptive_min_macd_gap_ratio=0.06,
        adaptive_min_hist_ratio=0.09,
        adaptive_score_open_min=1.0,
        adaptive_score_open_min_extreme=0.75,
        adaptive_long_bb_pos_max=90,
        adaptive_strong_long_bb_pos_max=30,
    )
    metrics = compute_metrics(parsed, config, journal_signal=journal_signal)
    assert metrics["strength_gate"] is True
    assert metrics["adaptive_long_open"] is True


def test_advance_simulated_streak_waits_one_neutral_tick_before_activation():
    streak = 0
    neutral_snaps = {"BTC-USD": _snapshot("NEUTRAL")}
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=False)
    assert streak == 1
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=False)
    assert streak == 2
    streak = _advance_simulated_streak(neutral_snaps, streak, 0, opened_this_tick=True)
    assert streak == 0


def test_advance_simulated_streak_does_not_increment_with_open_position():
    streak = 2
    streak = _advance_simulated_streak(
        {"BTC-USD": _snapshot("NEUTRAL")},
        streak,
        open_position_count=1,
        opened_this_tick=False,
    )
    assert streak == 2


def test_session_parity_journal_blocks_hold_without_create_plan():
    from routines.macdbb_scanner_aggressive_hl_replay.simulator import (
        _session_parity_journal_allows_entries,
        _session_parity_pair_allowed,
    )

    config = DynamicStrategyReplayConfig(replay_mode="session_parity")
    hold_meta = TickMeta(
        tick=3,
        timestamp=dt.datetime(2026, 6, 14, tzinfo=dt.timezone.utc),
        macd_pairs=[],
        entry_class="hold",
    )
    open_meta = TickMeta(
        tick=6,
        timestamp=dt.datetime(2026, 6, 15, tzinfo=dt.timezone.utc),
        macd_pairs=[],
        entry_class="regime_adaptive_half_size",
    )
    assert not _session_parity_journal_allows_entries(hold_meta, config)
    assert _session_parity_journal_allows_entries(open_meta, config)
    plan_meta = TickMeta(
        tick=8,
        timestamp=dt.datetime(2026, 6, 18, tzinfo=dt.timezone.utc),
        macd_pairs=["PURR-USD"],
        entry_class="formal",
        create_plans={
            "PURR-USD": JournalCreatePlan(
                pair="PURR-USD", side="short", entry_class="formal"
            )
        },
    )
    assert _session_parity_journal_allows_entries(plan_meta, config)
    assert _session_parity_pair_allowed("PURR-USD", plan_meta, config)
    assert not _session_parity_pair_allowed("ONDO-USD", plan_meta, config)


def test_slot_fill_budget_expires_when_slot_left_unfilled():
    """After a single adaptive open, the next hold tick must not chain-fill."""
    meta = TickMeta(
        tick=3,
        timestamp=dt.datetime(2026, 6, 14, tzinfo=dt.timezone.utc),
        macd_pairs=[],
    )
    assert not _adaptive_entry_allowed(
        pair="NEAR-USD",
        meta=meta,
        simulated_streak=0,
        open_position_count=1,
        adaptive_slot_fill_budget=0,
        activation_ticks=1,
    )


def test_adaptive_entry_allowed_when_activation_ticks_zero():
    meta = TickMeta(
        tick=2,
        timestamp=dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc),
        macd_pairs=[],
    )
    assert _adaptive_entry_allowed(
        pair="LIT-USD",
        meta=meta,
        simulated_streak=0,
        open_position_count=1,
        adaptive_slot_fill_budget=0,
        activation_ticks=0,
    )


def test_adaptive_entry_blocked_with_open_leg_and_zero_streak():
    meta = TickMeta(
        tick=92,
        timestamp=dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc),
        macd_pairs=[],
    )
    assert not _adaptive_entry_allowed(
        pair="CRV-USD",
        meta=meta,
        simulated_streak=0,
        open_position_count=1,
        adaptive_slot_fill_budget=0,
        activation_ticks=1,
    )


def test_adaptive_entry_allows_slot_fill_after_activation_window():
    meta = TickMeta(
        tick=7,
        timestamp=dt.datetime(2026, 6, 15, tzinfo=dt.timezone.utc),
        macd_pairs=[],
    )
    assert _adaptive_entry_allowed(
        pair="ADA-USD",
        meta=meta,
        simulated_streak=0,
        open_position_count=1,
        adaptive_slot_fill_budget=1,
        activation_ticks=1,
    )


def test_adaptive_entry_allows_journal_create_plan_with_zero_streak():
    meta = TickMeta(
        tick=77,
        timestamp=dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc),
        macd_pairs=["LIT-USD"],
        create_plans={
            "LIT-USD": JournalCreatePlan(pair="LIT-USD", side="long", entry_class="regime_adaptive_half_size")
        },
    )
    assert _adaptive_entry_allowed(
        pair="LIT-USD",
        meta=meta,
        simulated_streak=0,
        open_position_count=1,
        adaptive_slot_fill_budget=0,
        activation_ticks=1,
    )


def test_thesis_decay_intact_on_aligned_neutral_low_bb():
    position = _position(thesis_decay_streak=5)
    config = StrategyReplayConfig(adaptive_long_bb_pos_max=65.0)
    metrics = {"formal_long": False, "formal_short": False}
    _update_thesis_decay_streak(
        position,
        snapshot_signal="NEUTRAL",
        metrics=metrics,
        trend="bullish",
        bb_pos_pct=56.0,
        config=config,
    )
    assert position.thesis_decay_streak == 0
    assert position.monitor_state == "thesis_intact"


def test_thesis_decay_increments_on_misaligned_neutral():
    position = _position(thesis_decay_streak=3)
    config = StrategyReplayConfig()
    metrics = {"formal_long": False, "formal_short": False}
    _update_thesis_decay_streak(
        position,
        snapshot_signal="NEUTRAL",
        metrics=metrics,
        trend="bearish",
        bb_pos_pct=20.0,
        config=config,
    )
    assert position.thesis_decay_streak == 4
    assert position.monitor_state == "thesis_decay"


def test_thesis_decay_increments_on_adaptive_bb_revert_while_trend_aligned():
    position = _position(entry_bb_pos_pct=8.9, thesis_decay_streak=0)
    config = StrategyReplayConfig(adaptive_long_bb_pos_max=65.0)
    trend_decay, bb_decay = _thesis_decay_reasons(
        position,
        trend="bullish",
        bb_pos_pct=75.0,
        config=config,
    )
    assert trend_decay is False
    assert bb_decay is True
    _update_thesis_decay_streak(
        position,
        snapshot_signal="NEUTRAL",
        metrics={"formal_long": False, "formal_short": False},
        trend="bullish",
        bb_pos_pct=75.0,
        config=config,
    )
    assert position.thesis_decay_streak == 1
    assert position.monitor_state == "thesis_decay"


def test_thesis_decay_formal_requires_drift_threshold():
    position = _position(
        entry_class="formal",
        entry_bb_pos_pct=45.0,
        thesis_decay_streak=0,
    )
    config = StrategyReplayConfig(thesis_bb_drift_pts=25.0)
    trend_decay, bb_decay = _thesis_decay_reasons(
        position,
        trend="bullish",
        bb_pos_pct=60.0,
        config=config,
    )
    assert bb_decay is False
    trend_decay, bb_decay = _thesis_decay_reasons(
        position,
        trend="bullish",
        bb_pos_pct=71.0,
        config=config,
    )
    assert bb_decay is True


def test_parse_decision_line_accepts_legacy_and_new_streak_fields():
    tick_time = {25: parse_dt("2026-06-13 04:46")}
    legacy = (
        "- **#25** (04:46) entry_class=hold neutral_pressure_streak=1 "
        "thesis_decay_streak=0 macd_pairs=HYPE-USD"
    )
    parsed_legacy = _parse_decision_line(legacy, tick_time)
    assert parsed_legacy is not None
    assert parsed_legacy.adaptive_activation_streak == 1
    assert parsed_legacy.thesis_decay_streak == 0

    modern = (
        "- **#26** (05:16) entry_class=hold adaptive_activation_streak=0 "
        "neutral_streak=2 macd_pairs=HYPE-USD tick=26"
    )
    tick_time[26] = parse_dt("2026-06-13 05:16")
    parsed_modern = _parse_decision_line(modern, tick_time)
    assert parsed_modern is not None
    assert parsed_modern.adaptive_activation_streak == 0
    assert parsed_modern.thesis_decay_streak == 2


def test_scan_barriers_between_detects_long_stop_loss():
    start = dt.datetime(2026, 6, 12, 0, 0, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 6, 12, 1, 0, tzinfo=dt.timezone.utc)
    entry = 100.0
    candles = [
        {
            "timestamp_ms": int(start.timestamp() * 1000) + 600_000,
            "open": 99.0,
            "high": 99.5,
            "low": 96.0,
            "close": 97.0,
            "volume": 1.0,
        }
    ]
    hit = scan_barriers_between(candles, start, end, "long", entry, sl_pct=2.6, tp_pct=5.0)
    assert hit is not None
    reason, exit_price = hit
    assert reason == "stop_loss_close_proxy"
    assert exit_price == entry * (1.0 - 0.026)


def test_parse_barrier_events_from_structured_field_and_narrative():
    line = (
        "barrier_close=HMSTR-USD:STOP_LOSS:pnl=-9.58 "
        "Tick #5: HMSTR hit STOP_LOSS (-$9.58)"
    )
    events = _parse_barrier_events(line)
    assert len(events) == 1
    assert events[0].pair == "HMSTR-USD"
    assert events[0].close_type == "stop_loss"
    assert events[0].pnl_quote == -9.58


def test_parse_barrier_events_ignores_open_pair_on_barrier_open_decision():
    line = (
        "position_action=barrier_close,open pair=ZEC-USD close_type=STOP_LOSS pnl=-6.69 "
        "WLD-USD LONG hit **STOP_LOSS** between ticks (PnL **-$6.69**)"
    )
    events = _parse_barrier_events(line)
    assert len(events) == 1
    assert events[0].pair == "WLD-USD"
    assert events[0].pnl_quote == -6.69


def test_parse_snapshot_barrier_closes_from_barrier_section():
    from pathlib import Path
    from routines.macdbb_scanner_aggressive_hl_replay.journal import (
        _parse_snapshot_barrier_closes,
        parse_journal_ticks,
    )

    from routines.macdbb_scanner_aggressive_hl_replay.paths import strategy_data_dir

    session_dir = strategy_data_dir("macdbb_scanner_aggressive_hl") / "sessions/session_58"
    snapshot_text = (session_dir / "snapshots" / "snapshot_59.md").read_text()
    events = _parse_snapshot_barrier_closes(snapshot_text)
    assert len(events) == 1
    assert events[0].pair == "FARTCOIN-USD"
    assert events[0].close_type == "take_profit"
    assert events[0].pnl_quote == 25.61

    tick_map = parse_journal_ticks(
        (session_dir / "journal.md").read_text(),
        session_dir=session_dir,
    )
    assert tick_map[59].barrier_closes == events


def test_reports_only_resolve_snapshot_ignores_journal_signals():
    import datetime as dt

    from routines.macdbb_scanner_aggressive_hl_replay.models import JournalSignal1h, TickMeta
    from routines.macdbb_scanner_aggressive_hl_replay.signals import resolve_snapshot
    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig

    config = DynamicStrategyReplayConfig(
        preset="custom",
        data_source="reports_only",
    )
    meta = TickMeta(
        tick=1,
        timestamp=dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc),
        macd_pairs=["BTC-USD"],
        signals_1h={
            "BTC-USD": JournalSignal1h(
                pair="BTC-USD",
                bb_pos_pct=50.0,
                macd=1.0,
                signal_line=0.5,
                histogram=0.5,
                macd_gap_ratio=0.5,
                hist_ratio=0.5,
                trend="bullish",
                momentum="increasing",
                formal_long=True,
                formal_short=False,
                adaptive_long=False,
                adaptive_short=False,
                strength_long=0.0,
                strength_short=0.0,
            )
        },
    )
    snapshot = resolve_snapshot(
        "BTC-USD",
        meta,
        {},
        config,
        {},
    )
    assert snapshot is None or snapshot.source != "journal"


def test_barrier_exit_price_caps_sl_tp_at_configured_levels():
    position = _position(entry_price=100.0, side="long", sl_pct=1.4, tp_pct=7.5)
    assert barrier_exit_price(
        position,
        "take_profit_close_proxy",
        mark_price=135.0,
        sl_pct=1.4,
        tp_pct=7.5,
    ) == 107.5
    assert barrier_exit_price(
        position,
        "stop_loss_close_proxy",
        mark_price=95.0,
        sl_pct=1.4,
        tp_pct=7.5,
    ) == 98.6
    assert barrier_exit_price(
        position,
        "thesis_decay_exit",
        mark_price=102.0,
        sl_pct=1.4,
        tp_pct=7.5,
    ) == 102.0

    short = _position(entry_price=200.0, side="short", sl_pct=2.0, tp_pct=5.0)
    assert barrier_exit_price(
        short,
        "take_profit_close_proxy",
        mark_price=180.0,
        sl_pct=2.0,
        tp_pct=5.0,
    ) == 190.0
    assert barrier_exit_price(
        short,
        "stop_loss_close_proxy",
        mark_price=210.0,
        sl_pct=2.0,
        tp_pct=5.0,
    ) == 204.0

