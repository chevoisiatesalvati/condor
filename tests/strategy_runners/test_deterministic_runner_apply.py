"""Tests for DeterministicRunner client resolution and apply/notify behavior."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from condor.agents.journal import JournalManager
from condor.strategy_runners.catalog import DeterministicStrategy
from condor.strategy_runners.macdbb.types import (
    CreateAction,
    MacdbbDecision,
    MacdbbState,
    NotifyAction,
)
from condor.strategy_runners.runner import ApplyResult, DeterministicRunner


def _runner(**config) -> DeterministicRunner:
    strategy = DeterministicStrategy(
        slug="macdbb_scanner_aggressive_hl",
        name="MACDBB",
        description="test",
        data_slug="macdbb_scanner_aggressive_hl",
        strategy_slug="macdbb_scanner_aggressive_hl",
        require_promoted=False,
        default_config={"server_name": "local", "frequency_sec": 1800},
    )
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    session_dir = root / "sessions" / "session_1"
    session_dir.mkdir(parents=True)
    journal = JournalManager(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_1",
        strategy_name="MACDBB",
        strategy_description="test",
        session_dir=session_dir,
        agent_dir=root,
    )

    def _create_session(**_kwargs):
        return 1, session_dir, journal

    with patch(
        "condor.strategy_runners.runner.create_session", side_effect=_create_session
    ):
        with patch("condor.strategy_runners.runner.maybe_cleanup"):
            runner = DeterministicRunner(
                strategy=strategy,
                config={"server_name": "local", "total_amount_quote": 100, **config},
                chat_id=1,
                user_id=1,
            )
    # Keep temp dir alive for the runner lifetime.
    runner._tmp = tmp  # type: ignore[attr-defined]
    return runner


@pytest.mark.asyncio
async def test_get_client_uses_cm_get_client_not_for_server():
    runner = _runner()
    cm = MagicMock()
    cm.get_default_server.return_value = "local"
    client = object()
    cm.get_client = AsyncMock(return_value=client)
    with patch("config_manager.get_config_manager", return_value=cm):
        got = await runner._get_client()
    assert got is client
    cm.get_client.assert_awaited_once_with("local")
    assert not hasattr(cm, "get_client_for_server") or not isinstance(
        getattr(cm, "get_client_for_server", None), AsyncMock
    )


@pytest.mark.asyncio
async def test_apply_failure_does_not_queue_open_notifications():
    runner = _runner()
    decision = MacdbbDecision(
        hold=False,
        hold_reason="",
        creates=[
            CreateAction(
                pair="AAVE-USD",
                side="long",
                entry_class="formal",
                notional_quote=112.0,
                base_amount=1.0,
                sl_pct=2.8,
                tp_pct=6.8,
                volatility_proxy_pct=1.0,
                sizing_multiplier=1.0,
            )
        ],
        notifications=[
            NotifyAction(text="⚡ OPEN LONG AAVE-USD | formal | notional $112.00")
        ],
        state=MacdbbState(),
    )
    with patch.object(runner, "_get_client", AsyncMock(return_value=None)):
        result = await runner._apply_decision(decision)
    assert isinstance(result, ApplyResult)
    assert result.ok is False
    assert result.notified_opens == []
    assert "No API client" in result.error or "get_client" in result.error or result.error


@pytest.mark.asyncio
async def test_apply_success_queues_open_notification():
    runner = _runner()
    decision = MacdbbDecision(
        hold=False,
        hold_reason="",
        creates=[
            CreateAction(
                pair="AAVE-USD",
                side="long",
                entry_class="formal",
                notional_quote=100.0,
                base_amount=1.0,
                sl_pct=2.0,
                tp_pct=6.0,
                volatility_proxy_pct=1.0,
                sizing_multiplier=1.0,
            )
        ],
        notifications=[
            NotifyAction(text="⚡ OPEN LONG AAVE-USD | formal | notional $100.00")
        ],
        state=MacdbbState(),
    )
    client = object()
    with patch.object(runner, "_get_client", AsyncMock(return_value=client)):
        with patch(
            "condor.fetchers.executors.create_executor",
            AsyncMock(return_value={"id": "ex-1"}),
        ):
            with patch(
                "condor.fetchers.executors.stop_executor",
                AsyncMock(return_value={"status": "ok"}),
            ):
                result = await runner._apply_decision(decision)
    assert result.ok is True
    assert result.created_ids == ["ex-1"]
    assert any("OPEN LONG AAVE-USD" in t for t in result.notified_opens)
