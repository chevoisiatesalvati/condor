"""Tests for canonical agents strategy data paths."""

from pathlib import Path

from condor.agents.strategy_paths import resolve_strategy_data_dir


def test_resolve_strategy_data_dir_uses_agents_layout(tmp_path, monkeypatch):
    import condor.agents.strategy_paths as paths_module

    monkeypatch.setattr(paths_module, "REPO_ROOT", tmp_path)

    resolved = resolve_strategy_data_dir("macdbb", "macdbb")
    assert resolved == tmp_path / "agents" / "macdbb" / "strategies" / "macdbb"
