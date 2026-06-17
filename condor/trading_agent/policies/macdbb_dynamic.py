"""Shared MACDBB dynamic sizing and volatility-aware barriers (live + replay)."""

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

# Live strategy_params keys mapped to DynamicStrategyReplayConfig fields.
_LIVE_PARAM_KEYS: tuple[str, ...] = (
    "enable_dynamic_sizing",
    "enable_dynamic_barriers",
    "sl_pct",
    "tp_pct",
    "thesis_bb_drift_pts",
    "adaptive_long_bb_pos_max",
    "adaptive_short_bb_pos_min",
    "adaptive_strong_long_bb_pos_max",
    "adaptive_strong_short_bb_pos_min",
    "adaptive_min_macd_gap_ratio",
    "adaptive_min_hist_ratio",
    "adaptive_score_open_min",
    "adaptive_score_open_min_extreme",
    "adaptive_hist_sign_bonus",
    "adaptive_hist_sign_penalty",
    "adaptive_momentum_bonus",
    "adaptive_momentum_penalty",
    "bb_proximity_epsilon_pct",
    "min_notional_quote",
    "max_notional_quote",
    "min_conviction_mult",
    "max_conviction_mult",
    "strength_mult_per_unit",
    "extreme_displacement_mult",
    "activation_streak_mult_per_tick",
    "thin_universe_mult",
    "mature_tape_low_vol_mult",
    "vol_inverse_sizing",
    "min_vol_mult",
    "max_vol_mult",
    "ref_volatility_pct",
    "sl_vol_exponent",
    "tp_vol_exponent",
    "sl_min_pct",
    "sl_max_pct",
    "tp_min_pct",
    "tp_max_pct",
    "volatility_source",
)


@dataclass(frozen=True)
class EntryPolicyResult:
    notional_quote: float
    sl_pct: float
    tp_pct: float
    volatility_proxy_pct: float
    sizing_multiplier: float

    @property
    def stop_loss_decimal(self) -> float:
        return self.sl_pct / 100.0

    @property
    def take_profit_decimal(self) -> float:
        return self.tp_pct / 100.0


@dataclass(frozen=True)
class LivePolicyMeta:
    tradeable_count: int | None = None
    scanner_regime: Literal["mature", "degen"] | None = None


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
    pair_vol_override: float | None = None,
) -> float:
    source = config.volatility_source
    ref_vol = config.ref_volatility_pct

    if pair_vol_override is not None and pair_vol_override > 0:
        if source in ("natr", "auto"):
            return pair_vol_override

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
    pair_vol_override: float | None = None,
) -> EntryPolicyResult:
    base_notional = config.formal_notional_quote * _entry_class_base_multiplier(entry_class)
    pair_vol = estimate_pair_volatility(
        pair=pair,
        journal_signal=journal_signal,
        config=config,
        hl_candle_cache=hl_candle_cache,
        entry_time=entry_time,
        pair_vol_override=pair_vol_override,
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


def live_policy_config_from_params(
    strategy_params: dict[str, Any] | None,
    *,
    formal_notional_quote: float,
) -> DynamicStrategyReplayConfig:
    """Build replay-compatible dynamic config from live [STRATEGY CONFIG] params."""
    params = dict(strategy_params or {})
    payload: dict[str, Any] = {
        "preset": "custom",
        "formal_notional_quote": formal_notional_quote,
    }

    activation_ticks = params.get("adaptive_activation_ticks")
    if activation_ticks is not None:
        payload["activation_ticks"] = int(activation_ticks)
    else:
        payload["activation_ticks"] = 0

    for key in _LIVE_PARAM_KEYS:
        if key in params and params[key] is not None:
            payload[key] = params[key]

    return DynamicStrategyReplayConfig(**payload)


def _live_meta_to_tick_meta(meta: LivePolicyMeta) -> TickMeta:
    return TickMeta(
        tick=0,
        timestamp=dt.datetime.now(dt.timezone.utc),
        macd_pairs=[],
        tradeable_count=meta.tradeable_count,
        scanner_regime=meta.scanner_regime,
    )


def resolve_live_entry_policy(
    *,
    pair: str,
    side: str,
    entry_class: str,
    metrics: dict[str, float | bool],
    meta: LivePolicyMeta,
    entry_streak: int,
    strategy_params: dict[str, Any] | None,
    formal_notional_quote: float,
    natr_mean_pct: float | None = None,
    bb_mid: float | None = None,
    bb_upper: float | None = None,
) -> EntryPolicyResult:
    """Resolve entry sizing/barriers for the live MACDBB agent."""
    config = live_policy_config_from_params(
        strategy_params,
        formal_notional_quote=formal_notional_quote,
    )
    tick_meta = _live_meta_to_tick_meta(meta)

    journal_signal: JournalSignal1h | None = None
    if bb_mid is not None and bb_upper is not None:
        journal_signal = JournalSignal1h(
            pair=pair,
            bb_pos_pct=0.0,
            macd=0.0,
            signal_line=0.0,
            histogram=0.0,
            macd_gap_ratio=0.0,
            hist_ratio=0.0,
            trend="neutral",
            momentum="flat",
            formal_long=False,
            formal_short=False,
            adaptive_long=False,
            adaptive_short=False,
            strength_long=0.0,
            strength_short=0.0,
            bb_mid=bb_mid,
            bb_upper=bb_upper,
        )

    if (
        not config.enable_dynamic_sizing
        and not config.enable_dynamic_barriers
    ):
        return resolve_fixed_entry_policy(entry_class=entry_class, config=config)

    return resolve_entry_policy(
        pair=pair,
        side=side,
        entry_class=entry_class,
        metrics=metrics,
        meta=tick_meta,
        entry_streak=entry_streak,
        config=config,
        journal_signal=journal_signal,
        pair_vol_override=natr_mean_pct,
    )
