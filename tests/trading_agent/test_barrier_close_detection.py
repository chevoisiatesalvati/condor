"""Barrier-close detection and post-tick running-executor tracking."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from condor.trading_agent.engine import (
    _detect_barrier_closes,
    _extract_agent_created_executor_ids,
    _fetch_running_executor_ids,
    _format_barrier_closes_section,
    _running_executor_ids,
)
from condor.trading_agent.performance import AgentPerformance


def _executor_row(
    *,
    eid: str,
    status: str,
    close_type: str = "",
    pair: str = "XPL-USD",
    side: str = "long",
    pnl: float = 0.0,
) -> dict:
    return {
        "id": eid,
        "status": status,
        "close_type": close_type,
        "pair": pair,
        "side": side,
        "pnl": pnl,
    }


def test_detect_barrier_closes_finds_stop_loss_after_running_snapshot():
    eid = "7dATuJCW7kLcDdGeJ62j9iw9LFRiLCGN6y4xk5VuECuy"
    all_executors = [
        _executor_row(
            eid=eid,
            status="CLOSED",
            close_type="STOP_LOSS",
            pnl=-8.12,
        )
    ]
    closes = _detect_barrier_closes(
        all_executors,
        last_running_ids={eid},
        already_notified=set(),
        agent_closed_ids=set(),
    )
    assert len(closes) == 1
    assert closes[0]["id"] == eid
    section = _format_barrier_closes_section(closes)
    assert "[BARRIER CLOSES SINCE LAST TICK]" in section
    assert "XPL-USD" in section
    assert "STOP_LOSS" in section


def test_detect_barrier_closes_empty_when_last_running_not_tracked():
    """Session 74 bug: create during tick 1 never entered last_running snapshot."""
    eid = "7dATuJCW7kLcDdGeJ62j9iw9LFRiLCGN6y4xk5VuECuy"
    all_executors = [
        _executor_row(
            eid=eid,
            status="CLOSED",
            close_type="STOP_LOSS",
            pnl=-8.12,
        )
    ]
    closes = _detect_barrier_closes(
        all_executors,
        last_running_ids=set(),
        already_notified=set(),
        agent_closed_ids=set(),
    )
    assert closes == []


def test_detect_barrier_closes_ignores_agent_stops():
    eid = "abc123"
    all_executors = [
        _executor_row(
            eid=eid,
            status="CLOSED",
            close_type="EARLY_STOP",
            pnl=-3.0,
        )
    ]
    closes = _detect_barrier_closes(
        all_executors,
        last_running_ids={eid},
        already_notified=set(),
        agent_closed_ids=set(),
    )
    assert closes == []


def test_running_executor_ids_filters_running_only():
    rows = [
        _executor_row(eid="run1", status="RUNNING"),
        _executor_row(eid="done1", status="CLOSED", close_type="STOP_LOSS"),
    ]
    assert _running_executor_ids(rows) == {"run1"}


def test_extract_agent_created_executor_ids_from_create_output():
    tool_calls = [
        {
            "name": "manage_executors",
            "input": {"action": "create", "trading_pair": "XPL-USD"},
            "output": {"executor_id": "newExec123", "status": "ok"},
        }
    ]
    assert _extract_agent_created_executor_ids(tool_calls) == {"newExec123"}


@pytest.mark.asyncio
async def test_fetch_running_executor_ids(monkeypatch):
    perf = AgentPerformance(
        agent_id="macdbb_scanner_aggressive_hl_74",
        executors=[
            _executor_row(eid="run1", status="RUNNING"),
            _executor_row(eid="done1", status="CLOSED", close_type="STOP_LOSS"),
        ],
    )
    mock_fetch = AsyncMock(return_value=perf)
    monkeypatch.setattr(
        "condor.trading_agent.performance.fetch_agent_performance",
        mock_fetch,
    )
    client = object()
    ids = await _fetch_running_executor_ids(client, "macdbb_scanner_aggressive_hl_74")
    assert ids == {"run1"}
    mock_fetch.assert_awaited_once()
    assert mock_fetch.await_args.args[1] == "macdbb_scanner_aggressive_hl_74"
