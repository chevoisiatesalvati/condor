"""Telegram /agents menus — StrategyStore picker, session list, session executors."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config_manager import get_client, get_config_manager
from condor.agents.config import load_agent_config
from condor.agents.engine import get_all_engines
from condor.agents.performance import fetch_agent_performance
from condor.agents.sessions_index import list_sessions
from condor.agents.strategy import StrategyStore
from condor.fetchers.executors import stop_executor
from handlers.executors.menu import (
    _render_executor_detail,
    handle_stop_executor,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ._shared import (
    DEFAULT_CALLBACK_PREFIX,
    MAX_EXECUTORS_SHOWN,
    clear_sessions_state,
    executor_row_button_text,
    format_session_executors_table,
    get_callback_prefix,
    resolve_strategy,
    run_key_for_strategy,
    session_agent_id,
    session_page_nav_callbacks,
    session_view_callback,
    set_callback_prefix,
    sort_session_executors,
    stored_session_page,
    stored_session_view_callback,
)

logger = logging.getLogger(__name__)

SESSIONS_PER_PAGE = 10
CALLBACK_PREFIX = DEFAULT_CALLBACK_PREFIX


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


def _resolve_prefix(
    context: ContextTypes.DEFAULT_TYPE, callback_prefix: str | None
) -> str:
    if callback_prefix:
        set_callback_prefix(context, callback_prefix)
        return callback_prefix
    return get_callback_prefix(context)


# ============================================
# STRATEGY PICKER
# ============================================


async def show_strategy_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_prefix: str = CALLBACK_PREFIX,
) -> None:
    """List strategies for session browsing."""
    prefix = _resolve_prefix(context, callback_prefix)
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
            [InlineKeyboardButton(label, callback_data=f"{prefix}:list:{slug}")]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Close", callback_data=f"{prefix}:close")]
    )

    if prefix == "strategies":
        title = "Strategies"
    elif prefix == "agents":
        title = "Agents"
    else:
        title = "Sessions"
    text = f"📂 *{title}*\nSelect a strategy:"
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
    callback_prefix: str = CALLBACK_PREFIX,
) -> None:
    """List on-disk sessions for a strategy (newest first)."""
    prefix = _resolve_prefix(context, callback_prefix)
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    if strategy is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
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
                    label, callback_data=f"{prefix}:view:{strategy.slug}:{num}"
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"{prefix}:list:{strategy.slug}:{page - 1}"
            )
        )
    if start + SESSIONS_PER_PAGE < total:
        nav.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"{prefix}:list:{strategy.slug}:{page + 1}"
            )
        )
    if nav:
        keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Strategies", callback_data=f"{prefix}:menu"),
            InlineKeyboardButton("❌ Close", callback_data=f"{prefix}:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


# ============================================
# SESSION EXECUTORS VIEW
# ============================================


async def show_session_executors(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    session_num: int,
    page: int = 0,
    callback_prefix: str = CALLBACK_PREFIX,
) -> None:
    """Show session-scoped executors table (web UI parity for PnL/volume)."""
    prefix = _resolve_prefix(context, callback_prefix)
    store = StrategyStore()
    strategy = resolve_strategy(store, slug)
    chat_id = update.effective_chat.id
    view_callback = session_view_callback(prefix, slug, session_num, page)

    if strategy is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
            ),
        )
        return

    run_key = run_key_for_strategy(strategy)
    agent_id = session_agent_id(run_key, session_num)
    client, server_name = await _resolve_client_for_strategy(strategy, chat_id, context)

    context.user_data["sessions_slug"] = strategy.slug
    context.user_data["sessions_num"] = session_num
    context.user_data["sessions_page"] = page
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
                            callback_data=f"{prefix}:list:{strategy.slug}",
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
                            callback_data=view_callback,
                        ),
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data=f"{prefix}:list:{strategy.slug}",
                        ),
                    ]
                ]
            ),
        )
        return

    rows = sort_session_executors(list(perf.executors or []))
    if rows:
        max_page = max(0, (len(rows) - 1) // MAX_EXECUTORS_SHOWN)
        page = min(max(0, page), max_page)
    else:
        page = 0
    context.user_data["sessions_page"] = page
    view_callback = session_view_callback(prefix, strategy.slug, session_num, page)
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

    table_lines, displayed = format_session_executors_table(
        rows,
        page=page,
        per_page=MAX_EXECUTORS_SHOWN,
        total_pnl=total_pnl,
        total_volume=total_volume,
    )
    lines.extend(table_lines)

    keyboard = []
    if displayed:
        row_btns: list[InlineKeyboardButton] = []
        for row in displayed:
            executor_id = str(row.get("id") or "")
            if not executor_id:
                continue
            row_btns.append(
                InlineKeyboardButton(
                    executor_row_button_text(row),
                    callback_data=f"{prefix}:detail:{executor_id[:20]}",
                )
            )
            if len(row_btns) == 2:
                keyboard.append(row_btns)
                row_btns = []
        if row_btns:
            keyboard.append(row_btns)

    nav_specs = session_page_nav_callbacks(
        prefix,
        strategy.slug,
        session_num,
        page=page,
        total=total_count,
        per_page=MAX_EXECUTORS_SHOWN,
    )
    if nav_specs:
        keyboard.append(
            [
                InlineKeyboardButton(label, callback_data=callback)
                for label, callback in nav_specs
            ]
        )

    if prefix == "strategies":
        back_list_label = "⬅️ Overview"
    elif prefix == "agents":
        back_list_label = "⬅️ Sessions"
    else:
        back_list_label = "⬅️ Sessions"
    keyboard.append(
        [
            InlineKeyboardButton(
                back_list_label,
                callback_data=f"{prefix}:list:{strategy.slug}",
            ),
            InlineKeyboardButton("🔄 Refresh", callback_data=view_callback),
            InlineKeyboardButton("❌ Close", callback_data=f"{prefix}:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


# ============================================
# DETAIL / STOP
# ============================================


async def show_session_executor_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    executor_id: str,
    callback_prefix: str | None = None,
) -> None:
    """Show executor detail within the /sessions or /strategies flow."""
    prefix = _resolve_prefix(context, callback_prefix)
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug")
    session_num = context.user_data.get("sessions_num")
    back_callback = stored_session_view_callback(prefix, context) or f"{prefix}:menu"

    store = StrategyStore()
    strategy = resolve_strategy(store, slug) if slug else None
    if strategy is None:
        await query.answer("Session context lost", show_alert=True)
        await show_strategy_picker(update, context, callback_prefix=prefix)
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
            await show_session_executors(
                update,
                context,
                slug,
                int(session_num),
                page=stored_session_page(context),
                callback_prefix=prefix,
            )
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
        callback_prefix=prefix,
    )


async def handle_sessions_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    executor_id: str,
    callback_prefix: str | None = None,
) -> None:
    prefix = _resolve_prefix(context, callback_prefix)
    await handle_stop_executor(
        update, context, executor_id, callback_prefix=prefix
    )


async def handle_sessions_confirm_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    executor_id: str,
    callback_prefix: str | None = None,
) -> None:
    """Stop an executor using the strategy's API server, then return to session view."""
    prefix = _resolve_prefix(context, callback_prefix)
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug")
    list_callback = stored_session_view_callback(prefix, context) or f"{prefix}:menu"

    await query.answer("Stopping...")

    store = StrategyStore()
    strategy = resolve_strategy(store, slug) if slug else None
    if strategy is None:
        await query.message.edit_text(
            format_error_message("Session context lost"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
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
                        callback_data=f"{prefix}:detail:{executor_id}",
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
