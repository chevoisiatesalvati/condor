"""Compute and persist monitor MACD-BB rows for open-leg timeline replay."""

from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from routines.lib.binance_candle_cache import (
    DEFAULT_CACHE_DIR as DEFAULT_BINANCE_CACHE_DIR,
    load_candles_in_range as load_binance_candles_in_range,
)
from routines.lib.hl_candle_cache import (
    DEFAULT_CACHE_DIR as DEFAULT_HL_CACHE_DIR,
    load_candles_in_range as load_hl_candles_in_range,
)
from routines.lib.pair_format import binance_pair_from_any
from routines.macdbb_scanner_aggressive_hl_replay.models import ParsedReport
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    MONITOR_MACDBB_FILENAME,
    MONITOR_BACKFILL_VERSION,
    append_monitor_macdbb_rows,
    get_snapshot_dir,
    snapshot_dir_or_default,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import (
    compute_macdbb_from_closes,
    metrics_to_parsed_report,
)

logger = logging.getLogger(__name__)

MACDBB_1H_LOOKBACK_HOURS = 250
MonitorGap = tuple[str, str, dt.datetime]

_gap_recorder: list[MonitorGap] | None = None
_inline_compute: bool = True
_persist_supplement: bool = True
_write_buffer: list[dict[str, Any]] = []


def _tick_id(tick_time: dt.datetime) -> str:
    return tick_time.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def monitor_report_id(pair: str, tick_time: dt.datetime, interval: str = "1h") -> str:
    return f"{_tick_id(tick_time)}:{pair}:{interval}"


def inline_compute_enabled() -> bool:
    return _inline_compute


def persist_supplement_enabled() -> bool:
    return _persist_supplement


def set_persist_supplement(enabled: bool) -> None:
    """When False, discard buffered monitor rows instead of writing parquet."""
    global _persist_supplement
    _persist_supplement = enabled


def set_monitor_gap_recorder(
    recorder: list[MonitorGap] | None,
    *,
    inline_compute: bool = True,
) -> None:
    """When set, record (tick_id, pair, tick_time) on monitor snapshot misses."""
    global _gap_recorder, _inline_compute
    _gap_recorder = recorder
    _inline_compute = inline_compute


def record_monitor_gap(pair: str, tick_time: dt.datetime) -> None:
    if _gap_recorder is None:
        return
    tick_time = tick_time.astimezone(dt.timezone.utc)
    gap = (_tick_id(tick_time), pair, tick_time)
    if gap not in _gap_recorder:
        _gap_recorder.append(gap)


def parsed_report_to_macdbb_row(
    report: ParsedReport,
    tick_time: dt.datetime,
    *,
    source: str = "monitor",
) -> dict[str, Any]:
    tick_time = tick_time.astimezone(dt.timezone.utc)
    tick_ms = int(tick_time.timestamp() * 1000)
    tick_iso = tick_time.isoformat()
    row: dict[str, Any] = {
        "tick_ts_ms": tick_ms,
        "tick_ts_iso": tick_iso,
        "tick_id": _tick_id(tick_time),
        "pair": report.pair,
        "interval": report.interval,
        "signal": report.signal,
        "price": report.price,
        "bb_pos_pct": report.bb_pos_pct,
        "bb_mid": report.bb_mid,
        "bb_upper": report.bb_upper,
        "macd": report.macd,
        "signal_line": report.signal_line,
        "histogram": report.histogram,
        "trend": report.trend,
        "momentum": report.momentum,
        "bullish_cross": report.bullish_cross,
        "price_le_mid": report.price_le_mid,
        "bearish_cross": report.bearish_cross,
        "price_ge_upper": report.price_ge_upper,
        "macd_lt_zero": report.macd_lt_zero,
    }
    if source:
        row["source"] = source
    return row


def _default_cache_dir(candle_source: str) -> Path:
    if candle_source == "binance_perpetual":
        return DEFAULT_BINANCE_CACHE_DIR
    return DEFAULT_HL_CACHE_DIR


def _cache_pair(trading_pair: str, candle_source: str) -> str:
    if candle_source == "binance_perpetual":
        return binance_pair_from_any(trading_pair)
    if "-" in trading_pair:
        return trading_pair
    return f"{trading_pair}-USD"


