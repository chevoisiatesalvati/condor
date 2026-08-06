"""Strategies session lifecycle: duck-typed lookup, orphan guard, resume, notifs."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from condor.runtime.loops import LoopSupervisor
from condor.strategy_runners.catalog import DeterministicStrategy, get_strategy
from condor.strategy_runners.macdbb import paths, sessions
from condor.strategy_runners.macdbb.notifications import (
    format_close_notification,
    format_open_notification,
)
from condor.strategy_runners.macdbb.types import (
    MacdbbDecision,
    MacdbbState,
    NotifyAction,
    OpenPosition,
    StopAction,
)


def test_for_deterministic_slug_finds_non_isinstance_engine():
    """Hot-reload mints a new class; duck-typing must still see the live runner."""
    supervisor = LoopSupervisor()

    class ForeignRunner:
        runner_kind = "deterministic"

        def __init__(self):
            self.agent_id = "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_9"
            self.strategy = SimpleNamespace(slug="macdbb_scanner_aggressive_hl")
            self.is_running = True
            self.session_num = 9
            self.session_dir = None
            self.config = {}

        @property
        def agent(self):
            return SimpleNamespace(slug="macdbb_scanner_aggressive_hl")

    foreign = ForeignRunner()
    supervisor._engines[foreign.agent_id] = foreign
    found = supervisor.for_deterministic_slug("macdbb_scanner_aggressive_hl")
    assert found == [foreign]

    from condor.web.routes import strategies as strategies_routes

    with patch(
        "condor.runtime.loops.get_supervisor", return_value=supervisor
    ):
        running = strategies_routes._running_for("macdbb_scanner_aggressive_hl")
    assert running == [foreign]


def test_format_close_notification_includes_pnl():
    text = format_close_notification(
        pair="ETH-USD",
        reason="thesis_decay",
        close_type="EARLY_STOP",
        side="long",
        pnl=-12.5,
        net_pnl_pct=-1.25,
        executor_id="abc",
        session_num=3,
    )
    assert "CLOSED LONG ETH-USD" in text
    assert "PnL $-12.50" in text
    assert "-1.25%" in text
    assert "session_3" in text


def test_format_open_notification_includes_context():
    text = format_open_notification(
        side="short",
        pair="BTC-USD",
        entry_class="formal",
        notional_quote=250,
        sl_pct=1.5,
        tp_pct=3.0,
        leverage=5,
        price=100.0,
        bb_pos_pct=12.5,
        score=0.42,
        session_num=2,
    )
    assert "OPEN SHORT BTC-USD" in text
    assert "5x" in text
    assert "px 100" in text
    assert "score 0.42" in text
    assert "session_2" in text


@pytest.mark.asyncio
async def test_apply_emits_barrier_closes_alongside_stops():
    from tests.strategy_runners.test_deterministic_runner_apply import _runner

    runner = _runner()
    decision = MacdbbDecision(
        creates=[],
        stops=[
            StopAction(
                executor_id="exec-1",
                pair="ETH-USD",
                reason="thesis_decay",
                close_type="EARLY_STOP",
            )
        ],
        hold=False,
        hold_reason="",
        notifications=[
            NotifyAction(
                text="⚡ CLOSED LONG SOL-USD | STOP_LOSS | PnL $-3.00 | id: barrier-1"
            )
        ],
        state=MacdbbState(),
        journal_fields={},
    )

    client = MagicMock()
    stop_executor = AsyncMock(return_value={"status": "ok"})
    with patch.object(runner, "_get_client", AsyncMock(return_value=client)):
        with patch(
            "condor.agents.performance.fetch_agent_performance",
            AsyncMock(
                return_value=SimpleNamespace(
                    executors=[
                        {
                            "id": "exec-1",
                            "pair": "ETH-USD",
                            "side": "long",
                            "pnl": -4.5,
                            "net_pnl_pct": -0.9,
                            "volume": 100,
                        }
                    ]
                )
            ),
        ):
            with patch(
                "condor.fetchers.executors.stop_executor", stop_executor
            ):
                with patch(
                    "condor.fetchers.executors.create_executor", AsyncMock()
                ):
                    result = await runner._apply_decision(
                        decision,
                        open_positions=[
                            OpenPosition(
                                executor_id="exec-1",
                                pair="ETH-USD",
                                side="long",
                                entry_class="formal",
                                pnl=-4.5,
                            )
                        ],
                    )

    assert result.ok
    assert any("STOP_LOSS" in t and "SOL-USD" in t for t in result.notified_closes)
    assert any("ETH-USD" in t and "PnL" in t for t in result.notified_closes)


def test_open_existing_session_and_orphan_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(paths, "STRATEGIES_SUBMODULE", tmp_path / "strategies")
    monkeypatch.setattr(sessions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sessions, "_LEGACY_AGENTS_ROOT", tmp_path / "agents")

    run_key = "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl"
    num, session_dir, journal = sessions.create_session(
        slug="macdbb_scanner_aggressive_hl",
        strategy_name="MACDBB",
        strategy_description="test",
        config={"frequency_sec": 60},
        run_key=run_key,
    )
    journal.record_tick("running")
    journal_path = session_dir / "journal.md"
    assert journal_path.is_file()
    journal_path.write_text(
        "## Summary\nStatus: Running | PnL: $0\n\n## Log\n", encoding="utf-8"
    )

    reopened_num, reopened_dir, reopened_journal = sessions.open_existing_session(
        slug="macdbb_scanner_aggressive_hl",
        session_num=num,
        strategy_name="MACDBB",
        strategy_description="test",
        run_key=run_key,
    )
    assert reopened_num == num
    assert reopened_dir == session_dir
    reopened_journal.close()
    journal.close()

    from condor.agents.session_status import session_appears_orphaned

    assert session_appears_orphaned(
        session_dir, process_started_at=time.time() - 10
    )
    with patch(
        "condor.agents.session_status.session_appears_orphaned",
        side_effect=lambda d, **_kw: d == session_dir,
    ):
        orphaned = sessions.find_orphaned_strategy_sessions(
            data_slug="macdbb_scanner_aggressive_hl",
            run_key=run_key,
            is_registered=lambda _aid: False,
        )
    assert num in orphaned


@pytest.mark.asyncio
async def test_start_blocked_when_orphaned(tmp_path, monkeypatch):
    from condor.web.routes import strategies as strategies_routes
    from condor.web.models import WebUser

    strat = get_strategy("macdbb_scanner_aggressive_hl")
    assert strat is not None

    with patch.object(strategies_routes, "_running_for", return_value=[]):
        with patch.object(
            strategies_routes, "_orphaned_sessions", return_value=[7]
        ):
            with pytest.raises(Exception) as excinfo:
                await strategies_routes.start_strategy(
                    "macdbb_scanner_aggressive_hl",
                    strategies_routes.StartStrategyBody(config={}),
                    user=WebUser(id=1, username="t", role="admin"),
                )
    # FastAPI HTTPException
    assert getattr(excinfo.value, "status_code", None) == 409
    assert "Resume session 7" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_resume_session_passes_session_num(tmp_path, monkeypatch):
    from condor.web.routes import strategies as strategies_routes
    from condor.web.models import WebUser

    fake_runner = SimpleNamespace(
        agent_id="macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_3",
        session_num=3,
    )
    start_fn = AsyncMock(return_value=fake_runner)

    monkeypatch.setattr(paths, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(sessions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sessions, "_LEGACY_AGENTS_ROOT", tmp_path / "agents")
    session_dir = (
        tmp_path
        / "runs"
        / "macdbb_scanner_aggressive_hl"
        / "sessions"
        / "session_3"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "config.yml").write_text("frequency_sec: 60\n")

    with patch.object(strategies_routes, "_running_for", return_value=[]):
        with patch.object(strategies_routes, "_orphaned_sessions", return_value=[3]):
            with patch(
                "condor.web.routes.strategies.start_deterministic_strategy",
                start_fn,
            ):
                with patch.object(
                    strategies_routes,
                    "_expand_preset_params",
                    return_value=({}, {}),
                ):
                    result = await strategies_routes.start_strategy(
                        "macdbb_scanner_aggressive_hl",
                        strategies_routes.StartStrategyBody(
                            config={"strategy_preset": "custom"},
                            strategy_preset="custom",
                            session_num=3,
                        ),
                        user=WebUser(id=1, username="t", role="admin"),
                    )

    assert result["resumed"] is True
    assert result["session_num"] == 3
    assert start_fn.await_args.kwargs["resume_session_num"] == 3


def test_summary_reports_orphaned_status():
    from condor.web.routes import strategies as strategies_routes

    strat = DeterministicStrategy(
        slug="macdbb_scanner_aggressive_hl",
        name="MACDBB",
        description="t",
        data_slug="macdbb_scanner_aggressive_hl",
        strategy_slug="macdbb_scanner_aggressive_hl",
        require_promoted=False,
    )
    with patch.object(strategies_routes, "_running_for", return_value=[]):
        with patch.object(
            strategies_routes, "_orphaned_sessions", return_value=[4]
        ):
            with patch(
                "condor.strategy_runners.promote.load_manifest", return_value=None
            ):
                with patch.object(
                    strategies_routes,
                    "_merged_default_config",
                    return_value={},
                ):
                    with patch.object(
                        strategies_routes, "_preset_catalog", return_value=[]
                    ):
                        summary = strategies_routes._summary_for(strat)
    assert summary.status == "orphaned"
    assert summary.session_num == 4
