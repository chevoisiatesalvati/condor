"""Tests for execution-only learnings and human archive migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from condor.trading_agent.journal import (
    LEARNINGS_ARCHIVE_FILENAME,
    LEARNINGS_TEMPLATE,
    JournalManager,
    migrate_legacy_market_learnings,
)
from mcp_servers.condor.tools import trading_agent as ta_tools


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    agent = tmp_path / "test_agent"
    agent.mkdir()
    session = agent / "sessions" / "session_1"
    session.mkdir(parents=True)
    return agent


def _legacy_learnings_md() -> str:
    return """\
# Learnings

## Market Observations
- [2026-06-23 21:07] ZEC-USD formal LONG blocked by 4h bearish trend.
- [2026-06-24 00:37] WLD-USD formal_long after formal_short entry.

## Execution Notes
- [2026-06-24 00:05] position_executor create rejected notional_usd-only; retry with base amount.

## Retired Insights
"""


def test_read_learnings_execution_only(agent_dir: Path):
    (agent_dir / "learnings.md").write_text(_legacy_learnings_md())
    jm = JournalManager("test_agent_1", session_dir=agent_dir / "sessions" / "session_1", agent_dir=agent_dir)

    learnings = jm.read_learnings()

    assert "Execution Notes" in learnings
    assert "notional_usd-only" in learnings
    assert "ZEC-USD" not in learnings
    assert "Market Observations" not in learnings


def test_append_learning_rejects_market(agent_dir: Path):
    (agent_dir / "learnings.md").write_text(LEARNINGS_TEMPLATE)
    jm = JournalManager("test_agent_1", session_dir=agent_dir / "sessions" / "session_1", agent_dir=agent_dir)

    written = jm.append_learning("Some market tick log", category="market")

    assert written is False
    assert (agent_dir / "learnings.md").read_text() == LEARNINGS_TEMPLATE


def test_append_learning_execution_dedup(agent_dir: Path):
    (agent_dir / "learnings.md").write_text(LEARNINGS_TEMPLATE)
    jm = JournalManager("test_agent_1", session_dir=agent_dir / "sessions" / "session_1", agent_dir=agent_dir)

    first = jm.append_learning("Create requires base amount not notional_usd-only", category="execution")
    second = jm.append_learning("Create requires base amount not notional_usd-only", category="execution")

    assert first is True
    assert second is False
    text = (agent_dir / "learnings.md").read_text()
    assert text.count("notional_usd-only") == 1


def test_migrate_legacy_market_learnings(agent_dir: Path):
    (agent_dir / "learnings.md").write_text(_legacy_learnings_md())

    migrated = migrate_legacy_market_learnings(agent_dir)

    assert migrated is True
    learnings_text = (agent_dir / "learnings.md").read_text()
    archive_text = (agent_dir / LEARNINGS_ARCHIVE_FILENAME).read_text()

    assert "## Market Observations" not in learnings_text
    assert "ZEC-USD" in archive_text
    assert "WLD-USD" in archive_text
    assert "notional_usd-only" in learnings_text
    assert "Retired Insights" not in learnings_text

    # Idempotent on second run
    assert migrate_legacy_market_learnings(agent_dir) is False


def test_journal_manager_runs_migration_on_init(agent_dir: Path):
    (agent_dir / "learnings.md").write_text(_legacy_learnings_md())

    JournalManager("test_agent_1", session_dir=agent_dir / "sessions" / "session_1", agent_dir=agent_dir)

    assert (agent_dir / LEARNINGS_ARCHIVE_FILENAME).exists()
    assert "ZEC-USD" in (agent_dir / LEARNINGS_ARCHIVE_FILENAME).read_text()
    assert "## Market Observations" not in (agent_dir / "learnings.md").read_text()


def test_read_learnings_archive(agent_dir: Path):
    archive_path = agent_dir / LEARNINGS_ARCHIVE_FILENAME
    archive_path.write_text("# Learnings Archive\n\n## Market Observations\n- archived line\n")
    jm = JournalManager("test_agent_1", session_dir=agent_dir / "sessions" / "session_1", agent_dir=agent_dir)

    assert "archived line" in jm.read_learnings_archive()


def test_mcp_journal_write_rejects_market_category(agent_dir: Path, monkeypatch: pytest.MonkeyPatch):
    session_dir = agent_dir / "sessions" / "session_1"
    (agent_dir / "learnings.md").write_text(LEARNINGS_TEMPLATE)

    monkeypatch.setattr(
        "condor.trading_agent.journal.resolve_agent_dirs",
        lambda agent_id: (session_dir, agent_dir),
    )
    monkeypatch.setattr("condor.trading_agent.engine.get_engine", lambda agent_id: None)

    result = ta_tools.journal_write(
        "test_agent_1",
        entry_type="learning",
        text="BB position decay on VVV",
        category="market",
    )

    assert result["written"] is False
    assert "market learnings retired" in result["reason"]
