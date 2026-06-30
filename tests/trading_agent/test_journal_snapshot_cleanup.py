"""Regression tests for snapshot retention cleanup."""

from __future__ import annotations

import re
from pathlib import Path

from condor.trading_agent.journal import MAX_SNAPSHOTS, JournalManager, prune_old_snapshots


def _remaining_ticks(snap_dir: Path) -> list[int]:
    ticks = []
    for path in snap_dir.glob("snapshot_*.md"):
        m = re.match(r"snapshot_(\d+)\.md", path.name)
        if m:
            ticks.append(int(m.group(1)))
    return sorted(ticks)


def test_cleanup_deletes_lowest_tick_numbers_first(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions" / "session_1"
    snap_dir = session_dir / "snapshots"
    snap_dir.mkdir(parents=True)

    for tick in range(1, 109):
        (snap_dir / f"snapshot_{tick}.md").write_text(f"tick {tick}")

    jm = JournalManager("test_agent_1", session_dir=session_dir)
    prune_old_snapshots(snap_dir)

    remaining = _remaining_ticks(snap_dir)
    assert len(remaining) == MAX_SNAPSHOTS
    assert remaining == list(range(9, 109))
    assert 100 in remaining
    assert 1 not in remaining
    assert 8 not in remaining


def test_cleanup_noop_at_limit(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions" / "session_1"
    snap_dir = session_dir / "snapshots"
    snap_dir.mkdir(parents=True)

    for tick in range(1, MAX_SNAPSHOTS + 1):
        (snap_dir / f"snapshot_{tick}.md").write_text(f"tick {tick}")

    jm = JournalManager("test_agent_1", session_dir=session_dir)
    prune_old_snapshots(snap_dir)

    assert _remaining_ticks(snap_dir) == list(range(1, MAX_SNAPSHOTS + 1))
