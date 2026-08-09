"""Unit tests for macdbb_pullback_hl engine / entry quality (slim v1)."""

from __future__ import annotations

from condor.strategy_runners.macdbb_pullback.engine import decide
from condor.strategy_runners.macdbb_pullback.entry_quality import (
    compute_impulse_metrics,
    pullback_reached,
)
from condor.strategy_runners.macdbb_pullback.metrics import compute_thesis_metrics
from condor.strategy_runners.macdbb_pullback.types import (
    EntryMeta,
    MacdbbPullbackState,
    OpenPosition,
    PullbackTickInput,
    SignalSnapshot,
)


def _signal(**overrides) -> SignalSnapshot:
    base = dict(
        pair="BTC-USDT",
        price=100.0,
        bb_pos_pct=40.0,
        bb_mid=100.0,
        bb_upper=110.0,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
        atr_pct=2.0,
        impulse_signed_body_sum_pct=0.5,
        impulse_long=False,
        impulse_short=False,
    )
    base.update(overrides)
    return SignalSnapshot(**base)


def test_thesis_symmetric_cross_requires_macd_sign():
    long_ok = _signal(bullish_cross=True, macd=1.0)
    long_bad = _signal(bullish_cross=True, macd=-1.0, trend="bearish", histogram=-0.5)
    short_ok = _signal(
        bullish_cross=False,
        bearish_cross=True,
        macd=-1.0,
        signal_line=-0.5,
        histogram=-0.5,
        trend="bearish",
        price=110.0,
        bb_pos_pct=90.0,
    )
    assert compute_thesis_metrics(long_ok)["thesis_long"] is True
    assert compute_thesis_metrics(long_bad)["thesis_long"] is False
    assert compute_thesis_metrics(short_ok)["thesis_short"] is True


def test_impulse_flag_on_large_green_bodies():
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
        {"open": 100.5, "high": 104.0, "low": 100.0, "close": 103.5},
        {"open": 103.5, "high": 108.0, "low": 103.0, "close": 107.0},
    ]
    history = [
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0} for _ in range(20)
    ]
    metrics = compute_impulse_metrics(
        history + candles,
        "long",
        lookback_bars=2,
        impulse_atr_mult=1.0,
    )
    assert metrics.signed_body_sum_pct > 0
    assert metrics.atr_pct > 0


def test_pullback_reached_long():
    assert pullback_reached("long", 100.2, 100.0, pullback_epsilon_pct=0.35)
    assert not pullback_reached("long", 102.0, 100.0, pullback_epsilon_pct=0.35)


def test_decide_immediate_entry_when_not_extended():
    signal = _signal(impulse_long=False, bb_pos_pct=40.0)
    tick = PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[signal],
        open_positions=[],
        total_amount_quote=500.0,
        strategy_params={
            "sl_pct": 3.0,
            "tp_pct": 6.0,
            "chase_long_bb_pos_max": 70.0,
        },
        max_open_executors=3,
        frequency_sec=60,
    )
    decision = decide(tick, MacdbbPullbackState())
    assert len(decision.creates) == 1
    assert decision.creates[0].entry_class == "immediate"
    assert decision.creates[0].side == "long"


