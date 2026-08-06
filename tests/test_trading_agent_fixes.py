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

    def test_fills_max_when_leverage_unset(self, monkeypatch):
        monkeypatch.setattr(
            "condor.hyperliquid_leverage.hl_symbol_max_leverage",
            lambda tp: 40 if "BTC" in tp else 3,
        )
        cfg = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "BTC-USD",
        }
        note = apply_hyperliquid_leverage_cap(cfg)
        assert cfg["leverage"] == 40
        assert "max leverage 40x" in note.lower()

    def test_max_sentinel_uses_pair_max(self, monkeypatch):
        monkeypatch.setattr(
            "condor.hyperliquid_leverage.hl_symbol_max_leverage",
            lambda _tp: 5,
        )
        cfg = {
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "MANTA-USD",
            "leverage": "max",
        }
        note = apply_hyperliquid_leverage_cap(cfg)
        assert cfg["leverage"] == 5
        assert "max leverage" in note.lower()


class TestOpenPositionAudit:
    def test_summarize_detects_unfilled_running_executor(self):
        detail = {
            "status": "RUNNING",
            "trading_pair": "DYDX-USD",
            "connector_name": "hyperliquid_perpetual",
            "entry_price": 0.098085,
            "filled_amount_quote": 0.0,
            "custom_info": {"side": 1, "current_retries": 3, "max_retries": 10},
        }
        snap = summarize_executor_open_state(detail)
        assert snap["status"] == "RUNNING"
        assert snap["is_filled"] is False
        assert snap["has_position"] is False
        assert snap["current_retries"] == 3

    def test_summarize_detects_filled_position(self):
        detail = {
            "status": "RUNNING",
            "trading_pair": "DYDX-USD",
            "filled_amount_quote": 250.0,
            "entry_price": 0.19,
        }
        snap = summarize_executor_open_state(detail)
        assert snap["is_filled"] is True
        assert snap["has_position"] is True


class TestPositionReconcile:
    def test_detects_orphan_from_live_connector(self):
        from condor.position_reconcile import reconcile_executor_positions

        running = [{"pair": "BTC-USD"}, {"pair": "SOL-USD"}]
        connector = [
            {"trading_pair": "BTC-USD", "amount": 0.01, "connector_name": "hyperliquid_perpetual"},
            {"trading_pair": "XPL-USD", "amount": 2501, "connector_name": "hyperliquid_perpetual"},
        ]
        report = reconcile_executor_positions(
            running,
            connector,
            agent_id="macdbb_scanner_aggressive_hl_78",
        )
        assert report["orphan_position_count"] == 1
        assert report["orphan_positions"][0]["trading_pair"] == "XPL-USD"
        assert report["orphan_positions"][0]["source"] == "connector"
        assert report["effective_open_slots"] == 3

    def test_ignores_stale_hb_summary_from_other_controller(self):
        from condor.position_reconcile import reconcile_executor_positions

        running = [{"pair": f"P{i}-USD"} for i in range(9)]
        connector = [
            {"trading_pair": f"P{i}-USD", "amount": 1.0, "connector_name": "hyperliquid_perpetual"}
            for i in range(9)
        ] + [
            {"trading_pair": "XPL-USD", "amount": 2501, "connector_name": "hyperliquid_perpetual"},
        ]
        hb_stale = [
            {
                "trading_pair": "JTO-USD",
                "net_amount_base": 284.0,
                "controller_id": "macdbb_scanner_aggressive_hl_62",
            }
        ]
        report = reconcile_executor_positions(
            running,
            connector,
            agent_id="macdbb_scanner_aggressive_hl_78",
            hb_summary_positions=hb_stale,
        )
        assert report["orphan_position_count"] == 1
        assert report["orphan_positions"][0]["trading_pair"] == "XPL-USD"
        assert len(report["hb_stale_other_controller"]) == 1
        assert report["hb_stale_other_controller"][0]["trading_pair"] == "JTO-USD"

    def test_ghost_history_does_not_inflate_effective_slots(self):
        from condor.position_reconcile import reconcile_executor_positions

        running = [{"pair": f"P{i}-USD"} for i in range(9)]
        connector = [
            {"trading_pair": f"P{i}-USD", "amount": 1.0, "connector_name": "hyperliquid_perpetual"}
            for i in range(9)
        ] + [
            {"trading_pair": "XPL-USD", "amount": 2501, "connector_name": "hyperliquid_perpetual"},
        ]
        all_executors = [
            {
                "pair": "PUMP-USD",
                "controller_id": "macdbb_scanner_aggressive_hl_78",
                "status": "terminated",
            },
            {
                "pair": "ADA-USD",
                "controller_id": "macdbb_scanner_aggressive_hl_78",
                "status": "terminated",
            },
        ]
        report = reconcile_executor_positions(
            running,
            connector,
            agent_id="macdbb_scanner_aggressive_hl_78",
            all_executors=all_executors,
        )
        assert report["orphan_position_count"] == 1
        assert report["orphan_positions"][0]["trading_pair"] == "XPL-USD"
        assert report["effective_open_slots"] == 10
        assert "PUMP-USD" in report["ghost_without_position"]
        assert "ADA-USD" in report["ghost_without_position"]

    def test_format_summary_warns_against_market_create(self):
        from condor.position_reconcile import format_reconcile_summary

        report = {
            "running_executor_count": 9,
            "connector_open_position_count": 10,
            "effective_open_slots": 10,
            "orphan_positions": [
                {
                    "trading_pair": "XPL-USD",
                    "connector_name": "hyperliquid_perpetual",
                    "position_side": "LONG",
                    "amount": 2501,
                    "source": "connector",
                }
            ],
        }
        text = format_reconcile_summary(report, max_open=10)
        assert "Do NOT market-create" in text
        assert "doubles" in text.lower()
