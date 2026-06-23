"""Shared tick-level market state computation from HL candles (config-independent)."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from routines.macdbb_scanner_aggressive_hl_replay.models import ParsedReport
from routines.macdbb_scanner_aggressive_hl_replay.reports import ParsedScannerReport, ScannerPairRow
from routines.macdbb_scanner_aggressive_hl_replay.scanner_queue import build_scanner_queue
from routines.market_scanner import analyze_pair, classify_markets

# HL 1m candleSnapshot retention is short; use 5m for older ticks.
HL_1M_MAX_AGE_DAYS = 4
CANDLE_MINUTES = {"1m": 1, "5m": 5}


@dataclass(frozen=True)
class TickMarketSettings:
    lookback_hours: int = 6
    top_n: int = 30
    min_volume_usd: float = 2_000_000.0
    mature_count: int = 8
    degen_count: int = 8
    candidate_pool: int = 45
    macd_review_count: int = 5
    macd_pairs_superset: int = 12
    max_concurrent: int = 30


class CandleWindowLoader(Protocol):
    async def get_interval_window(
        self,
        trading_pair: str,
        interval: str,
        end: dt.datetime,
        hours: int,
    ) -> list[dict[str, float]]: ...


def scanner_interval_for_tick(tick_time: dt.datetime) -> str:
    age_days = (dt.datetime.now(dt.timezone.utc) - tick_time.astimezone(dt.timezone.utc)).days
    return "1m" if age_days <= HL_1M_MAX_AGE_DAYS else "5m"


def bars_for_hours(hours: int, interval: str) -> int:
    minutes = CANDLE_MINUTES[interval]
    return max(1, (hours * 60) // minutes)


def volume_window_bars(interval: str) -> int:
    return bars_for_hours(24, interval)


def quote_volume_24h(candles: list[dict[str, float]]) -> float:
    if not candles:
        return 0.0
    total = 0.0
    for candle in candles:
        try:
            total += float(candle["close"]) * float(candle["volume"])
        except (KeyError, TypeError, ValueError):
            continue
    return total


def price_change_pct(candles: list[dict[str, float]]) -> float:
    if len(candles) < 2:
        return 0.0
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first <= 0:
        return 0.0
    return ((last - first) / first) * 100.0


def quote_volume_window(candles: list[dict[str, float]], interval: str) -> float:
    window = candles[-volume_window_bars(interval) :]
    return quote_volume_24h(window)


def price_change_window(candles: list[dict[str, float]], interval: str) -> float:
    window = candles[-volume_window_bars(interval) :]
    return price_change_pct(window)


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for index in range(1, len(values)):
        result[index] = alpha * values[index] + (1 - alpha) * result[index - 1]
    return result


def compute_macdbb_from_closes(
    closes: np.ndarray,
    *,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> dict[str, Any] | None:
    min_required = macd_slow + macd_signal_period + bb_period
    if len(closes) < min_required:
        return None

    ema_fast = _ema(closes, macd_fast)
    ema_slow = _ema(closes, macd_slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, macd_signal_period)
    histogram = macd_line - signal_line

    n = len(closes)
    bb_mid = np.array([np.mean(closes[max(0, i - bb_period + 1) : i + 1]) for i in range(n)])
    bb_std_arr = np.array([np.std(closes[max(0, i - bb_period + 1) : i + 1]) for i in range(n)])
    bb_upper = bb_mid + bb_std * bb_std_arr
    bb_lower = bb_mid - bb_std * bb_std_arr

    close = float(closes[-1])
    macd_curr, macd_prev = float(macd_line[-1]), float(macd_line[-2])
    sig_curr, sig_prev = float(signal_line[-1]), float(signal_line[-2])
    hist_curr, hist_prev = float(histogram[-1]), float(histogram[-2])
    bb_up, bb_mid_val, bb_lo = float(bb_upper[-1]), float(bb_mid[-1]), float(bb_lower[-1])

    bb_range = bb_up - bb_lo
    bb_pos = (close - bb_lo) / bb_range if bb_range > 0 else 0.5

    bullish_cross = macd_prev < sig_prev and macd_curr >= sig_curr
    bearish_cross = macd_prev > sig_prev and macd_curr <= sig_curr
    c_long_cross = bullish_cross
    c_long_bb = close <= bb_mid_val
    c_short_cross = bearish_cross
    c_short_bb = close >= bb_up
    c_short_macd_neg = macd_curr < 0
    long_signal = c_long_cross and c_long_bb
    short_signal = c_short_cross and c_short_bb and c_short_macd_neg
    if long_signal:
        signal = "LONG"
    elif short_signal:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    trend = "bullish" if macd_curr > 0 else "bearish"
    momentum = "increasing" if abs(hist_curr) > abs(hist_prev) else "decreasing"

    return {
        "signal": signal,
        "close": close,
        "bb_up": bb_up,
        "bb_mid_val": bb_mid_val,
        "bb_lo": bb_lo,
        "bb_pos": bb_pos,
        "macd_curr": macd_curr,
        "sig_curr": sig_curr,
        "hist_curr": hist_curr,
        "trend": trend,
        "momentum": momentum,
        "c_long_cross": c_long_cross,
        "c_long_bb": c_long_bb,
        "c_short_cross": c_short_cross,
        "c_short_bb": c_short_bb,
        "c_short_macd_neg": c_short_macd_neg,
    }


def metrics_to_parsed_report(
    trading_pair: str,
    interval: str,
    metrics: dict[str, Any],
) -> ParsedReport:
    return ParsedReport(
        pair=trading_pair,
        interval=interval,
        signal=metrics["signal"],
        price=float(metrics["close"]),
        bb_pos_pct=float(metrics["bb_pos"] * 100),
        bb_mid=float(metrics["bb_mid_val"]),
        bb_upper=float(metrics["bb_up"]),
        macd=float(metrics["macd_curr"]),
        signal_line=float(metrics["sig_curr"]),
        histogram=float(metrics["hist_curr"]),
        trend=str(metrics["trend"]),
        momentum=str(metrics["momentum"]),
        bullish_cross=bool(metrics["c_long_cross"]),
        price_le_mid=bool(metrics["c_long_bb"]),
        bearish_cross=bool(metrics["c_short_cross"]),
        price_ge_upper=bool(metrics["c_short_bb"]),
        macd_lt_zero=bool(metrics["c_short_macd_neg"]),
    )


def analysis_rows_to_scanner_rows(items: list[dict[str, Any]]) -> list[ScannerPairRow]:
    return [
        ScannerPairRow(
            pair=item["trading_pair"],
            volume_24h_usd=float(item["volume_24h_usd"]),
            price_change_24h=float(item["price_change_24h"]),
            natr_mean=float(item["natr_mean"]),
            natr_cv=float(item["natr_cv"]),
            bucket_cv=float(item["bucket_cv"]),
            price_range_pct=float(item["price_range_pct"]),
        )
        for item in items
    ]


def classified_to_parsed_scanner(
    classified: dict[str, Any],
    *,
    lookback_hours: int,
) -> ParsedScannerReport:
    return ParsedScannerReport(
        total_analyzed=int(classified["total_analyzed"]),
        mature=analysis_rows_to_scanner_rows(classified["mature"]),
        degen=analysis_rows_to_scanner_rows(classified["degen"]),
        lookback_hours=lookback_hours,
    )


@dataclass
class TickMarketState:
    tick_time: dt.datetime
    scanner_interval: str
    parsed_scanner: ParsedScannerReport | None
    macdbb_reports: list[ParsedReport]
    macd_pairs: list[str]


async def _quote_volume_for_pair(
    *,
    pair: str,
    scanner_interval: str,
    tick_time: dt.datetime,
    fetch_hours: int,
    loader: CandleWindowLoader,
    volume_loader: CandleWindowLoader | None,
    primary_candles: list[dict[str, float]],
) -> float | None:
    """Return 24h quote volume from volume_loader when set, else from primary candles."""
    if volume_loader is None:
        return quote_volume_window(primary_candles, scanner_interval)
    try:
        volume_candles = await volume_loader.get_interval_window(
            pair,
            scanner_interval,
            tick_time,
            fetch_hours,
        )
    except Exception:
        return None
    min_bars = volume_window_bars(scanner_interval)
    if len(volume_candles) < min_bars:
        return None
    return quote_volume_window(volume_candles, scanner_interval)


async def compute_tick_market_state(
    tick_time: dt.datetime,
    *,
    universe: list[dict[str, Any]],
    loader: CandleWindowLoader,
    settings: TickMarketSettings | None = None,
    strategy_params: dict[str, Any] | None = None,
    store_macd_for_superset: bool = False,
    volume_loader: CandleWindowLoader | None = None,
) -> TickMarketState:
    """Compute config-independent scanner + MACD market snapshots for one tick."""
    settings = settings or TickMarketSettings()
    scanner_interval = scanner_interval_for_tick(tick_time)
    candle_minutes = CANDLE_MINUTES[scanner_interval]
    fetch_hours = max(settings.lookback_hours + 24, 30)
    min_bars = bars_for_hours(settings.lookback_hours, scanner_interval)
    bucket_size = max(1, 15 // candle_minutes)
    semaphore = asyncio.Semaphore(max(1, settings.max_concurrent))

    async def _rank_candidate(
        candidate: dict[str, Any],
    ) -> tuple[float, dict[str, Any], list[dict[str, float]]] | None:
        pair = candidate["trading_pair"]
        async with semaphore:
            try:
                candles = await loader.get_interval_window(
                    pair,
                    scanner_interval,
                    tick_time,
                    fetch_hours,
                )
            except Exception:
                return None
        if len(candles) < min_bars:
            return None
        volume_24h = await _quote_volume_for_pair(
            pair=pair,
            scanner_interval=scanner_interval,
            tick_time=tick_time,
            fetch_hours=fetch_hours,
            loader=loader,
            volume_loader=volume_loader,
            primary_candles=candles,
        )
        if volume_24h is None or volume_24h < settings.min_volume_usd:
            return None
        return (volume_24h, candidate, candles)

    candidate_results = await asyncio.gather(
        *[
            _rank_candidate(candidate)
            for candidate in universe[: settings.candidate_pool]
        ],
        return_exceptions=True,
    )
    ranked: list[tuple[float, dict[str, Any], list[dict[str, float]]]] = []
    for result in candidate_results:
        if isinstance(result, Exception) or result is None:
            continue
        ranked.append(result)

    ranked.sort(key=lambda row: row[0], reverse=True)
    top = ranked[: settings.top_n]
    analyses: list[dict[str, Any]] = []
    lookback_bars = bars_for_hours(settings.lookback_hours, scanner_interval)
    for volume_24h, candidate, candles in top:
        analysis_candles = candles[-lookback_bars:]
        pair_info = {
            "trading_pair": candidate["trading_pair"],
            "price": float(analysis_candles[-1]["close"]),
            "price_change_pct": price_change_window(candles, scanner_interval),
            "volume_24h_usd": volume_24h,
        }
        result = analyze_pair(
            analysis_candles,
            pair_info,
            bucket_size=bucket_size,
        )
        if result:
            analyses.append(result)

    if not analyses:
        return TickMarketState(
            tick_time=tick_time,
            scanner_interval=scanner_interval,
            parsed_scanner=None,
            macdbb_reports=[],
            macd_pairs=[],
        )

    classified = classify_markets(analyses, settings.mature_count, settings.degen_count)
    parsed_scanner = classified_to_parsed_scanner(
        classified,
        lookback_hours=settings.lookback_hours,
    )

    macd_pairs: list[str] = []
    if strategy_params is not None:
        queue = build_scanner_queue(parsed_scanner, strategy_params)
        review_count = (
            settings.macd_pairs_superset
            if store_macd_for_superset
            else settings.macd_review_count
        )
        macd_pairs = queue.macd_pairs[:review_count]
    else:
        macd_pairs = [row.pair for row in parsed_scanner.mature + parsed_scanner.degen][
            : settings.macd_review_count
        ]

    macdbb_reports: list[ParsedReport] = []

    async def _fetch_macdbb(pair: str, interval: str, hours: int) -> ParsedReport | None:
        async with semaphore:
            try:
                candles = await loader.get_interval_window(
                    pair,
                    interval,
                    tick_time,
                    hours,
                )
            except Exception:
                return None
        closes = np.array([float(c["close"]) for c in candles], dtype=float)
        metrics = compute_macdbb_from_closes(closes)
        if metrics is None:
            return None
        return metrics_to_parsed_report(pair, interval, metrics)

    macd_tasks = [
        _fetch_macdbb(pair, interval, hours)
        for pair in macd_pairs
        for interval, hours in (("1h", 250), ("4h", 400))
    ]
    macd_results = await asyncio.gather(*macd_tasks, return_exceptions=True)
    for result in macd_results:
        if isinstance(result, Exception) or result is None:
            continue
        macdbb_reports.append(result)

    return TickMarketState(
        tick_time=tick_time,
        scanner_interval=scanner_interval,
        parsed_scanner=parsed_scanner,
        macdbb_reports=macdbb_reports,
        macd_pairs=macd_pairs,
    )
