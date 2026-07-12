"""Tests for session config loader."""

from __future__ import annotations

from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.session_config import replay_config_from_session

from routines.macdbb_scanner_aggressive_hl_replay.paths import strategy_data_dir

STRATEGY_DIR = strategy_data_dir("macdbb_scanner_aggressive_hl") / "sessions"


def test_session_60_config_loader():
    session_dir = STRATEGY_DIR / "session_60"
    if not session_dir.is_dir():
        return
    config, params = replay_config_from_session(
        session_dir,
        "macdbb_scanner_aggressive_hl",
    )
    assert config.max_open_executors == 3
    assert config.sl_pct == 3.2
    assert config.formal_notional_quote == 500.0
    assert config.activation_ticks == 0
    assert params.get("adaptive_activation_ticks") == 0


def test_session_config_preserves_reports_only_from_base():
    session_dir = STRATEGY_DIR / "session_60"
    if not session_dir.is_dir():
        return
    base = DynamicStrategyReplayConfig(
        data_source="reports_only",
        replay_mode="session_parity",
        time_window_min=5,
    )
    config, _ = replay_config_from_session(
        session_dir,
        "macdbb_scanner_aggressive_hl",
        base=base,
    )
    assert config.data_source == "reports_only"
    assert config.replay_mode == "session_parity"
    assert config.time_window_min == 5


def test_session_58_uses_fixed_policy_when_dynamic_flags_absent():
    session_dir = STRATEGY_DIR / "session_58"
    if not session_dir.is_dir():
        return
    config, params = replay_config_from_session(
        session_dir,
        "macdbb_scanner_aggressive_hl",
    )
    assert params.get("enable_dynamic_sizing") is None
    assert config.enable_dynamic_sizing is False
    assert config.enable_dynamic_barriers is False
    assert config.sl_pct == 1.8
    assert config.tp_pct == 10.0


def test_session_60_uses_dynamic_policy_from_snapshot():
    session_dir = STRATEGY_DIR / "session_60"
    if not session_dir.is_dir():
        return
    config, params = replay_config_from_session(
        session_dir,
        "macdbb_scanner_aggressive_hl",
    )
    assert params.get("enable_dynamic_sizing") is True
    assert config.enable_dynamic_sizing is True
    assert config.enable_dynamic_barriers is True
