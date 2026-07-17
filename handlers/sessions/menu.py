"""Telegram /sessions menus — strategy picker, session list, session executors."""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config_manager import get_client, get_config_manager
from condor.agents.config import load_agent_config
from condor.agents.engine import get_all_engines
from condor.agents.performance import fetch_agent_performance, is_running_status
from condor.agents.sessions_index import list_sessions
from condor.agents.strategy import StrategyStore
from condor.fetchers.executors import stop_executor
from handlers.executors._shared import (
    SIDE_LONG,
    get_executor_type,
    normalize_side,
)
from handlers.executors.menu import (
    _render_executor_detail,
    handle_stop_executor,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ._shared import (
    clear_sessions_state,
    format_vol_col,
    resolve_strategy,
    run_key_for_strategy,
    session_agent_id,
    sort_session_executors,
)

logger = logging.getLogger(__name__)

SESSIONS_PER_PAGE = 10
MAX_EXECUTORS_SHOWN = 8
CALLBACK_PREFIX = "sessions"


async def _resolve_client_for_strategy(
    strategy,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Pick API client: strategy config server first, else user's default."""
    defaults = strategy.default_config if strategy else None
    agent_dir = strategy.data_dir if strategy else None
    server_name = ""

    if agent_dir is not None and agent_dir.is_dir():
        cfg = load_agent_config(agent_dir, defaults)
        if cfg.server_name:
            cm = get_config_manager()
            try:
                client = await cm.get_client(cfg.server_name)
                if client:
                    return client, cfg.server_name
            except Exception as e:
                logger.warning("get_client(%s) failed: %s", cfg.server_name, e)

    client = await get_client(chat_id, context=context)
    if client is not None:
        from handlers.config.user_preferences import get_active_server

        server_name = get_active_server(context.user_data) or ""
    return client, server_name


def _strategy_is_running(run_key: str, slug: str) -> bool:
    engines = get_all_engines()
    prefixes = (f"{run_key}_", f"{slug}_")
    for eid in engines:
        for prefix in prefixes:
            if eid.startswith(prefix) and not eid[len(prefix) :].startswith("e"):
                return True
    return False


def _session_is_running(run_key: str, session_num: int) -> bool:
    agent_id = session_agent_id(run_key, session_num)
    return agent_id in get_all_engines()


async def _edit_or_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    query = update.callback_query
    msg = update.message or (query.message if query else None)
    if not msg:
        return
    if query:
        try:
            await query.message.edit_text(
                text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                await query.answer("✅ Already up to date")
            elif "no text in the message" in str(e).lower():
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            else:
                raise
    else:
        await msg.reply_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


# ============================================
# STRATEGY PICKER
# ============================================


async def show_strategy_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List strategies for session browsing."""
    store = StrategyStore()
    strategies = store.list_all()
    query = update.callback_query
    msg = update.message or (query.message if query else None)
    if not msg:
        return

    if not strategies:
        text = "No trading agent strategies found\\."
        if query:
            await query.message.edit_text(text, parse_mode="MarkdownV2")
        else:
            await msg.reply_text(text, parse_mode="MarkdownV2")
        return

    buttons = []
    for s in strategies[:12]:
        slug = s.slug
        label = s.name or slug
        run_key = run_key_for_strategy(s)
        if _strategy_is_running(run_key, slug):
            label = f"● {label}"
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"sessions:list:{slug}")]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Close", callback_data="sessions:close")]
    )

    text = "📂 *Sessions*\nSelect a strategy:"
    reply_markup = InlineKeyboardMarkup(buttons)
    if query:
        try:
            await query.message.edit_text(
                text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise
    else:
        await msg.reply_text(text, parse_mode="MarkdownV2", reply_markup=reply_markup)


# ============================================
# SESSION LIST
# ============================================


async def show_session_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    page: int = 0,
) -> None:
    """List on-disk sessions for a strategy (newest first)."""
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    if strategy is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="sessions:menu")]]
            ),
        )
        return

    run_key = run_key_for_strategy(strategy)
    sessions = list_sessions(strategy.data_dir)
    context.user_data["sessions_slug"] = strategy.slug
    context.user_data["sessions_run_key"] = run_key

    total = len(sessions)
    start = page * SESSIONS_PER_PAGE
    page_items = sessions[start : start + SESSIONS_PER_PAGE]

    lines = [
        f"📂 *Sessions* \\| `{escape_markdown_v2(strategy.slug)}`",
        "",
    ]
    if not page_items:
        lines.append("_No sessions found\\._")
    else:
        lines.append(f"_{total} session\\(s\\)\\. Select one:_")

    keyboard = []
    for sess in page_items:
        num = int(sess["number"])
        live = _session_is_running(run_key, num)
        label = f"{'● ' if live else ''}session_{num}"
        created = sess.get("created_at") or ""
        if created:
            label = f"{label} · {str(created)[:10]}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"sessions:view:{strategy.slug}:{num}"
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"sessions:list:{strategy.slug}:{page - 1}"
            )
        )
    if start + SESSIONS_PER_PAGE < total:
        nav.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"sessions:list:{strategy.slug}:{page + 1}"
            )
        )
    if nav:
        keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Strategies", callback_data="sessions:menu"),
            InlineKeyboardButton("❌ Close", callback_data="sessions:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


# ============================================
# SESSION EXECUTORS VIEW
# ============================================


def _side_display(row: dict[str, Any]) -> str:
    config = row.get("config") if isinstance(row.get("config"), dict) else {}
    side_raw = row.get("side") or config.get("side") or SIDE_LONG
    side_val = normalize_side(side_raw)
    leverage = config.get("leverage", 1) or 1
    return f"{'L' if side_val == SIDE_LONG else 'S'} {leverage}x"


async def show_session_executors(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    session_num: int,
) -> None:
    """Show session-scoped executors table (web UI parity for PnL/volume)."""
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    query = update.callback_query
    chat_id = update.effective_chat.id

    if strategy is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="sessions:menu")]]
            ),
        )
        return

    run_key = run_key_for_strategy(strategy)
    agent_id = session_agent_id(run_key, session_num)
    client, server_name = await _resolve_client_for_strategy(strategy, chat_id, context)

    context.user_data["sessions_slug"] = strategy.slug
    context.user_data["sessions_num"] = session_num
    context.user_data["sessions_run_key"] = run_key
    context.user_data["sessions_server_name"] = server_name

    if not client:
        await _edit_or_reply(
            update,
            context,
            format_error_message(
                "No Hummingbot API server available. Configure a server in /config."
            ),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"sessions:list:{strategy.slug}",
                        )
                    ]
                ]
            ),
        )
        return

    try:
        perf = await fetch_agent_performance(client, agent_id)
    except Exception as e:
        logger.exception("fetch_agent_performance(%s) failed", agent_id)
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Failed to load session executors: {e}"),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Retry",
                            callback_data=f"sessions:view:{strategy.slug}:{session_num}",
                        ),
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"sessions:list:{strategy.slug}",
                        ),
                    ]
                ]
            ),
        )
        return

    rows = sort_session_executors(list(perf.executors or []))
    context.user_data["sessions_executors"] = rows
    context.user_data["sessions_perf"] = {
        "total_pnl": perf.total_pnl,
        "volume": perf.volume,
        "fees": perf.fees,
        "open_count": perf.open_count,
        "trade_count": perf.trade_count,
    }

    live = _session_is_running(run_key, session_num)
    server_label = server_name or "default"
    status_label = "live" if live else "closed"
    lines = [
        f"📂 *Session* `{escape_markdown_v2(strategy.slug)}` "
        f"`session\\_{session_num}`",
        f"_{escape_markdown_v2(server_label)}_ \\| "
        f"{escape_markdown_v2(status_label)}",
        "",
    ]

    total_pnl = perf.total_pnl
    total_volume = perf.volume
    total_fees = perf.fees
    active_count = perf.open_count
    total_count = len(rows)

    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(
        f"{pnl_emoji} PnL: `{escape_markdown_v2(f'${total_pnl:+,.2f}')}` \\| "
        f"📊 Vol: `{escape_markdown_v2(f'${total_volume:,.0f}')}` \\| "
        f"💸 Fees: `{escape_markdown_v2(f'${total_fees:,.2f}')}`"
    )
    lines.append(
        f"Executors: `{total_count}`"
        + (f" \\(`{active_count}` active\\)" if active_count else "")
    )

    displayed: list[dict[str, Any]] = []
    if rows:
        lines.append("")
        lines.append("```")
        lines.append("Pair         Type Side    PnL      Vol")
        lines.append("──────────── ──── ──── ──────── ───────")

        for row in rows[:MAX_EXECUTORS_SHOWN]:
            pair = str(row.get("pair") or "???")
            ex_type = str(row.get("type") or get_executor_type(row) or "ord")
            type_col = {"grid": "Grid", "position": "Pos"}.get(ex_type, "Ord")
            side_display = _side_display(row)
            pnl = float(row.get("pnl") or 0)
            vol = float(row.get("volume") or 0)

            pair_col = pair[:12].ljust(12)
            type_display = type_col.ljust(4)
            side_col = side_display.ljust(4)
            pnl_col = f"{pnl:+.2f}".rjust(8)
            vol_col = format_vol_col(vol)
            status_mark = "*" if is_running_status(str(row.get("status") or "")) else " "
            lines.append(
                f"{pair_col} {type_display} {side_col} {pnl_col} {vol_col}{status_mark}"
            )
            displayed.append(row)

        if len(rows) > MAX_EXECUTORS_SHOWN:
            lines.append(f"  ...and {len(rows) - MAX_EXECUTORS_SHOWN} more")

        if len(rows) > 1:
            lines.append("──────────── ──── ──── ──────── ───────")
            total_label = "TOTAL".ljust(22)
            pnl_col = f"{total_pnl:+.2f}".rjust(8)
            vol_col = format_vol_col(total_volume)
            lines.append(f"{total_label} {pnl_col} {vol_col}")

        lines.append("```")
    else:
        lines.append("")
        lines.append("_No executors for this session\\._")

    keyboard = []
    if displayed:
        row_btns: list[InlineKeyboardButton] = []
        for row in displayed:
            executor_id = str(row.get("id") or "")
            if not executor_id:
                continue
            pair = str(row.get("pair") or "???")[:10]
            side_val = normalize_side(
                row.get("side")
                or (row.get("config") or {}).get("side")
                or SIDE_LONG
            )
            side_label = "L" if side_val == SIDE_LONG else "S"
            ex_type = str(row.get("type") or "")
            type_icon = "📐" if ex_type == "grid" else "🎯"
            row_btns.append(
                InlineKeyboardButton(
                    f"{type_icon} {pair} {side_label}",
                    callback_data=f"sessions:detail:{executor_id[:20]}",
                )
            )
            if len(row_btns) == 2:
                keyboard.append(row_btns)
                row_btns = []
        if row_btns:
            keyboard.append(row_btns)

    keyboard.append(
        [
            InlineKeyboardButton(
                "📜 History",
                callback_data=f"sessions:history:{strategy.slug}:{session_num}",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Sessions",
                callback_data=f"sessions:list:{strategy.slug}",
            ),
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data=f"sessions:view:{strategy.slug}:{session_num}",
            ),
            InlineKeyboardButton("❌ Close", callback_data="sessions:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


# ============================================
# SESSION HISTORY (terminated executors)
# ============================================


async def show_session_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str | None = None,
    session_num: int | None = None,
    page: int = 0,
) -> None:
    """Show terminated executors for the current strategy session."""
    slug = slug or context.user_data.get("sessions_slug")
    session_num = (
        session_num
        if session_num is not None
        else context.user_data.get("sessions_num")
    )
    per_page = MAX_EXECUTORS_SHOWN

    if slug is None or session_num is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message("Session context lost"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="sessions:menu")]]
            ),
        )
        return

    session_num = int(session_num)
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    if strategy is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="sessions:menu")]]
            ),
        )
        return

    run_key = run_key_for_strategy(strategy)
    agent_id = session_agent_id(run_key, session_num)
    chat_id = update.effective_chat.id
    client, server_name = await _resolve_client_for_strategy(strategy, chat_id, context)

    context.user_data["sessions_slug"] = strategy.slug
    context.user_data["sessions_num"] = session_num
    context.user_data["sessions_run_key"] = run_key
    context.user_data["sessions_server_name"] = server_name

    if not client:
        await _edit_or_reply(
            update,
            context,
            format_error_message("No Hummingbot API server available."),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"sessions:view:{strategy.slug}:{session_num}",
                        )
                    ]
                ]
            ),
        )
        return

    try:
        perf = await fetch_agent_performance(client, agent_id)
    except Exception as e:
        logger.exception("fetch_agent_performance(%s) failed for history", agent_id)
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Failed to load session history: {e}"),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"sessions:view:{strategy.slug}:{session_num}",
                        )
                    ]
                ]
            ),
        )
        return

    history = [
        row
        for row in (perf.executors or [])
        if not is_running_status(str(row.get("status") or ""))
    ]
    history.sort(key=lambda row: float(row.get("timestamp") or 0), reverse=True)
    context.user_data["sessions_history"] = history

    total = len(history)
    start = page * per_page
    page_items = history[start : start + per_page]
    server_label = server_name or "default"

    lines = [
        f"📜 *Session History* \\| `{escape_markdown_v2(strategy.slug)}` "
        f"`session\\_{session_num}`",
        f"_{escape_markdown_v2(server_label)}_",
    ]

    displayed: list[dict[str, Any]] = []
    if page_items:
        lines.append("")
        lines.append("```")
        lines.append("Pair         Type Side    PnL   Status")
        lines.append("──────────── ──── ──── ──────── ──────")

        for row in page_items:
            pair = str(row.get("pair") or "???")
            ex_type = str(row.get("type") or get_executor_type(row) or "ord")
            type_col = {"grid": "Grid", "position": "Pos"}.get(ex_type, "Ord")
            side_display = _side_display(row)
            pnl = float(row.get("pnl") or 0)
            status = str(row.get("status") or "?")[:6]

            pair_col = pair[:12].ljust(12)
            type_display = type_col.ljust(4)
            side_col = side_display.ljust(4)
            pnl_col = f"{pnl:+.2f}".rjust(8)
            status_col = status.ljust(6)
            lines.append(
                f"{pair_col} {type_display} {side_col} {pnl_col} {status_col}"
            )
            displayed.append(row)

        lines.append("```")
        if total > per_page:
            lines.append(
                f"_Page {page + 1}/{(total + per_page - 1) // per_page} "
                f"\\({total} total\\)_"
            )
    else:
        lines.append("")
        lines.append("_No terminated executors for this session\\._")

    keyboard: list[list[InlineKeyboardButton]] = []
    if displayed:
        row_btns: list[InlineKeyboardButton] = []
        for row in displayed:
            executor_id = str(row.get("id") or "")
            if not executor_id:
                continue
            pair = str(row.get("pair") or "???")[:10]
            side_val = normalize_side(
                row.get("side")
                or (row.get("config") or {}).get("side")
                or SIDE_LONG
            )
            side_label = "L" if side_val == SIDE_LONG else "S"
            ex_type = str(row.get("type") or "")
            type_icon = "📐" if ex_type == "grid" else "🎯"
            pnl = float(row.get("pnl") or 0)
            pnl_label = f"+{pnl:.0f}" if pnl >= 0 else f"{pnl:.0f}"
            row_btns.append(
                InlineKeyboardButton(
                    f"{type_icon} {pair} {side_label} {pnl_label}",
                    callback_data=f"sessions:hist_detail:{executor_id[:20]}",
                )
            )
            if len(row_btns) == 2:
                keyboard.append(row_btns)
                row_btns = []
        if row_btns:
            keyboard.append(row_btns)

    if total > per_page:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "◀️ Prev",
                    callback_data=(
                        f"sessions:history:{strategy.slug}:{session_num}:{page - 1}"
                    ),
                )
            )
        if start + per_page < total:
            nav_row.append(
                InlineKeyboardButton(
                    "Next ▶️",
                    callback_data=(
                        f"sessions:history:{strategy.slug}:{session_num}:{page + 1}"
                    ),
                )
            )
        if nav_row:
            keyboard.append(nav_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Session",
                callback_data=f"sessions:view:{strategy.slug}:{session_num}",
            ),
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data=f"sessions:history:{strategy.slug}:{session_num}:{page}",
            ),
            InlineKeyboardButton("❌ Close", callback_data="sessions:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


async def show_session_history_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    """Show detail for a terminated session executor (Back → History)."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug")
    session_num = context.user_data.get("sessions_num")
    back_callback = (
        f"sessions:history:{slug}:{session_num}"
        if slug is not None and session_num is not None
        else "sessions:menu"
    )

    history = context.user_data.get("sessions_history") or []
    executor = None
    for ex in history:
        ex_id = str(ex.get("id") or ex.get("executor_id") or "")
        if ex_id.startswith(executor_id) or executor_id.startswith(ex_id[:20]):
            executor = ex
            break

    store = StrategyStore()
    strategy = resolve_strategy(store, slug) if slug else None
    client = None
    if strategy is not None:
        client, _ = await _resolve_client_for_strategy(strategy, chat_id, context)

    if executor is None and client is not None:
        try:
            executor = await client.executors.get_executor(executor_id=executor_id)
        except Exception as e:
            logger.warning("Could not fetch historical executor %s: %s", executor_id, e)

    if executor is not None and client is not None:
        full_id = str(executor.get("id") or executor.get("executor_id") or "")
        if full_id:
            try:
                fresh = await client.executors.get_executor(executor_id=full_id)
                if fresh:
                    executor = fresh
            except Exception:
                pass

    if not executor:
        await query.answer("Executor not found", show_alert=True)
        await show_session_history(update, context, slug=slug, session_num=session_num)
        return

    full_id = executor.get("id", executor.get("executor_id", executor_id))
    context.user_data["current_executor"] = executor
    context.user_data["current_executor_id"] = full_id

    await _render_executor_detail(
        update,
        context,
        executor,
        back_callback=back_callback,
        callback_prefix=CALLBACK_PREFIX,
    )


# ============================================
# DETAIL / STOP
# ============================================


async def show_session_executor_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    """Show executor detail within the /sessions flow."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug")
    session_num = context.user_data.get("sessions_num")
    back_callback = (
        f"sessions:view:{slug}:{session_num}"
        if slug is not None and session_num is not None
        else "sessions:menu"
    )

    store = StrategyStore()
    strategy = resolve_strategy(store, slug) if slug else None
    if strategy is None:
        await query.answer("Session context lost", show_alert=True)
        await show_strategy_picker(update, context)
        return

    client, server_name = await _resolve_client_for_strategy(strategy, chat_id, context)
    context.user_data["sessions_server_name"] = server_name
    if not client:
        await query.answer("No API server", show_alert=True)
        return

    executor = None
    try:
        executor = await client.executors.get_executor(executor_id=executor_id)
    except Exception as e:
        logger.warning("Could not fetch executor %s: %s", executor_id, e)

    if not executor:
        # Fall back to cached session rows / ID prefix match via search
        for row in context.user_data.get("sessions_executors") or []:
            ex_id = str(row.get("id") or "")
            if ex_id.startswith(executor_id) or executor_id.startswith(ex_id[:20]):
                try:
                    executor = await client.executors.get_executor(executor_id=ex_id)
                except Exception:
                    executor = row
                break

    if not executor:
        await query.answer("Executor not found", show_alert=True)
        if slug is not None and session_num is not None:
            await show_session_executors(update, context, slug, int(session_num))
        return

    full_id = executor.get("id", executor.get("executor_id", executor_id))
    context.user_data["current_executor"] = executor
    context.user_data["current_executor_id"] = full_id
    context.user_data["sessions_executors_raw"] = context.user_data.get(
        "sessions_executors_raw"
    ) or [executor]

    await _render_executor_detail(
        update,
        context,
        executor,
        back_callback=back_callback,
        callback_prefix=CALLBACK_PREFIX,
    )


async def handle_sessions_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    await handle_stop_executor(
        update, context, executor_id, callback_prefix=CALLBACK_PREFIX
    )


async def handle_sessions_confirm_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    """Stop an executor using the strategy's API server, then return to session view."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug")
    session_num = context.user_data.get("sessions_num")
    list_callback = (
        f"sessions:view:{slug}:{session_num}"
        if slug is not None and session_num is not None
        else "sessions:menu"
    )

    await query.answer("Stopping...")

    store = StrategyStore()
    strategy = resolve_strategy(store, slug) if slug else None
    if strategy is None:
        await query.message.edit_text(
            format_error_message("Session context lost"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="sessions:menu")]]
            ),
        )
        return

    client, _ = await _resolve_client_for_strategy(strategy, chat_id, context)
    if not client:
        await query.message.edit_text(
            format_error_message("No API server available"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=list_callback)]]
            ),
        )
        return

    try:
        executor = context.user_data.get("current_executor", {})
        full_id = executor.get("id", executor.get("executor_id", executor_id))

        if len(str(full_id)) <= 20:
            for cache_key in ("sessions_executors", "sessions_executors_raw"):
                for ex in context.user_data.get(cache_key) or []:
                    ex_id = str(ex.get("id") or ex.get("executor_id") or "")
                    if ex_id.startswith(executor_id) or executor_id.startswith(
                        ex_id[:20]
                    ):
                        full_id = ex_id
                        break
                if len(str(full_id)) > 20:
                    break

        result = await stop_executor(client, full_id, keep_position=False)
        context.user_data.pop("current_executor", None)
        context.user_data.pop("sessions_executors", None)
        context.user_data.pop("sessions_executors_raw", None)

        ok = (
            result.get("status") in ("success", "stopping", "stopped")
            or "stop" in str(result).lower()
        )
        if ok:
            keyboard = [
                [InlineKeyboardButton("📋 Back to Session", callback_data=list_callback)]
            ]
            await query.message.edit_text(
                f"✅ *Executor Stopped*\n\n🆔 `{escape_markdown_v2(str(full_id)[:30])}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            error_msg = result.get("message", str(result))
            keyboard = [
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data=f"sessions:detail:{executor_id}",
                    )
                ]
            ]
            await query.message.edit_text(
                f"❌ *Stop Failed*\n\n{escape_markdown_v2(str(error_msg)[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    except Exception as e:
        logger.error("Error stopping session executor: %s", e, exc_info=True)
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data=list_callback)]]
            ),
        )


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    clear_sessions_state(context)
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning("Could not delete sessions message: %s", e)
