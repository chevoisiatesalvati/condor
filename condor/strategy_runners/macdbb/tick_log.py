"""TTL'd per-tick fetch/decide/apply audit logs for MACDBB live runs."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from condor.strategy_runners.macdbb.paths import (
    DEFAULT_TICK_LOG_RETENTION_DAYS,
    MACDBB_SLUG,
    ticks_dir,
)

log = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SEC = 6 * 3600
_last_cleanup_at: dict[str, float] = {}


def _day_dir(slug: str, when: datetime | None = None) -> Path:
    moment = when or datetime.now(timezone.utc)
    return ticks_dir(slug) / moment.strftime("%Y%m%d")


def tick_log_enabled(config: dict[str, Any] | None) -> bool:
    if not config:
        return True
    return bool(config.get("tick_log_enabled", True))


def retention_days(config: dict[str, Any] | None) -> int:
    if not config:
        return DEFAULT_TICK_LOG_RETENTION_DAYS
    try:
        days = int(config.get("tick_log_retention_days", DEFAULT_TICK_LOG_RETENTION_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_TICK_LOG_RETENTION_DAYS
    return max(1, days)


def write_tick_log(
    *,
    slug: str = MACDBB_SLUG,
    session_num: int,
    tick_number: int,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> Path | None:
    """Append one compact JSON object for a live tick. Returns path or None."""
    if not tick_log_enabled(config):
        return None
    day = _day_dir(slug)
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"session_{session_num}_ticks.jsonl"
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "slug": slug,
        "session": session_num,
        "tick": tick_number,
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
    maybe_cleanup(slug, config=config)
    return path


def maybe_cleanup(
    slug: str = MACDBB_SLUG,
    *,
    config: dict[str, Any] | None = None,
    force: bool = False,
) -> int:
    """Delete tick day dirs older than retention. Returns removed file count."""
    now = time.time()
    last = _last_cleanup_at.get(slug, 0.0)
    if not force and (now - last) < _CLEANUP_INTERVAL_SEC:
        return 0
    _last_cleanup_at[slug] = now
    days = retention_days(config)
    cutoff = now - days * 86400
    root = ticks_dir(slug)
    removed = 0
    if not root.is_dir():
        return 0
    for day_path in list(root.iterdir()):
        if not day_path.is_dir():
            continue
        try:
            # YYYYMMDD folder mtime or parse name
            stamp = datetime.strptime(day_path.name, "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
            age_ref = stamp.timestamp()
        except ValueError:
            age_ref = day_path.stat().st_mtime
        if age_ref >= cutoff:
            continue
        for child in day_path.iterdir():
            try:
                child.unlink()
                removed += 1
            except OSError:
                log.debug("tick_log cleanup unlink failed: %s", child, exc_info=True)
        try:
            day_path.rmdir()
        except OSError:
            pass
    if removed:
        log.info("MACDBB tick_log cleanup removed %s files (retention=%sd)", removed, days)
    return removed


def list_recent_ticks(
    slug: str = MACDBB_SLUG,
    *,
    session: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Newest-first tick summaries (compact)."""
    root = ticks_dir(slug)
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for day_path in sorted(root.iterdir(), reverse=True):
        if not day_path.is_dir():
            continue
        files = sorted(day_path.glob("session_*_ticks.jsonl"), reverse=True)
        for path in files:
            if session is not None and f"session_{session}_" not in path.name:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if session is not None and int(row.get("session") or -1) != session:
                    continue
                records.append(row)
                if len(records) >= limit:
                    return records
    return records


def get_tick(
    slug: str,
    *,
    session: int,
    tick: int,
) -> dict[str, Any] | None:
    for row in list_recent_ticks(slug, session=session, limit=10_000):
        if int(row.get("tick") or -1) == tick:
            return row
    return None
