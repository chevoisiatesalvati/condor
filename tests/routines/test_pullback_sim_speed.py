"""Logic-preserving pullback sim speed: packed candles and slim price marks."""

from __future__ import annotations

import datetime as dt

from condor.strategy_runners.macdbb_pullback.metrics import compute_thesis_metrics
from condor.strategy_runners.macdbb_pullback.types import SignalSnapshot
from routines.macdbb_pullback_hl_backtest import Config
from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
from routines.macdbb_pullback_hl_replay.signal_tape import PullbackSignalTape, RawTickSignal
from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session
from routines.macdbb_scanner_aggressive_hl_replay.candle_shared_store import (
    SharedCandleStore,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta

PAIR = "BTC-USDT"
IDLE_PAIR = "ETH-USDT"
START = dt.datetime(2026, 7, 18, 0, 0, tzinfo=dt.timezone.utc)


def _raw(price: float, *, thesis: bool = True) -> RawTickSignal:
    # Impulse off so decide() takes an immediate entry (impulse arms pullback).
    return RawTickSignal(
        price=price,
        bb_pos_pct=40.0 if thesis else 50.0,
        bb_mid=price,
        bb_upper=price * 1.1,
        macd=1.0 if thesis else 0.0,
        signal_line=0.5 if thesis else 0.0,
        histogram=0.5 if thesis else 0.0,
        trend="bullish" if thesis else "neutral",
        momentum="increasing" if thesis else "flat",
        bullish_cross=thesis,
        bearish_cross=False,
        atr_pct=1.0,
        signed_body_sum_long=0.5,
        signed_body_sum_short=0.0,
    )


def _fingerprint(trades: list) -> list[tuple]:
    return [
        (
            t.pair,
            t.side,
            t.entry_class,
            int(t.entry_tick),
            int(t.exit_tick),
            round(float(t.pnl_quote), 6),
            str(t.exit_reason),
        )
        for t in trades
    ]


def _synthetic_session(tick_count: int = 16) -> tuple[dict, dict, PullbackSignalTape, dict]:
    ticks: dict[int, TickMeta] = {}
    prices: dict[tuple[str, int], float] = {}
    tape_by_tick: dict[int, dict[str, RawTickSignal]] = {}
    candles: list[dict[str, float]] = []
    for index in range(tick_count):
        timestamp = START + dt.timedelta(seconds=60 * index)
        tick = index + 1
        ticks[tick] = TickMeta(
            tick=tick,
            timestamp=timestamp,
            macd_pairs=[PAIR, IDLE_PAIR],
            tradeable_count=3,
        )
        # Drift up so a long can hit TP after entry.
        price = 100.0 + index * 0.8
        prices[(PAIR, tick)] = price
        prices[(IDLE_PAIR, tick)] = 50.0
        tape_by_tick[tick] = {
            PAIR: _raw(price),
            IDLE_PAIR: _raw(50.0, thesis=False),
        }
        candles.append(
            {
                "timestamp_ms": float(timestamp.timestamp() * 1000),
                "open": price,
                "high": price + 0.4,
                "low": price - 0.15,
                "close": price,
            }
        )
        if index > 0:
            prev = START + dt.timedelta(seconds=60 * index - 30)
            mid = 100.0 + (index - 0.5) * 0.8
            candles.append(
                {
                    "timestamp_ms": float(prev.timestamp() * 1000),
                    "open": mid,
                    "high": mid + 0.4,
                    "low": mid - 0.15,
                    "close": mid,
                }
            )
    candles.sort(key=lambda row: float(row["timestamp_ms"]))
    tape = PullbackSignalTape(
        by_tick=tape_by_tick,
        pairs=(PAIR, IDLE_PAIR),
        tick_count=tick_count,
    )
    return ticks, prices, tape, {PAIR: candles}


def _run_session(
    ticks: dict,
    prices: dict,
    tape: PullbackSignalTape,
    barrier_cache,
    *,
    use_scanner_price_snapshots: bool = False,
    filter_inactive_decide_pairs: bool = True,
) -> list:
    config = resolve_pullback_config(
        Config(
            preset="pullback_decay_2h_60s",
            price_source="hl_candles",
            require_price_data=True,
            sl_pct=2.0,
            tp_pct=4.0,
            total_amount_quote=100.0,
        )
    )
    _pairs, _rows, trades, summary = simulate_pullback_session(
        session_num=0,
        tick_meta_map=ticks,
        reports_by_pair={},
        config=config,
        signal_config=config,
        hl_price_cache=prices,
        hl_barrier_candle_cache=barrier_cache,
        signal_tape=tape,
        collect_debug_rows=False,
        use_scanner_price_snapshots=use_scanner_price_snapshots,
        filter_inactive_decide_pairs=filter_inactive_decide_pairs,
    )
    assert summary.get("status") != "skipped_no_price_data"
    return trades


def test_packed_barriers_match_list_fingerprint():
    ticks, prices, tape, barrier_dicts = _synthetic_session()
    list_trades = _run_session(ticks, prices, tape, barrier_dicts)
    store = SharedCandleStore.pack(barrier_dicts, name_prefix="pb_speed_bar")
    try:
        packed_trades = _run_session(ticks, prices, tape, store)
    finally:
        store.close_unlink()
    assert _fingerprint(list_trades) == _fingerprint(packed_trades)
    assert len(list_trades) >= 1


def test_slim_marks_and_inactive_filter_match_snapshot_fingerprint():
    ticks, prices, tape, barrier_dicts = _synthetic_session()
    snapshot_full = _run_session(
        ticks,
        prices,
        tape,
        barrier_dicts,
        use_scanner_price_snapshots=True,
        filter_inactive_decide_pairs=False,
    )
    slim_filtered = _run_session(
        ticks,
        prices,
        tape,
        barrier_dicts,
        use_scanner_price_snapshots=False,
        filter_inactive_decide_pairs=True,
    )
    assert _fingerprint(snapshot_full) == _fingerprint(slim_filtered)
    assert len(slim_filtered) >= 1


def test_compute_thesis_metrics_reads_params_without_copy():
    params = {"bb_proximity_epsilon_pct": 0.22}
    signal = SignalSnapshot(
        pair=PAIR,
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
    )
    metrics = compute_thesis_metrics(signal, params)
    assert metrics["thesis_long"] is True
    assert params == {"bb_proximity_epsilon_pct": 0.22}