def test_decide_arms_on_impulse_then_enters_on_pullback():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "chase_long_bb_pos_max": 70.0,
        "pullback_timeout_ticks": 10,
        "pullback_epsilon_pct": 0.5,
    }
    armed_signal = _signal(
        price=108.0,
        bb_pos_pct=85.0,
        bb_mid=100.0,
        impulse_long=True,
    )
    tick1 = PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[armed_signal],
        open_positions=[],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d1 = decide(tick1, MacdbbPullbackState())
    assert d1.creates == []
    assert "BTC-USDT" in d1.state.armed_by_pair

    pullback_signal = _signal(
        price=100.1,
        bb_pos_pct=45.0,
        bb_mid=100.0,
        impulse_long=False,
    )
    tick2 = PullbackTickInput(
        tick_number=2,
        tradeable_count=5,
        signals=[pullback_signal],
        open_positions=[],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d2 = decide(tick2, d1.state)
    assert len(d2.creates) == 1
    assert d2.creates[0].entry_class == "pullback"


def test_decide_expires_arm_when_thesis_dies():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "pullback_timeout_ticks": 10,
        "pullback_epsilon_pct": 0.1,
    }
    signal = _signal(price=108.0, bb_pos_pct=85.0, impulse_long=True)
    tick1 = PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[signal],
        open_positions=[],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d1 = decide(tick1, MacdbbPullbackState())
    assert "BTC-USDT" in d1.state.armed_by_pair

    dead = _signal(
        price=108.0,
        bb_pos_pct=85.0,
        bullish_cross=False,
        macd=-0.5,
        histogram=-0.2,
        trend="bearish",
        momentum="decreasing",
        impulse_long=False,
    )
    tick2 = PullbackTickInput(
        tick_number=2,
        tradeable_count=5,
        signals=[dead],
        open_positions=[],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d2 = decide(tick2, d1.state)
    assert "BTC-USDT" not in d2.state.armed_by_pair
    assert d2.creates == []


def _open_long(**overrides) -> OpenPosition:
    base = dict(
        executor_id="ex-long",
        pair="BTC-USDT",
        side="long",
        entry_class="immediate",
        pnl=0.0,
        entry_bb_pos_pct=30.0,
        filled=True,
    )
    base.update(overrides)
    return OpenPosition(**base)


def _short_thesis_signal(**overrides) -> SignalSnapshot:
    return _signal(
        bullish_cross=False,
        bearish_cross=True,
        macd=-1.0,
        signal_line=-0.5,
        histogram=-0.5,
        trend="bearish",
        momentum="increasing",
        price=110.0,
        bb_pos_pct=90.0,
        bb_upper=110.0,
        impulse_long=False,
        impulse_short=False,
        **overrides,
    )


def _neutral_bearish_signal(*, bb_pos_pct: float = 40.0) -> SignalSnapshot:
    return _signal(
        bullish_cross=False,
        bearish_cross=False,
        macd=0.1,
        signal_line=0.1,
        histogram=0.0,
        trend="bearish",
        momentum="decreasing",
        price=100.0,
        bb_pos_pct=bb_pos_pct,
        impulse_long=False,
        impulse_short=False,
    )


def test_early_exits_disabled_by_default():
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="immediate", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    tick = PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[_short_thesis_signal()],
        open_positions=[_open_long()],
        total_amount_quote=500.0,
        strategy_params={"sl_pct": 3.0, "tp_pct": 6.0},
        max_open_executors=3,
        frequency_sec=60,
    )
    decision = decide(tick, state)
    assert decision.stops == []


def test_flip_confirm_stop_when_enabled():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_flip_exit": True,
        "flip_confirm_ticks": 2,
        "flip_cooldown_hours": 1.0,
        "flip_cooldown_ticks": 2,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="immediate", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = _open_long()
    short_sig = _short_thesis_signal()

    tick1 = PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[short_sig],
        open_positions=[open_pos],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d1 = decide(tick1, state)
    assert d1.stops == []
    assert d1.state.flip_streak_by_pair.get("BTC-USDT") == 1

    tick2 = PullbackTickInput(
        tick_number=2,
        tradeable_count=5,
        signals=[short_sig],
        open_positions=[open_pos],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
    )
    d2 = decide(tick2, d1.state)
    assert len(d2.stops) == 1
    assert d2.stops[0].reason == "flip_confirm"
    assert d2.state.flip_cooldown_until_tick.get("BTC-USDT", 0) > 2


def test_thesis_decay_stop_when_enabled():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 2,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="pullback", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = _open_long(entry_class="pullback", pnl=-1.5)
    sig = _neutral_bearish_signal(bb_pos_pct=40.0)

    decision = None
    for tick_n in (1, 2, 3):
        tick = PullbackTickInput(
            tick_number=tick_n,
            tradeable_count=5,
            signals=[sig],
            open_positions=[open_pos],
            total_amount_quote=500.0,
            strategy_params=params,
            max_open_executors=3,
            frequency_sec=60,
        )
        decision = decide(tick, state if decision is None else decision.state)

    assert decision is not None
    assert any(s.reason == "thesis_decay" for s in decision.stops)


def test_thesis_bb_drift_decay_when_enabled():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 2,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="immediate", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    open_pos = _open_long(pnl=1.0)
    # NEUTRAL bullish (no trend decay) but BB drifted +25 pts → bb decay.
    sig = _signal(
        bullish_cross=False,
        bearish_cross=False,
        macd=0.2,
        signal_line=0.1,
        histogram=0.05,
        trend="bullish",
        momentum="decreasing",
        bb_pos_pct=55.0,
        impulse_long=False,
        impulse_short=False,
    )
    decision = None
    for tick_n in (1, 2):
        tick = PullbackTickInput(
            tick_number=tick_n,
            tradeable_count=5,
            signals=[sig],
            open_positions=[open_pos],
            total_amount_quote=500.0,
            strategy_params=params,
            max_open_executors=3,
            frequency_sec=60,
        )
        decision = decide(tick, state if decision is None else decision.state)
    assert decision is not None
    assert any(s.reason == "thesis_decay" for s in decision.stops)
