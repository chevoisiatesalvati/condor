"""Pullback ATR/vol barriers and inverse-vol sizing (live + replay).

Conviction-style scanner multipliers are intentionally not ported. Vol proxy is
``signal.atr_pct`` already on the pullback tape.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL = 100.0
ANNUALIZATION_DAYS = 365.25

PULLBACK_DYNAMIC_PARAM_KEYS: tuple[str, ...] = (
    "enable_dynamic_barriers",
    "ref_volatility_pct",
    "sl_vol_exponent",
    "tp_vol_exponent",
    "sl_min_pct",
    "sl_max_pct",
    "tp_min_pct",
    "tp_max_pct",
    "enable_dynamic_sizing",
    "min_vol_mult",
    "max_vol_mult",
)


@dataclass(frozen=True)
class PullbackEntryPolicy:
    notional_quote: float
    sl_pct: float
    tp_pct: float
    volatility_proxy_pct: float
    sizing_multiplier: float


def _clamp(value: float, lower: float, upper: float) -> float:
    lo, hi = (lower, upper) if lower <= upper else (upper, lower)
    return max(lo, min(hi, value))


def _as_bool(value: Any) -> bool:
    return bool(value)


def pair_volatility_pct(atr_pct: float | None, ref_volatility_pct: float) -> float:
    if atr_pct is None or float(atr_pct) <= 0:
        return float(ref_volatility_pct)
    return float(atr_pct)


def compute_inverse_vol_multiplier(
    pair_vol: float,
    ref_vol: float,
    min_mult: float,
    max_mult: float,
) -> float:
    if pair_vol <= 0 or ref_vol <= 0:
        return 1.0
    return _clamp(ref_vol / pair_vol, min_mult, max_mult)


def compute_vol_scaled_barriers(
    pair_vol: float,
    *,
    ref_vol: float,
    sl_pct: float,
    tp_pct: float,
    sl_vol_exponent: float,
    tp_vol_exponent: float,
    sl_min_pct: float,
    sl_max_pct: float,
    tp_min_pct: float,
    tp_max_pct: float,
) -> tuple[float, float]:
    if ref_vol <= 0:
        return sl_pct, tp_pct
    vol_ratio = pair_vol / ref_vol
    scaled_sl = sl_pct * (vol_ratio ** sl_vol_exponent)
    scaled_tp = tp_pct * (vol_ratio ** tp_vol_exponent)
    return (
        _clamp(scaled_sl, sl_min_pct, sl_max_pct),
        _clamp(scaled_tp, tp_min_pct, tp_max_pct),
    )


def capital_normalized_pnl(
    raw_pnl: float,
    avg_notional: float,
    benchmark_avg_notional: float = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
) -> float:
    """Scale raw PnL to a fixed budget so variable sizing does not rank 'size up'."""
    if avg_notional <= 0 or benchmark_avg_notional <= 0:
        return float(raw_pnl)
    return float(raw_pnl) * (benchmark_avg_notional / avg_notional)


def _parse_range_utc(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def window_days(range_start_utc: str, range_end_utc: str) -> float:
    """Inclusive UTC window length in days, or 0 if either bound is missing."""
    start = _parse_range_utc(range_start_utc)
    end = _parse_range_utc(range_end_utc)
    if start is None or end is None:
        return 0.0
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / 86400.0


def annualized_cap_norm(cap_norm: float, *, window_days: float) -> float:
    """Scale capital-normalized PnL to a 365.25-day year.

    Only compare annualized values from the same (or nearly same) window.
    Do not rank a 30d annualized figure against a 1y run.
    """
    if window_days <= 0:
        return float(cap_norm)
    return float(cap_norm) * (ANNUALIZATION_DAYS / window_days)


def resolve_pullback_entry_policy(
    *,
    budget_quote: float,
    atr_pct: float | None,
    params: dict[str, Any],
) -> PullbackEntryPolicy:
    """Map budget + ATR% into per-fill notional / SL / TP.

    When both dynamic flags are off, returns the fixed budget and ``sl_pct`` /
    ``tp_pct`` unchanged (identity with the pre-dynamics ``_build_create`` path).
    """
    ref_vol = float(params.get("ref_volatility_pct") or 1.0)
    pair_vol = pair_volatility_pct(atr_pct, ref_vol)
    base_sl = float(params.get("sl_pct") or 3.0)
    base_tp = float(params.get("tp_pct") or 6.0)

    sizing_multiplier = 1.0
    notional = float(budget_quote)
    if _as_bool(params.get("enable_dynamic_sizing")):
        sizing_multiplier = compute_inverse_vol_multiplier(
            pair_vol,
            ref_vol,
            float(params.get("min_vol_mult") or 0.5),
            float(params.get("max_vol_mult") or 1.5),
        )
        notional = float(budget_quote) * sizing_multiplier

    sl_pct = base_sl
    tp_pct = base_tp
    if _as_bool(params.get("enable_dynamic_barriers")):
        sl_pct, tp_pct = compute_vol_scaled_barriers(
            pair_vol,
            ref_vol=ref_vol,
            sl_pct=base_sl,
            tp_pct=base_tp,
            sl_vol_exponent=float(params.get("sl_vol_exponent") or 1.0),
            tp_vol_exponent=float(params.get("tp_vol_exponent") or 1.0),
            sl_min_pct=float(params.get("sl_min_pct") or 2.0),
            sl_max_pct=float(params.get("sl_max_pct") or 6.0),
            tp_min_pct=float(params.get("tp_min_pct") or 4.0),
            tp_max_pct=float(params.get("tp_max_pct") or 12.0),
        )

    return PullbackEntryPolicy(
        notional_quote=notional,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        volatility_proxy_pct=pair_vol,
        sizing_multiplier=sizing_multiplier,
    )
