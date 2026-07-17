"""
Sessions Handler - Browse strategy session executors via Telegram.

Flow:
  /sessions → pick strategy → pick session → session executors table
                                          → detail → stop

Commands:
- /sessions [slug] [session_num]
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from condor.agents.strategy import StrategyStore
from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import clear_sessions_state, resolve_strategy

logger = logging.getLogger(__name__)


@restricted
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sessions [slug] [session_num]."""
    clear_all_input_states(context)
    clear_sessions_state(context)

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text("Use /sessions in a private chat.")
        return

    args = context.args or []
    from .menu import show_session_executors, show_session_list, show_strategy_picker

    if not args:
        await show_strategy_picker(update, context)
        return

    slug = args[0].strip()
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    if strategy is None:
        await update.message.reply_text(f"Strategy not found: {slug}")
        return

    if len(args) >= 2:
        try:
            session_num = int(args[1])
        except ValueError:
            await update.message.reply_text("Session number must be an integer.")
            return
        await show_session_executors(update, context, strategy.slug, session_num)
        return

    await show_session_list(update, context, strategy.slug)


@restricted
async def sessions_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route sessions:* callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 2:
        return

    action = parts[1]

    from .menu import (
        handle_close,
        handle_sessions_confirm_stop,
        handle_sessions_stop,
        show_session_executor_detail,
        show_session_executors,
        show_session_history,
        show_session_history_detail,
        show_session_list,
        show_strategy_picker,
    )

    if action == "menu":
        await show_strategy_picker(update, context)

    elif action == "list" and len(parts) >= 3:
        slug = parts[2]
        page = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 0
        await show_session_list(update, context, slug, page=page)

    elif action == "view" and len(parts) >= 4:
        slug = parts[2]
        try:
            session_num = int(parts[3])
        except ValueError:
            await query.answer("Invalid session number", show_alert=True)
            return
        await show_session_executors(update, context, slug, session_num)

    elif action == "history" and len(parts) >= 4:
        slug = parts[2]
        try:
            session_num = int(parts[3])
        except ValueError:
            await query.answer("Invalid session number", show_alert=True)
            return
        page = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else 0
        await show_session_history(
            update, context, slug=slug, session_num=session_num, page=page
        )

    elif action == "hist_detail" and len(parts) >= 3:
        await show_session_history_detail(update, context, parts[2])

    elif action == "detail" and len(parts) >= 3:
        await show_session_executor_detail(update, context, parts[2])

    elif action == "stop" and len(parts) >= 3:
        await handle_sessions_stop(update, context, parts[2])

    elif action == "confirm_stop" and len(parts) >= 3:
        await handle_sessions_confirm_stop(update, context, parts[2])

    elif action == "close":
        await handle_close(update, context)
