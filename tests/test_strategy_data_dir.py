"""Tests for legacy trading_agents session path fallback."""

from pathlib import Path

from condor.agents.strategy_paths import resolve_strategy_data_dir


def test_resolve_strategy_data_dir_prefers_upstream_sessions(tmp_path, monkeypatch):
    import condor.agents.strategy as strategy_module
    import condor.agents.strategy_paths as paths_module

    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path / "agents")
    monkeypatch.setattr(paths_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths_module, "TRADING_AGENTS_DIR", tmp_path / "trading_agents")

    upstream = (
        tmp_path
        / "agents"
        / "macdbb"
        / "strategies"
        / "macdbb"
        / "sessions"
        / "session_1"
    )
    upstream.mkdir(parents=True)
    (upstream / "journal.md").write_text("# journal\n")

    legacy = tmp_path / "trading_agents" / "macdbb" / "sessions" / "session_99"
    legacy.mkdir(parents=True)

    resolved = resolve_strategy_data_dir("macdbb", "macdbb")
    assert resolved == tmp_path / "agents" / "macdbb" / "strategies" / "macdbb"


def test_resolve_strategy_data_dir_falls_back_to_trading_agents(tmp_path, monkeypatch):
    import condor.agents.strategy as strategy_module
    import condor.agents.strategy_paths as paths_module

    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path / "agents")
    monkeypatch.setattr(paths_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths_module, "TRADING_AGENTS_DIR", tmp_path / "trading_agents")

    legacy = tmp_path / "trading_agents" / "macdbb" / "sessions" / "session_78"
    legacy.mkdir(parents=True)
    (legacy / "journal.md").write_text("# journal\n")

    resolved = resolve_strategy_data_dir("macdbb", "macdbb")
    assert resolved == tmp_path / "trading_agents" / "macdbb"
