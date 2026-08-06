"""Tests for dynamic replay sizing and volatility barriers."""

from __future__ import annotations

import datetime as dt

from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import (
    DynamicReplayPolicy,
    bb_width_pct,
    compute_dynamic_barriers,
    compute_conviction_multiplier,
    resolve_entry_policy,
    resolve_fixed_entry_policy,
    static_tier_volatility_pct,
)
from routines.macdbb_scanner_aggressive_hl_replay.journal import _parse_decision_line, parse_dt
from routines.macdbb_scanner_aggressive_hl_replay.metrics import compute_metrics, parsed_report_from_journal
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    JournalSignal1h,
    StrategyReplayConfig,
    TickMeta,
)


def _journal_signal(**overrides) -> JournalSignal1h:
    base = dict(
        pair="BTC-USD",
        bb_pos_pct=30.0,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        macd_gap_ratio=0.2,
        hist_ratio=0.15,
        trend="bullish",
        momentum="increasing",
        formal_long=False,
        formal_short=False,
        adaptive_long=True,
        adaptive_short=False,
        strength_long=2.5,
        strength_short=0.0,
        bb_mid=100.0,
        bb_upper=102.0,
    )
    base.update(overrides)
    return JournalSignal1h(**base)


def _tick_meta(**overrides) -> TickMeta:
    base = dict(
        tick=1,
        timestamp=dt.datetime(2026, 6, 12, tzinfo=dt.timezone.utc),
        macd_pairs=["BTC-USD"],
        tradeable_count=5,
        scanner_regime="mature",
    )
    base.update(overrides)
    return TickMeta(**base)


def test_bb_width_pct_from_journal_bands():
    signal = _journal_signal(bb_mid=100.0, bb_upper=103.0)
    assert bb_width_pct(signal) == 3.0


def test_static_tier_volatility_pct_for_majors():
    assert static_tier_volatility_pct("BTC-USD") == 0.30
    assert static_tier_volatility_pct("ETH") == 0.40


def test_compute_dynamic_barriers_scales_with_volatility():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        sl_pct=2.0,
        tp_pct=10.0,
        ref_volatility_pct=0.5,
        sl_vol_exponent=0.7,
        tp_vol_exponent=1.0,
    )
    low_vol_sl, low_vol_tp = compute_dynamic_barriers(0.3, config)
    high_vol_sl, high_vol_tp = compute_dynamic_barriers(2.5, config)

    assert low_vol_sl < config.sl_pct
    assert low_vol_tp < config.tp_pct
    assert high_vol_sl > config.sl_pct
    assert high_vol_tp > config.tp_pct


def test_compute_dynamic_barriers_respects_clamps():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        sl_pct=2.0,
        tp_pct=10.0,
        ref_volatility_pct=0.5,
        sl_min_pct=0.8,
        sl_max_pct=4.0,
        tp_min_pct=3.0,
        tp_max_pct=15.0,
    )
    sl_pct, tp_pct = compute_dynamic_barriers(10.0, config)
    assert sl_pct == config.sl_max_pct
    assert tp_pct == config.tp_max_pct


def test_disabled_dynamic_policy_matches_fixed_sizing():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        formal_notional_quote=500.0,
        enable_dynamic_sizing=False,
        enable_dynamic_barriers=False,
        sl_pct=2.4,
        tp_pct=10.0,
    )
    signal = _journal_signal()
    parsed = parsed_report_from_journal(signal, price=100.0)
    metrics = compute_metrics(parsed, config, journal_signal=signal)
    meta = _tick_meta()

    formal_fixed = resolve_fixed_entry_policy(entry_class="formal", config=config)
    adaptive_fixed = resolve_fixed_entry_policy(
        entry_class="regime_adaptive_half_size",
        config=config,
    )
    formal_dynamic = resolve_entry_policy(
        pair="BTC-USD",
        side="long",
        entry_class="formal",
        metrics=metrics,
        meta=meta,
        entry_streak=6,
        config=config,
        journal_signal=signal,
    )
    adaptive_dynamic = resolve_entry_policy(
        pair="BTC-USD",
        side="long",
        entry_class="regime_adaptive_half_size",
        metrics=metrics,
        meta=meta,
        entry_streak=6,
        config=config,
        journal_signal=signal,
    )

    assert formal_dynamic.notional_quote == formal_fixed.notional_quote == 500.0
    assert adaptive_dynamic.notional_quote == adaptive_fixed.notional_quote == 250.0
    assert formal_dynamic.sl_pct == config.sl_pct
    assert formal_dynamic.tp_pct == config.tp_pct
    assert formal_dynamic.sizing_multiplier == 1.0


