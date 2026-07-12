"""Tick schedule fixtures for report-driven replay."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.journal import parse_dt
from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta

_TICK_RE = re.compile(r"- tick#(\d+)\s+\|\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+\|")


def parse_tick_schedule(journal_text: str) -> dict[int, TickMeta]:
    """Extract tick timestamps from journal ``## Ticks`` only — no Decisions/snapshots."""
    tick_meta_map: dict[int, TickMeta] = {}
    for line in journal_text.splitlines():
        tick_match = _TICK_RE.match(line)
        if not tick_match:
            continue
        tick_number = int(tick_match.group(1))
        tick_meta_map[tick_number] = TickMeta(
            tick=tick_number,
            timestamp=parse_dt(tick_match.group(2)),
            macd_pairs=[],
        )
    return tick_meta_map


def parse_tick_schedule_file(journal_path: Path) -> dict[int, TickMeta]:
    return parse_tick_schedule(journal_path.read_text(encoding="utf-8"))


def build_range_tick_schedule(
    range_start: dt.datetime,
    range_end: dt.datetime,
    frequency_sec: int,
) -> dict[int, TickMeta]:
    """Synthetic tick clock from UTC range and step size (no journal)."""
    if frequency_sec <= 0:
        raise ValueError("frequency_sec must be positive")
    start = range_start.astimezone(dt.timezone.utc)
    end = range_end.astimezone(dt.timezone.utc)
    if start > end:
        raise ValueError("range_start must be <= range_end")

    tick_meta_map: dict[int, TickMeta] = {}
    tick_number = 1
    current = start
    step = dt.timedelta(seconds=frequency_sec)
    while current <= end:
        tick_meta_map[tick_number] = TickMeta(
            tick=tick_number,
            timestamp=current,
            macd_pairs=[],
        )
        tick_number += 1
        current += step
    return tick_meta_map


def session_tick_range(tick_meta_map: dict[int, TickMeta]) -> tuple[dt.datetime, dt.datetime] | None:
    """Min/max tick timestamps from a schedule (e.g. journal ticks for sweep range helper)."""
    if not tick_meta_map:
        return None
    times = [meta.timestamp for meta in tick_meta_map.values()]
    return min(times), max(times)


def parse_iso_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iter_timeline_tick_chunks(
    tick_meta_map: dict[int, TickMeta],
    *,
    chunk_days: int = 28,
    overlap_days: int = 7,
) -> list[dict[int, TickMeta]]:
    """Split a tick schedule into overlapping UTC chunks for bounded-memory replay."""
    if chunk_days <= 0 or not tick_meta_map:
        return [tick_meta_map]
    ordered = sorted(tick_meta_map.items(), key=lambda item: item[1].timestamp)
    start_time = ordered[0][1].timestamp
    end_time = ordered[-1][1].timestamp
    overlap = dt.timedelta(days=max(0, overlap_days))
    step = dt.timedelta(days=chunk_days)
    chunks: list[dict[int, TickMeta]] = []
    chunk_start = start_time
    while chunk_start <= end_time:
        chunk_end = min(chunk_start + step, end_time)
        window_start = chunk_start - overlap if chunks else chunk_start
        window_end = chunk_end
        chunk = {
            tick: meta
            for tick, meta in ordered
            if window_start <= meta.timestamp <= window_end
        }
        if chunk:
            chunks.append(chunk)
        if chunk_end >= end_time:
            break
        chunk_start = chunk_end
    return chunks or [tick_meta_map]
