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
        "thesis_decay_negative_grace_ticks": 1,
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


def _decay_decide(tick_n, signal, pos, params, state, *, frequency_sec=60):
    tick = PullbackTickInput(
        tick_number=tick_n,
        tradeable_count=5,
        signals=[signal],
        open_positions=[pos],
        total_amount_quote=500.0,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=frequency_sec,
    )
    return decide(tick, state)


def test_minutes_to_ticks_preserves_wall_clock():
    from condor.strategy_runners.macdbb_pullback.params import minutes_to_ticks

    assert minutes_to_ticks(30.0, 60) == 30
    assert minutes_to_ticks(30.0, 1800) == 1
    assert minutes_to_ticks(0.0, 60) == 0
    assert minutes_to_ticks(5.0, 1800) == 0


def test_resolve_effective_params_converts_grace_as_minutes():
    from condor.agents.strategy_configs.registry import resolve_effective_strategy_params

    at_60 = resolve_effective_strategy_params(
        "macdbb_pullback_hl",
        {"thesis_decay_negative_grace_minutes": 30},
        60,
    )
    assert at_60["thesis_decay_negative_grace_ticks"] == 30
    at_1800 = resolve_effective_strategy_params(
        "macdbb_pullback_hl",
        {"thesis_decay_negative_grace_minutes": 30},
        1800,
    )
    assert at_1800["thesis_decay_negative_grace_ticks"] == 1


def test_decay_grace_waits_red_then_closes():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 1,
        "thesis_decay_negative_grace_ticks": 2,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="pullback", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    sig = _neutral_bearish_signal()
    pos = _open_long(entry_class="pullback", pnl=-1.5)
    d1 = _decay_decide(1, sig, pos, params, state)
    assert d1.stops == []
    assert d1.state.thesis_decay_grace_until_tick["BTC-USDT"] == 3
    d2 = _decay_decide(2, sig, pos, params, d1.state)
    assert d2.stops == []
    d3 = _decay_decide(3, sig, pos, params, d2.state)
    assert any(s.reason == "thesis_decay" for s in d3.stops)
    assert "BTC-USDT" not in d3.state.thesis_decay_grace_until_tick


def test_decay_grace_closes_immediately_when_pnl_turns_green():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 1,
        "thesis_decay_negative_grace_ticks": 30,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="pullback", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    sig = _neutral_bearish_signal()
    d1 = _decay_decide(1, sig, _open_long(entry_class="pullback", pnl=-1.5), params, state)
    assert d1.stops == []
    d2 = _decay_decide(
        2, sig, _open_long(entry_class="pullback", pnl=0.4), params, d1.state
    )
    assert any(s.reason == "thesis_decay" for s in d2.stops)


def test_decay_grace_clears_when_thesis_returns():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 1,
        "thesis_decay_negative_grace_ticks": 30,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="immediate", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    d1 = _decay_decide(
        1, _neutral_bearish_signal(), _open_long(pnl=-1.5), params, state
    )
    assert d1.stops == []
    assert "BTC-USDT" in d1.state.thesis_decay_grace_until_tick
    long_sig = _signal(
        bullish_cross=True,
        bearish_cross=False,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        trend="bullish",
        momentum="increasing",
        impulse_long=False,
        impulse_short=False,
    )
    d2 = _decay_decide(2, long_sig, _open_long(pnl=-1.5), params, d1.state)
    assert d2.stops == []
    assert "BTC-USDT" not in d2.state.thesis_decay_grace_until_tick
    assert d2.state.thesis_decay_by_pair.get("BTC-USDT", 0) == 0


def test_legacy_extra_pending_migrates_to_one_tick_grace():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 1,
        "thesis_decay_negative_grace_ticks": 30,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="pullback", entry_bb_pos_pct=30.0, side="long"
            )
        },
        thesis_decay_extra_pending_by_pair={"BTC-USDT": True},
    )
    sig = _neutral_bearish_signal()
    pos = _open_long(entry_class="pullback", pnl=-1.5)
    d1 = _decay_decide(5, sig, pos, params, state)
    assert d1.stops == []
    assert d1.state.thesis_decay_grace_until_tick["BTC-USDT"] == 6
    assert d1.state.thesis_decay_extra_pending_by_pair == {}
    d2 = _decay_decide(6, sig, pos, params, d1.state)
    assert any(s.reason == "thesis_decay" for s in d2.stops)


def test_decay_grace_zero_ticks_closes_red_immediately():
    params = {
        "sl_pct": 3.0,
        "tp_pct": 6.0,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_ticks": 1,
        "thesis_decay_negative_grace_ticks": 0,
        "thesis_bb_drift_pts": 20.0,
    }
    state = MacdbbPullbackState(
        entry_meta_by_pair={
            "BTC-USDT": EntryMeta(
                entry_class="pullback", entry_bb_pos_pct=30.0, side="long"
            )
        }
    )
    d1 = _decay_decide(
        1,
        _neutral_bearish_signal(),
        _open_long(entry_class="pullback", pnl=-1.5),
        params,
        state,
    )
    assert any(s.reason == "thesis_decay" for s in d1.stops)


