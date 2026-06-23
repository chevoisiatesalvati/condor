"""Prefetch Hyperliquid historical closes for replay tick timestamps."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import numpy as np

from condor.trading_agent.policies.macdbb_dynamic import (
    SCANNER_NATR_LOOKBACK_HOURS_DEFAULT,
    SCANNER_NATR_MIN_BARS,
)

if TYPE_CHECKING:
    from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta

logger = logging.getLogger(__name__)

HlPriceCache = dict[tuple[str, int], float]
HlCandleCache = dict[str, list[dict[str, float]]]
ReplayHlPrefetch = tuple[
    dict[int, HlPriceCache],
    HlCandleCache,
    HlCandleCache,
    HlCandleCache,
]

# Hyperliquid 1m candleSnapshot retention is short; skip 1m API for older sessions.
HL_1M_API_MAX_AGE_DAYS = 4

_INTERVAL_MAX_DELTA_MS: dict[str, int] = {
    "1m": 45 * 60 * 1000,
    "5m": 20 * 60 * 1000,
    "15m": 45 * 60 * 1000,
    "1h": 90 * 60 * 1000,
    "4h": 5 * 60 * 60 * 1000,
}


@dataclass(frozen=True)
class HlPrefetchSettings:
    interval: str = "5m"
    barrier_interval: str = "1m"
    buffer_hours: int = 1
    vol_lookback_hours: int = SCANNER_NATR_LOOKBACK_HOURS_DEFAULT
    max_concurrent: int = 1
    request_interval_ms: int = 400
    max_retries: int = 6
    use_cache: bool = True
    refresh_cache: bool = False
    cache_dir: Path | None = None
    candle_source: str = "hyperliquid"
    build_session_prices: bool = True


def _needs_session_price_cache(config: object) -> bool:
    price_source = getattr(config, "price_source", "auto")
    return price_source in ("auto", "hl_candles", "binance_candles")


def hl_prefetch_settings_from_config(config: object) -> HlPrefetchSettings:
    cache_dir_raw = getattr(config, "hl_cache_dir", None)
    cache_dir = Path(cache_dir_raw) if cache_dir_raw else None
    candle_source = getattr(config, "candle_source", "hyperliquid")
    return HlPrefetchSettings(
        interval=getattr(config, "hl_price_interval", "5m"),
        barrier_interval=getattr(config, "hl_barrier_interval", "1m"),
        vol_lookback_hours=getattr(
            config, "scanner_lookback_hours", SCANNER_NATR_LOOKBACK_HOURS_DEFAULT
        ),
        max_concurrent=getattr(config, "hl_max_concurrent", 1),
        request_interval_ms=getattr(config, "hl_request_interval_ms", 400),
        max_retries=getattr(config, "hl_max_retries", 6),
        use_cache=getattr(config, "hl_use_cache", True),
        refresh_cache=getattr(config, "hl_refresh_cache", False),
        cache_dir=cache_dir,
        candle_source=candle_source,
        build_session_prices=_needs_session_price_cache(config),
    )


def _is_binance_source(source: str) -> bool:
    return source == "binance_perpetual"


def _fill_session_prices_for_pair(
    candles: list[dict[str, float]],
    requests: list[tuple[int, int, dt.datetime, str]],
    session_caches: dict[int, HlPriceCache],
    *,
    max_delta_ms: int,
) -> int:
    """Vectorized nearest-close lookup for one pair across all tick times."""
    if not candles or not requests:
        return 0

    timestamps = np.array(
        [int(c["timestamp_ms"]) for c in candles if "timestamp_ms" in c],
        dtype=np.int64,
    )
    closes = np.array(
        [float(c["close"]) for c in candles if "timestamp_ms" in c],
        dtype=np.float64,
    )
    if timestamps.size == 0:
        return 0

    order = np.argsort(timestamps)
    timestamps = timestamps[order]
    closes = closes[order]

    tick_ms = np.array(
        [int(tick_time.timestamp() * 1000) for _, _, tick_time, _ in requests],
        dtype=np.int64,
    )
    right = np.searchsorted(timestamps, tick_ms, side="left")
    left = np.clip(right - 1, 0, timestamps.size - 1)
    right = np.clip(right, 0, timestamps.size - 1)
    pick_left = np.abs(timestamps[left] - tick_ms) <= np.abs(timestamps[right] - tick_ms)
    indices = np.where(pick_left, left, right)
    deltas = np.abs(timestamps[indices] - tick_ms)
    hits = deltas <= max_delta_ms

    filled = 0
    for index, (session_num, tick_num, _tick_time, journal_pair) in enumerate(requests):
        if not hits[index]:
            continue
        price = float(closes[indices[index]])
        if price <= 0:
            continue
        session_caches[session_num][(journal_pair, tick_num)] = price
        filled += 1
    return filled


def _load_hl_candles():
    """Reload hl_candles so dev hot-reload picks up new exports."""
    import routines.lib.hl_candles as hl_candles_mod

    return importlib.reload(hl_candles_mod)


def _load_binance_candle_cache():
    import routines.lib.binance_candle_cache as binance_candle_cache_mod

    return importlib.reload(binance_candle_cache_mod)


def _load_binance_candles():
    import routines.lib.binance_candles as binance_candles_mod

    return importlib.reload(binance_candles_mod)


def _cache_pair_for_source(pair: str, source: str) -> str:
    if _is_binance_source(source):
        from routines.lib.pair_format import binance_pair_from_any

        return binance_pair_from_any(pair)
    return _canonical_trading_pair(pair)


def _max_nearest_delta_ms(interval: str, interval_ms: int | None) -> int:
    if interval_ms is None:
        return 45 * 60 * 1000
    return _INTERVAL_MAX_DELTA_MS.get(interval, interval_ms * 3)


def _canonical_trading_pair(pair: str) -> str:
    """Map journal pair aliases (BTC, BTC-USD) to one cache/API key."""
    if "-" in pair:
        return pair
    return f"{pair}-USD"


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _filter_candles_in_range(
    candles: list[dict[str, float]],
    start: dt.datetime,
    end: dt.datetime,
) -> list[dict[str, float]]:
    start_ms = int(_ensure_utc(start).timestamp() * 1000)
    end_ms = int(_ensure_utc(end).timestamp() * 1000)
    filtered = [
        candle
        for candle in candles
        if "timestamp_ms" in candle
        and start_ms <= int(candle["timestamp_ms"]) <= end_ms
    ]
    filtered.sort(key=lambda candle: int(candle["timestamp_ms"]))
    return filtered


def _vol_bars_in_lookback(
    candles: list[dict[str, float]],
    tick_times: list[dt.datetime],
    lookback_hours: int,
) -> int:
    if not candles or not tick_times:
        return 0
    end_ms = int(_ensure_utc(max(tick_times)).timestamp() * 1000)
    start_ms = end_ms - lookback_hours * 3600 * 1000
    return sum(
        1
        for candle in candles
        if start_ms <= int(candle.get("timestamp_ms", -1)) <= end_ms
    )


def _vol_series_usable(
    candles: list[dict[str, float]],
    tick_times: list[dt.datetime],
    lookback_hours: int,
) -> bool:
    return _vol_bars_in_lookback(candles, tick_times, lookback_hours) >= SCANNER_NATR_MIN_BARS


def _load_hl_candle_cache():
    """Reload hl_candle_cache so dev hot-reload picks up new exports."""
    import routines.lib.hl_candle_cache as hl_candle_cache_mod

    return importlib.reload(hl_candle_cache_mod)


def _within_1m_api_window(tick_times: list[dt.datetime], *, candle_source: str) -> bool:
    if _is_binance_source(candle_source):
        return True
    if not tick_times:
        return False
    latest = max(_ensure_utc(tick_time) for tick_time in tick_times)
    age = dt.datetime.now(dt.timezone.utc) - latest
    return age <= dt.timedelta(days=HL_1M_API_MAX_AGE_DAYS)


def _tick_pairs(meta: TickMeta) -> set[str]:
    raw = set(meta.macd_pairs) | set(meta.queue_total) | set(meta.signals_1h)
    return {_canonical_trading_pair(pair) for pair in raw}


def _aggregate_pair_requests(
    session_tick_maps: dict[int, dict[int, TickMeta]],
    extra_pairs: set[str] | None = None,
) -> dict[str, list[tuple[int, int, dt.datetime, str]]]:
    """Canonical pair -> [(session_num, tick_num, tick_time, journal_pair), ...]."""
    pair_requests: dict[str, list[tuple[int, int, dt.datetime, str]]] = {}
    session_pairs: dict[int, set[str]] = {}
    session_ticks: dict[int, list[tuple[int, dt.datetime]]] = {}

    for session_num, tick_meta_map in session_tick_maps.items():
        pairs: set[str] = set()
        if extra_pairs:
            pairs.update(_canonical_trading_pair(pair) for pair in extra_pairs)
        ticks: list[tuple[int, dt.datetime]] = []
        for tick_num, meta in sorted(tick_meta_map.items()):
            ticks.append((tick_num, meta.timestamp))
            pairs.update(_tick_pairs(meta))
        session_pairs[session_num] = pairs
        session_ticks[session_num] = ticks

    for session_num, pairs in session_pairs.items():
        ticks = session_ticks[session_num]
        for canonical in sorted(pairs):
            journal_pair = canonical
            for tick_num, tick_time in ticks:
                pair_requests.setdefault(canonical, []).append(
                    (session_num, tick_num, tick_time, journal_pair)
                )
    return pair_requests


def scan_barriers_between(
    candles: list[dict[str, float]],
    start: dt.datetime,
    end: dt.datetime,
    side: str,
    entry_price: float,
    sl_pct: float,
    tp_pct: float,
) -> tuple[str, float] | None:
    """Return (exit_reason, barrier_price) for the first SL/TP hit in (start, end]."""
    if entry_price <= 0 or not candles:
        return None

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    sl_threshold = sl_pct / 100.0
    tp_threshold = tp_pct / 100.0

    if side == "long":
        sl_price = entry_price * (1.0 - sl_threshold)
        tp_price = entry_price * (1.0 + tp_threshold)
    else:
        sl_price = entry_price * (1.0 + sl_threshold)
        tp_price = entry_price * (1.0 - tp_threshold)

    window = [
        candle
        for candle in candles
        if "timestamp_ms" in candle and start_ms < int(candle["timestamp_ms"]) <= end_ms
    ]
    window.sort(key=lambda candle: int(candle["timestamp_ms"]))

    for candle in window:
        low = float(candle["low"])
        high = float(candle["high"])
        if side == "long":
            if low <= sl_price:
                return ("stop_loss_close_proxy", sl_price)
            if high >= tp_price:
                return ("take_profit_close_proxy", tp_price)
        else:
            if high >= sl_price:
                return ("stop_loss_close_proxy", sl_price)
            if low <= tp_price:
                return ("take_profit_close_proxy", tp_price)
    return None


def hl_cache_has_prices(
    tick_meta_map: dict[int, TickMeta],
    hl_price_cache: HlPriceCache | None,
) -> bool:
    if not hl_price_cache:
        return False
    for meta in tick_meta_map.values():
        for pair in _tick_pairs(meta):
            if hl_price_cache.get((pair, meta.tick), 0.0) > 0:
                return True
    return False


def _configure_hl_throttle(settings: HlPrefetchSettings) -> None:
    hl_candles = _load_hl_candles()
    hl_candles.configure_hl_rate_limit(
        request_interval_ms=settings.request_interval_ms,
        max_retries=settings.max_retries,
    )
    hl_candles.reset_hl_rate_limit_state()


def _configure_candle_throttle(settings: HlPrefetchSettings) -> None:
    if _is_binance_source(settings.candle_source):
        binance_candles = _load_binance_candles()
        binance_candles.configure_binance_rate_limit(
            request_interval_ms=settings.request_interval_ms,
            max_retries=settings.max_retries,
        )
        return
    _configure_hl_throttle(settings)


async def prefetch_replay_hl_prices(
    session_tick_maps: dict[int, dict[int, TickMeta]],
    *,
    settings: HlPrefetchSettings | None = None,
    extra_pairs: set[str] | None = None,
) -> ReplayHlPrefetch:
    """Fetch tick-close prices and OHLC series for replay (price + barrier + vol)."""
    if not session_tick_maps:
        return {}, {}, {}, {}

    opts = settings or HlPrefetchSettings()
    _configure_candle_throttle(opts)

    use_binance = _is_binance_source(opts.candle_source)
    if use_binance:
        candle_mod = _load_binance_candles()
        candle_cache_mod = _load_binance_candle_cache()
        fetch_between_cached = candle_cache_mod.fetch_binance_candles_between_cached
        pair_label = lambda pair: candle_mod.trading_pair_to_symbol(_cache_pair_for_source(pair, opts.candle_source))
    else:
        candle_mod = _load_hl_candles()
        candle_cache_mod = _load_hl_candle_cache()
        fetch_between_cached = candle_cache_mod.fetch_hl_candles_between_cached
        pair_label = lambda pair: candle_mod.trading_pair_to_hl_coin(pair)

    interval_ms = candle_mod._INTERVAL_MS.get(opts.interval)
    barrier_interval_ms = candle_mod._INTERVAL_MS.get(opts.barrier_interval)
    max_delta_ms = _max_nearest_delta_ms(opts.interval, interval_ms)

    pair_requests = _aggregate_pair_requests(session_tick_maps, extra_pairs=extra_pairs)
    if not pair_requests:
        return {session_num: {} for session_num in session_tick_maps}, {}, {}, {}

    session_caches: dict[int, HlPriceCache] = {
        session_num: {} for session_num in session_tick_maps
    }
    pair_candles: HlCandleCache = {}
    pair_barrier_candles: HlCandleCache = {}
    pair_vol_candles: HlCandleCache = {}
    semaphore = asyncio.Semaphore(max(1, opts.max_concurrent))
    pairs_sorted = sorted(pair_requests)
    load_barrier_series = opts.barrier_interval != opts.interval
    source_label = "Binance" if use_binance else "HL"

    async with aiohttp.ClientSession() as session:

        async def _fetch_series(
            pair: str,
            interval: str,
            coverage_ms: int,
            *,
            fill_gaps: bool = True,
            history_hours: int | None = None,
            ignore_api_skip: bool = False,
        ) -> list[dict[str, float]]:
            requests = pair_requests[pair]
            tick_times = [tick_time for _, _, tick_time, _ in requests]
            back_hours = history_hours if history_hours is not None else opts.buffer_hours
            start = min(tick_times) - dt.timedelta(hours=back_hours)
            end = max(tick_times) + dt.timedelta(hours=opts.buffer_hours)
            cache_pair = _cache_pair_for_source(pair, opts.candle_source)
            async with semaphore:
                try:
                    return await fetch_between_cached(
                        cache_pair,
                        interval,
                        start,
                        end,
                        session=session,
                        cache_dir=opts.cache_dir,
                        use_cache=opts.use_cache,
                        refresh_cache=opts.refresh_cache,
                        coverage_end_ms=coverage_ms,
                        fill_gaps=fill_gaps,
                        ignore_api_skip=ignore_api_skip,
                    )
                except Exception as error:
                    candle_cache_mod.mark_api_fetch_failed(
                        cache_pair,
                        interval,
                        cache_dir=opts.cache_dir,
                    )
                    logger.warning(
                        "%s price prefetch failed for %s %s (%s): %s",
                        "Binance" if use_binance else "HL",
                        pair,
                        interval,
                        pair_label(pair),
                        error,
                    )
                    return []

        async def _load_cached_only(
            pair: str,
            interval: str,
            tick_times: list[dt.datetime],
            history_hours: int,
        ) -> list[dict[str, float]]:
            if not opts.use_cache or opts.refresh_cache:
                return []
            cache_pair = _cache_pair_for_source(pair, opts.candle_source)
            cached = candle_cache_mod.load_candles(
                cache_pair,
                interval,
                cache_dir=opts.cache_dir,
            )
            if not cached:
                return []
            start = min(tick_times) - dt.timedelta(hours=history_hours)
            end = max(tick_times) + dt.timedelta(hours=opts.buffer_hours)
            return _filter_candles_in_range(cached, start, end)

        async def _resolve_vol_candles(
            pair: str,
            tick_times: list[dt.datetime],
            price_candles: list[dict[str, float]],
            *,
            vol_history_hours: int,
            price_coverage_end_ms: int,
        ) -> tuple[list[dict[str, float]], str]:
            cache_pair = _cache_pair_for_source(pair, opts.candle_source)
            skip_1m_api = (
                not _within_1m_api_window(tick_times, candle_source=opts.candle_source)
                or candle_cache_mod.is_api_fetch_skipped(
                    cache_pair,
                    opts.barrier_interval,
                    cache_dir=opts.cache_dir,
                )
            )

            cached_1m = await _load_cached_only(
                pair,
                opts.barrier_interval,
                tick_times,
                vol_history_hours,
            )
            if _vol_series_usable(cached_1m, tick_times, vol_history_hours):
                return cached_1m, opts.barrier_interval

            if not skip_1m_api:
                fetched_1m = await _fetch_series(
                    pair,
                    opts.barrier_interval,
                    price_coverage_end_ms,
                    fill_gaps=True,
                    history_hours=vol_history_hours,
                    ignore_api_skip=False,
                )
                if _vol_series_usable(fetched_1m, tick_times, vol_history_hours):
                    return fetched_1m, opts.barrier_interval

            skip_5m_api = candle_cache_mod.is_api_fetch_skipped(
                _cache_pair_for_source(pair, opts.candle_source),
                opts.interval,
                cache_dir=opts.cache_dir,
            )
            cached_5m = await _load_cached_only(
                pair,
                opts.interval,
                tick_times,
                vol_history_hours,
            )
            if _vol_series_usable(cached_5m, tick_times, vol_history_hours):
                if skip_1m_api:
                    logger.info(
                        "HL vol %s cache for %s (1m unavailable — scanner NATR on %s)",
                        opts.interval,
                        pair,
                        opts.interval,
                    )
                return cached_5m, opts.interval

            if not skip_5m_api:
                fetched_5m = await _fetch_series(
                    pair,
                    opts.interval,
                    price_coverage_end_ms,
                    fill_gaps=True,
                    history_hours=vol_history_hours,
                    ignore_api_skip=False,
                )
                if _vol_series_usable(fetched_5m, tick_times, vol_history_hours):
                    if skip_1m_api:
                        logger.info(
                            "HL vol %s for %s (1m unavailable — scanner NATR on %s)",
                            opts.interval,
                            pair,
                            opts.interval,
                        )
                    return fetched_5m, opts.interval

            fallback = cached_5m or price_candles
            if skip_1m_api and fallback:
                logger.info(
                    "HL vol partial %s for %s (1m unavailable — best-effort scanner NATR)",
                    opts.interval,
                    pair,
                )
            return fallback, opts.interval

        async def load_pair(pair: str) -> None:
            requests = pair_requests[pair]
            tick_times = [tick_time for _, _, tick_time, _ in requests]
            latest_tick_ms = int(max(tick_times).timestamp() * 1000)
            price_coverage_end_ms = latest_tick_ms + (interval_ms or 300_000)
            candles = await _fetch_series(pair, opts.interval, price_coverage_end_ms)
            if not candles:
                logger.warning("HL price prefetch empty for %s %s", pair, opts.interval)
                return
            pair_candles[pair] = candles

            vol_history_hours = max(opts.buffer_hours, opts.vol_lookback_hours)
            vol_candles, vol_interval = await _resolve_vol_candles(
                pair,
                tick_times,
                candles,
                vol_history_hours=vol_history_hours,
                price_coverage_end_ms=price_coverage_end_ms,
            )
            pair_vol_candles[pair] = vol_candles

            if not load_barrier_series:
                pair_barrier_candles[pair] = vol_candles
                return

            if vol_interval == opts.barrier_interval:
                pair_barrier_candles[pair] = vol_candles
                return

            skip_1m_api = (
                not _within_1m_api_window(tick_times, candle_source=opts.candle_source)
                or candle_cache_mod.is_api_fetch_skipped(
                    _cache_pair_for_source(pair, opts.candle_source),
                    opts.barrier_interval,
                    cache_dir=opts.cache_dir,
                )
            )
            if skip_1m_api:
                pair_barrier_candles[pair] = vol_candles
                return

            barrier_coverage_end_ms = latest_tick_ms + (barrier_interval_ms or 60_000)
            barrier_1m = await _fetch_series(
                pair,
                opts.barrier_interval,
                barrier_coverage_end_ms,
                fill_gaps=False,
                history_hours=vol_history_hours,
                ignore_api_skip=False,
            )
            pair_barrier_candles[pair] = barrier_1m or vol_candles

        total_pairs = len(pairs_sorted)
        logger.info(
            "%s replay prefetch: loading %d pairs (%s price, %s barrier/vol)...",
            source_label,
            total_pairs,
            opts.interval,
            opts.barrier_interval,
        )
        completed = 0
        for pair in pairs_sorted:
            try:
                await load_pair(pair)
            except Exception as error:
                logger.warning("Prefetch failed for %s: %s", pair, error)
            completed += 1
            if completed == 1 or completed % 5 == 0 or completed == total_pairs:
                logger.info("Prefetch progress: %d/%d pairs", completed, total_pairs)

    for pair, requests in pair_requests.items():
        candles = pair_candles.get(pair)
        if not candles:
            continue
        if not opts.build_session_prices:
            continue
        _fill_session_prices_for_pair(
            candles,
            requests,
            session_caches,
            max_delta_ms=max_delta_ms,
        )

    if not opts.build_session_prices:
        logger.info(
            "%s replay prefetch: skipped tick price map (price_source=reports; "
            "barrier/vol series only)",
            source_label,
        )

    total_prices = sum(len(cache) for cache in session_caches.values())
    logger.info(
        "%s replay prefetch: %d prices across %d sessions "
        "(%d unique pairs, %d price series, %d barrier series, %d vol series)",
        source_label,
        total_prices,
        len(session_tick_maps),
        len(pair_requests),
        len(pair_candles),
        len(pair_barrier_candles),
        len(pair_vol_candles),
    )
    return session_caches, pair_candles, pair_barrier_candles, pair_vol_candles


async def prefetch_session_hl_prices(
    tick_meta_map: dict[int, TickMeta],
    *,
    interval: str = "5m",
    buffer_hours: int = 1,
    max_concurrent: int = 1,
    request_interval_ms: int = 400,
    max_retries: int = 6,
) -> HlPriceCache:
    """Prefetch prices for a single session (delegates to batched replay prefetch)."""
    settings = HlPrefetchSettings(
        interval=interval,
        buffer_hours=buffer_hours,
        max_concurrent=max_concurrent,
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
    )
    caches, _, _, _ = await prefetch_replay_hl_prices({0: tick_meta_map}, settings=settings)
    return caches.get(0, {})
