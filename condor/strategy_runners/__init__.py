"""Condor-native deterministic strategy runners (Strategies product surface).

These are not Agents (LLM chat/loops) and not Hummingbot V2 controller bots.
They share a decision engine with timeline backtests and create position
executors directly via the Hummingbot API.
"""

from __future__ import annotations

from condor.strategy_runners.catalog import (
    DeterministicStrategy,
    get_strategy,
    is_deterministic_strategy_slug,
    list_strategies,
)

__all__ = [
    "DeterministicStrategy",
    "get_strategy",
    "is_deterministic_strategy_slug",
    "list_strategies",
]
