"""Shared fast depth-probe helpers for exchange candle sources."""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

FetchWindowFn = Callable[
    [aiohttp.ClientSession, str, str, dt.datetime, dt.datetime],
    Awaitable[list[dict[str, float]]],
]

FAST_ANCHOR_WINDOW_HOURS = 48
FAST_BINARY_SEARCH_YEARS = 3
FAST_BINARY_PROBE_BARS = 48


def utc_iso_from_ms(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat()


def summarize_candles(candles: list[dict[str, float]]) -> dict[str, Any]:
    timestamps = [
        int(candle["timestamp_ms"]) for candle in candles if "timestamp_ms" in candle
    ]
    if not timestamps:
        return {
            "has_data": False,
            "bar_count": 0,
            "earliest_ts_ms": None,
            "latest_ts_ms": None,
            "earliest_utc": None,
            "latest_utc": None,
        }
    earliest_ms = min(timestamps)
    latest_ms = max(timestamps)
    return {
        "has_data": True,
        "bar_count": len(timestamps),
        "earliest_ts_ms": earliest_ms,
        "latest_ts_ms": latest_ms,
        "earliest_utc": utc_iso_from_ms(earliest_ms),
        "latest_utc": utc_iso_from_ms(latest_ms),
    }


async def probe_anchor_window(
    session: aiohttp.ClientSession,
    pair: str,
    interval: str,
    anchor: dt.datetime,
    fetch_window: FetchWindowFn,
    *,
    window_hours: int = FAST_ANCHOR_WINDOW_HOURS,
) -> dict[str, Any]:
    end = anchor + dt.timedelta(hours=window_hours)
    candles = await fetch_window(session, pair, interval, anchor, end)
    summary = summarize_candles(candles)
    return {
        "pair": pair,
        "interval": interval,
        "probe_type": "anchor",
        "anchor_utc": anchor.astimezone(dt.timezone.utc).isoformat(),
        "window_end_utc": end.astimezone(dt.timezone.utc).isoformat(),
        "window_hours": window_hours,
        **summary,
    }


async def find_earliest_bar(
    session: aiohttp.ClientSession,
    pair: str,
    interval: str,
    search_start: dt.datetime,
    search_end: dt.datetime,
    interval_ms: int,
    fetch_window: FetchWindowFn,
    *,
    probe_bars: int = FAST_BINARY_PROBE_BARS,
) -> dict[str, Any]:
    """Binary-search earliest available bar using small single-request windows."""
    probe_window_ms = interval_ms * probe_bars
    lo_ms = int(search_start.astimezone(dt.timezone.utc).timestamp() * 1000)
    hi_ms = int(search_end.astimezone(dt.timezone.utc).timestamp() * 1000)
    search_end_ms = hi_ms
    requests = 0

    async def _window_has_data(start_ms: int) -> tuple[bool, list[dict[str, float]]]:
        nonlocal requests
        window_end_ms = min(start_ms + probe_window_ms, search_end_ms)
        if window_end_ms <= start_ms:
            return False, []
        start = dt.datetime.fromtimestamp(start_ms / 1000, tz=dt.timezone.utc)
        end = dt.datetime.fromtimestamp(window_end_ms / 1000, tz=dt.timezone.utc)
        candles = await fetch_window(session, pair, interval, start, end)
        requests += 1
        return bool(candles), candles

    while lo_ms + interval_ms < hi_ms:
        mid_ms = (lo_ms + hi_ms) // 2
        has_data, _candles = await _window_has_data(mid_ms)
        if has_data:
            hi_ms = mid_ms
        else:
            lo_ms = mid_ms + interval_ms

    # hi_ms is the earliest window start known to contain data (lo may still sit
    # in a pre-listing gap when search_start predates the first bar).
    has_data, final_candles = await _window_has_data(hi_ms)
    summary = summarize_candles(final_candles if has_data else [])
    return {
        "pair": pair,
        "interval": interval,
        "probe_type": "earliest_binary_search",
        "search_start_utc": search_start.astimezone(dt.timezone.utc).isoformat(),
        "search_end_utc": search_end.astimezone(dt.timezone.utc).isoformat(),
        "api_requests": requests,
        **summary,
    }


async def probe_depth_fast(
    *,
    pairs: list[str],
    intervals: tuple[str, ...],
    interval_ms_map: dict[str, int],
    fetch_window: FetchWindowFn,
    configure_rate_limit: Callable[..., None],
    lookbacks_years: tuple[int, ...] = (1, 2, 3),
    request_interval_ms: int = 100,
    max_retries: int = 6,
    find_earliest: bool = True,
    include_1m_recent: bool = True,
    recent_1m_days: int = 2,
    binary_search_years: int = FAST_BINARY_SEARCH_YEARS,
) -> dict[str, Any]:
    configure_rate_limit(request_interval_ms=request_interval_ms, max_retries=max_retries)
    now = dt.datetime.now(dt.timezone.utc)
    anchor_results: list[dict[str, Any]] = []
    earliest_results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        for pair in pairs:
            for interval in intervals:
                interval_ms = interval_ms_map.get(interval)
                if interval_ms is None:
                    continue
                for years in lookbacks_years:
                    anchor = now - dt.timedelta(days=365 * years)
                    try:
                        row = await probe_anchor_window(
                            session,
                            pair,
                            interval,
                            anchor,
                            fetch_window,
                        )
                        row["lookback_years"] = years
                        anchor_results.append(row)
                    except Exception as error:
                        anchor_results.append(
                            {
                                "pair": pair,
                                "interval": interval,
                                "probe_type": "anchor",
                                "lookback_years": years,
                                "anchor_utc": anchor.isoformat(),
                                "error": str(error),
                                "has_data": False,
                                "bar_count": 0,
                            }
                        )

                if find_earliest:
                    search_start = now - dt.timedelta(days=365 * binary_search_years)
                    try:
                        earliest_results.append(
                            await find_earliest_bar(
                                session,
                                pair,
                                interval,
                                search_start,
                                now,
                                interval_ms,
                                fetch_window,
                            )
                        )
                    except Exception as error:
                        earliest_results.append(
                            {
                                "pair": pair,
                                "interval": interval,
                                "probe_type": "earliest_binary_search",
                                "error": str(error),
                                "has_data": False,
                                "bar_count": 0,
                            }
                        )

            if include_1m_recent and "1m" in interval_ms_map:
                recent_start = now - dt.timedelta(days=recent_1m_days)
                try:
                    row = await probe_anchor_window(
                        session,
                        pair,
                        "1m",
                        recent_start,
                        fetch_window,
                        window_hours=recent_1m_days * 24,
                    )
                    row["lookback_years"] = None
                    row["probe_type"] = "recent_1m"
                    anchor_results.append(row)
                except Exception as error:
                    anchor_results.append(
                        {
                            "pair": pair,
                            "interval": "1m",
                            "probe_type": "recent_1m",
                            "error": str(error),
                            "has_data": False,
                            "bar_count": 0,
                        }
                    )

    total_requests = len([row for row in anchor_results if "error" not in row]) + sum(
        row.get("api_requests", 0) for row in earliest_results
    )

    return {
        "mode": "fast",
        "probed_at": now.isoformat(),
        "pairs": pairs,
        "intervals": list(intervals),
        "lookbacks_years": list(lookbacks_years),
        "anchor_results": anchor_results,
        "earliest_results": earliest_results,
        "estimated_api_requests": total_requests,
    }
