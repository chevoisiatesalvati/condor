"""Shared helpers for the Telegram /agents command (agent StrategyStore browser)."""

from __future__ import annotations

from typing import Any

from condor.agents.performance import is_running_status
from condor.agents.strategy import Strategy, StrategyStore
from handlers.executors._shared import SIDE_LONG, get_executor_type, normalize_side

SESSIONS_STATE_KEYS = (
    "sessions_slug",
    "sessions_num",
    "sessions_page",
    "sessions_run_key",
    "sessions_server_name",
    "sessions_executors",
    "sessions_executors_raw",
    "sessions_perf",
    "sessions_history",
    "sessions_callback_prefix",
)

MAX_EXECUTORS_SHOWN = 8
PAIR_COL_WIDTH = 10
TYPE_COL_WIDTH = 4
SIDE_COL_WIDTH = 4
PNL_COL_WIDTH = 8
VOL_COL_WIDTH = 7
STATUS_COL_WIDTH = 6
_TYPE_LABELS = {"grid": "Grid", "position": "Pos"}

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
        return f"{volume / 1000:.1f}k".rjust(VOL_COL_WIDTH)
    return f"{volume:.0f}".rjust(VOL_COL_WIDTH)


def session_view_callback(
    prefix: str, slug: str, session_num: int, page: int = 0
) -> str:
    """Build view callback; omit page when 0 so legacy callbacks keep working."""
    if page:
        return f"{prefix}:view:{slug}:{session_num}:{page}"
    return f"{prefix}:view:{slug}:{session_num}"


def stored_session_page(context) -> int:
    try:
        return int(context.user_data.get("sessions_page") or 0)
    except (TypeError, ValueError):
        return 0


def stored_session_view_callback(prefix: str, context) -> str | None:
    slug = context.user_data.get("sessions_slug")
    session_num = context.user_data.get("sessions_num")
    if slug is None or session_num is None:
        return None
    return session_view_callback(prefix, slug, session_num, stored_session_page(context))


def session_page_nav_callbacks(
    prefix: str,
    slug: str,
    session_num: int,
    *,
    page: int,
    total: int,
    per_page: int = MAX_EXECUTORS_SHOWN,
) -> list[tuple[str, str]]:
    """Return (label, callback_data) pairs for Prev/Next."""
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(
            (
                "◀️ Prev",
                session_view_callback(prefix, slug, session_num, page - 1),
            )
        )
    if (page + 1) * per_page < total:
        nav.append(
            (
                "Next ▶️",
                session_view_callback(prefix, slug, session_num, page + 1),
            )
        )
    return nav


def _side_display(row: dict[str, Any]) -> str:
    config = row.get("config") if isinstance(row.get("config"), dict) else {}
    side_raw = row.get("side") or config.get("side") or SIDE_LONG
    side_val = normalize_side(side_raw)
    leverage = config.get("leverage", 1) or 1
    return f"{'L' if side_val == SIDE_LONG else 'S'} {leverage}x"


def executor_row_button_text(row: dict[str, Any]) -> str:
    """Inline button label: PnL emoji, pair, side, two-decimal dollar PnL."""
    pnl = float(row.get("pnl") or 0)
    emoji = "🟢" if pnl >= 0 else "🔴"
    pair = str(row.get("pair") or "???")[:PAIR_COL_WIDTH]
    config = row.get("config") if isinstance(row.get("config"), dict) else {}
    side_raw = row.get("side") or config.get("side") or SIDE_LONG
    side_val = normalize_side(side_raw)
    side_label = "L" if side_val == SIDE_LONG else "S"
    return f"{emoji} {pair} {side_label} ${pnl:+.2f}"


def format_session_executors_table(
    rows: list[dict[str, Any]],
    *,
    page: int = 0,
    per_page: int = MAX_EXECUTORS_SHOWN,
    total_pnl: float,
    total_volume: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return markdown table lines (fences + optional page caption) and displayed rows."""
    if not rows:
        return ["", "_No executors for this session\\._"], []

    per_page = max(1, per_page)
    total = len(rows)
    max_page = max(0, (total - 1) // per_page)
    page = min(max(0, page), max_page)
    page_items = rows[page * per_page : page * per_page + per_page]

    header = (
        f"{'Pair':<{PAIR_COL_WIDTH}} {'Type':<{TYPE_COL_WIDTH}} "
        f"{'Side':<{SIDE_COL_WIDTH}} {'PnL':>{PNL_COL_WIDTH}} "
        f"{'Vol':>{VOL_COL_WIDTH}} {'Status':<{STATUS_COL_WIDTH}}"
    )
    separator = (
        f"{'─' * PAIR_COL_WIDTH} {'─' * TYPE_COL_WIDTH} "
        f"{'─' * SIDE_COL_WIDTH} {'─' * PNL_COL_WIDTH} "
        f"{'─' * VOL_COL_WIDTH} {'─' * STATUS_COL_WIDTH}"
    )

    lines = ["", "```", header, separator]
    displayed: list[dict[str, Any]] = []
    for row in page_items:
        pair = str(row.get("pair") or "???")[:PAIR_COL_WIDTH]
        ex_type = str(row.get("type") or get_executor_type(row) or "ord")
        type_col = _TYPE_LABELS.get(ex_type, "Ord")
        side_display = _side_display(row)
        pnl = float(row.get("pnl") or 0)
        vol = float(row.get("volume") or 0)
        status = str(row.get("status") or "?")[:STATUS_COL_WIDTH]

        pair_col = pair.ljust(PAIR_COL_WIDTH)
        type_display = type_col.ljust(TYPE_COL_WIDTH)
        side_col = side_display.ljust(SIDE_COL_WIDTH)
        pnl_col = f"{pnl:+.2f}".rjust(PNL_COL_WIDTH)
        vol_col = format_vol_col(vol)
        status_col = status.ljust(STATUS_COL_WIDTH)
        lines.append(
            f"{pair_col} {type_display} {side_col} {pnl_col} {vol_col} {status_col}"
        )
        displayed.append(row)

    if total > 1:
        lines.append(separator)
        prefix_width = PAIR_COL_WIDTH + 1 + TYPE_COL_WIDTH + 1 + SIDE_COL_WIDTH
        total_label = "TOTAL".ljust(prefix_width)
        pnl_col = f"{total_pnl:+.2f}".rjust(PNL_COL_WIDTH)
        vol_col = format_vol_col(total_volume)
        lines.append(f"{total_label} {pnl_col} {vol_col}")

    lines.append("```")
    if total > per_page:
        page_count = (total + per_page - 1) // per_page
        lines.append(f"_Page {page + 1}/{page_count} \\({total} total\\)_")
    return lines, displayed
