"""Load completed 1h OHLC bars for impulse diagnostics / pullback replay."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from routines.lib.binance_candle_cache import load_candles_in_range


def completed_1h_candles_before(
    pair: str,
    as_of: dt.datetime,
    *,
    lookback_hours: int = 48,
    cache_dir: Path | None = None,
) -> list[dict[str, float]]:
    """Return completed 1h candles with open_time < as_of (UTC)."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=dt.timezone.utc)
    else:
        as_of = as_of.astimezone(dt.timezone.utc)
    end_ms = int(as_of.timestamp() * 1000) - 1
    start = as_of - dt.timedelta(hours=max(24, int(lookback_hours)))
    start_ms = int(start.timestamp() * 1000)
    candles = load_candles_in_range(
        pair,
        "1h",
        start_ms,
        end_ms,
        cache_dir=cache_dir,
    )
    # Drop any bar that has not fully closed by as_of.
    hour_ms = 3_600_000
    return [
        c
        for c in candles
        if int(c["timestamp_ms"]) + hour_ms <= int(as_of.timestamp() * 1000)
    ]


def candles_1h_for_tick(
    pair: str,
    tick_time: dt.datetime,
    *,
    cache_dir: Path | None = None,
    lookback_hours: int = 48,
) -> list[dict[str, float]]:
    return completed_1h_candles_before(
        pair,
        tick_time,
        lookback_hours=lookback_hours,
        cache_dir=cache_dir,
    )


def annotate_trade_impulse(
    trade: Any,
    *,
    cache_dir: Path | None = None,
    lookback_bars: int = 2,
    impulse_atr_mult: float = 1.25,
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
    candles = completed_1h_candles_before(pair, entry_time, cache_dir=cache_dir)
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
