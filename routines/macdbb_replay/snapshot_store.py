"""Parquet-backed market snapshot store for report-free replay."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from routines.macdbb_replay.models import ParsedReport, ReportMeta
from routines.macdbb_replay.reports import (
    ParsedScannerReport,
    ScannerPairRow,
    ScannerReportMeta,
    parse_dt,
)
from routines.macdbb_replay.tick_market_state import TickMarketState

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SNAPSHOT_DIR = ROOT_DIR / "data" / "replay_snapshots"

SCANNER_FILENAME = "scanner.parquet"
MACDBB_FILENAME = "macdbb.parquet"
MANIFEST_FILENAME = "manifest.json"

_active_snapshot_dir: Path | None = None
_scanner_index: list[ScannerReportMeta] | None = None
_macdbb_index: list[ReportMeta] | None = None
_scanner_frame: pd.DataFrame | None = None
_macdbb_frame: pd.DataFrame | None = None
_parsed_scanner_by_tick: dict[str, ParsedScannerReport] | None = None
_parsed_macdbb_by_id: dict[str, ParsedReport] | None = None


def configure_snapshot_dir(snapshot_dir: Path | str | None) -> None:
    """Set active snapshot directory and clear cached indexes."""
    global _active_snapshot_dir, _scanner_index, _macdbb_index, _scanner_frame, _macdbb_frame
    global _parsed_scanner_by_tick, _parsed_macdbb_by_id
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


def _activate_snapshot_dir(root: Path) -> None:
    """Mark snapshot dir active without invalidating loaded caches."""
    global _active_snapshot_dir
    _active_snapshot_dir = root.resolve()


def get_snapshot_dir() -> Path | None:
    return _active_snapshot_dir


def is_snapshot_store_active() -> bool:
    if _active_snapshot_dir is None:
        return False
    return (_active_snapshot_dir / SCANNER_FILENAME).is_file() or (
        _active_snapshot_dir / MACDBB_FILENAME
    ).is_file()


def snapshot_dir_or_default(snapshot_dir: Path | str | None = None) -> Path:
    if snapshot_dir is not None:
        return Path(snapshot_dir)
    if _active_snapshot_dir is not None:
        return _active_snapshot_dir
    return DEFAULT_SNAPSHOT_DIR


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
    if _scanner_frame is not None and get_snapshot_dir() == snapshot_dir:
        return _scanner_frame
    path = snapshot_dir / SCANNER_FILENAME
    if not path.is_file():
        _scanner_frame = pd.DataFrame()
        return _scanner_frame
    _scanner_frame = pd.read_parquet(path)
    return _scanner_frame


def _load_macdbb_frame(snapshot_dir: Path) -> pd.DataFrame:
    global _macdbb_frame
    if _macdbb_frame is not None and get_snapshot_dir() == snapshot_dir:
        return _macdbb_frame
    path = snapshot_dir / MACDBB_FILENAME
    if not path.is_file():
        _macdbb_frame = pd.DataFrame()
        return _macdbb_frame
    _macdbb_frame = pd.read_parquet(path)
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
    global _scanner_index, _macdbb_index, _scanner_frame, _macdbb_frame, _parsed_scanner_by_tick
    _scanner_index = None
    _macdbb_index = None
    _scanner_frame = None
    _macdbb_frame = None
    _parsed_scanner_by_tick = None
    _parsed_macdbb_by_id = None


def _ensure_parsed_scanner_cache(root: Path) -> dict[str, ParsedScannerReport]:
    global _parsed_scanner_by_tick
    if _parsed_scanner_by_tick is not None and get_snapshot_dir() == root:
        return _parsed_scanner_by_tick

    frame = _load_scanner_frame(root)
    cache: dict[str, ParsedScannerReport] = {}
    if frame.empty:
        _parsed_scanner_by_tick = cache
        return cache

    for tick_id, group in frame.groupby("tick_id"):
        first = group.iloc[0]
        mature: list[ScannerPairRow] = []
        degen: list[ScannerPairRow] = []
        for _, row in group.iterrows():
            pair_row = ScannerPairRow(
                pair=str(row["pair"]),
                volume_24h_usd=float(row["volume_24h_usd"]),
                price_change_24h=float(row["price_change_24h"]),
                natr_mean=float(row["natr_mean"]),
                natr_cv=float(row["natr_cv"]),
                bucket_cv=float(row["bucket_cv"]),
                price_range_pct=float(row["price_range_pct"]),
            )
            if str(row["bucket"]) == "degen":
                degen.append(pair_row)
            else:
                mature.append(pair_row)
        cache[str(tick_id)] = ParsedScannerReport(
            total_analyzed=int(first["total_analyzed"]),
            mature=mature,
            degen=degen,
            lookback_hours=int(first["lookback_hours"]),
        )

    _parsed_scanner_by_tick = cache
    _activate_snapshot_dir(root)
    return cache


def _ensure_parsed_macdbb_cache(root: Path) -> dict[str, ParsedReport]:
    global _parsed_macdbb_by_id
    if _parsed_macdbb_by_id is not None and get_snapshot_dir() == root:
        return _parsed_macdbb_by_id

    frame = _load_macdbb_frame(root)
    cache: dict[str, ParsedReport] = {}
    if frame.empty:
        _parsed_macdbb_by_id = cache
        return cache

    for _, row in frame.iterrows():
        tick_id = str(row["tick_id"])
        pair = str(row["pair"])
        interval = str(row["interval"])
        report_id = f"{tick_id}:{pair}:{interval}"
        cache[report_id] = ParsedReport(
            pair=pair,
            interval=interval,
            signal=str(row["signal"]),
            price=float(row["price"]),
            bb_pos_pct=float(row["bb_pos_pct"]),
            bb_mid=float(row["bb_mid"]),
            bb_upper=float(row["bb_upper"]),
            macd=float(row["macd"]),
            signal_line=float(row["signal_line"]),
            histogram=float(row["histogram"]),
            trend=str(row["trend"]),
            momentum=str(row["momentum"]),
            bullish_cross=bool(row["bullish_cross"]),
            price_le_mid=bool(row["price_le_mid"]),
            bearish_cross=bool(row["bearish_cross"]),
            price_ge_upper=bool(row["price_ge_upper"]),
            macd_lt_zero=bool(row["macd_lt_zero"]),
        )

    _parsed_macdbb_by_id = cache
    _activate_snapshot_dir(root)
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
    global _scanner_index
    root = snapshot_dir_or_default(snapshot_dir)
    if _scanner_index is not None and get_snapshot_dir() == root:
        return _scanner_index

    frame = _load_scanner_frame(root)
    if frame.empty:
        _scanner_index = []
        return _scanner_index

    metas: list[ScannerReportMeta] = []
    for tick_id, group in frame.groupby("tick_id"):
        first = group.iloc[0]
        tick_time = parse_dt(str(first["tick_ts_iso"]))
        lookback = int(first.get("lookback_hours", 6))
        total = int(first.get("total_analyzed", len(group)))
        metas.append(
            ScannerReportMeta(
                report_id=str(tick_id),
                filename=f"snapshot://scanner/{tick_id}",
                created_at=tick_time,
                lookback_hours=lookback,
                total_analyzed=total,
            )
        )
    metas.sort(key=lambda item: item.created_at)
    _scanner_index = metas
    _activate_snapshot_dir(root)
    return metas


def load_macdbb_index(*, snapshot_dir: Path | None = None) -> list[ReportMeta]:
    global _macdbb_index
    root = snapshot_dir_or_default(snapshot_dir)
    if _macdbb_index is not None and get_snapshot_dir() == root:
        return _macdbb_index

    frame = _load_macdbb_frame(root)
    if frame.empty:
        _macdbb_index = []
        return _macdbb_index

    metas: list[ReportMeta] = []
    for _, row in frame.iterrows():
        tick_time = parse_dt(str(row["tick_ts_iso"]))
        tick_key = str(row["tick_id"])
        pair = str(row["pair"])
        interval = str(row["interval"])
        metas.append(
            ReportMeta(
                report_id=f"{tick_key}:{pair}:{interval}",
                filename=f"snapshot://macdbb/{tick_key}/{pair}/{interval}",
                created_at=tick_time,
                pair=pair,
                interval=interval,
            )
        )
    metas.sort(key=lambda item: item.created_at)
    _macdbb_index = metas
    _activate_snapshot_dir(root)
    return metas


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
