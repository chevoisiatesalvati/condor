"""Load tick schedules and configs for report-driven replay."""

from __future__ import annotations

from typing import Any

from routines.macdbb_scanner_aggressive_hl_replay.journal import parse_journal_ticks
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig, parse_session_selector
from routines.macdbb_scanner_aggressive_hl_replay.paths import strategy_sessions_dir
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import is_report_driven_data_source
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import (
    build_session_parity_ticks,
    hydrate_timeline_ticks,
)
from routines.macdbb_scanner_aggressive_hl_replay.session_config import replay_config_from_session


def load_replay_sessions(
    config: DynamicStrategyReplayConfig,
) -> tuple[dict[int, dict[int, Any]], dict[int, DynamicStrategyReplayConfig], list[int]]:
    """Return tick maps, per-session configs, and session numbers to simulate."""
    if config.replay_mode == "timeline_backtest":
        tick_map = hydrate_timeline_ticks(config)
        if not tick_map:
            return {}, {}, []
        return {0: tick_map}, {0: config}, [0]

    sessions_dir = strategy_sessions_dir(config.strategy_slug)
    selected = parse_session_selector(config.session_nums, sessions_dir)

    tick_maps: dict[int, dict[int, Any]] = {}
    configs: dict[int, DynamicStrategyReplayConfig] = {}

    for session_num in selected:
        session_dir = sessions_dir / f"session_{session_num}"
        journal_path = session_dir / "journal.md"
        if not journal_path.is_file():
            continue

        if is_report_driven_data_source(config.data_source):
            ticks, session_config, _params = build_session_parity_ticks(
                session_dir,
                config.strategy_slug,
                config=config,
            )
        else:
            ticks = parse_journal_ticks(
                journal_path.read_text(encoding="utf-8"),
                session_dir=session_dir,
            )
            session_config = config
            if config.config_source == "session":
                session_config, _ = replay_config_from_session(
                    session_dir,
                    config.strategy_slug,
                    base=config,
                )

        if ticks:
            tick_maps[session_num] = ticks
            configs[session_num] = session_config

    return tick_maps, configs, sorted(tick_maps.keys())
