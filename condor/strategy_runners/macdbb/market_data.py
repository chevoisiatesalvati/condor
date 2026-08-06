"""Live market signal fetch for MACDBB DeterministicRunner."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from condor.strategy_runners.macdbb.types import SignalSnapshot

log = logging.getLogger(__name__)


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def signal_from_closes(
    pair: str,
    closes: np.ndarray,
    *,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    natr_mean_pct: float | None = None,
) -> SignalSnapshot | None:
    """Compute a SignalSnapshot from close prices (same math as macd_bb_analysis)."""
    if len(closes) < max(macd_slow, bb_period) + 2:
        return None
    ema_fast = _ema(closes, macd_fast)
    ema_slow = _ema(closes, macd_slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, macd_signal_period)
    histogram = macd_line - signal_line
    n = len(closes)
    p = bb_period
    bb_mid = np.array([np.mean(closes[max(0, i - p + 1) : i + 1]) for i in range(n)])
    bb_std_arr = np.array([np.std(closes[max(0, i - p + 1) : i + 1]) for i in range(n)])
    bb_upper = bb_mid + bb_std * bb_std_arr
    bb_lower = bb_mid - bb_std * bb_std_arr
    close = float(closes[-1])
    macd_curr, macd_prev = float(macd_line[-1]), float(macd_line[-2])
    sig_curr, sig_prev = float(signal_line[-1]), float(signal_line[-2])
    hist_curr, hist_prev = float(histogram[-1]), float(histogram[-2])
    bb_up, bb_mid_val, bb_lo = float(bb_upper[-1]), float(bb_mid[-1]), float(bb_lower[-1])
    bb_range = bb_up - bb_lo
    bb_pos = (close - bb_lo) / bb_range if bb_range > 0 else 0.5
    return SignalSnapshot(
        pair=pair,
        price=close,
        bb_pos_pct=bb_pos * 100.0,
        bb_mid=bb_mid_val,
        bb_upper=bb_up,
        macd=macd_curr,
        signal_line=sig_curr,
        histogram=hist_curr,
        trend="bullish" if macd_curr > 0 else "bearish",
        momentum="increasing" if abs(hist_curr) > abs(hist_prev) else "decreasing",
        bullish_cross=macd_prev < sig_prev and macd_curr >= sig_curr,
        bearish_cross=macd_prev > sig_prev and macd_curr <= sig_curr,
        natr_mean_pct=natr_mean_pct,
    )


async def fetch_candidate_pairs(params: dict[str, Any]) -> tuple[list[str], str | None, int]:
    """Return (pairs, scanner_regime, tradeable_count) from HL scanner or watchlist."""
    watchlist = params.get("watchlist") or params.get("scanner_pairs")
    if isinstance(watchlist, list) and watchlist:
        pairs = [str(p) for p in watchlist if str(p).strip()]
        return pairs, None, len(pairs)

    try:
        from routines.hyperliquid_market_scanner import Config, run
        from types import SimpleNamespace

        cfg = Config(
            top_n=int(params.get("scanner_top_n") or 30),
            lookback_hours=int(params.get("scanner_lookback_hours") or 6),
            min_volume_usd=float(params.get("scanner_min_volume_usd") or 2_000_000),
            mature_count=int(params.get("scanner_mature_count") or 8),
            degen_count=int(params.get("scanner_degen_count") or 8),
            exclude_hip3=bool(params.get("scanner_exclude_hip3") or False),
        )
        context = SimpleNamespace(_chat_id=None, user_data={}, chat_data={})
        text = await run(cfg, context)  # type: ignore[arg-type]
        pairs = _parse_pairs_from_scanner_text(str(text or ""))
        primary = int(params.get("macd_queue_primary_size") or 8)
        return pairs[: max(primary, 8)], "mature", len(pairs)
    except Exception:
        log.warning("MACDBB scanner fetch failed", exc_info=True)
        return [], None, 0


def _parse_pairs_from_scanner_text(text: str) -> list[str]:
    """Best-effort extract trading pairs from scanner markdown/table text."""
    pairs: list[str] = []
    seen: set[str] = set()
    for token in text.replace("|", " ").replace(",", " ").split():
        if "-" not in token:
            continue
        candidate = token.strip("`* ")
        if candidate.count("-") != 1:
            continue
        base, quote = candidate.split("-", 1)
        if not base or not quote:
            continue
        if not quote.upper().endswith(("USD", "USDC", "USDT")):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        pairs.append(candidate)
    return pairs


async def load_macdbb_signals(
    params: dict[str, Any],
    *,
    extra_pairs: list[str] | None = None,
) -> tuple[list[SignalSnapshot], str | None, int]:
    """Fetch scanner candidates + MACD/BB snapshots for the live runner.

    ``extra_pairs`` (typically open-leg pairs) are unioned into the candle queue
    so Step 5 monitoring still runs when a symbol drops out of the scanner top-N.
    """
    pairs, regime, tradeable = await fetch_candidate_pairs(params)
    seen: set[str] = set()
    ordered: list[str] = []
    for pair in list(pairs) + list(extra_pairs or []):
        key = str(pair or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    if not ordered:
        return [], regime, tradeable

    from routines.lib.hl_candles import fetch_hl_candles

    signals: list[SignalSnapshot] = []
    interval = str(params.get("macd_interval") or "1h")
    max_records = int(params.get("macd_max_records") or 200)
    for pair in ordered:
        try:
            candles = await fetch_hl_candles(pair, interval, max_records)
            closes = np.array([float(c["close"]) for c in candles], dtype=float)
            snap = signal_from_closes(pair, closes)
            if snap is not None:
                signals.append(snap)
        except Exception:
            log.warning("MACDBB signal load failed for %s", pair, exc_info=True)
    return signals, regime, tradeable or len(signals)
