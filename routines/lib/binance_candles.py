"""Binance USDT-M perpetual klines helpers (public REST, no Hummingbot server required)."""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
_MAX_KLINES_PER_REQUEST = 1500

_rate_limit_lock: asyncio.Lock | None = None
_rate_limit_interval_s: float = 0.0
_base_rate_limit_interval_s: float = 0.0
_last_request_at: float = 0.0
_max_retries: int = 6
_MAX_RATE_LIMIT_INTERVAL_S = 2.0


def configure_binance_rate_limit(
    *,
    request_interval_ms: int = 250,
    max_retries: int = 6,
) -> None:
    global _rate_limit_interval_s, _base_rate_limit_interval_s, _max_retries, _rate_limit_lock
    interval_s = max(0.0, request_interval_ms / 1000.0)
    _base_rate_limit_interval_s = interval_s
    _rate_limit_interval_s = interval_s
    _max_retries = max(1, max_retries)
    if _rate_limit_lock is None:
        _rate_limit_lock = asyncio.Lock()


def _bump_rate_limit_after_throttle(*, retry_after_s: float | None = None) -> None:
    """Increase spacing after 429/418 so bulk prefetch stops hammering the API."""
    global _rate_limit_interval_s
    if retry_after_s is not None and retry_after_s > 0:
        target_s = retry_after_s
    else:
        target_s = min(_MAX_RATE_LIMIT_INTERVAL_S, max(_rate_limit_interval_s * 1.5, 0.5))
    if target_s <= _rate_limit_interval_s:
        return
    _rate_limit_interval_s = min(_MAX_RATE_LIMIT_INTERVAL_S, target_s)
    logger.info(
        "Binance throttle increased to %.0fms after rate limit",
        _rate_limit_interval_s * 1000,
    )


def reset_binance_rate_limit_state() -> None:
    """Restore throttle to the configured base interval between runs."""
    global _last_request_at, _rate_limit_interval_s
    _last_request_at = 0.0
    _rate_limit_interval_s = _base_rate_limit_interval_s


async def _await_rate_limit() -> None:
    global _last_request_at
    if _rate_limit_interval_s <= 0:
        return
    if _rate_limit_lock is None:
        configure_binance_rate_limit()
    async with _rate_limit_lock:
        now = time.monotonic()
        wait_s = _rate_limit_interval_s - (now - _last_request_at)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        _last_request_at = time.monotonic()


_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def trading_pair_to_symbol(trading_pair: str) -> str:
    """Map connector pair to Binance symbol (BTC-USDT -> BTCUSDT)."""
    return trading_pair.replace("-", "").replace("/", "")


