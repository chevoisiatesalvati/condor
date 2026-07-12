"""Tests for canonical agents strategy data paths."""

from pathlib import Path

from condor.agents.strategy_paths import ensure_strategy_data_dir, resolve_strategy_data_dir


def test_resolve_strategy_data_dir_uses_agents_layout(tmp_path, monkeypatch):
    import condor.agents.strategy_paths as paths_module

    monkeypatch.setattr(paths_module, "REPO_ROOT", tmp_path)

    resolved = resolve_strategy_data_dir("macdbb", "macdbb")
    assert resolved == tmp_path / "agents" / "macdbb" / "strategies" / "macdbb"


def test_ensure_strategy_data_dir_replaces_broken_symlinks(tmp_path, monkeypatch):
    import condor.agents.strategy_paths as paths_module

    monkeypatch.setattr(paths_module, "REPO_ROOT", tmp_path)
    strategy_dir = tmp_path / "agents" / "demo" / "strategies" / "demo"
    strategy_dir.mkdir(parents=True)
    broken_sessions = strategy_dir / "sessions"
    broken_sessions.symlink_to(tmp_path / "missing_trading_agents" / "sessions")
    broken_learnings = strategy_dir / "learnings.md"
    broken_learnings.symlink_to(tmp_path / "missing_trading_agents" / "learnings.md")

    resolved = ensure_strategy_data_dir("demo", "demo")

    assert resolved == strategy_dir
    assert broken_sessions.is_dir() and not broken_sessions.is_symlink()
    assert broken_learnings.is_file() and not broken_learnings.is_symlink()
