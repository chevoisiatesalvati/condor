"""Tick schedule fixtures for report-driven replay."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from routines.macdbb_replay.journal import parse_dt
from routines.macdbb_replay.models import TickMeta

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
