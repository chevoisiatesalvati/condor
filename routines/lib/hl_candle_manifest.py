"""Coverage manifest and HL historical depth probing for the candle cache."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

from routines.lib import hl_candle_cache
from routines.lib.hl_candles import (
    _INTERVAL_MS,
    configure_hl_rate_limit,
    fetch_hl_candle_window,
    fetch_hl_candles_between,
)

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
DEPTH_PROBE_FILENAME = "depth_probe.json"

DEFAULT_PROBE_PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD")
DEFAULT_PROBE_INTERVALS = ("5m", "1h", "4h", "1m")
DEFAULT_FAST_PROBE_INTERVALS = ("5m", "1h", "4h")
DEFAULT_PROBE_LOOKBACKS_YEARS = (1, 2, 3)
FAST_ANCHOR_WINDOW_HOURS = 48
FAST_BINARY_SEARCH_YEARS = 3
FAST_BINARY_PROBE_BARS = 48


def _utc_iso_from_ms(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat()


def _summarize_candles(candles: list[dict[str, float]]) -> dict[str, Any]:
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
        "earliest_utc": _utc_iso_from_ms(earliest_ms),
        "latest_utc": _utc_iso_from_ms(latest_ms),
    }


def manifest_path(cache_dir: Path | None = None) -> Path:
    root = cache_dir or hl_candle_cache.DEFAULT_CACHE_DIR
    return root / MANIFEST_FILENAME


def depth_probe_path(cache_dir: Path | None = None) -> Path:
    root = cache_dir or hl_candle_cache.DEFAULT_CACHE_DIR
    return root / DEPTH_PROBE_FILENAME


def _pair_coverage(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    candles = hl_candle_cache.load_candles(trading_pair, interval, cache_dir=cache_dir)
    if not candles:
        return None
    timestamps = [int(candle["timestamp_ms"]) for candle in candles if "timestamp_ms" in candle]
    if not timestamps:
        return None
    return {
        "pair": trading_pair,
        "interval": interval,
        "min_ts_ms": min(timestamps),
        "max_ts_ms": max(timestamps),
        "bar_count": len(timestamps),
    }


def build_manifest(
    *,
    cache_dir: Path | None = None,
    pairs: list[str] | None = None,
    intervals: list[str] | None = None,
    range_start: dt.datetime | None = None,
    range_end: dt.datetime | None = None,
    universe_source: str | None = None,
) -> dict[str, Any]:
    """Summarize on-disk candle coverage for prefetch validation."""
    root = cache_dir or hl_candle_cache.DEFAULT_CACHE_DIR
    discovered_pairs: set[str] = set(pairs or [])
    discovered_intervals: set[str] = set(intervals or [])

    if root.is_dir():
        for interval_dir in sorted(root.iterdir()):
            if not interval_dir.is_dir():
                continue
            interval = interval_dir.name
            discovered_intervals.add(interval)
            for parquet_path in interval_dir.glob("*.parquet"):
                pair_name = parquet_path.stem.replace("_", "-")
                if "-" not in pair_name:
                    pair_name = f"{pair_name}-USD"
                discovered_pairs.add(pair_name)

    coverage: list[dict[str, Any]] = []
    for pair in sorted(discovered_pairs):
        for interval in sorted(discovered_intervals):
            row = _pair_coverage(pair, interval, cache_dir=root)
            if row:
                coverage.append(row)

    manifest: dict[str, Any] = {
        "version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cache_dir": str(root),
        "pairs": sorted(discovered_pairs),
        "intervals": sorted(discovered_intervals),
        "coverage": coverage,
    }
    if range_start is not None:
        manifest["range_start_utc"] = range_start.astimezone(dt.timezone.utc).isoformat()
    if range_end is not None:
        manifest["range_end_utc"] = range_end.astimezone(dt.timezone.utc).isoformat()
    if universe_source:
        manifest["universe_source"] = universe_source
    return manifest


def write_manifest(manifest: dict[str, Any], *, cache_dir: Path | None = None) -> Path:
    path = manifest_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def load_manifest(*, cache_dir: Path | None = None) -> dict[str, Any] | None:
    path = manifest_path(cache_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def coverage_covers_range(
    manifest: dict[str, Any],
    trading_pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
) -> bool:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    for row in manifest.get("coverage", []):
        if row.get("pair") != trading_pair or row.get("interval") != interval:
            continue
        return int(row["min_ts_ms"]) <= start_ms and int(row["max_ts_ms"]) >= end_ms
    return False


async def _probe_anchor_window(
    session: aiohttp.ClientSession,
    pair: str,
    interval: str,
    anchor: dt.datetime,
    *,
    window_hours: int = FAST_ANCHOR_WINDOW_HOURS,
) -> dict[str, Any]:
    end = anchor + dt.timedelta(hours=window_hours)
    candles = await fetch_hl_candle_window(pair, interval, anchor, end, session=session)
    summary = _summarize_candles(candles)
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
    *,
    probe_bars: int = FAST_BINARY_PROBE_BARS,
) -> dict[str, Any]:
    """Binary-search the earliest available bar using small single-request windows."""
    interval_ms = _INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"Unsupported HL candle interval: {interval}")

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
        candles = await fetch_hl_candle_window(pair, interval, start, end, session=session)
        requests += 1
        return bool(candles), candles

    while lo_ms + interval_ms < hi_ms:
        mid_ms = (lo_ms + hi_ms) // 2
        has_data, _candles = await _window_has_data(mid_ms)
        if has_data:
            hi_ms = mid_ms
        else:
            lo_ms = mid_ms + interval_ms

    has_data, final_candles = await _window_has_data(lo_ms)
    summary = _summarize_candles(final_candles if has_data else [])
    return {
        "pair": pair,
        "interval": interval,
        "probe_type": "earliest_binary_search",
        "search_start_utc": search_start.astimezone(dt.timezone.utc).isoformat(),
        "search_end_utc": search_end.astimezone(dt.timezone.utc).isoformat(),
        "api_requests": requests,
        **summary,
    }


async def probe_hl_depth_fast(
    *,
    pairs: list[str] | None = None,
    intervals: tuple[str, ...] = DEFAULT_FAST_PROBE_INTERVALS,
    lookbacks_years: tuple[int, ...] = DEFAULT_PROBE_LOOKBACKS_YEARS,
    request_interval_ms: int = 600,
    max_retries: int = 8,
    find_earliest: bool = True,
    include_1m_recent: bool = True,
    recent_1m_days: int = 2,
) -> dict[str, Any]:
    """Quick retention probe: anchor windows + optional binary-search earliest bar."""
    configure_hl_rate_limit(
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
    )
    probe_pairs = list(pairs or DEFAULT_PROBE_PAIRS)
    now = dt.datetime.now(dt.timezone.utc)
    anchor_results: list[dict[str, Any]] = []
    earliest_results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        for pair in probe_pairs:
            for interval in intervals:
                for years in lookbacks_years:
                    anchor = now - dt.timedelta(days=365 * years)
                    try:
                        row = await _probe_anchor_window(session, pair, interval, anchor)
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
                    search_start = now - dt.timedelta(days=365 * FAST_BINARY_SEARCH_YEARS)
                    try:
                        earliest_results.append(
                            await find_earliest_bar(
                                session,
                                pair,
                                interval,
                                search_start,
                                now,
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

            if include_1m_recent:
                recent_start = now - dt.timedelta(days=recent_1m_days)
                try:
                    row = await _probe_anchor_window(
                        session,
                        pair,
                        "1m",
                        recent_start,
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

    total_requests = len(
        [row for row in anchor_results if "error" not in row]
    ) + sum(row.get("api_requests", 0) for row in earliest_results)

    return {
        "mode": "fast",
        "probed_at": now.isoformat(),
        "pairs": probe_pairs,
        "intervals": list(intervals),
        "lookbacks_years": list(lookbacks_years),
        "anchor_results": anchor_results,
        "earliest_results": earliest_results,
        "estimated_api_requests": total_requests,
    }


async def probe_hl_depth(
    *,
    pairs: list[str] | None = None,
    intervals: tuple[str, ...] = DEFAULT_PROBE_INTERVALS,
    lookbacks_years: tuple[int, ...] = DEFAULT_PROBE_LOOKBACKS_YEARS,
    request_interval_ms: int = 600,
    max_retries: int = 8,
) -> dict[str, Any]:
    """Probe earliest available HL candleSnapshot bar for each pair/interval/lookback."""
    configure_hl_rate_limit(
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
    )
    probe_pairs = list(pairs or DEFAULT_PROBE_PAIRS)
    now = dt.datetime.now(dt.timezone.utc)
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        for pair in probe_pairs:
            for interval in intervals:
                for years in lookbacks_years:
                    start = now - dt.timedelta(days=365 * years)
                    try:
                        candles = await fetch_hl_candles_between(
                            pair,
                            interval,
                            start,
                            now,
                            session=session,
                        )
                    except Exception as error:
                        results.append(
                            {
                                "pair": pair,
                                "interval": interval,
                                "lookback_years": years,
                                "error": str(error),
                                "bar_count": 0,
                            }
                        )
                        continue

                    timestamps = [
                        int(candle["timestamp_ms"])
                        for candle in candles
                        if "timestamp_ms" in candle
                    ]
                    earliest_ms = min(timestamps) if timestamps else None
                    latest_ms = max(timestamps) if timestamps else None
                    results.append(
                        {
                            "pair": pair,
                            "interval": interval,
                            "lookback_years": years,
                            "requested_start_utc": start.isoformat(),
                            "requested_end_utc": now.isoformat(),
                            "bar_count": len(timestamps),
                            "earliest_ts_ms": earliest_ms,
                            "latest_ts_ms": latest_ms,
                            "earliest_utc": (
                                dt.datetime.fromtimestamp(
                                    earliest_ms / 1000,
                                    tz=dt.timezone.utc,
                                ).isoformat()
                                if earliest_ms is not None
                                else None
                            ),
                        }
                    )

    return {
        "mode": "full",
        "probed_at": now.isoformat(),
        "pairs": probe_pairs,
        "intervals": list(intervals),
        "lookbacks_years": list(lookbacks_years),
        "results": results,
    }


def write_depth_probe(payload: dict[str, Any], *, cache_dir: Path | None = None) -> Path:
    path = depth_probe_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_depth_probe(*, cache_dir: Path | None = None) -> dict[str, Any] | None:
    path = depth_probe_path(cache_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def run_depth_probe_and_save(
    *,
    cache_dir: Path | None = None,
    pairs: list[str] | None = None,
    request_interval_ms: int = 600,
    max_retries: int = 8,
    mode: str = "fast",
    find_earliest: bool = True,
    include_1m_recent: bool = True,
) -> dict[str, Any]:
    if mode == "fast":
        payload = await probe_hl_depth_fast(
            pairs=pairs,
            request_interval_ms=request_interval_ms,
            max_retries=max_retries,
            find_earliest=find_earliest,
            include_1m_recent=include_1m_recent,
        )
    elif mode == "full":
        payload = await probe_hl_depth(
            pairs=pairs,
            request_interval_ms=request_interval_ms,
            max_retries=max_retries,
        )
    else:
        raise ValueError(f"Unknown probe mode: {mode}")
    write_depth_probe(payload, cache_dir=cache_dir)
    write_manifest(build_manifest(cache_dir=cache_dir), cache_dir=cache_dir)
    return payload


__all__ = [
    "DEFAULT_FAST_PROBE_INTERVALS",
    "DEFAULT_PROBE_INTERVALS",
    "DEFAULT_PROBE_LOOKBACKS_YEARS",
    "DEFAULT_PROBE_PAIRS",
    "DEPTH_PROBE_FILENAME",
    "MANIFEST_FILENAME",
    "build_manifest",
    "coverage_covers_range",
    "depth_probe_path",
    "find_earliest_bar",
    "load_depth_probe",
    "load_manifest",
    "manifest_path",
    "probe_hl_depth",
    "probe_hl_depth_fast",
    "run_depth_probe_and_save",
    "write_depth_probe",
    "write_manifest",
]
