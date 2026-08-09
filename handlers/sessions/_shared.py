"""Shared helpers for the Telegram /agents command (agent StrategyStore browser)."""

from __future__ import annotations

from typing import Any

from condor.agents.performance import is_running_status
from condor.agents.strategy import Strategy, StrategyStore

SESSIONS_STATE_KEYS = (
    "sessions_slug",
    "sessions_num",
    "sessions_run_key",
    "sessions_server_name",
    "sessions_executors",
    "sessions_executors_raw",
    "sessions_perf",
    "sessions_history",
    "sessions_callback_prefix",
)

DEFAULT_CALLBACK_PREFIX = "agents"


def get_callback_prefix(context, default: str = DEFAULT_CALLBACK_PREFIX) -> str:
    """Return the active browse-flow callback prefix stored in user_data."""
    return context.user_data.get("sessions_callback_prefix") or default


def set_callback_prefix(context, callback_prefix: str) -> None:
    context.user_data["sessions_callback_prefix"] = callback_prefix


def clear_sessions_state(context) -> None:
    """Clear agent-strategy browse flow keys from user context."""
    for key in SESSIONS_STATE_KEYS:
        context.user_data.pop(key, None)


def resolve_strategy(store: StrategyStore, slug: str) -> Strategy | None:
    """Resolve a strategy by flat slug (agent == strategy slug) or strategy slug."""
    strategy = store.get(slug, slug)
    if strategy is not None:
        return strategy
    for candidate in store.list_all():
        if candidate.slug == slug:
            return candidate
    return None


def run_key_for_strategy(strategy: Strategy) -> str:
    return f"{strategy.agent_slug}.{strategy.slug}"


def session_agent_id(run_key: str, session_num: int) -> str:
    """Build the HB controller_id / agent_id for a strategy session."""
    return f"{run_key}_{session_num}"


def sort_session_executors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Running executors first, then newest/other rows."""
    def _sort_key(row: dict[str, Any]) -> tuple:
        running = 0 if is_running_status(str(row.get("status") or "")) else 1
        ts = float(row.get("timestamp") or 0)
        return (running, -ts)

    return sorted(rows, key=_sort_key)


def format_vol_col(volume: float) -> str:
    if volume >= 1000:
        return f"{volume / 1000:.1f}k".rjust(7)
    return f"{volume:.0f}".rjust(7)
