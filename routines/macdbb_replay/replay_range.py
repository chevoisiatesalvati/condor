"""Timeline replay date-range helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from routines.macdbb_replay.reports import load_scanner_reports_index


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timeline_range_from_reports() -> tuple[str, str]:
    reports = load_scanner_reports_index()
    if not reports:
        raise ValueError("No scanner reports in index for timeline replay range")
    oldest = min(report.created_at for report in reports)
    newest = max(report.created_at for report in reports)
    return iso_utc(oldest), iso_utc(newest)


def timeline_range_from_snapshots(
    snapshot_dir: Path | str | None = None,
) -> tuple[str, str]:
    """Return inclusive UTC range covered by parquet replay snapshots."""
    from routines.macdbb_replay.snapshot_store import (
        load_manifest,
        load_scanner_index,
        snapshot_dir_or_default,
    )

    root = snapshot_dir_or_default(Path(snapshot_dir) if snapshot_dir else None)
    manifest = load_manifest(snapshot_dir=root)
    if manifest:
        start = manifest.get("range_start_utc")
        end = manifest.get("range_end_utc")
        if start and end:
            return str(start), str(end)

    reports = load_scanner_index(snapshot_dir=root)
    if not reports:
        raise ValueError(f"No snapshot ticks in {root} for timeline replay range")
    oldest = min(report.created_at for report in reports)
    newest = max(report.created_at for report in reports)
    return iso_utc(oldest), iso_utc(newest)
