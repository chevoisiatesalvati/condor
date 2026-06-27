"""Tests for agent_key / trading_context single-source defaults."""

from __future__ import annotations

from condor.trading_agent.config import (
    load_full_config,
    resolve_session_overrides,
    strip_session_overrides,
)


def test_strip_session_overrides_removes_redundant_keys():
    config = {
        "server_name": "local",
        "agent_key": "",
        "trading_context": "",
        "strategy_params": {"sl_pct": 3.8},
    }
    stripped = strip_session_overrides(config)
    assert "agent_key" not in stripped
    assert "trading_context" not in stripped
    assert stripped["server_name"] == "local"
    assert stripped["strategy_params"]["sl_pct"] == 3.8


def test_load_full_config_excludes_session_overrides(tmp_path):
    defaults = {
        "server_name": "local",
        "agent_key": "cursor:default",
        "trading_context": "legacy nested context",
        "strategy_params": {"tp_pct": 5.5},
    }
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "config.yml").write_text("agent_key: ollama:llama3.1\ntrading_context: saved\n")

    loaded = load_full_config(agent_dir, defaults)
    assert "agent_key" not in loaded
    assert "trading_context" not in loaded
    assert loaded["server_name"] == "local"


def test_resolve_session_overrides_uses_strategy_defaults():
    resolved = resolve_session_overrides(
        {"server_name": "local"},
        strategy_agent_key="cursor:default",
        strategy_trading_context="Trade majors only",
    )
    assert resolved["agent_key"] == "cursor:default"
    assert resolved["trading_context"] == "Trade majors only"


def test_resolve_session_overrides_prefers_explicit_overrides():
    resolved = resolve_session_overrides(
        {"agent_key": "ollama:llama3.1", "trading_context": "old"},
        strategy_agent_key="cursor:default",
        strategy_trading_context="default context",
        trading_context_override="session override",
        agent_key_override="gemini",
    )
    assert resolved["agent_key"] == "gemini"
    assert resolved["trading_context"] == "session override"