def test_conviction_multiplier_increases_with_strength():
    config = DynamicStrategyReplayConfig(
        preset="custom",
        strength_mult_per_unit=0.08,
        min_conviction_mult=0.75,
        max_conviction_mult=1.35,
    )
    signal = _journal_signal(strength_long=3.0)
    parsed = parsed_report_from_journal(signal, price=100.0)
    metrics = compute_metrics(parsed, config, journal_signal=signal)
    meta = _tick_meta()

    low = compute_conviction_multiplier(
        side="long",
        entry_class="formal",
        metrics=metrics,
        meta=meta,
        entry_streak=6,
        config=config,
        pair_vol=0.5,
    )
    metrics_strong = dict(metrics)
    metrics_strong["adaptive_strength_long"] = float(metrics["adaptive_strength_long"]) + 1.0
    high = compute_conviction_multiplier(
        side="long",
        entry_class="formal",
        metrics=metrics_strong,
        meta=meta,
        entry_streak=6,
        config=config,
        pair_vol=0.5,
    )
    assert high > low


def test_parse_scanner_regime_from_decision_line():
    tick_time_map = {12: parse_dt("2026-06-12 10:00")}
    line = (
        "- **#12** tick=12 | entry_class=hold | scanner_regime=degen | "
        "scanner_analyzed=16 | tradeable_count=8 | natr_floor_used=0.1 | "
        "best_score=2.45 | macd_pairs=BTC-USD,ETH-USD"
    )
    meta = _parse_decision_line(line, tick_time_map)
    assert meta is not None
    assert meta.scanner_regime == "degen"
    assert meta.scanner_analyzed == 16
    assert meta.tradeable_count == 8
    assert meta.natr_floor_used == 0.1
    assert meta.best_score == 2.45


def test_strategy_replay_config_still_works_without_dynamic_fields():
    config = StrategyReplayConfig(preset="custom", formal_notional_quote=200.0)
    fixed = resolve_fixed_entry_policy(entry_class="formal", config=config)
    assert fixed.notional_quote == 200.0


def test_scanner_natr_mean_matches_market_scanner_analyze_pair():
    import numpy as np

    from routines.market_scanner import analyze_pair

    rng = np.random.default_rng(42)
    candles: list[dict[str, float]] = []
    price = 0.17
    for index in range(400):
        shock = float(rng.normal(0, 0.0004))
        price = max(0.01, price + shock)
        candles.append(
            {
                "timestamp_ms": index * 60_000,
                "open": price,
                "high": price * 1.001,
                "low": price * 0.999,
                "close": price,
                "volume": 1000.0,
            }
        )
    pair_info = {
        "trading_pair": "ADA-USD",
        "price": price,
        "price_change_pct": 0.0,
        "volume_24h_usd": 10_000_000.0,
    }
    analyzed = analyze_pair(candles, pair_info)
    assert analyzed is not None
    from condor.strategy_runners.macdbb.dynamic import scanner_natr_mean_from_candles

    shared = scanner_natr_mean_from_candles(candles, entry_time=None)
    assert shared is not None
    assert abs(shared - analyzed["natr_mean"]) < 1e-4


def test_estimate_pair_volatility_uses_scanner_natr_from_1m_cache():
    from condor.strategy_runners.macdbb.dynamic import (
        estimate_pair_volatility,
        scanner_natr_mean_from_candles,
    )

    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_session_parity",
        volatility_source="natr",
    )
    entry_time = dt.datetime(2026, 6, 17, 20, 32, tzinfo=dt.timezone.utc)
    candles: list[dict[str, float]] = []
    price = 0.17
    start_ms = int(entry_time.timestamp() * 1000) - 6 * 3600 * 1000
    for index in range(360):
        candles.append(
            {
                "timestamp_ms": start_ms + index * 60_000,
                "open": price,
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": 1000.0,
            }
        )
    expected = scanner_natr_mean_from_candles(
        candles,
        entry_time,
        lookback_hours=config.scanner_lookback_hours,
    )
    assert expected is not None
    vol = estimate_pair_volatility(
        pair="ADA-USD",
        journal_signal=None,
        config=config,
        hl_vol_candle_cache={"ADA-USD": candles},
        entry_time=entry_time,
    )
    assert abs(vol - expected) < 1e-9
