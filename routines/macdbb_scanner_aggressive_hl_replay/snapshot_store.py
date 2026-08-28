"""Parquet-backed market snapshot store for report-free replay."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from routines.macdbb_scanner_aggressive_hl_replay.models import ParsedReport, ReportMeta
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    ParsedScannerReport,
    ScannerPairRow,
    ScannerReportMeta,
    parse_dt,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SNAPSHOT_DIR = ROOT_DIR / "data" / "replay_snapshots"

SCANNER_FILENAME = "scanner.parquet"
MACDBB_FILENAME = "macdbb.parquet"
MONITOR_MACDBB_FILENAME = "macdbb_monitor.parquet"
MANIFEST_FILENAME = "manifest.json"
MONITOR_BACKFILL_VERSION = 1

_active_snapshot_dir: Path | None = None
_scanner_index: list[ScannerReportMeta] | None = None
_macdbb_index: list[ReportMeta] | None = None
_scanner_frame: pd.DataFrame | None = None
_macdbb_frame: pd.DataFrame | None = None
_parsed_scanner_by_tick: dict[str, ParsedScannerReport] | None = None
_parsed_macdbb_by_id: dict[str, ParsedReport] | None = None
_parsed_cache_range: tuple[int | None, int | None] | None = None


def configure_snapshot_dir(snapshot_dir: Path | str | None) -> None:
    """Set active snapshot directory and clear cached indexes."""
    global _active_snapshot_dir, _scanner_index, _macdbb_index, _scanner_frame, _macdbb_frame
    global _parsed_scanner_by_tick, _parsed_macdbb_by_id, _parsed_cache_range
    new_dir = None if snapshot_dir is None else Path(snapshot_dir).resolve()
    old_dir = _active_snapshot_dir.resolve() if _active_snapshot_dir is not None else None
    if new_dir == old_dir:
        return
    _active_snapshot_dir = new_dir
    _scanner_index = None
    _macdbb_index = None
    _scanner_frame = None
    _macdbb_frame = None
    _parsed_scanner_by_tick = None
    _parsed_macdbb_by_id = None
    _parsed_cache_range = None


def _activate_snapshot_dir(root: Path) -> None:
    """Mark snapshot dir active without invalidating loaded caches."""
    global _active_snapshot_dir
    _active_snapshot_dir = root.resolve()


def get_snapshot_dir() -> Path | None:
    return _active_snapshot_dir


def is_snapshot_store_active() -> bool:
    if _active_snapshot_dir is None:
        return False
    root = _active_snapshot_dir
    return (
        (root / SCANNER_FILENAME).is_file()
        or (root / MACDBB_FILENAME).is_file()
        or (root / MONITOR_MACDBB_FILENAME).is_file()
    )


def snapshot_dir_or_default(snapshot_dir: Path | str | None = None) -> Path:
    if snapshot_dir is not None:
        return Path(snapshot_dir).resolve()
    if _active_snapshot_dir is not None:
        return _active_snapshot_dir
    return DEFAULT_SNAPSHOT_DIR.resolve()


def _tick_range_ms(
    range_start_utc: str | None,
    range_end_utc: str | None,
) -> tuple[int | None, int | None]:
    if range_start_utc and range_end_utc:
        start_ms = int(parse_iso_utc(range_start_utc).timestamp() * 1000)
        end_ms = int(parse_iso_utc(range_end_utc).timestamp() * 1000)
        return (start_ms, end_ms)
    return (None, None)


def parsed_snapshot_cache_range() -> tuple[int | None, int | None] | None:
    """Tick ms range the parsed Python caches were built for, or None if empty."""
    return _parsed_cache_range


def _set_parsed_cache_range(tick_start_ms: int | None, tick_end_ms: int | None) -> None:
    global _parsed_cache_range
    _parsed_cache_range = (tick_start_ms, tick_end_ms)


def warm_snapshot_caches(
    snapshot_dir: Path | str | None = None,
    *,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> None:
    """Eagerly load scanner + macdbb parsed caches and indexes (single pass each)."""
    root = snapshot_dir_or_default(snapshot_dir)
    start_ms, end_ms = _tick_range_ms(range_start_utc, range_end_utc)
    _ensure_parsed_scanner_cache(root, tick_start_ms=start_ms, tick_end_ms=end_ms)
    _ensure_parsed_macdbb_cache(root, tick_start_ms=start_ms, tick_end_ms=end_ms)
    _set_parsed_cache_range(start_ms, end_ms)


def reload_snapshot_caches(
    snapshot_dir: Path | str | None = None,
    *,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> None:
    """Drop in-memory snapshot indexes and reload parquet from disk.

    When ``range_start_utc`` / ``range_end_utc`` match an already-warmed parsed
    cache for this directory, skip the reload so a 30d hydrate does not rebuild
    the full-year Python object cache.
    """
    global _active_snapshot_dir
    root = snapshot_dir_or_default(snapshot_dir).resolve()
    requested = _tick_range_ms(range_start_utc, range_end_utc)
    if (
        _parsed_scanner_by_tick is not None
        and _parsed_macdbb_by_id is not None
        and get_snapshot_dir() == root
        and _parsed_cache_range == requested
    ):
        return
    _active_snapshot_dir = root
    _invalidate_indexes()
    _ensure_parsed_scanner_cache(
        root,
        tick_start_ms=requested[0],
        tick_end_ms=requested[1],
    )
    _ensure_parsed_macdbb_cache(
        root,
        tick_start_ms=requested[0],
        tick_end_ms=requested[1],
    )
    _set_parsed_cache_range(requested[0], requested[1])


def _tick_id(tick_time: dt.datetime) -> str:
    return tick_time.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def _scanner_rows_from_state(state: TickMarketState) -> list[dict[str, Any]]:
    if state.parsed_scanner is None:
        return []
    rows: list[dict[str, Any]] = []
    tick_ms = int(state.tick_time.timestamp() * 1000)
    tick_iso = state.tick_time.astimezone(dt.timezone.utc).isoformat()
    lookback = state.parsed_scanner.lookback_hours or 6
    for bucket, items in (
        ("mature", state.parsed_scanner.mature),
        ("degen", state.parsed_scanner.degen),
    ):
        for row in items:
            rows.append(
                {
                    "tick_ts_ms": tick_ms,
                    "tick_ts_iso": tick_iso,
                    "tick_id": _tick_id(state.tick_time),
                    "lookback_hours": lookback,
                    "scanner_interval": state.scanner_interval,
                    "total_analyzed": state.parsed_scanner.total_analyzed,
                    "bucket": bucket,
                    "pair": row.pair,
                    "volume_24h_usd": row.volume_24h_usd,
                    "price_change_24h": row.price_change_24h,
                    "natr_mean": row.natr_mean,
                    "natr_cv": row.natr_cv,
                    "bucket_cv": row.bucket_cv,
                    "price_range_pct": row.price_range_pct,
                }
            )
    return rows


def _macdbb_rows_from_state(state: TickMarketState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tick_ms = int(state.tick_time.timestamp() * 1000)
    tick_iso = state.tick_time.astimezone(dt.timezone.utc).isoformat()
    tick_key = _tick_id(state.tick_time)
    for report in state.macdbb_reports:
        rows.append(
            {
                "tick_ts_ms": tick_ms,
                "tick_ts_iso": tick_iso,
                "tick_id": tick_key,
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
        )
    return rows


def _load_scanner_frame(snapshot_dir: Path) -> pd.DataFrame:
    global _scanner_frame
    resolved = snapshot_dir.resolve()
    if _scanner_frame is not None and get_snapshot_dir() == resolved:
        return _scanner_frame
    path = resolved / SCANNER_FILENAME
    if not path.is_file():
        _scanner_frame = pd.DataFrame()
        return _scanner_frame
    _scanner_frame = pd.read_parquet(path)
    return _scanner_frame


def _load_monitor_macdbb_frame(snapshot_dir: Path) -> pd.DataFrame:
    path = snapshot_dir / MONITOR_MACDBB_FILENAME
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _load_macdbb_frame(snapshot_dir: Path) -> pd.DataFrame:
    global _macdbb_frame
    resolved = snapshot_dir.resolve()
    if _macdbb_frame is not None and get_snapshot_dir() == resolved:
        return _macdbb_frame
    base_path = resolved / MACDBB_FILENAME
    if base_path.is_file():
        base = pd.read_parquet(base_path)
    else:
        base = pd.DataFrame()
    monitor = _load_monitor_macdbb_frame(resolved)
    if base.empty:
        merged = monitor
    elif monitor.empty:
        merged = base
    else:
        merged = pd.concat([base, monitor], ignore_index=True)
        subset = [
            col for col in ("tick_id", "pair", "interval") if col in merged.columns
        ]
        if subset:
            merged = merged.drop_duplicates(subset=subset, keep="last")
    _macdbb_frame = merged
    return _macdbb_frame


def existing_tick_ids(*, snapshot_dir: Path | None = None) -> set[str]:
    root = snapshot_dir_or_default(snapshot_dir)
    frame = _load_scanner_frame(root)
    if frame.empty or "tick_id" not in frame.columns:
        return set()
    return set(frame["tick_id"].astype(str).tolist())


def _write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    temp_path = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temp_path, index=False)
    temp_path.replace(path)


def append_states(
    states: list[TickMarketState],
    *,
    snapshot_dir: Path | None = None,
) -> None:
    root = snapshot_dir_or_default(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)

    scanner_rows: list[dict[str, Any]] = []
    macdbb_rows: list[dict[str, Any]] = []
    for state in states:
        scanner_rows.extend(_scanner_rows_from_state(state))
        macdbb_rows.extend(_macdbb_rows_from_state(state))

    for filename, rows in ((SCANNER_FILENAME, scanner_rows), (MACDBB_FILENAME, macdbb_rows)):
        if not rows:
            continue
        path = root / filename
        new_frame = pd.DataFrame(rows)
        if path.is_file():
            existing = pd.read_parquet(path)
            merged = pd.concat([existing, new_frame], ignore_index=True)
            if "tick_id" in merged.columns:
                subset = [
                    col
                    for col in ("tick_id", "pair", "interval", "bucket")
                    if col in merged.columns
                ]
                merged = merged.drop_duplicates(subset=subset, keep="last")
            frame = merged
        else:
            frame = new_frame
        _write_parquet_atomic(path, frame)

    configure_snapshot_dir(root)
    _invalidate_indexes()


def _invalidate_indexes() -> None:
    global _scanner_index, _macdbb_index, _scanner_frame, _macdbb_frame
    global _parsed_scanner_by_tick, _parsed_macdbb_by_id, _parsed_cache_range
    _scanner_index = None
    _macdbb_index = None
    _scanner_frame = None
    _macdbb_frame = None
    _parsed_scanner_by_tick = None
    _parsed_macdbb_by_id = None
    _parsed_cache_range = None


def invalidate_macdbb_indexes() -> None:
    """Clear merged macdbb index caches after monitor supplement writes."""
    global _macdbb_index, _macdbb_frame, _parsed_macdbb_by_id
    _macdbb_index = None
    _macdbb_frame = None
    _parsed_macdbb_by_id = None


def append_monitor_macdbb_rows(
    rows: list[dict[str, Any]],
    *,
    snapshot_dir: Path | None = None,
) -> int:
    """Append deduped rows to macdbb_monitor.parquet supplement file."""
    if not rows:
        return 0
    root = snapshot_dir_or_default(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MONITOR_MACDBB_FILENAME
    new_frame = pd.DataFrame(rows)
    if path.is_file():
        existing = pd.read_parquet(path)
        merged = pd.concat([existing, new_frame], ignore_index=True)
        subset = [
            col for col in ("tick_id", "pair", "interval") if col in merged.columns
        ]
        if subset:
            merged = merged.drop_duplicates(subset=subset, keep="last")
        frame = merged
    else:
        frame = new_frame
    _write_parquet_atomic(path, frame)
    configure_snapshot_dir(root)
    invalidate_macdbb_indexes()
    return len(rows)


def _filter_frame_by_tick_ms(
    frame: pd.DataFrame,
    *,
    tick_start_ms: int | None,
    tick_end_ms: int | None,
) -> pd.DataFrame:
    if frame.empty or tick_start_ms is None or tick_end_ms is None:
        return frame
    if "tick_ts_ms" not in frame.columns:
        return frame
    return frame[
        (frame["tick_ts_ms"] >= tick_start_ms) & (frame["tick_ts_ms"] <= tick_end_ms)
    ]


def _ensure_parsed_scanner_cache(
    root: Path,
    *,
    tick_start_ms: int | None = None,
    tick_end_ms: int | None = None,
) -> dict[str, ParsedScannerReport]:
    global _parsed_scanner_by_tick, _scanner_index, _scanner_frame
    resolved = root.resolve()
    requested = (tick_start_ms, tick_end_ms)
    if (
        _parsed_scanner_by_tick is not None
        and get_snapshot_dir() == resolved
        and (
            _parsed_cache_range == requested
            or (tick_start_ms is None and tick_end_ms is None)
        )
    ):
        return _parsed_scanner_by_tick

    frame = _filter_frame_by_tick_ms(
        _load_scanner_frame(resolved),
        tick_start_ms=tick_start_ms,
        tick_end_ms=tick_end_ms,
    )
    cache: dict[str, ParsedScannerReport] = {}
    metas: list[ScannerReportMeta] = []
    if frame.empty:
        _parsed_scanner_by_tick = cache
        _scanner_index = []
        _activate_snapshot_dir(resolved)
        return cache

    for tick_id, group in frame.groupby("tick_id", sort=False):
        first = group.iloc[0]
        mature: list[ScannerPairRow] = []
        degen: list[ScannerPairRow] = []
        for row in group.itertuples(index=False):
            pair_row = ScannerPairRow(
                pair=str(row.pair),
                volume_24h_usd=float(row.volume_24h_usd),
                price_change_24h=float(row.price_change_24h),
                natr_mean=float(row.natr_mean),
                natr_cv=float(row.natr_cv),
                bucket_cv=float(row.bucket_cv),
                price_range_pct=float(row.price_range_pct),
            )
            if str(row.bucket) == "degen":
                degen.append(pair_row)
            else:
                mature.append(pair_row)
        tick_key = str(tick_id)
        cache[tick_key] = ParsedScannerReport(
            total_analyzed=int(first["total_analyzed"]),
            mature=mature,
            degen=degen,
            lookback_hours=int(first["lookback_hours"]),
        )
        metas.append(
            ScannerReportMeta(
                report_id=tick_key,
                filename=f"snapshot://scanner/{tick_key}",
                created_at=parse_dt(str(first["tick_ts_iso"])),
                lookback_hours=int(first.get("lookback_hours", 6)),
                total_analyzed=int(first.get("total_analyzed", len(group))),
            )
        )

    metas.sort(key=lambda item: item.created_at)
    _parsed_scanner_by_tick = cache
    _scanner_index = metas
    _scanner_frame = None
    _activate_snapshot_dir(resolved)
    return cache


def _ensure_parsed_macdbb_cache(
    root: Path,
    *,
    tick_start_ms: int | None = None,
    tick_end_ms: int | None = None,
) -> dict[str, ParsedReport]:
    global _parsed_macdbb_by_id, _macdbb_index, _macdbb_frame
    resolved = root.resolve()
    requested = (tick_start_ms, tick_end_ms)
    if (
        _parsed_macdbb_by_id is not None
        and get_snapshot_dir() == resolved
        and (
            _parsed_cache_range == requested
            or (tick_start_ms is None and tick_end_ms is None)
        )
    ):
        return _parsed_macdbb_by_id

    frame = _filter_frame_by_tick_ms(
        _load_macdbb_frame(resolved),
        tick_start_ms=tick_start_ms,
        tick_end_ms=tick_end_ms,
    )
    cache: dict[str, ParsedReport] = {}
    metas: list[ReportMeta] = []
    if frame.empty:
        _parsed_macdbb_by_id = cache
        _macdbb_index = []
        _activate_snapshot_dir(resolved)
        return cache

    for row in frame.itertuples(index=False):
        tick_id = str(row.tick_id)
        pair = str(row.pair)
        interval = str(row.interval)
        report_id = f"{tick_id}:{pair}:{interval}"
        tick_time = parse_dt(str(row.tick_ts_iso))
        cache[report_id] = ParsedReport(
            pair=pair,
            interval=interval,
            signal=str(row.signal),
            price=float(row.price),
            bb_pos_pct=float(row.bb_pos_pct),
            bb_mid=float(row.bb_mid),
            bb_upper=float(row.bb_upper),
            macd=float(row.macd),
            signal_line=float(row.signal_line),
            histogram=float(row.histogram),
            trend=str(row.trend),
            momentum=str(row.momentum),
            bullish_cross=bool(row.bullish_cross),
            price_le_mid=bool(row.price_le_mid),
            bearish_cross=bool(row.bearish_cross),
            price_ge_upper=bool(row.price_ge_upper),
            macd_lt_zero=bool(row.macd_lt_zero),
        )
        metas.append(
            ReportMeta(
                report_id=report_id,
                filename=f"snapshot://macdbb/{tick_id}/{pair}/{interval}",
                created_at=tick_time,
                pair=pair,
                interval=interval,
            )
        )

    metas.sort(key=lambda item: item.created_at)
    _parsed_macdbb_by_id = cache
    _macdbb_index = metas
    _macdbb_frame = None
    _activate_snapshot_dir(resolved)
    return cache


def write_manifest(
    manifest: dict[str, Any],
    *,
    snapshot_dir: Path | None = None,
) -> Path:
    root = snapshot_dir_or_default(snapshot_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def load_manifest(*, snapshot_dir: Path | None = None) -> dict[str, Any] | None:
    path = snapshot_dir_or_default(snapshot_dir) / MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_scanner_index(*, snapshot_dir: Path | None = None) -> list[ScannerReportMeta]:
    root = snapshot_dir_or_default(snapshot_dir)
    if _scanner_index is not None and get_snapshot_dir() == root:
        return _scanner_index
    _ensure_parsed_scanner_cache(root)
    return _scanner_index or []


def load_macdbb_index(*, snapshot_dir: Path | None = None) -> list[ReportMeta]:
    root = snapshot_dir_or_default(snapshot_dir)
    if _macdbb_index is not None and get_snapshot_dir() == root:
        return _macdbb_index
    _ensure_parsed_macdbb_cache(root)
    return _macdbb_index or []


def nearest_scanner_snapshot(
    tick_time: dt.datetime,
    max_window_minutes: int,
    *,
    snapshot_dir: Path | None = None,
) -> ScannerReportMeta | None:
    index = load_scanner_index(snapshot_dir=snapshot_dir)
    if not index:
        return None
    max_delta = dt.timedelta(minutes=max_window_minutes)
    nearest: tuple[dt.timedelta, ScannerReportMeta] | None = None
    for candidate in index:
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def load_parsed_scanner_snapshot(
    report_meta: ScannerReportMeta,
    *,
    snapshot_dir: Path | None = None,
) -> ParsedScannerReport | None:
    root = snapshot_dir_or_default(snapshot_dir)
    cache = _ensure_parsed_scanner_cache(root)
    return cache.get(str(report_meta.report_id))


def nearest_macdbb_snapshot(
    pair: str,
    tick_time: dt.datetime,
    max_window_minutes: int,
    interval: str = "1h",
    *,
    snapshot_dir: Path | None = None,
) -> ReportMeta | None:
    index = [
        item
        for item in load_macdbb_index(snapshot_dir=snapshot_dir)
        if item.pair == pair
    ]
    if not index:
        return None
    max_delta = dt.timedelta(minutes=max_window_minutes)
    nearest: tuple[dt.timedelta, ReportMeta] | None = None
    for candidate in index:
        if candidate.interval != interval:
            continue
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def load_parsed_macdbb_snapshot(
    report_meta: ReportMeta,
    *,
    snapshot_dir: Path | None = None,
) -> ParsedReport | None:
    root = snapshot_dir_or_default(snapshot_dir)
    cache = _ensure_parsed_macdbb_cache(root)
    return cache.get(str(report_meta.report_id))


def _concat_snapshot_parquet(
    source_path: Path,
    dest_path: Path,
    *,
    subset: tuple[str, ...],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in (dest_path, source_path):
        if path.is_file():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    present = [col for col in subset if col in merged.columns]
    if present:
        merged = merged.drop_duplicates(subset=present, keep="last")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(dest_path, merged)
    return merged


def merge_snapshot_stores(source_dir: Path | str, dest_dir: Path | str) -> dict[str, Any]:
    """Concat source parquets into dest, dedupe tick identity, merge manifests."""
    source = Path(source_dir)
    dest = Path(dest_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"Source snapshot dir does not exist: {source}")
    dest.mkdir(parents=True, exist_ok=True)

    scanner = _concat_snapshot_parquet(
        source / SCANNER_FILENAME,
        dest / SCANNER_FILENAME,
        subset=("tick_id", "pair", "interval", "bucket"),
    )
    macdbb = _concat_snapshot_parquet(
        source / MACDBB_FILENAME,
        dest / MACDBB_FILENAME,
        subset=("tick_id", "pair", "interval"),
    )
    monitor = _concat_snapshot_parquet(
        source / MONITOR_MACDBB_FILENAME,
        dest / MONITOR_MACDBB_FILENAME,
        subset=("tick_id", "pair", "interval"),
    )

    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import merge_manifest

    dest_manifest = load_manifest(snapshot_dir=dest) or {}
    source_manifest = load_manifest(snapshot_dir=source) or {}
    unique_ticks = (
        int(scanner["tick_id"].nunique()) if not scanner.empty and "tick_id" in scanner.columns else 0
    )
    merged_manifest = merge_manifest(
        dest_manifest or None,
        built=0,
        range_start_utc=source_manifest.get("range_start_utc"),
        range_end_utc=source_manifest.get("range_end_utc"),
        snapshot_dir=dest,
        cache_dir=Path(
            str(
                source_manifest.get("cache_dir")
                or dest_manifest.get("cache_dir")
                or dest
            )
        ),
        candle_source=str(
            source_manifest.get("candle_source")
            or dest_manifest.get("candle_source")
            or "binance_perpetual"
        ),
        volume_source=str(
            source_manifest.get("volume_source")
            or dest_manifest.get("volume_source")
            or "binance_perpetual"
        ),
        frequency_sec=int(
            source_manifest.get("frequency_sec")
            or dest_manifest.get("frequency_sec")
            or 60
        ),
        intersection_manifest=None,
        sessions=str(
            source_manifest.get("sessions") or dest_manifest.get("sessions") or ""
        ),
    )
    merged_manifest["tick_count"] = unique_ticks
    prior_merged = list(dest_manifest.get("merged_from") or [])
    source_label = str(source)
    if source_label not in prior_merged:
        prior_merged.append(source_label)
    merged_manifest["merged_from"] = prior_merged
    if not monitor.empty:
        merged_manifest["monitor_macdbb_rows"] = int(len(monitor))
    write_manifest(merged_manifest, snapshot_dir=dest)
    configure_snapshot_dir(dest)
    _invalidate_indexes()
    logger.info(
        "Merged %s into %s ticks=%d scanner_rows=%d macdbb_rows=%d",
        source,
        dest,
        unique_ticks,
        len(scanner),
        len(macdbb),
    )
    return merged_manifest
