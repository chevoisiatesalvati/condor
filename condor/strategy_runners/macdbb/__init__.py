"""MACDBB strategy_runners package."""

from __future__ import annotations

from condor.strategy_runners.macdbb.engine import decide
from condor.strategy_runners.macdbb.replay_bridge import decide_from_sim_tick
from condor.strategy_runners.macdbb.types import (
    CreateAction,
    MacdbbDecision,
    MacdbbState,
    MacdbbTickInput,
    OpenPosition,
    SignalSnapshot,
    StopAction,
)

__all__ = [
    "CreateAction",
    "MacdbbDecision",
    "MacdbbState",
    "MacdbbTickInput",
    "OpenPosition",
    "SignalSnapshot",
    "StopAction",
    "decide",
    "decide_from_sim_tick",
]
