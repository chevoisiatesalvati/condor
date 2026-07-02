"""Regression tests for journal tick resolution and position_executor notional sizing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_servers.condor.tools.trading_agent import _resolve_journal_tick, journal_write
from mcp_servers.hummingbot_api.tools.executors import (
    _apply_notional_usd_to_amount,
    _apply_position_amount_from_trading_rules,
    _position_executor_positive_amount_issue,
)
from condor.hyperliquid_leverage import apply_hyperliquid_leverage_cap
from condor.open_position_audit import summarize_executor_open_state


class TestJournalTickResolution:
    def test_explicit_tick_param(self):
        assert _resolve_journal_tick(7, "tick=7 entry_class=hold", "agent_1") == 7

    def test_parse_tick_from_text_when_param_zero(self):
        assert _resolve_journal_tick(
            0, "tick=7 entry_class=regime_adaptive_half_size pair=XRP-USD", "agent_1"
        ) == 7

    def test_journal_write_uses_text_tick(self, tmp_path: Path, monkeypatch):
        session_dir = tmp_path / "sessions" / "session_99"
        session_dir.mkdir(parents=True)
        (session_dir / "journal.md").write_text(
            "# Journal - test_agent_99\n\n## Summary\n\n## Decisions\n\n## Ticks\n"
            "- tick#6 | 2026-06-09 09:11 | actions=0 | hold\n\n## Executors\n\n## Snapshots\n"
        )
        agent_dir = tmp_path

        monkeypatch.setattr(
            "condor.trading_agent.journal.resolve_agent_dirs",
            lambda agent_id: (session_dir, agent_dir),
        )
        monkeypatch.setattr(
            "condor.trading_agent.engine.get_engine",
            lambda agent_id: None,
        )

        result = journal_write(
            "test_agent_99",
            "action",
            "tick=7 entry_class=hold pair=none",
            tick=0,
        )
        assert result == {"written": True}
        journal_text = (session_dir / "journal.md").read_text()
        assert "**#7**" in journal_text
        assert "**#0**" not in journal_text


class TestPositionExecutorSizing:
    def test_notional_only_passes_precheck_before_conversion(self):
        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "WLD-USD",
            "notional_usd": 564.38,
        }
        assert _position_executor_positive_amount_issue(config) is None

    def test_notional_only_pipeline_sets_base_amount(self):
        client = MagicMock()
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"HYPE-USD": 63.474}}
        )
        client.connectors.get_trading_rules = AsyncMock(
            return_value={
                "HYPE-USD": {
                    "min_base_amount_increment": 0.01,
                    "min_order_size": 0.01,
                    "min_notional_size": 10,
                }
            }
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "HYPE-USD",
            "notional_usd": 564.38,
        }
        assert _position_executor_positive_amount_issue(config) is None

        err, note = asyncio.run(_apply_notional_usd_to_amount(client, config))
        assert err is None
        assert config["amount"] == pytest.approx(564.38 / 63.474, rel=1e-4)
        assert "notional_usd" not in config

        err, rules_note = asyncio.run(
            _apply_position_amount_from_trading_rules(client, config)
        )
        assert err is None
        assert config["amount"] > 0
        assert _position_executor_positive_amount_issue(config) is None

    def test_notional_usd_converts_with_live_price(self):
        client = MagicMock()
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "notional_usd": 200,
            "amount": 0.1735,
        }
        err, note = asyncio.run(_apply_notional_usd_to_amount(client, config))
        assert err is None
        assert config["amount"] == pytest.approx(100.0)
        assert "notional_usd=200" in note
        assert "notional_usd" not in config

    def test_wrong_manual_amount_rejected(self):
        client = MagicMock()
        client.connectors.get_trading_rules = AsyncMock(
            return_value={
                "XRP-USD": {
                    "min_base_amount_increment": 1,
                    "min_order_size": 1,
                    "min_notional_size": 10,
                }
            }
        )
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "amount": 0.1735,
        }
        err, note = asyncio.run(
            _apply_position_amount_from_trading_rules(client, config)
        )
        assert err is not None
        assert "notional_usd" in err
        assert "0.1735" in err

    def test_correct_amount_passes_rules(self):
        client = MagicMock()
        client.connectors.get_trading_rules = AsyncMock(
            return_value={
                "XRP-USD": {
                    "min_base_amount_increment": 1,
                    "min_order_size": 1,
                    "min_notional_size": 10,
                }
            }
        )
        client.market_data.get_prices = AsyncMock(
            return_value={"prices": {"XRP-USD": 2.0}}
        )

        config = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "XRP-USD",
            "amount": 100,
        }
        err, note = asyncio.run(
            _apply_position_amount_from_trading_rules(client, config)
        )
        assert err is None
        assert config["amount"] == 100


class TestHyperliquidLeverageCap:
    def test_clamps_low_max_leverage_assets(self, monkeypatch):
        monkeypatch.setattr(
            "condor.hyperliquid_leverage.hl_symbol_max_leverage",
            lambda tp: {"MANTA-USD": 3, "DYDX-USD": 5, "ETH-USD": 25}.get(tp),
        )
        for pair, expected in (("MANTA-USD", 3), ("DYDX-USD", 5), ("ETH-USD", 25)):
            cfg = {
                "connector_name": "hyperliquid_perpetual",
                "trading_pair": pair,
                "leverage": 30,
            }
            note = apply_hyperliquid_leverage_cap(cfg)
            assert cfg["leverage"] == expected
            assert "clamped" in note.lower()

    def test_leaves_btc_leverage_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            "condor.hyperliquid_leverage.hl_symbol_max_leverage",
            lambda _tp: 40,
        )
        cfg = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "BTC-USD",
            "leverage": 30,
        }
        note = apply_hyperliquid_leverage_cap(cfg)
        assert cfg["leverage"] == 30
        assert note == ""


class TestOpenPositionAudit:
    def test_summarize_detects_unfilled_running_executor(self):
        detail = {
            "status": "RUNNING",
            "trading_pair": "DYDX-USD",
            "connector_name": "hyperliquid_perpetual",
            "custom_info": {"side": 1},
        }
        snap = summarize_executor_open_state(detail)
        assert snap["status"] == "RUNNING"
        assert snap["has_position"] is False

    def test_summarize_detects_filled_position(self):
        detail = {
            "status": "RUNNING",
            "trading_pair": "DYDX-USD",
            "filled_amount_quote": 250.0,
            "entry_price": 0.19,
        }
        snap = summarize_executor_open_state(detail)
        assert snap["has_position"] is True
