"""Trading-pair format helpers for cross-venue candle caches."""

from __future__ import annotations


def binance_pair_from_any(trading_pair: str) -> str:
    """Map HL-style or bare symbols to Binance USDT-M pair names."""
    if trading_pair.endswith("-USDT"):
        return trading_pair
    if trading_pair.endswith("-USD"):
        return f"{trading_pair[:-4]}-USDT"
    if "-" in trading_pair:
        base = trading_pair.split("-", 1)[0]
        return f"{base}-USDT"
    return f"{trading_pair}-USDT"


def hl_pair_from_any(trading_pair: str) -> str:
    """Map Binance-style pairs to HL USD pair names."""
    if trading_pair.endswith("-USD") and not trading_pair.endswith("-USDT"):
        return trading_pair
    if trading_pair.endswith("-USDT"):
        return f"{trading_pair[:-5]}-USD"
    return f"{trading_pair}-USD"
