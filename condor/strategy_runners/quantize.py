"""Shared size quantization for live + backtest parity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuantizeResult:
    base_amount: float
    notional_quote: float
    clipped: bool


def quote_to_base_amount(
    *,
    notional_quote: float,
    price: float,
    min_notional_quote: float = 0.0,
    max_notional_quote: float | None = None,
    amount_step: float = 0.0,
) -> QuantizeResult:
    """Convert quote notional to base amount with optional lot step and clamps.

    Live and timeline sim must call this before opening so sizes match within
    the same fee/lot model version.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    notional = float(notional_quote)
    clipped = False
    if min_notional_quote > 0 and notional < min_notional_quote:
        notional = float(min_notional_quote)
        clipped = True
    if max_notional_quote is not None and max_notional_quote > 0 and notional > max_notional_quote:
        notional = float(max_notional_quote)
        clipped = True
    amount = notional / price
    if amount_step and amount_step > 0:
        steps = int(amount / amount_step)
        amount = steps * amount_step
        notional = amount * price
        clipped = True
    return QuantizeResult(base_amount=amount, notional_quote=notional, clipped=clipped)


def apply_fee_slippage(
    notional_quote: float,
    *,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> float:
    """Inflate required notional for round-trip fee + adverse slippage (research model)."""
    mult = 1.0 + (fee_bps + slippage_bps) / 10_000.0
    return float(notional_quote) * mult
