"""Binance USDT-M perpetual candle depth probing and cache manifests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import aiohttp

from routines.lib import binance_candle_cache, binance_candles
from routines.lib.binance_candles import fetch_binance_candle_window
from routines.lib.candle_depth_probe import probe_depth_fast

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = ROOT_DIR / "data" / "binance_candles"
DEPTH_PROBE_FILENAME = "depth_probe.json"
MANIFEST_FILENAME = "manifest.json"

DEFAULT_PROBE_PAIRS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
DEFAULT_FAST_PROBE_INTERVALS = ("5m", "1h", "4h")


async def _fetch_window(
    session: aiohttp.ClientSession,
    pair: str,
    interval: str,
    start,
    end,
) -> list[dict[str, float]]:
    return await fetch_binance_candle_window(pair, interval, start, end, session=session)


async def probe_binance_depth_fast(
    *,
    pairs: list[str] | None = None,
    intervals: tuple[str, ...] = DEFAULT_FAST_PROBE_INTERVALS,
    lookbacks_years: tuple[int, ...] = (1, 2, 3),
    request_interval_ms: int = 100,
    max_retries: int = 6,
    find_earliest: bool = True,
    include_1m_recent: bool = True,
    binary_search_years: int = 3,
) -> dict[str, Any]:
    probe_pairs = list(pairs or DEFAULT_PROBE_PAIRS)
    payload = await probe_depth_fast(
        pairs=probe_pairs,
        intervals=intervals,
        interval_ms_map=binance_candles._INTERVAL_MS,
        fetch_window=_fetch_window,
        configure_rate_limit=binance_candles.configure_binance_rate_limit,
        lookbacks_years=lookbacks_years,
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
        find_earliest=find_earliest,
        include_1m_recent=include_1m_recent,
        binary_search_years=binary_search_years,
    )
    payload["exchange"] = "binance_perpetual"
    payload["connector"] = "binance_perpetual"
    return payload


def depth_probe_path(cache_dir: Path | None = None) -> Path:
    root = cache_dir or DEFAULT_CACHE_DIR
    return root / DEPTH_PROBE_FILENAME


def write_depth_probe(payload: dict[str, Any], *, cache_dir: Path | None = None) -> Path:
    root = cache_dir or DEFAULT_CACHE_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = depth_probe_path(cache_dir=root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


async def run_depth_probe_and_save(
    *,
    cache_dir: Path | None = None,
    pairs: list[str] | None = None,
    request_interval_ms: int = 100,
    max_retries: int = 6,
    find_earliest: bool = True,
    include_1m_recent: bool = True,
    binary_search_years: int = 3,
) -> dict[str, Any]:
    payload = await probe_binance_depth_fast(
        pairs=pairs,
        request_interval_ms=request_interval_ms,
        max_retries=max_retries,
        find_earliest=find_earliest,
        include_1m_recent=include_1m_recent,
        binary_search_years=binary_search_years,
    )
    write_depth_probe(payload, cache_dir=cache_dir)
    return payload


def manifest_path(cache_dir: Path | None = None) -> Path:
    root = cache_dir or DEFAULT_CACHE_DIR
    return root / MANIFEST_FILENAME


def _pair_coverage(
    pair: str,
    interval: str,
    *,
    cache_dir: Path,
) -> dict[str, Any] | None:
    candles = binance_candle_cache.load_candles(pair, interval, cache_dir=cache_dir)
    if not candles:
        return None
    timestamps = [int(candle["timestamp_ms"]) for candle in candles]
    return {
        "pair": pair,
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
    """Summarize on-disk Binance kline coverage for prefetch validation."""
    root = cache_dir or binance_candle_cache.DEFAULT_CACHE_DIR
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
                if not pair_name.endswith("-USDT"):
                    pair_name = f"{pair_name}-USDT"
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
        "exchange": "binance_perpetual",
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
