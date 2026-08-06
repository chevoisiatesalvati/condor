"""Sessions + defaults under data/strategy_runs / strategy.yaml."""

from __future__ import annotations

from condor.strategy_runners.macdbb import paths, sessions


def test_save_load_default_config(tmp_path, monkeypatch):
    private = tmp_path / "strategies" / "macdbb_scanner_aggressive_hl"
    private.mkdir(parents=True)
    monkeypatch.setattr(paths, "STRATEGIES_SUBMODULE", tmp_path / "strategies")
    monkeypatch.setattr(paths, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(sessions, "REPO_ROOT", tmp_path)

    written = sessions.save_default_config(
        "macdbb_scanner_aggressive_hl",
        {"frequency_sec": 90, "strategy_preset": "custom"},
    )
    assert written.name == "strategy.yaml"
    loaded = sessions.load_default_config("macdbb_scanner_aggressive_hl")
    assert loaded["frequency_sec"] == 90
    assert loaded["strategy_preset"] == "custom"


def test_create_session_under_runs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(paths, "STRATEGIES_SUBMODULE", tmp_path / "strategies")
    monkeypatch.setattr(sessions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sessions, "_LEGACY_AGENTS_ROOT", tmp_path / "agents")

    num, session_dir, journal = sessions.create_session(
        slug="macdbb_scanner_aggressive_hl",
        strategy_name="MACDBB",
        strategy_description="test",
        config={"frequency_sec": 60},
        run_key="macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl",
    )
    assert num == 1
    assert session_dir.is_dir()
    assert "sessions" in session_dir.parts
    assert str(tmp_path / "runs") in str(session_dir)
    journal.close()
