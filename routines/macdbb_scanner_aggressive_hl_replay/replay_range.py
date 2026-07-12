"""Timeline replay date-range helpers."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from routines.macdbb_scanner_aggressive_hl_replay.reports import load_scanner_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc

if TYPE_CHECKING:
    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
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


@dataclass(frozen=True)
class SnapshotCoverage:
    start_utc: str | None
    end_utc: str | None
    tick_count: int | None
    updated_at: str | None


@dataclass(frozen=True)
class CoverageGap:
    gap_start_utc: str
    gap_end_utc: str
    gap_days: float
    coverage_end_utc: str | None
    coverage_start_utc: str | None


def snapshot_coverage(snapshot_dir: Path | str | None = None) -> SnapshotCoverage:
    """Return manifest/index coverage metadata for a snapshot directory."""
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import coverage_datetimes
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
        load_manifest,
        snapshot_dir_or_default,
    )

    root = snapshot_dir_or_default(snapshot_dir)
    manifest = load_manifest(snapshot_dir=root) or {}
    coverage_start, coverage_end = coverage_datetimes(root)
    start_utc = iso_utc(coverage_start) if coverage_start else None
    end_utc = iso_utc(coverage_end) if coverage_end else None
    if manifest.get("range_start_utc"):
        start_utc = str(manifest["range_start_utc"])
    if manifest.get("range_end_utc"):
        end_utc = str(manifest["range_end_utc"])
    tick_count = manifest.get("tick_count")
    return SnapshotCoverage(
        start_utc=start_utc,
        end_utc=end_utc,
        tick_count=int(tick_count) if tick_count is not None else None,
        updated_at=str(manifest["updated_at"]) if manifest.get("updated_at") else None,
    )


def requested_range_exceeds_coverage(
    config: DynamicStrategyReplayConfig,
) -> CoverageGap | None:
    """Return a coverage gap when the requested timeline exceeds snapshot data."""
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_build import (
        SnapshotGap,
        compute_snapshot_gap,
        coverage_datetimes,
    )

    if config.replay_mode != "timeline_backtest" or config.data_source != "snapshots":
        return None
    if not config.range_start_utc or not config.range_end_utc:
        return None

    requested_start = parse_iso_utc(config.range_start_utc)
    requested_end = parse_iso_utc(config.range_end_utc)
    coverage_start, coverage_end = coverage_datetimes(config.snapshot_dir)
    gap: SnapshotGap | None = compute_snapshot_gap(
        requested_start,
        requested_end,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    if gap is None:
        return None
    coverage = snapshot_coverage(config.snapshot_dir)
    return CoverageGap(
        gap_start_utc=gap.gap_start_utc,
        gap_end_utc=gap.gap_end_utc,
        gap_days=gap.gap_days,
        coverage_end_utc=gap.coverage_end_utc,
        coverage_start_utc=coverage.start_utc,
    )
