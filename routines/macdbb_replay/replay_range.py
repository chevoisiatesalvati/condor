"""Timeline replay date-range helpers."""

from __future__ import annotations

from datetime import datetime, timezone

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
