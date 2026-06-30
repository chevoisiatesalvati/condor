"""Tests for session orphan detection on disk."""

from __future__ import annotations

import time
from pathlib import Path

from condor.trading_agent.session_status import (
    find_orphaned_active_sessions,
    journal_shows_active,
    session_appears_orphaned,
)


def test_stale_running_journal_not_orphaned(tmp_path: Path) -> None:
    agent_dir = tmp_path
    session_dir = agent_dir / "sessions" / "session_1"
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").write_text(
        "## Summary\nStatus: Running | PnL: $0\n",
        encoding="utf-8",
    )
    # Backdate mtime so it is outside the grace window.
    old = time.time() - 86400
    import os

    os.utime(session_dir / "journal.md", (old, old))

    assert journal_shows_active(session_dir)
    assert not session_appears_orphaned(session_dir, now=time.time())
    assert find_orphaned_active_sessions("slug", agent_dir, is_registered=lambda _: False) == []


def test_activity_before_process_start_not_orphaned(tmp_path: Path) -> None:
    agent_dir = tmp_path
    session_dir = agent_dir / "sessions" / "session_4"
    session_dir.mkdir(parents=True)
    (session_dir / "config.yml").write_text("frequency_sec: 60\n", encoding="utf-8")
    (session_dir / "journal.md").write_text(
        "## Summary\nStatus: Running | PnL: $0\n",
        encoding="utf-8",
    )
    started = time.time()
    assert not session_appears_orphaned(
        session_dir,
        process_started_at=started + 1,
    )


def test_recent_running_journal_is_orphaned(tmp_path: Path) -> None:
    agent_dir = tmp_path
    session_dir = agent_dir / "sessions" / "session_2"
    session_dir.mkdir(parents=True)
    (session_dir / "config.yml").write_text("frequency_sec: 60\n", encoding="utf-8")
    (session_dir / "journal.md").write_text(
        "## Summary\nStatus: Running | PnL: $0\n",
        encoding="utf-8",
    )

    assert session_appears_orphaned(session_dir)
    assert find_orphaned_active_sessions("slug", agent_dir, is_registered=lambda _: False) == [2]


def test_registered_session_excluded(tmp_path: Path) -> None:
    agent_dir = tmp_path
    session_dir = agent_dir / "sessions" / "session_3"
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").write_text(
        "## Summary\nStatus: Running | PnL: $0\n",
        encoding="utf-8",
    )

    assert (
        find_orphaned_active_sessions(
            "slug",
            agent_dir,
            is_registered=lambda aid: aid == "slug_3",
        )
        == []
    )
