"""Shared helpers for the Telegram /strategies command (deterministic catalog)."""

from __future__ import annotations

from typing import Any

from utils.telegram_formatters import escape_markdown_v2

from handlers.sessions._shared import clear_sessions_state

CALLBACK_PREFIX = "strategies"

STRATEGIES_STATE_KEYS = (
    "strategies_slug",
    "strategies_run_key",
)


def clear_strategies_state(context) -> None:
    """Clear /strategies-specific keys and shared browse-flow state."""
    for key in STRATEGIES_STATE_KEYS:
        context.user_data.pop(key, None)
    clear_sessions_state(context)


def deterministic_session_agent_id(run_key: str, session_num: int) -> str:
    """HB controller_id for a deterministic strategy session."""
    return f"{run_key}_{session_num}"


def format_pnl_plain(value: float) -> str:
    """Format a PnL amount for button labels (no Markdown escaping)."""
    return f"{value:+,.2f}"


def format_pnl_md(value: float) -> str:
    """Format a dollar PnL amount for MarkdownV2 code spans."""
    return escape_markdown_v2(f"${value:+,.2f}")


def format_strategy_overview_lines(
    *,
    slug: str,
    name: str,
    status: str,
    total_pnl: float,
    last_session_pnl: float,
    realized_pnl: float,
    unrealized_pnl: float,
    volume: float,
    open_positions: int,
    session_count: int,
    fees: float = 0.0,
) -> list[str]:
    """Build MarkdownV2 overview lines matching Condor StrategyRunnerDetail totals."""
    display_name = name or slug
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    last_emoji = "🟢" if last_session_pnl >= 0 else "🔴"

    lines = [
        f"📊 *Strategy* \\| `{escape_markdown_v2(slug)}`",
        f"_{escape_markdown_v2(display_name)}_ \\| "
        f"{escape_markdown_v2(status)}",
        "",
        f"{pnl_emoji} Total PnL: `{format_pnl_md(total_pnl)}` \\| "
        f"{last_emoji} Last: `{format_pnl_md(last_session_pnl)}`",
        f"Realized: `{format_pnl_md(realized_pnl)}` \\| "
        f"Unrealized: `{format_pnl_md(unrealized_pnl)}`",
        f"📊 Vol: `{escape_markdown_v2(f'${volume:,.0f}')}` \\| "
        f"Open: `{open_positions}` \\| Sessions: `{session_count}`",
    ]
    if fees:
        lines.append(f"💸 Fees: `{escape_markdown_v2(f'${fees:,.2f}')}`")
    return lines


def session_pnl_by_number(sessions_perf: list[Any]) -> dict[int, float]:
    """Map session_num → total_pnl from deterministic performance rows."""
    result: dict[int, float] = {}
    for row in sessions_perf:
        kind = getattr(row, "kind", None)
        if kind is None and isinstance(row, dict):
            kind = row.get("kind")
        # Deterministic rows have no kind; agent rows use kind=="session".
        if kind is not None and kind != "session":
            continue
        num = getattr(row, "session_num", None)
        if num is None and isinstance(row, dict):
            num = row.get("session_num")
        if num is None:
            continue
        pnl = getattr(row, "total_pnl", None)
        if pnl is None and isinstance(row, dict):
            pnl = row.get("total_pnl", 0.0)
        result[int(num)] = float(pnl or 0.0)
    return result


async def fetch_deterministic_performance(strat) -> dict[str, Any]:
    """Roll up session performance (same logic as web get_strategy_performance)."""
    from condor.agents.performance import fetch_agent_performance
    from condor.strategy_runners.macdbb.sessions import list_session_dirs
    from condor.web.routes.strategies import (
        _get_client,
        _merged_default_config,
        _running_for,
        _session_disk_status,
    )

    defaults = _merged_default_config(strat)
    client = await _get_client(str(defaults.get("server_name") or ""))
    running = _running_for(strat.slug)
    running_ids = {e.agent_id for e in running}

    sessions: list[dict[str, Any]] = []
    totals = {
        "total_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "volume": 0.0,
        "fees": 0.0,
        "open_positions": 0,
    }
    if client is not None:
        for path in list_session_dirs(strat.data_slug):
            try:
                num = int(path.name.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            agent_id = f"{strat.key}_{num}"
            try:
                perf = await fetch_agent_performance(client, agent_id)
            except Exception:
                continue
            fees = float(getattr(perf, "fees", 0) or 0)
            row = {
                "agent_id": agent_id,
                "session_num": num,
                "status": _session_disk_status(
                    strat, num, running_ids=running_ids
                ),
                "realized_pnl": perf.realized_pnl,
                "unrealized_pnl": perf.unrealized_pnl,
                "total_pnl": perf.total_pnl,
                "volume": perf.volume,
                "fees": fees,
                "trade_count": getattr(perf, "trade_count", 0) or 0,
                "open_count": perf.open_count,
                "closed_count": perf.closed_count,
            }
            sessions.append(row)
            totals["total_pnl"] += float(perf.total_pnl or 0)
            totals["realized_pnl"] += float(perf.realized_pnl or 0)
            totals["unrealized_pnl"] += float(perf.unrealized_pnl or 0)
            totals["volume"] += float(perf.volume or 0)
            totals["fees"] += fees
            totals["open_positions"] += int(perf.open_count or 0)
    sessions.sort(key=lambda r: r["session_num"], reverse=True)
    return {"slug": strat.slug, "sessions": sessions, "totals": totals, "client": client}
