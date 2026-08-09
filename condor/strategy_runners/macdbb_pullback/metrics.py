"""Symmetric MACD/BB thesis metrics for macdbb_pullback_hl (no adaptive path)."""

from __future__ import annotations

from typing import Any

from condor.strategy_runners.macdbb_pullback.types import SignalSnapshot


def _price_at_or_below_mid(price: float, bb_mid: float, epsilon_pct: float) -> bool:
    if bb_mid <= 0:
        return False
    return price <= bb_mid * (1.0 + epsilon_pct / 100.0)


def _price_at_or_above_upper(price: float, bb_upper: float, epsilon_pct: float) -> bool:
    if bb_upper <= 0:
        return False
    return price >= bb_upper * (1.0 - epsilon_pct / 100.0)


def compute_thesis_metrics(
    signal: SignalSnapshot,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, float | bool]:
    params = dict(strategy_params or {})
    epsilon = float(params.get("bb_proximity_epsilon_pct") or 0.22)
    hist_increasing = signal.momentum == "increasing"

    price_le_mid = _price_at_or_below_mid(signal.price, signal.bb_mid, epsilon)
    price_ge_upper = _price_at_or_above_upper(signal.price, signal.bb_upper, epsilon)

    thesis_long = (signal.bullish_cross and signal.macd > 0) or (
        price_le_mid
        and signal.trend == "bullish"
        and signal.histogram > 0
        and hist_increasing
    )
    thesis_short = (signal.bearish_cross and signal.macd < 0) or (
        price_ge_upper
        and signal.trend == "bearish"
        and signal.histogram < 0
        and hist_increasing
    )

    strength_long = 0.0
    if thesis_long:
        strength_long = (
            min(1.4, max(0.0, (50.0 - signal.bb_pos_pct) / 12.0))
            + min(1.0, abs(signal.macd - signal.signal_line) / max(abs(signal.signal_line), 1e-6))
            + min(0.6, abs(signal.histogram) / max(abs(signal.macd), 1e-6))
            + (0.35 if hist_increasing else 0.0)
        )
    strength_short = 0.0
    if thesis_short:
        strength_short = (
            min(1.4, max(0.0, (signal.bb_pos_pct - 50.0) / 12.0))
            + min(1.0, abs(signal.macd - signal.signal_line) / max(abs(signal.signal_line), 1e-6))
            + min(0.6, abs(signal.histogram) / max(abs(signal.macd), 1e-6))
            + (0.35 if hist_increasing else 0.0)
        )

    return {
        "thesis_long": thesis_long,
        "thesis_short": thesis_short,
        "has_thesis": thesis_long or thesis_short,
        "price_le_mid": price_le_mid,
        "price_ge_upper": price_ge_upper,
        "strength_long": strength_long,
        "strength_short": strength_short,
    }


def infer_signal_label(metrics: dict[str, float | bool]) -> str:
    if bool(metrics.get("thesis_long")):
        return "LONG"
    if bool(metrics.get("thesis_short")):
        return "SHORT"
    return "NEUTRAL"
