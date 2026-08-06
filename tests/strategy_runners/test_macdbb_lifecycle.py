"""Lifecycle tests: dedup, Step 5 exits, SL cooldown, inventory fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from condor.strategy_runners.macdbb import decide
from condor.strategy_runners.macdbb.state_store import (
    load_runner_state,
    save_runner_state,
)
from condor.strategy_runners.macdbb.types import (
    EntryMeta,
    MacdbbState,
    MacdbbTickInput,
    OpenPosition,
    SignalSnapshot,
)
from condor.strategy_runners.runner import (
    DeterministicRunner,
    _position_side_from_executor,
)


def _base_params(**overrides):
    params = {
        "adaptive_activation_ticks": 3,
        "bb_proximity_epsilon_pct": 0.5,
        "adaptive_long_bb_pos_max": 45.0,
        "adaptive_short_bb_pos_min": 55.0,
        "adaptive_strong_long_bb_pos_max": 25.0,
        "adaptive_strong_short_bb_pos_min": 75.0,
        "adaptive_min_macd_gap_ratio": 0.05,
        "adaptive_min_hist_ratio": 0.1,
        "adaptive_score_open_min": 1.5,
        "adaptive_score_open_min_extreme": 1.0,
        "adaptive_hist_sign_bonus": 0.25,
        "adaptive_hist_sign_penalty": 0.25,
        "adaptive_momentum_bonus": 0.15,
        "adaptive_momentum_penalty": 0.15,
        "sl_pct": 2.0,
        "tp_pct": 6.0,
        "enable_dynamic_sizing": False,
        "enable_dynamic_barriers": False,
        "thesis_decay_exit_ticks": 3,
        "flip_confirm_ticks": 2,
        "flip_cooldown_ticks": 2,
        "sl_symbol_cooldown_ticks": 2,
        "thesis_bb_drift_pts": 20.0,
    }
    params.update(overrides)
    return params


def _formal_long_signal(pair: str = "BTC-USD") -> SignalSnapshot:
    return SignalSnapshot(
        pair=pair,
        price=100.0,
        bb_pos_pct=30.0,
        bb_mid=98.0,
        bb_upper=102.0,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
    )


def _formal_short_signal(pair: str = "BTC-USD") -> SignalSnapshot:
    return SignalSnapshot(
        pair=pair,
        price=110.0,
        bb_pos_pct=90.0,
        bb_mid=100.0,
        bb_upper=105.0,
        macd=-1.0,
        signal_line=-0.5,
        histogram=-0.5,
        trend="bearish",
        momentum="decreasing",
        bullish_cross=False,
        bearish_cross=True,
        filter_4h_trend="bearish",
        filter_4h_pass=True,
    )


def _neutral_bearish_signal(pair: str = "BTC-USD", bb_pos_pct: float = 40.0) -> SignalSnapshot:
    """NEUTRAL (no formal) with bearish trend for long thesis decay."""
    return SignalSnapshot(
        pair=pair,
        price=100.0,
        bb_pos_pct=bb_pos_pct,
        bb_mid=98.0,
        bb_upper=102.0,
        macd=-0.2,
        signal_line=-0.1,
        histogram=-0.05,
        trend="bearish",
        momentum="increasing",
        bullish_cross=False,
        bearish_cross=False,
    )


def test_pending_unfilled_skips_thesis_monitor_but_blocks_reentry():
    params = _base_params()
    signal = _formal_long_signal("BTC-USD")
    pending = OpenPosition(
        executor_id="pending:BTC-USD",
        pair="BTC-USD",
        side="long",
        entry_class="regime_adaptive_half_size",
        entry_bb_pos_pct=20.0,
        filled=False,
    )
    tick = MacdbbTickInput(
        tick_number=5,
        scanner_regime="mature",
        tradeable_count=10,
        signals=[signal],
        open_positions=[pending],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=10,
    )
    decision = decide(tick, MacdbbState())
    assert decision.creates == []
    assert decision.stops == []
    assert decision.journal_fields.get("monitor_BTC-USD") == "pending_unfilled"


def test_open_pair_skipped_when_signal_still_true():
    params = _base_params()
    signal = _formal_long_signal("CASHCAT-USD")
    open_pos = OpenPosition(
        executor_id="ex1",
        pair="CASHCAT-USD",
        side="long",
        entry_class="regime_adaptive_half_size",
        entry_bb_pos_pct=20.0,
    )
    tick = MacdbbTickInput(
        tick_number=5,
        scanner_regime="mature",
        tradeable_count=10,
        signals=[signal],
        open_positions=[open_pos],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=10,
    )
    decision = decide(tick, MacdbbState())
    assert decision.creates == []
    assert "CASHCAT-USD" not in (decision.journal_fields.get("best_candidate") or "")


def test_inventory_unavailable_blocks_creates():
    params = _base_params()
    signal = _formal_long_signal()
    tick = MacdbbTickInput(
        tick_number=1,
        scanner_regime="mature",
        tradeable_count=5,
        signals=[signal],
        open_positions=[],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=3,
        inventory_available=False,
    )
    decision = decide(tick, MacdbbState())
    assert decision.creates == []
    assert decision.hold_reason == "inventory_unavailable"


def test_sl_barrier_close_arms_cooldown():
    params = _base_params(sl_symbol_cooldown_ticks=2)
    signal = _formal_long_signal("ETH-USD")
    state = MacdbbState()
    tick = MacdbbTickInput(
        tick_number=10,
        scanner_regime="mature",
        tradeable_count=5,
        signals=[signal],
        open_positions=[],
        barrier_closes=[
            {
                "id": "ex-sl",
                "pair": "ETH-USD",
                "side": "long",
                "close_type": "STOP_LOSS",
                "pnl": -5.0,
            }
        ],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=3,
    )
    decision = decide(tick, state)
    assert "ETH-USD" in decision.state.sl_cooldown_until_tick
    assert decision.state.sl_cooldown_until_tick["ETH-USD"] == 12
    assert decision.creates == []


def test_flip_confirm_stop_after_two_ticks():
    params = _base_params(flip_confirm_ticks=2)
    state = MacdbbState(
        entry_meta_by_pair={
            "BTC-USD": EntryMeta(
                entry_class="formal", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = OpenPosition(
        executor_id="ex-long",
        pair="BTC-USD",
        side="long",
        entry_class="formal",
        entry_bb_pos_pct=30.0,
    )
    short_sig = _formal_short_signal("BTC-USD")

    tick1 = MacdbbTickInput(
        tick_number=1,
        scanner_regime="mature",
        tradeable_count=5,
        signals=[short_sig],
        open_positions=[open_pos],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=3,
    )
    d1 = decide(tick1, state)
    assert d1.stops == []
    assert d1.state.flip_streak_by_pair.get("BTC-USD") == 1
    assert d1.state.monitor_state_by_pair.get("BTC-USD") == "flip_pending"

    tick2 = MacdbbTickInput(
        tick_number=2,
        scanner_regime="mature",
        tradeable_count=5,
        signals=[short_sig],
        open_positions=[open_pos],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=3,
    )
    d2 = decide(tick2, d1.state)
    assert len(d2.stops) == 1
    assert d2.stops[0].reason == "flip_confirm"
    # Reverse create when 4h filter passes + opposing formal present.
    assert any(c.pair == "BTC-USD" and c.side == "short" for c in d2.creates)


def test_thesis_decay_neutral_gating_and_negative_pnl_extra_tick():
    params = _base_params(thesis_decay_exit_ticks=2)
    state = MacdbbState(
        entry_meta_by_pair={
            "BTC-USD": EntryMeta(
                entry_class="formal", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = OpenPosition(
        executor_id="ex-long",
        pair="BTC-USD",
        side="long",
        entry_class="formal",
        pnl=-1.5,
        entry_bb_pos_pct=30.0,
    )
    # Bearish NEUTRAL — trend decay for long.
    sig = _neutral_bearish_signal("BTC-USD", bb_pos_pct=40.0)

    d = None
    for tick_n in (1, 2, 3):
        tick = MacdbbTickInput(
            tick_number=tick_n,
            scanner_regime="mature",
            tradeable_count=5,
            signals=[sig],
            open_positions=[open_pos],
            formal_notional_quote=200.0,
            strategy_params=params,
            max_open_executors=3,
        )
        d = decide(tick, state if d is None else d.state)

    assert d is not None
    # At limit on tick 2 with negative PnL → extra pending; tick 3 force close.
    assert any(s.reason == "thesis_decay" for s in d.stops)


def test_formal_bb_drift_thesis_decay():
    params = _base_params(thesis_decay_exit_ticks=2, thesis_bb_drift_pts=20.0)
    state = MacdbbState(
        entry_meta_by_pair={
            "BTC-USD": EntryMeta(
                entry_class="formal", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = OpenPosition(
        executor_id="ex-long",
        pair="BTC-USD",
        side="long",
        entry_class="formal",
        pnl=1.0,
        entry_bb_pos_pct=30.0,
    )
    # NEUTRAL bullish (no trend decay) but BB drifted +20 pts → bb decay.
    sig = SignalSnapshot(
        pair="BTC-USD",
        price=100.0,
        bb_pos_pct=55.0,
        bb_mid=98.0,
        bb_upper=102.0,
        macd=0.2,
        signal_line=0.1,
        histogram=0.05,
        trend="bullish",
        momentum="decreasing",
        bullish_cross=False,
        bearish_cross=False,
    )
    d = None
    for tick_n in (1, 2):
        tick = MacdbbTickInput(
            tick_number=tick_n,
            scanner_regime="mature",
            tradeable_count=5,
            signals=[sig],
            open_positions=[open_pos],
            formal_notional_quote=200.0,
            strategy_params=params,
            max_open_executors=3,
        )
        d = decide(tick, state if d is None else d.state)
    assert d is not None
    assert any(s.reason == "thesis_decay" for s in d.stops)


def test_position_side_mapping():
    assert _position_side_from_executor("BUY", fallback=None) == "long"
    assert _position_side_from_executor("SELL", fallback=None) == "short"
    assert _position_side_from_executor(1, fallback=None) == "long"
    assert _position_side_from_executor(2, fallback=None) == "short"
    assert _position_side_from_executor("LONG", fallback=None) == "long"
    assert _position_side_from_executor(None, fallback="short") == "short"
    assert _position_side_from_executor("???", fallback=None) is None


def test_pending_opens_merged_for_dedup():
    strategy = type(
        "S",
        (),
        {
            "key": "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl",
            "data_slug": "macdbb_scanner_aggressive_hl",
            "name": "macdbb",
            "description": "test",
            "slug": "macdbb_scanner_aggressive_hl",
            "require_promoted": False,
            "connector": "hyperliquid_perpetual",
            "default_config": {},
        },
    )()
    # Bypass __post_init__ session creation by constructing manually.
    runner = object.__new__(DeterministicRunner)
    runner.strategy = strategy
    runner.config = {}
    runner._macdbb_state = MacdbbState(
        entry_meta_by_pair={
            "CASHCAT-USD": EntryMeta(
                entry_class="regime_adaptive_half_size",
                entry_bb_pos_pct=15.0,
                side="long",
            )
        }
    )
    runner._pending_opens = {
        "CASHCAT-USD": {"executor_id": "pending-1", "tick": 4},
    }
    merged = runner._merge_pending_into_positions([], tick_num=5)
    assert len(merged) == 1
    assert merged[0].pair == "CASHCAT-USD"
    assert merged[0].side == "long"

    # Confirmed API position clears pending.
    confirmed = [
        OpenPosition(
            executor_id="real-1",
            pair="CASHCAT-USD",
            side="long",
            entry_class="regime_adaptive_half_size",
        )
    ]
    merged2 = runner._merge_pending_into_positions(confirmed, tick_num=5)
    assert "CASHCAT-USD" not in runner._pending_opens
    assert len(merged2) == 1
    assert merged2[0].executor_id == "real-1"


def test_state_store_roundtrip(tmp_path: Path):
    state = MacdbbState(
        adaptive_activation_streak=4,
        sl_cooldown_until_tick={"ETH-USD": 12},
        entry_meta_by_pair={
            "ETH-USD": EntryMeta(
                entry_class="formal", entry_bb_pos_pct=22.5, side="short"
            )
        },
    )
    save_runner_state(
        tmp_path,
        macdbb_state=state,
        pending_opens={"ETH-USD": {"executor_id": "x", "tick": 3}},
        last_running_ids={"x"},
        barrier_notified_ids={"old"},
    )
    loaded = load_runner_state(tmp_path)
    assert loaded["macdbb_state"].adaptive_activation_streak == 4
    assert loaded["macdbb_state"].sl_cooldown_until_tick["ETH-USD"] == 12
    assert loaded["macdbb_state"].entry_meta_by_pair["ETH-USD"].side == "short"
    assert loaded["pending_opens"]["ETH-USD"]["executor_id"] == "x"
    assert "x" in loaded["last_running_ids"]


@pytest.mark.asyncio
async def test_load_macdbb_signals_unions_extra_pairs(monkeypatch):
    from condor.strategy_runners.macdbb import market_data

    async def fake_candidates(params):
        return ["BTC-USD"], "mature", 1

    async def fake_candles(pair, interval, max_records):
        # Enough closes for MACD/BB.
        return [{"close": 100.0 + i * 0.1} for i in range(80)]

    monkeypatch.setattr(market_data, "fetch_candidate_pairs", fake_candidates)
    monkeypatch.setattr(
        "routines.lib.hl_candles.fetch_hl_candles", fake_candles
    )
    signals, regime, tradeable = await market_data.load_macdbb_signals(
        {}, extra_pairs=["CASHCAT-USD", "BTC-USD"]
    )
    pairs = {s.pair for s in signals}
    assert "BTC-USD" in pairs
    assert "CASHCAT-USD" in pairs
    assert regime == "mature"
    assert tradeable == 1