def _load_1h_closes(
    pair: str,
    tick_time: dt.datetime,
    *,
    cache_dir: Path | None,
    candle_source: str,
    hours: int = MACDBB_1H_LOOKBACK_HOURS,
) -> np.ndarray | None:
    end = tick_time.astimezone(dt.timezone.utc)
    start = end - dt.timedelta(hours=hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    cache_pair = _cache_pair(pair, candle_source)
    resolved_cache = cache_dir or _default_cache_dir(candle_source)
    if candle_source == "binance_perpetual":
        candles = load_binance_candles_in_range(
            cache_pair,
            "1h",
            start_ms,
            end_ms,
            cache_dir=resolved_cache,
        )
    else:
        candles = load_hl_candles_in_range(
            cache_pair,
            "1h",
            start_ms,
            end_ms,
            cache_dir=resolved_cache,
        )
    if not candles:
        return None
    return np.array([float(c["close"]) for c in candles], dtype=float)


def compute_macdbb_at_tick(
    pair: str,
    tick_time: dt.datetime,
    *,
    cache_dir: Path | str | None = None,
    candle_source: str = "binance_perpetual",
    interval: str = "1h",
    hours: int = MACDBB_1H_LOOKBACK_HOURS,
) -> ParsedReport | None:
    """Compute MACD-BB metrics from cached 1h candles at a replay tick."""
    resolved_cache = Path(cache_dir) if cache_dir else None
    closes = _load_1h_closes(
        pair,
        tick_time,
        cache_dir=resolved_cache,
        candle_source=candle_source,
        hours=hours,
    )
    if closes is None:
        return None
    metrics = compute_macdbb_from_closes(closes)
    if metrics is None:
        return None
    return metrics_to_parsed_report(pair, interval, metrics)


def buffer_monitor_macdbb_row(
    report: ParsedReport,
    tick_time: dt.datetime,
    *,
    source: str = "monitor",
) -> None:
    _write_buffer.append(parsed_report_to_macdbb_row(report, tick_time, source=source))


def flush_monitor_macdbb_buffer(*, snapshot_dir: Path | str | None = None) -> int:
    """Persist buffered monitor rows; returns number of rows written."""
    global _write_buffer
    if not _write_buffer:
        return 0
    rows = list(_write_buffer)
    _write_buffer = []
    if not persist_supplement_enabled():
        return 0
    append_monitor_macdbb_rows(rows, snapshot_dir=snapshot_dir)
    return len(rows)


def batch_compute_macdbb_gaps(
    gaps: list[MonitorGap],
    *,
    cache_dir: Path | str | None = None,
    candle_source: str = "binance_perpetual",
    max_workers: int = 8,
) -> list[dict[str, Any]]:
    """Parallel batch compute for proactive backfill."""
    unique_gaps: list[MonitorGap] = []
    seen: set[tuple[str, str, str]] = set()
    for tick_id, pair, tick_time in gaps:
        tick_time = tick_time.astimezone(dt.timezone.utc)
        key = (tick_id, pair, tick_time.isoformat())
        if key in seen:
            continue
        seen.add(key)
        unique_gaps.append((tick_id, pair, tick_time))

    rows: list[dict[str, Any]] = []
    total = len(unique_gaps)
    if total:
        logger.info("Batch compute monitor MACD for %d unique gaps (%d workers)", total, max(1, min(max_workers, total)))

    def _compute_one(gap: MonitorGap) -> dict[str, Any] | None:
        _tick_id_val, pair, tick_time = gap
        report = compute_macdbb_at_tick(
            pair,
            tick_time,
            cache_dir=cache_dir,
            candle_source=candle_source,
        )
        if report is None:
            return None
        return parsed_report_to_macdbb_row(report, tick_time, source="monitor")

    workers = max(1, min(max_workers, len(unique_gaps) or 1))
    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_compute_one, gap): gap for gap in unique_gaps}
        for future in as_completed(futures):
            completed += 1
            try:
                row = future.result()
            except Exception:
                failed += 1
                logger.exception("Monitor MACD compute failed for %s", futures[future])
                continue
            if row is not None:
                rows.append(row)
            if completed == 1 or completed % max(1, total // 20) == 0 or completed == total:
                logger.info(
                    "Batch compute progress: %d/%d (%d rows, %d failed)",
                    completed,
                    total,
                    len(rows),
                    failed,
                )
    return rows


def update_monitor_manifest(
    *,
    snapshot_dir: Path | str | None = None,
    rows_added: int = 0,
) -> None:
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest, write_manifest

    root = snapshot_dir_or_default(snapshot_dir)
    manifest = load_manifest(snapshot_dir=root) or {}
    monitor_path = root / MONITOR_MACDBB_FILENAME
    total_rows = 0
    if monitor_path.is_file():
        import pandas as pd

        frame = pd.read_parquet(monitor_path)
        total_rows = len(frame)
    manifest["monitor_macdbb_rows"] = total_rows
    manifest["monitor_backfill_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["monitor_backfill_version"] = MONITOR_BACKFILL_VERSION
    if rows_added:
        manifest["monitor_backfill_last_batch"] = rows_added
    write_manifest(manifest, snapshot_dir=root)


def active_snapshot_dir() -> Path | None:
    return get_snapshot_dir()
