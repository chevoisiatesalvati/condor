"""Dynamic position sizing and volatility-aware barriers for replay backtest."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

from routines.macdbb_replay.models import (
    DynamicStrategyReplayConfig,
    JournalSignal1h,
    StrategyReplayConfig,
    TickMeta,
)

STATIC_TIER_VOL_PCT: dict[str, float] = {
    "BTC-USD": 0.30,
    "ETH-USD": 0.40,
    "SOL-USD": 0.55,
}

NATR_LOOKBACK_PERIODS = 14
NATR_MIN_CANDLES = NATR_LOOKBACK_PERIODS * 2


@dataclass(frozen=True)
class EntryPolicyResult:
    notional_quote: float
    sl_pct: float
    tp_pct: float
    volatility_proxy_pct: float
    sizing_multiplier: float


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _canonical_trading_pair(pair: str) -> str:
    if "-" in pair:
        return pair
    return f"{pair}-USD"


def bb_width_pct(signal: JournalSignal1h | None) -> float | None:
    if signal is None or not signal.has_replay_bands():
        return None
    mid = signal.bb_mid
    upper = signal.bb_upper
    if mid is None or upper is None or mid <= 0:
        return None
    return ((upper - mid) / mid) * 100.0


def natr_from_candles(
    candles: list[dict[str, float]],
    entry_time: dt.datetime,
    *,
    lookback_periods: int = NATR_LOOKBACK_PERIODS,
) -> float | None:
    if not candles:
        return None

    end_ms = int(entry_time.timestamp() * 1000)
    window = [
        candle
        for candle in candles
        if "timestamp_ms" in candle and int(candle["timestamp_ms"]) <= end_ms
    ]
    if len(window) < lookback_periods + 1:
        return None

    window.sort(key=lambda candle: int(candle["timestamp_ms"]))
    closes = [float(candle["close"]) for candle in window]
    highs = [float(candle["high"]) for candle in window]
    lows = [float(candle["low"]) for candle in window]

    true_ranges: list[float] = []
    for index in range(1, len(window)):
        high_low = highs[index] - lows[index]
        high_prev = abs(highs[index] - closes[index - 1])
        low_prev = abs(lows[index] - closes[index - 1])
        true_ranges.append(max(high_low, high_prev, low_prev))

    if len(true_ranges) < lookback_periods:
        return None

    recent_tr = true_ranges[-lookback_periods:]
    atr = sum(recent_tr) / len(recent_tr)
    last_close = closes[-1]
    if last_close <= 0:
        return None
    return (atr / last_close) * 100.0


def static_tier_volatility_pct(pair: str) -> float | None:
    return STATIC_TIER_VOL_PCT.get(_canonical_trading_pair(pair))


def estimate_pair_volatility(
    *,
    pair: str,
    journal_signal: JournalSignal1h | None,
    config: DynamicStrategyReplayConfig,
    hl_candle_cache: dict[str, list[dict[str, float]]] | None = None,
    entry_time: dt.datetime | None = None,
) -> float:
    source = config.volatility_source
    ref_vol = config.ref_volatility_pct

    def _bb() -> float | None:
        return bb_width_pct(journal_signal)

    def _natr() -> float | None:
        if hl_candle_cache is None or entry_time is None:
            return None
        candles = hl_candle_cache.get(_canonical_trading_pair(pair))
        if not candles:
            return None
        return natr_from_candles(candles, entry_time)

    def _static() -> float | None:
        return static_tier_volatility_pct(pair)

    if source == "bb_width":
        candidates = [_bb()]
    elif source == "natr":
        candidates = [_natr()]
    elif source == "static_tier":
        candidates = [_static()]
    else:
        candidates = [_natr(), _bb(), _static()]

    for candidate in candidates:
        if candidate is not None and candidate > 0:
            return candidate
    return ref_vol


def _entry_class_base_multiplier(entry_class: str) -> float:
    if entry_class == "regime_adaptive_half_size":
        return 0.5
    return 1.0


def _fixed_notional(config: StrategyReplayConfig, entry_class: str) -> float:
    if entry_class == "regime_adaptive_half_size":
        return config.formal_notional_quote / 2.0
    return config.formal_notional_quote


def compute_conviction_multiplier(
    *,
    side: str,
    entry_class: str,
    metrics: dict[str, float | bool],
    meta: TickMeta,
    entry_streak: int,
    config: DynamicStrategyReplayConfig,
    pair_vol: float,
) -> float:
    score_key = "adaptive_strength_long" if side == "long" else "adaptive_strength_short"
    threshold_key = "long_open_threshold" if side == "long" else "short_open_threshold"
    extreme_key = "extreme_long_candidate" if side == "long" else "extreme_short_candidate"

    score = float(metrics[score_key])
    threshold = float(metrics[threshold_key])
    excess = max(0.0, score - threshold)
    strength_mult = 1.0 + excess * config.strength_mult_per_unit

    extreme_mult = (
        config.extreme_displacement_mult
        if bool(metrics[extreme_key])
        else 1.0
    )

    streak_mult = 1.0
    if entry_class == "regime_adaptive_half_size":
        streak_excess = max(0, entry_streak - config.activation_ticks)
        streak_mult = 1.0 + streak_excess * config.activation_streak_mult_per_tick

    universe_mult = 1.0
    if meta.tradeable_count is not None and meta.tradeable_count <= 2:
        universe_mult = config.thin_universe_mult

    regime_mult = 1.0
    if (
        meta.scanner_regime == "mature"
        and pair_vol < config.ref_volatility_pct
    ):
        regime_mult = config.mature_tape_low_vol_mult

    conviction = strength_mult * extreme_mult * streak_mult * universe_mult * regime_mult
    return _clamp(conviction, config.min_conviction_mult, config.max_conviction_mult)


def compute_vol_risk_multiplier(
    pair_vol: float,
    config: DynamicStrategyReplayConfig,
) -> float:
    if not config.vol_inverse_sizing or pair_vol <= 0:
        return 1.0
    ratio = config.ref_volatility_pct / pair_vol
    return _clamp(ratio, config.min_vol_mult, config.max_vol_mult)


def compute_dynamic_barriers(
    pair_vol: float,
    config: DynamicStrategyReplayConfig,
) -> tuple[float, float]:
    ref_vol = config.ref_volatility_pct
    if ref_vol <= 0:
        return config.sl_pct, config.tp_pct

    vol_ratio = pair_vol / ref_vol
    sl_pct = config.sl_pct * (vol_ratio ** config.sl_vol_exponent)
    tp_pct = config.tp_pct * (vol_ratio ** config.tp_vol_exponent)
    return (
        _clamp(sl_pct, config.sl_min_pct, config.sl_max_pct),
        _clamp(tp_pct, config.tp_min_pct, config.tp_max_pct),
    )


def resolve_entry_policy(
    *,
    pair: str,
    side: str,
    entry_class: str,
    metrics: dict[str, float | bool],
    meta: TickMeta,
    entry_streak: int,
    config: DynamicStrategyReplayConfig,
    journal_signal: JournalSignal1h | None = None,
    hl_candle_cache: dict[str, list[dict[str, float]]] | None = None,
    entry_time: dt.datetime | None = None,
) -> EntryPolicyResult:
    base_notional = config.formal_notional_quote * _entry_class_base_multiplier(entry_class)
    pair_vol = estimate_pair_volatility(
        pair=pair,
        journal_signal=journal_signal,
        config=config,
        hl_candle_cache=hl_candle_cache,
        entry_time=entry_time,
    )

    if config.enable_dynamic_sizing:
        conviction = compute_conviction_multiplier(
            side=side,
            entry_class=entry_class,
            metrics=metrics,
            meta=meta,
            entry_streak=entry_streak,
            config=config,
            pair_vol=pair_vol,
        )
        vol_risk = compute_vol_risk_multiplier(pair_vol, config)
        sizing_multiplier = conviction * vol_risk
        notional = _clamp(
            base_notional * sizing_multiplier,
            config.min_notional_quote,
            config.max_notional_quote,
        )
    else:
        sizing_multiplier = 1.0
        notional = base_notional

    if config.enable_dynamic_barriers:
        sl_pct, tp_pct = compute_dynamic_barriers(pair_vol, config)
    else:
        sl_pct = config.sl_pct
        tp_pct = config.tp_pct

    return EntryPolicyResult(
        notional_quote=notional,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        volatility_proxy_pct=pair_vol,
        sizing_multiplier=sizing_multiplier,
    )


def resolve_fixed_entry_policy(
    *,
    entry_class: str,
    config: StrategyReplayConfig,
) -> EntryPolicyResult:
    notional = _fixed_notional(config, entry_class)
    return EntryPolicyResult(
        notional_quote=notional,
        sl_pct=config.sl_pct,
        tp_pct=config.tp_pct,
        volatility_proxy_pct=0.0,
        sizing_multiplier=1.0,
    )


class DynamicReplayPolicy:
    """Replay policy for dynamic sizing and volatility-aware barriers."""

    def __init__(self, config: DynamicStrategyReplayConfig) -> None:
        self.config = config

    def resolve_entry(
        self,
        *,
        pair: str,
        side: str,
        entry_class: str,
        metrics: dict[str, float | bool],
        meta: TickMeta,
        entry_streak: int,
        journal_signal: JournalSignal1h | None = None,
        hl_candle_cache: dict[str, list[dict[str, float]]] | None = None,
        entry_time: dt.datetime | None = None,
    ) -> EntryPolicyResult:
        if (
            not self.config.enable_dynamic_sizing
            and not self.config.enable_dynamic_barriers
        ):
            return resolve_fixed_entry_policy(
                entry_class=entry_class,
                config=self.config,
            )
        return resolve_entry_policy(
            pair=pair,
            side=side,
            entry_class=entry_class,
            metrics=metrics,
            meta=meta,
            entry_streak=entry_streak,
            config=self.config,
            journal_signal=journal_signal,
            hl_candle_cache=hl_candle_cache,
            entry_time=entry_time,
        )

    def skip_journal_barriers(self) -> bool:
        return (
            self.config.enable_dynamic_barriers
            and self.config.ignore_journal_barriers_when_dynamic
        )


ReplayPolicy = DynamicReplayPolicy