def _create_tick(*, total_amount_quote: float = 100.0, **params) -> PullbackTickInput:
    return PullbackTickInput(
        tick_number=1,
        tradeable_count=5,
        signals=[],
        open_positions=[],
        total_amount_quote=total_amount_quote,
        strategy_params=params,
        max_open_executors=3,
        frequency_sec=60,
        fee_bps=0.0,
        slippage_bps=0.0,
        amount_step=0.0,
    )


def test_build_create_dynamics_off_matches_fixed_path():
    from condor.strategy_runners.macdbb_pullback.engine import _build_create
    from condor.strategy_runners.macdbb_pullback.params import default_strategy_params
    from condor.strategy_runners.quantize import apply_fee_slippage, quote_to_base_amount

    signal = _signal(atr_pct=2.0)
    params = {
        "sl_pct": 3.8,
        "tp_pct": 9.0,
        "min_notional_quote": 10.0,
        "max_notional_quote": 1000.0,
    }
    tick = _create_tick(total_amount_quote=100.0, **params)
    create = _build_create(
        signal=signal,
        side="long",
        entry_class="immediate",
        score=1.0,
        tick=tick,
        params=params,
    )
    assert create is not None
    expected = quote_to_base_amount(
        notional_quote=apply_fee_slippage(100.0, fee_bps=0.0, slippage_bps=0.0),
        price=signal.price,
        min_notional_quote=10.0,
        max_notional_quote=1000.0,
    )
    assert create.notional_quote == expected.notional_quote
    assert create.base_amount == expected.base_amount
    assert create.sl_pct == 3.8
    assert create.tp_pct == 9.0

    defaults = default_strategy_params()
    assert defaults["enable_dynamic_barriers"] is False
    assert defaults["enable_dynamic_sizing"] is False
    create_default = _build_create(
        signal=signal,
        side="long",
        entry_class="immediate",
        score=1.0,
        tick=_create_tick(total_amount_quote=100.0, **defaults),
        params={**defaults, "sl_pct": 3.8, "tp_pct": 9.0},
    )
    assert create_default is not None
    assert create_default.notional_quote == create.notional_quote
    assert create_default.sl_pct == 3.8
    assert create_default.tp_pct == 9.0


def test_build_create_dynamic_sizing_and_barriers_use_atr_pct():
    from condor.strategy_runners.macdbb_pullback.engine import _build_create

    signal = _signal(atr_pct=2.0, price=100.0)
    params = {
        "sl_pct": 3.8,
        "tp_pct": 9.0,
        "min_notional_quote": 10.0,
        "max_notional_quote": 1000.0,
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": True,
        "ref_volatility_pct": 1.0,
        "sl_vol_exponent": 1.0,
        "tp_vol_exponent": 1.0,
        "sl_min_pct": 2.0,
        "sl_max_pct": 6.0,
        "tp_min_pct": 4.0,
        "tp_max_pct": 12.0,
        "min_vol_mult": 0.5,
        "max_vol_mult": 1.5,
    }
    create = _build_create(
        signal=signal,
        side="long",
        entry_class="immediate",
        score=1.0,
        tick=_create_tick(total_amount_quote=100.0, **params),
        params=params,
    )
    assert create is not None
    assert create.notional_quote == 50.0
    assert create.sl_pct == 6.0
    assert create.tp_pct == 12.0


def test_build_create_missing_atr_falls_back_to_ref_vol():
    from condor.strategy_runners.macdbb_pullback.engine import _build_create

    signal = _signal(atr_pct=None)
    params = {
        "sl_pct": 3.8,
        "tp_pct": 9.0,
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": True,
        "ref_volatility_pct": 1.0,
        "sl_vol_exponent": 1.0,
        "tp_vol_exponent": 1.0,
        "min_vol_mult": 0.5,
        "max_vol_mult": 1.5,
        "sl_min_pct": 2.0,
        "sl_max_pct": 6.0,
        "tp_min_pct": 4.0,
        "tp_max_pct": 12.0,
        "min_notional_quote": 10.0,
        "max_notional_quote": 1000.0,
    }
    create = _build_create(
        signal=signal,
        side="long",
        entry_class="immediate",
        score=1.0,
        tick=_create_tick(total_amount_quote=100.0, **params),
        params=params,
    )
    assert create is not None
    assert create.notional_quote == 100.0
    assert create.sl_pct == 3.8
    assert create.tp_pct == 9.0
