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
    create_mock = AsyncMock(return_value={"id": "ex-1"})
    with patch.object(runner, "_get_client", AsyncMock(return_value=client)):
        with patch(
            "condor.fetchers.executors.create_executor",
            create_mock,
        ):
            with patch(
                "condor.fetchers.executors.stop_executor",
                AsyncMock(return_value={"status": "ok"}),
            ):
                result = await runner._apply_decision(decision)
    assert result.ok is True
    assert result.created_ids == ["ex-1"]
    assert any("OPEN LONG AAVE-USD" in t for t in result.notified_opens)
    create_cfg = create_mock.await_args.args[1]
    assert create_cfg["triple_barrier_config"]["open_order_type"] == 1  # MARKET
    # Default: per-pair Hyperliquid max (mocked via apply_hyperliquid_leverage_cap path).
    assert isinstance(create_cfg.get("leverage"), int)
    assert create_cfg["leverage"] > 0


@pytest.mark.asyncio
async def test_apply_uses_strategy_params_leverage_clamped_to_hl_max(monkeypatch):
    monkeypatch.setattr(
        "condor.hyperliquid_leverage.hl_symbol_max_leverage",
        lambda tp: 40 if "BTC" in tp else 10,
    )
    runner = _runner(strategy_params={"leverage": 25})
    decision = MacdbbDecision(
        hold=False,
        hold_reason="",
        creates=[
            CreateAction(
                pair="BTC-USD",
                side="long",
                entry_class="formal",
                notional_quote=200.0,
                base_amount=0.003,
                sl_pct=2.0,
                tp_pct=6.0,
                volatility_proxy_pct=1.0,
                sizing_multiplier=1.0,
            )
        ],
        state=MacdbbState(),
    )
    create_mock = AsyncMock(return_value={"id": "ex-btc"})
    with patch.object(runner, "_get_client", AsyncMock(return_value=object())):
        with patch("condor.fetchers.executors.create_executor", create_mock):
            await runner._apply_decision(decision)
    assert create_mock.await_args.args[1]["leverage"] == 25


@pytest.mark.asyncio
async def test_apply_defaults_to_pair_max_leverage(monkeypatch):
    monkeypatch.setattr(
        "condor.hyperliquid_leverage.hl_symbol_max_leverage",
        lambda tp: 40 if "BTC" in tp else 5,
    )
    runner = _runner()
    decision = MacdbbDecision(
        hold=False,
        hold_reason="",
        creates=[
            CreateAction(
                pair="BTC-USD",
                side="long",
                entry_class="formal",
                notional_quote=200.0,
                base_amount=0.003,
                sl_pct=2.0,
                tp_pct=6.0,
                volatility_proxy_pct=1.0,
                sizing_multiplier=1.0,
            )
        ],
        state=MacdbbState(),
    )
    create_mock = AsyncMock(return_value={"id": "ex-btc"})
    with patch.object(runner, "_get_client", AsyncMock(return_value=object())):
        with patch("condor.fetchers.executors.create_executor", create_mock):
            await runner._apply_decision(decision)
    assert create_mock.await_args.args[1]["leverage"] == 40


def test_clear_never_filled_pendings_drops_ib_and_sets_cooldown():
    runner = _runner(strategy_params={"sl_cooldown_ticks": 3})
    runner._pending_opens["BTC-USD"] = {
        "executor_id": "ex-ib",
        "tick": 1,
        "side": "long",
        "entry_class": "formal",
    }
    runner._macdbb_state.entry_meta_by_pair["BTC-USD"] = __import__(
        "condor.strategy_runners.macdbb.types", fromlist=["EntryMeta"]
    ).EntryMeta(entry_class="formal", entry_bb_pos_pct=20.0, side="long")
    cleared = runner._clear_never_filled_pendings(
        [
            {
                "id": "ex-ib",
                "status": "terminated",
                "close_type": "insufficient_balance",
                "volume": 0.0,
                "pair": "BTC-USD",
            }
        ],
        tick_num=4,
    )
    assert len(cleared) == 1
    assert cleared[0]["pair"] == "BTC-USD"
    assert "BTC-USD" not in runner._pending_opens
    assert "BTC-USD" not in runner._macdbb_state.entry_meta_by_pair
    assert runner._macdbb_state.sl_cooldown_until_tick["BTC-USD"] == 7


def test_merge_pending_marks_unfilled():
    runner = _runner()
    runner._pending_opens["BTC-USD"] = {
        "executor_id": "ex-pending",
        "tick": 1,
        "side": "long",
        "entry_class": "formal",
    }
    merged = runner._merge_pending_into_positions([], tick_num=1)
    assert len(merged) == 1
    assert merged[0].pair == "BTC-USD"
    assert merged[0].filled is False
