"""Replay/backtest library for trading_agents/macdbb_scanner_aggressive_hl."""

from routines.macdbb_scanner_aggressive_hl_replay.models import (
    AdaptiveReplayConfig,
    SimTrade,
    StrategyReplayConfig,
    TickMeta,
)

__all__ = [
    "AdaptiveReplayConfig",
    "StrategyReplayConfig",
    "SimTrade",
    "TickMeta",
    "simulate_adaptive_session",
    "simulate_strategy_session",
]


def __getattr__(name: str):
    if name == "simulate_adaptive_session":
        from routines.macdbb_scanner_aggressive_hl_replay.adaptive_simulator import simulate_adaptive_session

        return simulate_adaptive_session
    if name == "simulate_strategy_session":
        from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

        return simulate_strategy_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
