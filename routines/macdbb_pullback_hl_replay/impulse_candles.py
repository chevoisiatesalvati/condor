"""Load 1h OHLC as-of a tick for impulse / MACD (matches live forming bar)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

from routines.lib.as_of_1h_candles import (
    LIVE_MACD_MAX_RECORDS,
    as_of_1h_candles,
)


CandleSource = Literal["hyperliquid", "binance_perpetual"]


def _load_candles_in_range(
    pair: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    cache_dir: Path | None,
    candle_source: CandleSource,
) -> list[dict[str, float]]:
    if candle_source == "binance_perpetual":
        from routines.lib.binance_candle_cache import load_candles_in_range
    else:
        from routines.lib.hl_candle_cache import load_candles_in_range
    return load_candles_in_range(
        pair,
        interval,
        start_ms,
        end_ms,
        cache_dir=cache_dir,
    )


def candles_1h_for_tick(
    pair: str,
    tick_time: dt.datetime,
    *,
    cache_dir: Path | None = None,
    candle_source: CandleSource = "hyperliquid",
    lookback_hours: int = LIVE_MACD_MAX_RECORDS,
) -> list[dict[str, float]]:
    """Completed 1h bars plus 1m-synthesized forming bar, last lookback_hours."""
    if tick_time.tzinfo is None:
        tick_time = tick_time.replace(tzinfo=dt.timezone.utc)
    else:
        tick_time = tick_time.astimezone(dt.timezone.utc)
    as_of_ms = int(tick_time.timestamp() * 1000)
    lookback = max(24, int(lookback_hours))
    start_1h_ms = as_of_ms - lookback * 3_600_000
    candles_1h = _load_candles_in_range(
        pair,
        "1h",
        start_1h_ms,
        as_of_ms,
        cache_dir=cache_dir,
        candle_source=candle_source,
    )
    hour_open_ms = (as_of_ms // 3_600_000) * 3_600_000
    candles_1m = _load_candles_in_range(
        pair,
        "1m",
        hour_open_ms,
        as_of_ms,
        cache_dir=cache_dir,
        candle_source=candle_source,
    )
    return as_of_1h_candles(
        candles_1h,
        candles_1m,
        as_of_ms,
        max_records=lookback,
    )


def completed_1h_candles_before(
    pair: str,
    as_of: dt.datetime,
    *,
    lookback_hours: int = LIVE_MACD_MAX_RECORDS,
    cache_dir: Path | None = None,
    candle_source: CandleSource = "hyperliquid",
) -> list[dict[str, float]]:
    """Alias used by trade-impulse annotation (same as-of series as live)."""
    return candles_1h_for_tick(
        pair,
        as_of,
        cache_dir=cache_dir,
        candle_source=candle_source,
        lookback_hours=lookback_hours,
    )


def annotate_trade_impulse(
    trade: Any,
    *,
    cache_dir: Path | None = None,
    lookback_bars: int = 2,
    impulse_atr_mult: float = 1.25,
    candle_source: CandleSource = "hyperliquid",
) -> dict[str, Any]:
    from condor.strategy_runners.macdbb_pullback.entry_quality import compute_impulse_metrics

    entry_time = getattr(trade, "entry_time_utc", None)
    side = str(getattr(trade, "side", "long")).lower()
    pair = str(getattr(trade, "pair", ""))
    exit_reason = str(getattr(trade, "exit_reason", ""))
    row: dict[str, Any] = {
        "pair": pair,
        "side": side,
        "exit_reason": exit_reason,
        "pnl_quote": float(getattr(trade, "pnl_quote", 0) or 0),
        "return_pct": float(getattr(trade, "return_pct", 0) or 0),
        "entry_class": str(getattr(trade, "entry_class", "")),
        "entry_time_utc": entry_time.isoformat() if entry_time else "",
        "is_stop_loss": "stop_loss" in exit_reason,
        "is_take_profit": "take_profit" in exit_reason,
        "impulse": False,
        "atr_pct": 0.0,
        "signed_body_sum_pct": 0.0,
        "bars_used": 0,
    }
    if entry_time is None or not pair:
        return row
    candles = candles_1h_for_tick(
        pair, entry_time, cache_dir=cache_dir, candle_source=candle_source
    )
    metrics = compute_impulse_metrics(
        candles,
        "long" if side == "long" else "short",
        lookback_bars=lookback_bars,
        impulse_atr_mult=impulse_atr_mult,
    )
    row.update(
        {
            "impulse": metrics.is_impulse,
            "atr_pct": metrics.atr_pct,
            "signed_body_sum_pct": metrics.signed_body_sum_pct,
            "bars_used": metrics.bars_used,
        }
    )
    return row


def summarize_impulse_exit_rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _bucket(flag: bool) -> dict[str, Any]:
        subset = [r for r in rows if bool(r.get("impulse")) is flag]
        n = len(subset)
        sl = sum(1 for r in subset if r.get("is_stop_loss"))
        tp = sum(1 for r in subset if r.get("is_take_profit"))
        return {
            "trades": n,
            "stop_loss": sl,
            "take_profit": tp,
            "sl_rate": (sl / n) if n else 0.0,
            "tp_rate": (tp / n) if n else 0.0,
            "avg_pnl": (sum(float(r["pnl_quote"]) for r in subset) / n) if n else 0.0,
        }

    return {
        "total_trades": len(rows),
        "impulse_true": _bucket(True),
        "impulse_false": _bucket(False),
    }
