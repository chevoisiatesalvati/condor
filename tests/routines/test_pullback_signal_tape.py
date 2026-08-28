"""Pullback signal tape: param-independent raw values, per-config impulse flags."""

from condor.strategy_runners.macdbb_pullback.entry_quality import compute_impulse_metrics
from routines.macdbb_pullback_hl_replay.signal_tape import (
    PullbackSignalTape,
    RawTickSignal,
    _impulse_flag,
    _raw_from_as_of_candles,
)


def _bar(ts_ms: int, open_px: float, close: float, high: float, low: float) -> dict[str, float]:
    return {
        "timestamp_ms": float(ts_ms),
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10.0,
    }


def test_impulse_flag_uses_mult_not_baked_boolean():
    atr_pct = 1.0
    signed = 1.3
    assert _impulse_flag(atr_pct, signed, 1.0) is True
    assert _impulse_flag(atr_pct, signed, 1.25) is True
    assert _impulse_flag(atr_pct, signed, 1.75) is False
    assert _impulse_flag(0.0, signed, 1.0) is False


def test_materialize_applies_impulse_atr_mult_per_config():
    raw = RawTickSignal(
        price=100.0,
        bb_pos_pct=40.0,
        bb_mid=99.0,
        bb_upper=110.0,
        macd=0.5,
        signal_line=0.2,
        histogram=0.3,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
        atr_pct=1.0,
        signed_body_sum_long=1.3,
        signed_body_sum_short=0.0,
    )
    tape = PullbackSignalTape(by_tick={10: {"ETH-USD": raw}}, pairs=("ETH-USD",), tick_count=1)
    loose = tape.materialize_signals(10, ["ETH-USD"], {"impulse_atr_mult": 1.0})
    tight = tape.materialize_signals(10, ["ETH-USD"], {"impulse_atr_mult": 1.75})
    assert loose["ETH-USD"].impulse_long is True
    assert tight["ETH-USD"].impulse_long is False
    assert loose["ETH-USD"].atr_pct == 1.0
    assert tight["ETH-USD"].impulse_signed_body_sum_pct == 1.3


def test_raw_from_as_of_matches_impulse_metrics_numerics():
    hour = 1_700_000_000_000
    candles = []
    close = 100.0
    for index in range(40):
        open_px = close
        close = close + (1.5 if index % 2 == 0 else -0.4)
        candles.append(
            _bar(
                hour + index * 3_600_000,
                open_px,
                close,
                max(open_px, close) + 0.5,
                min(open_px, close) - 0.5,
            )
        )
    raw = _raw_from_as_of_candles(
        "ETH-USD",
        candles,
        impulse_lookback_bars=2,
        atr_period=14,
    )
    assert raw is not None
    long_m = compute_impulse_metrics(
        candles,
        "long",
        lookback_bars=2,
        atr_period=14,
        impulse_atr_mult=1.0,
    )
    short_m = compute_impulse_metrics(
        candles,
        "short",
        lookback_bars=2,
        atr_period=14,
        impulse_atr_mult=1.0,
    )
    assert raw.atr_pct == long_m.atr_pct
    assert raw.signed_body_sum_long == long_m.signed_body_sum_pct
    assert raw.signed_body_sum_short == short_m.signed_body_sum_pct
    assert not raw.atr_pct_by_period
    assert not raw.signed_body_sum_long_by_lookback
    assert not raw.signed_body_sum_short_by_lookback

    probed = _raw_from_as_of_candles(
        "ETH-USD",
        candles,
        impulse_lookback_bars=2,
        atr_period=14,
        include_probe_windows=True,
    )
    assert probed is not None
    assert probed.atr_pct_by_period[14] == long_m.atr_pct
    assert probed.signed_body_sum_long_by_lookback[2] == long_m.signed_body_sum_pct
    assert probed.signed_body_sum_short_by_lookback[2] == short_m.signed_body_sum_pct


def test_materialize_selects_lookback_and_atr_period_windows():
    raw = RawTickSignal(
        price=100.0,
        bb_pos_pct=40.0,
        bb_mid=99.0,
        bb_upper=110.0,
        macd=0.5,
        signal_line=0.2,
        histogram=0.3,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
        atr_pct=1.0,
        signed_body_sum_long=1.3,
        signed_body_sum_short=0.0,
        atr_pct_by_period={7: 2.0, 14: 1.0, 21: 0.4},
        signed_body_sum_long_by_lookback={1: 0.5, 2: 1.3, 4: 2.5},
        signed_body_sum_short_by_lookback={1: 0.0, 2: 0.0, 4: 0.0},
    )
    tape = PullbackSignalTape(by_tick={10: {"ETH-USD": raw}}, pairs=("ETH-USD",), tick_count=1)
    lookback_one = tape.materialize_signals(
        10,
        ["ETH-USD"],
        {"impulse_atr_mult": 1.0, "impulse_lookback_bars": 1, "atr_period": 7},
    )
    lookback_four = tape.materialize_signals(
        10,
        ["ETH-USD"],
        {"impulse_atr_mult": 1.0, "impulse_lookback_bars": 4, "atr_period": 21},
    )
    assert lookback_one["ETH-USD"].atr_pct == 2.0
    assert lookback_one["ETH-USD"].impulse_signed_body_sum_pct == 0.5
    assert lookback_one["ETH-USD"].impulse_long is False
    assert lookback_four["ETH-USD"].atr_pct == 0.4
    assert lookback_four["ETH-USD"].impulse_signed_body_sum_pct == 2.5
    assert lookback_four["ETH-USD"].impulse_long is True


def test_materialize_falls_back_to_scalars_without_probe_windows():
    raw = RawTickSignal(
        price=100.0,
        bb_pos_pct=40.0,
        bb_mid=99.0,
        bb_upper=110.0,
        macd=0.5,
        signal_line=0.2,
        histogram=0.3,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
        atr_pct=1.0,
        signed_body_sum_long=1.3,
        signed_body_sum_short=0.0,
    )
    tape = PullbackSignalTape(by_tick={10: {"ETH-USD": raw}}, pairs=("ETH-USD",), tick_count=1)
    selected = tape.materialize_signals(
        10,
        ["ETH-USD"],
        {"impulse_atr_mult": 1.0, "impulse_lookback_bars": 4, "atr_period": 21},
    )
    assert selected["ETH-USD"].atr_pct == 1.0
    assert selected["ETH-USD"].impulse_signed_body_sum_pct == 1.3
