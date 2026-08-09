"""Impulse / pullback entry-quality helpers for macdbb_pullback_hl."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

Side = Literal["long", "short"]

DEFAULT_IMPULSE_LOOKBACK_BARS = 2
DEFAULT_IMPULSE_ATR_MULT = 1.25
DEFAULT_ATR_PERIOD = 14
DEFAULT_PULLBACK_EPSILON_PCT = 0.35
DEFAULT_CHASE_LONG_BB_POS_MAX = 70.0
DEFAULT_CHASE_SHORT_BB_POS_MIN = 30.0


@dataclass(frozen=True)
class ImpulseMetrics:
    atr_pct: float
    signed_body_sum_pct: float
    is_impulse: bool
    lookback_bars: int
    bars_used: int


def _candle_field(candle: Any, key: str) -> float:
    if isinstance(candle, dict):
        return float(candle[key])
    return float(getattr(candle, key))


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_pct_from_candles(
    candles: Sequence[Any],
    *,
    period: int = DEFAULT_ATR_PERIOD,
) -> float:
    """ATR% from completed OHLC bars using the last bar's close as denominator."""
    if len(candles) < period + 1:
        return 0.0
    window = candles[-(period + 1) :]
    trs: list[float] = []
    for index in range(1, len(window)):
        high = _candle_field(window[index], "high")
        low = _candle_field(window[index], "low")
        prev_close = _candle_field(window[index - 1], "close")
        trs.append(true_range(high, low, prev_close))
    if not trs:
        return 0.0
    atr = sum(trs[-period:]) / float(period)
    close = _candle_field(window[-1], "close")
    if close <= 0:
        return 0.0
    return (atr / close) * 100.0


def signed_body_pct(candle: Any, side: Side) -> float:
    open_px = _candle_field(candle, "open")
    close_px = _candle_field(candle, "close")
    if open_px <= 0:
        return 0.0
    body = ((close_px - open_px) / open_px) * 100.0
    if side == "long":
        return body if body > 0 else 0.0
    return (-body) if body < 0 else 0.0


def compute_impulse_metrics(
    candles_1h: Sequence[Any],
    side: Side,
    *,
    lookback_bars: int = DEFAULT_IMPULSE_LOOKBACK_BARS,
    atr_period: int = DEFAULT_ATR_PERIOD,
    impulse_atr_mult: float = DEFAULT_IMPULSE_ATR_MULT,
) -> ImpulseMetrics:
    """Measure directional impulse on the last N completed 1h candles."""
    lookback = max(1, int(lookback_bars))
    if len(candles_1h) < 2:
        return ImpulseMetrics(
            atr_pct=0.0,
            signed_body_sum_pct=0.0,
            is_impulse=False,
            lookback_bars=lookback,
            bars_used=0,
        )
    # Exclude a potentially incomplete last bar when timestamps are wall-clock aligned;
    # callers should pass completed bars only. Use the full list as completed.
    completed = list(candles_1h)
    atr_pct = atr_pct_from_candles(completed, period=atr_period)
    recent = completed[-lookback:]
    signed_sum = sum(signed_body_pct(candle, side) for candle in recent)
    threshold = float(impulse_atr_mult) * atr_pct if atr_pct > 0 else float("inf")
    is_impulse = atr_pct > 0 and signed_sum >= threshold
    return ImpulseMetrics(
        atr_pct=atr_pct,
        signed_body_sum_pct=signed_sum,
        is_impulse=is_impulse,
        lookback_bars=lookback,
        bars_used=len(recent),
    )


def is_chase_extended(
    side: Side,
    bb_pos_pct: float,
    *,
    chase_long_bb_pos_max: float = DEFAULT_CHASE_LONG_BB_POS_MAX,
    chase_short_bb_pos_min: float = DEFAULT_CHASE_SHORT_BB_POS_MIN,
) -> bool:
    if side == "long":
        return bb_pos_pct > float(chase_long_bb_pos_max)
    return bb_pos_pct < float(chase_short_bb_pos_min)


def pullback_reached(
    side: Side,
    price: float,
    bb_mid: float,
    *,
    pullback_epsilon_pct: float = DEFAULT_PULLBACK_EPSILON_PCT,
) -> bool:
    if bb_mid <= 0 or price <= 0:
        return False
    eps = float(pullback_epsilon_pct) / 100.0
    if side == "long":
        return price <= bb_mid * (1.0 + eps)
    return price >= bb_mid * (1.0 - eps)


def allow_immediate_entry(
    *,
    impulse: ImpulseMetrics,
    chase_extended: bool,
) -> bool:
    return (not impulse.is_impulse) and (not chase_extended)
