"""On-disk Parquet cache for Hyperliquid OHLCV candles used by replay backtests."""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from routines.lib import hl_candles

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = ROOT_DIR / "data" / "hl_candles"

_CANDLE_COLUMNS = ["timestamp_ms", "open", "high", "low", "close", "volume"]
_ROW_GROUP_SIZE = 8640


def _sanitize_pair_filename(trading_pair: str) -> str:
    return trading_pair.replace("/", "_").replace("\\", "_")


def _canonical_cache_pair(trading_pair: str) -> str:
    if "-" in trading_pair:
        return trading_pair
    return f"{trading_pair}-USD"


def _api_skip_path(parquet_path: Path) -> Path:
    return parquet_path.with_suffix(".parquet.api_skip")


def _should_skip_api_fetch(parquet_path: Path) -> bool:
    skip_path = _api_skip_path(parquet_path)
    if not skip_path.is_file():
        return False
    try:
        payload = json.loads(skip_path.read_text(encoding="utf-8"))
        return (time.time() - float(payload.get("failed_at", 0))) < 86_400
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def is_api_fetch_skipped(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> bool:
    """Return True when a recent API failure marker exists for this pair/interval."""
    return _should_skip_api_fetch(cache_path(trading_pair, interval, cache_dir=cache_dir))


def _mark_api_fetch_failed(parquet_path: Path) -> None:
    skip_path = _api_skip_path(parquet_path)
    skip_path.parent.mkdir(parents=True, exist_ok=True)
    skip_path.write_text(json.dumps({"failed_at": time.time()}), encoding="utf-8")


def mark_api_fetch_failed(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> None:
    _mark_api_fetch_failed(cache_path(trading_pair, interval, cache_dir=cache_dir))


def _cache_covers_range(
    cached: list[dict[str, float]],
    start_ms: int,
    required_end_ms: int,
    interval_ms: int,
) -> bool:
    timestamps = sorted(int(candle["timestamp_ms"]) for candle in cached if "timestamp_ms" in candle)
    if not timestamps:
        return False
    if timestamps[0] > start_ms:
        return False
    return timestamps[-1] >= required_end_ms - interval_ms


def cache_path(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> Path:
    root = cache_dir or DEFAULT_CACHE_DIR
    canonical = _canonical_cache_pair(trading_pair)
    return root / interval / f"{_sanitize_pair_filename(canonical)}.parquet"


def _meta_path(parquet_path: Path) -> Path:
    return parquet_path.with_suffix(".parquet.meta.json")


def _candles_to_records(candles: list[dict[str, float]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candle in candles:
        timestamp_ms = candle.get("timestamp_ms")
        if timestamp_ms is None:
            continue
        records.append(
            {
                "timestamp_ms": int(timestamp_ms),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle["volume"]),
            }
        )
    return records


def _records_to_candles(records: list[dict[str, Any]]) -> list[dict[str, float]]:
    return [
        {
            "timestamp_ms": float(record["timestamp_ms"]),
            "open": float(record["open"]),
            "high": float(record["high"]),
            "low": float(record["low"]),
            "close": float(record["close"]),
            "volume": float(record["volume"]),
        }
        for record in records
    ]


def _read_meta(path: Path) -> dict[str, Any] | None:
    meta_path = _meta_path(path)
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def meta_covers_range(
    meta: dict[str, Any],
    start_ms: int,
    required_end_ms: int,
    interval_ms: int,
) -> bool:
    try:
        min_ts = int(meta["min_ts_ms"])
        max_ts = int(meta["max_ts_ms"])
    except (KeyError, TypeError, ValueError):
        return False
    if min_ts > start_ms:
        return False
    return max_ts >= required_end_ms - interval_ms


def _write_meta(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    timestamps = [int(record["timestamp_ms"]) for record in records]
    meta = {
        "min_ts_ms": min(timestamps),
        "max_ts_ms": max(timestamps),
        "bar_count": len(records),
        "updated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    meta_path = _meta_path(path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_candles(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> list[dict[str, float]]:
    path = cache_path(trading_pair, interval, cache_dir=cache_dir)
    if not path.is_file():
        legacy_pair = trading_pair.rsplit("-", 1)[0] if trading_pair.endswith("-USD") else trading_pair
        legacy_path = (cache_dir or DEFAULT_CACHE_DIR) / interval / f"{_sanitize_pair_filename(legacy_pair)}.parquet"
        if legacy_path.is_file() and legacy_path != path:
            path = legacy_path
    if not path.is_file():
        return []
    frame = pd.read_parquet(path, columns=_CANDLE_COLUMNS)
    if frame.empty:
        return []
    records = frame.sort_values("timestamp_ms").to_dict(orient="records")
    return _records_to_candles(records)


def _resolve_cache_path(
    trading_pair: str,
    interval: str,
    *,
    cache_dir: Path | None = None,
) -> Path | None:
    path = cache_path(trading_pair, interval, cache_dir=cache_dir)
    if path.is_file():
        return path
    legacy_pair = trading_pair.rsplit("-", 1)[0] if trading_pair.endswith("-USD") else trading_pair
    legacy_path = (cache_dir or DEFAULT_CACHE_DIR) / interval / f"{_sanitize_pair_filename(legacy_pair)}.parquet"
    if legacy_path.is_file() and legacy_path != path:
        return legacy_path
    return None


def load_candles_in_range(
    trading_pair: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    cache_dir: Path | None = None,
) -> list[dict[str, float]]:
    """Load a timestamp slice from cache without reading unrelated row groups when possible."""
    if end_ms < start_ms:
        return []

    path = _resolve_cache_path(trading_pair, interval, cache_dir=cache_dir)
    if path is None:
        return []

    meta = _read_meta(path)
    if meta is not None:
        try:
            if int(meta["max_ts_ms"]) < start_ms or int(meta["min_ts_ms"]) > end_ms:
                return []
        except (KeyError, TypeError, ValueError):
            pass

    try:
        table = pq.read_table(
            path,
            columns=_CANDLE_COLUMNS,
            filters=[
                ("timestamp_ms", ">=", start_ms),
                ("timestamp_ms", "<=", end_ms),
            ],
        )
        if table.num_rows == 0:
            return []
        records = table.sort_by("timestamp_ms").to_pydict()
        return _records_to_candles(
            [
                {
                    "timestamp_ms": records["timestamp_ms"][index],
                    "open": records["open"][index],
                    "high": records["high"][index],
                    "low": records["low"][index],
                    "close": records["close"][index],
                    "volume": records["volume"][index],
                }
                for index in range(table.num_rows)
            ]
        )
    except Exception:
        logger.debug("Parquet filter read failed for %s, falling back to index slice", path)

    ts_frame = pd.read_parquet(path, columns=["timestamp_ms"])
    if ts_frame.empty:
        return []
    timestamps = ts_frame["timestamp_ms"].to_numpy(dtype=np.int64)
    start_index = int(np.searchsorted(timestamps, start_ms, side="left"))
    end_index = int(np.searchsorted(timestamps, end_ms, side="right"))
    if start_index >= end_index:
        return []
    frame = pd.read_parquet(path, columns=_CANDLE_COLUMNS).iloc[start_index:end_index]
    records = frame.sort_values("timestamp_ms").to_dict(orient="records")
    return _records_to_candles(records)


def save_candles(
    trading_pair: str,
    interval: str,
    candles: list[dict[str, float]],
    *,
    cache_dir: Path | None = None,
) -> None:
    new_records = _candles_to_records(candles)
    if not new_records:
        return

    path = cache_path(trading_pair, interval, cache_dir=cache_dir)
    existing_records = _candles_to_records(load_candles(trading_pair, interval, cache_dir=cache_dir))
    merged = {record["timestamp_ms"]: record for record in existing_records}
    merged.update({record["timestamp_ms"]: record for record in new_records})
    records = [merged[key] for key in sorted(merged)]

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".parquet.tmp")
    pd.DataFrame(records, columns=_CANDLE_COLUMNS).to_parquet(
        temp_path,
        index=False,
        row_group_size=_ROW_GROUP_SIZE,
    )
    temp_path.replace(path)
    _write_meta(path, records)
    logger.debug(
        "HL candle cache saved %s %s (%d bars)",
        trading_pair,
        interval,
        len(records),
    )


def coverage_gaps(
    cached: list[dict[str, float]],
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    *,
    coverage_end_ms: int | None = None,
) -> list[tuple[int, int]]:
    """Return uncovered sub-ranges within [start_ms, end_ms].

    ``coverage_end_ms`` clamps the required span for backtests so buffer padding
    beyond the last journal tick does not force a refetch every run.
    """
    if end_ms <= start_ms:
        return []

    required_end_ms = end_ms if coverage_end_ms is None else min(end_ms, coverage_end_ms)

    if not cached:
        return [(start_ms, required_end_ms)]

    timestamps = sorted(int(candle["timestamp_ms"]) for candle in cached if "timestamp_ms" in candle)
    if not timestamps:
        return [(start_ms, required_end_ms)]

    gaps: list[tuple[int, int]] = []
    hole_threshold_ms = int(interval_ms * 1.5)

    if timestamps[0] > start_ms:
        gaps.append((start_ms, min(required_end_ms, timestamps[0])))

    for index in range(len(timestamps) - 1):
        current_ts = timestamps[index]
        next_ts = timestamps[index + 1]
        if next_ts - current_ts > hole_threshold_ms:
            gap_start = current_ts + interval_ms
            gap_end = next_ts
            if gap_end > start_ms and gap_start < required_end_ms:
                gaps.append((max(start_ms, gap_start), min(required_end_ms, gap_end)))

    last_ts = timestamps[-1]
    if last_ts < required_end_ms - interval_ms:
        gap_start = max(start_ms, last_ts + interval_ms)
        if gap_start < required_end_ms:
            gaps.append((gap_start, required_end_ms))

    return [(gap_start, gap_end) for gap_start, gap_end in gaps if gap_end > gap_start]


def _filter_candles_in_range(
    candles: list[dict[str, float]],
    start_ms: int,
    end_ms: int,
) -> list[dict[str, float]]:
    return [
        candle
        for candle in candles
        if start_ms <= int(candle["timestamp_ms"]) <= end_ms
    ]


async def fetch_hl_candles_between_cached(
    trading_pair: str,
    interval: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    session: aiohttp.ClientSession | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    refresh_cache: bool = False,
    coverage_end_ms: int | None = None,
    fill_gaps: bool = True,
    ignore_api_skip: bool = False,
) -> list[dict[str, float]]:
    """Load candles from disk cache, fetching and merging only missing ranges."""
    interval_ms = hl_candles._INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"Unsupported HL candle interval: {interval}")

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if end_ms <= start_ms:
        return []

    if not use_cache:
        candles = await hl_candles.fetch_hl_candles_between(
            trading_pair,
            interval,
            start,
            end,
            session=session,
        )
        if candles:
            save_candles(trading_pair, interval, candles, cache_dir=cache_dir)
        return candles

    parquet_path = cache_path(trading_pair, interval, cache_dir=cache_dir)
    required_end_ms = end_ms if coverage_end_ms is None else min(end_ms, coverage_end_ms)

    if not refresh_cache:
        meta = _read_meta(parquet_path) if parquet_path.is_file() else None
        if meta is not None and meta_covers_range(meta, start_ms, required_end_ms, interval_ms):
            ranged = load_candles_in_range(
                trading_pair,
                interval,
                start_ms,
                end_ms,
                cache_dir=cache_dir,
            )
            if ranged:
                return ranged

    cached = [] if refresh_cache else load_candles(trading_pair, interval, cache_dir=cache_dir)

    if cached and not refresh_cache and _cache_covers_range(
        cached, start_ms, required_end_ms, interval_ms
    ):
        return _filter_candles_in_range(cached, start_ms, end_ms)

    gaps = (
        [(start_ms, end_ms)]
        if refresh_cache
        else coverage_gaps(
            cached,
            start_ms,
            end_ms,
            interval_ms,
            coverage_end_ms=coverage_end_ms,
        )
    )

    if not ignore_api_skip and _should_skip_api_fetch(parquet_path):
        return _filter_candles_in_range(cached, start_ms, end_ms)

    if not gaps:
        logger.info(
            "HL candle cache hit for %s %s (%d bars in range)",
            trading_pair,
            interval,
            len(_filter_candles_in_range(cached, start_ms, end_ms)),
        )
        return _filter_candles_in_range(cached, start_ms, end_ms)

    if cached and not refresh_cache:
        logger.info(
            "HL candle cache partial for %s %s (%d gaps)",
            trading_pair,
            interval,
            len(gaps),
        )
    else:
        logger.info(
            "HL candle cache miss for %s %s",
            trading_pair,
            interval,
        )

    if not fill_gaps and cached:
        logger.info(
            "HL candle cache partial for %s %s (%d gaps) — using cached bars without gap fetch",
            trading_pair,
            interval,
            len(gaps),
        )
        return _filter_candles_in_range(cached, start_ms, end_ms)

    fetched: list[dict[str, float]] = []
    for gap_start_ms, gap_end_ms in gaps:
        gap_start = dt.datetime.fromtimestamp(gap_start_ms / 1000, tz=dt.UTC)
        gap_end = dt.datetime.fromtimestamp(gap_end_ms / 1000, tz=dt.UTC)
        gap_candles = await hl_candles.fetch_hl_candles_between(
            trading_pair,
            interval,
            gap_start,
            gap_end,
            session=session,
        )
        if gap_candles:
            fetched.extend(gap_candles)

    if not fetched:
        _mark_api_fetch_failed(parquet_path)
        return _filter_candles_in_range(cached, start_ms, end_ms)

    save_candles(trading_pair, interval, fetched, cache_dir=cache_dir)
    skip_path = _api_skip_path(parquet_path)
    if skip_path.is_file():
        skip_path.unlink(missing_ok=True)
    merged = load_candles(trading_pair, interval, cache_dir=cache_dir)
    return _filter_candles_in_range(merged, start_ms, end_ms)


__all__ = [
    "DEFAULT_CACHE_DIR",
    "cache_path",
    "coverage_gaps",
    "fetch_hl_candles_between_cached",
    "is_api_fetch_skipped",
    "load_candles",
    "load_candles_in_range",
    "mark_api_fetch_failed",
    "meta_covers_range",
    "save_candles",
]
