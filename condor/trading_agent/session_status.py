"""Helpers for inferring whether a session is actively running on disk."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

import yaml


def journal_shows_active(session_dir: Path) -> bool:
    """True when journal Summary still says Running/Paused (not Stopped)."""
    journal_path = session_dir / "journal.md"
    if not journal_path.exists():
        return False
    text = journal_path.read_text(errors="replace")
    m = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    summary = m.group(1) if m else text
    if re.search(r"Status:\s*Stopped", summary):
        return False
    return bool(re.search(r"Status:\s*(Running|Paused)", summary))


def session_last_activity_ts(session_dir: Path) -> float | None:
    """Unix timestamp of the most recent journal or snapshot write."""
    last: float | None = None
    journal_path = session_dir / "journal.md"
    if journal_path.exists():
        last = journal_path.stat().st_mtime
    snap_dir = session_dir / "snapshots"
    if snap_dir.exists():
        for snap in snap_dir.glob("snapshot_*.md"):
            last = max(last or 0.0, snap.stat().st_mtime)
    return last


def session_grace_sec(session_dir: Path, *, default_frequency_sec: int = 1800) -> float:
    """How long after the last write we still treat a session as potentially live."""
    freq = default_frequency_sec
    config_path = session_dir / "config.yml"
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("frequency_sec"):
                freq = int(data["frequency_sec"])
        except Exception:
            pass
    # Allow ~2.5 tick intervals; never less than 10 minutes.
    return max(freq * 2.5, 600.0)


def session_appears_orphaned(
    session_dir: Path,
    *,
    now: float | None = None,
    grace_sec: float | None = None,
    process_started_at: float | None = None,
) -> bool:
    """Journal says active and files were written recently (likely still ticking).

    Activity must occur after this process started — stale journals from prior
    runs do not count as orphaned after a full restart.
    """
    if not journal_shows_active(session_dir):
        return False
    last = session_last_activity_ts(session_dir)
    if last is None:
        return False
    if process_started_at is None:
        from condor.runtime import process_started_at as _process_started_at

        process_started_at = _process_started_at()
    if last < process_started_at:
        return False
    grace = grace_sec if grace_sec is not None else session_grace_sec(session_dir)
    ts = now if now is not None else time.time()
    return (ts - last) <= grace


def iter_session_dirs(agent_dir: Path) -> list[tuple[int, Path]]:
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return []
    out: list[tuple[int, Path]] = []
    for d in sessions_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            num = int(d.name.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        out.append((num, d))
    return sorted(out, key=lambda x: x[0])


def find_orphaned_active_sessions(
    slug: str,
    agent_dir: Path,
    *,
    is_registered: Callable[[str], bool] | None = None,
) -> list[int]:
    """Sessions with recent disk activity but no registered TickEngine."""
    orphaned: list[int] = []
    for num, session_dir in iter_session_dirs(agent_dir):
        agent_id = f"{slug}_{num}"
        if is_registered and is_registered(agent_id):
            continue
        if session_appears_orphaned(session_dir):
            orphaned.append(num)
    return orphaned


def latest_orphaned_session_num(slug: str, agent_dir: Path) -> int | None:
    """Highest session number that appears orphaned on disk."""
    orphaned = find_orphaned_active_sessions(
        slug,
        agent_dir,
        is_registered=lambda _aid: False,
    )
    return orphaned[-1] if orphaned else None