def _parse_klines(raw: list[Any]) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            candles.append(
                {
                    "timestamp_ms": float(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    return candles


async def fetch_binance_candle_window(
    trading_pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    session: aiohttp.ClientSession,
) -> list[dict[str, float]]:
    """Fetch one klines request for [start, end] (up to 1500 bars)."""
    if interval not in _INTERVAL_MS:
        raise ValueError(f"Unsupported Binance kline interval: {interval}")

    start_ms = int(start.astimezone(dt.timezone.utc).timestamp() * 1000)
    end_ms = int(end.astimezone(dt.timezone.utc).timestamp() * 1000)
    if end_ms <= start_ms:
        return []

    symbol = trading_pair_to_symbol(trading_pair)
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": _MAX_KLINES_PER_REQUEST,
    }

    last_error = ""
    for attempt in range(_max_retries):
        await _await_rate_limit()
        async with session.get(BINANCE_FUTURES_KLINES_URL, params=params) as resp:
            if resp.status in {418, 429, 500, 502, 503}:
                retry_after_header = resp.headers.get("Retry-After")
                retry_after_s: float | None = None
                if retry_after_header:
                    try:
                        retry_after_s = float(retry_after_header)
                    except ValueError:
                        retry_after_s = None
                backoff_s = retry_after_s if retry_after_s else min(30.0, 2.0**attempt)
                if resp.status in {418, 429}:
                    _bump_rate_limit_after_throttle(retry_after_s=retry_after_s)
                last_error = f"HTTP {resp.status}"
                logger.info(
                    "Binance klines %s for %s %s — retry in %.1fs (%d/%d)",
                    resp.status,
                    symbol,
                    interval,
                    backoff_s,
                    attempt + 1,
                    _max_retries,
                )
                await asyncio.sleep(backoff_s)
                continue
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"Binance klines HTTP {resp.status} for {symbol} {interval}: {body[:200]}"
                )
            data = await resp.json()

        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected Binance klines response for {symbol} {interval}")
        return _parse_klines(data)

    raise RuntimeError(
        f"Binance klines {last_error or 'failed'} for {symbol} {interval} after {_max_retries} retries"
    )


async def fetch_binance_candles_between(
    trading_pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[dict[str, float]]:
    """Fetch OHLCV between start and end, paginating in 1500-bar chunks."""
    interval_ms = _INTERVAL_MS[interval]
    start_ms = int(start.astimezone(dt.timezone.utc).timestamp() * 1000)
    end_ms = int(end.astimezone(dt.timezone.utc).timestamp() * 1000)
    if end_ms <= start_ms:
        return []

    chunk_ms = _MAX_KLINES_PER_REQUEST * interval_ms
    all_candles: list[dict[str, float]] = []

    async def _load(http_session: aiohttp.ClientSession) -> list[dict[str, float]]:
        candles: list[dict[str, float]] = []
        cursor_ms = start_ms
        while cursor_ms < end_ms:
            chunk_end_ms = min(cursor_ms + chunk_ms, end_ms)
            chunk_start = dt.datetime.fromtimestamp(cursor_ms / 1000, tz=dt.timezone.utc)
            chunk_end = dt.datetime.fromtimestamp(chunk_end_ms / 1000, tz=dt.timezone.utc)
            chunk = await fetch_binance_candle_window(
                trading_pair,
                interval,
                chunk_start,
                chunk_end,
                session=http_session,
            )
            if not chunk:
                break
            candles.extend(chunk)
            last_ts = int(chunk[-1]["timestamp_ms"])
            next_cursor = last_ts + interval_ms
            if next_cursor <= cursor_ms:
                break
            cursor_ms = next_cursor
        return candles

    if session is not None:
        all_candles = await _load(session)
    else:
        async with aiohttp.ClientSession() as http_session:
            all_candles = await _load(http_session)

    deduped: dict[int, dict[str, float]] = {}
    for candle in all_candles:
        ts = int(candle["timestamp_ms"])
        if start_ms <= ts <= end_ms:
            deduped[ts] = candle
    return [deduped[key] for key in sorted(deduped)]


def close_nearest(
    candles: list[dict[str, float]],
    target: dt.datetime,
    max_delta_ms: int = 45 * 60 * 1000,
) -> float | None:
    """Return close price from the candle whose open time is nearest to target."""
    if not candles:
        return None
    target_ms = int(target.astimezone(dt.timezone.utc).timestamp() * 1000)
    with_ts = [(int(c["timestamp_ms"]), float(c["close"])) for c in candles if "timestamp_ms" in c]
    if not with_ts:
        return None
    with_ts.sort(key=lambda item: item[0])
    timestamps = [item[0] for item in with_ts]
    closes = [item[1] for item in with_ts]
    idx = bisect.bisect_left(timestamps, target_ms)
    best_idx: int | None = None
    best_delta = max_delta_ms + 1
    for candidate in (idx - 1, idx):
        if 0 <= candidate < len(timestamps):
            delta = abs(timestamps[candidate] - target_ms)
            if delta < best_delta:
                best_delta = delta
                best_idx = candidate
    if best_idx is None or best_delta > max_delta_ms:
        return None
    return closes[best_idx]


__all__ = [
    "BINANCE_FUTURES_KLINES_URL",
    "_INTERVAL_MS",
    "close_nearest",
    "configure_binance_rate_limit",
    "fetch_binance_candle_window",
    "fetch_binance_candles_between",
    "reset_binance_rate_limit_state",
    "trading_pair_to_symbol",
]
