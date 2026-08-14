"""Telegram /strategies menus — deterministic catalog overview + session browse."""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config_manager import get_client
from condor.agents.performance import fetch_agent_performance
from condor.fetchers.executors import stop_executor
from condor.strategy_runners.catalog import get_strategy, list_strategies
from condor.strategy_runners.macdbb.sessions import list_session_dirs
from handlers.executors.menu import (
    _render_executor_detail,
    handle_stop_executor,
)
from handlers.sessions._shared import (
    MAX_EXECUTORS_SHOWN,
    executor_row_button_text,
    format_session_executors_table,
    session_page_nav_callbacks,
    session_view_callback,
    sort_session_executors,
    stored_session_page,
    stored_session_view_callback,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ._shared import (
    CALLBACK_PREFIX,
    clear_strategies_state,
    deterministic_session_agent_id,
    fetch_deterministic_performance,
    format_pnl_plain,
    format_strategy_overview_lines,
    session_pnl_by_number,
)

logger = logging.getLogger(__name__)

SESSIONS_PER_PAGE = 10


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


async def _resolve_client(strat, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Strategy server_name first, else user's default server."""
    from condor.web.routes.strategies import _get_client, _merged_default_config

    defaults = _merged_default_config(strat)
    server_name = str(defaults.get("server_name") or "")
    client = await _get_client(server_name)
    if client is not None:
        return client, server_name or "default"

    client = await get_client(chat_id, context=context)
    if client is not None:
        from handlers.config.user_preferences import get_active_server

        server_name = get_active_server(context.user_data) or ""
    return client, server_name or "default"


def _session_is_live(strat, session_num: int) -> bool:
    from condor.web.routes.strategies import _running_for

    agent_id = deterministic_session_agent_id(strat.key, session_num)
    return any(getattr(e, "agent_id", None) == agent_id for e in _running_for(strat.slug))


# ============================================
# STRATEGY PICKER
# ============================================


async def show_strategy_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List deterministic catalog strategies."""
    prefix = CALLBACK_PREFIX
    strategies = list_strategies()
    query = update.callback_query
    msg = update.message or (query.message if query else None)
    if not msg:
        return

    if not strategies:
        text = "No deterministic strategies found\\."
        if query:
            await query.message.edit_text(text, parse_mode="MarkdownV2")
        else:
            await msg.reply_text(text, parse_mode="MarkdownV2")
        return

    from condor.web.routes.strategies import _summary_for

    buttons = []
    for s in strategies[:12]:
        label = s.name or s.slug
        try:
            summary = _summary_for(s)
            status = summary.status or "idle"
            if status in ("running", "paused", "orphaned"):
                label = f"● {label}"
        except Exception:
            pass
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"{prefix}:list:{s.slug}")]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Close", callback_data=f"{prefix}:close")]
    )

    text = "📊 *Strategies*\nSelect a deterministic strategy:"
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
# OVERVIEW + SESSION LIST
# ============================================


async def show_session_list_with_overview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    page: int = 0,
) -> None:
    """List sessions for a deterministic strategy with UI-parity overview."""
    prefix = CALLBACK_PREFIX
    strat = get_strategy(slug)
    if strat is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
            ),
        )
        return

    context.user_data["sessions_slug"] = strat.slug
    context.user_data["sessions_run_key"] = strat.key
    context.user_data["sessions_callback_prefix"] = prefix
    context.user_data["strategies_slug"] = strat.slug
    context.user_data["strategies_run_key"] = strat.key

    summary = None
    try:
        from condor.web.routes.strategies import _summary_for

        summary = _summary_for(strat)
    except Exception as e:
        logger.warning("deterministic summary failed for %s: %s", slug, e)

    perf_payload: dict[str, Any] = {"sessions": [], "totals": {}}
    try:
        perf_payload = await fetch_deterministic_performance(strat)
    except Exception as e:
        logger.warning("deterministic performance failed for %s: %s", slug, e)

    sessions_perf = list(perf_payload.get("sessions") or [])
    totals = dict(perf_payload.get("totals") or {})
    pnl_by_session = session_pnl_by_number(sessions_perf)

    disk_dirs = list_session_dirs(strat.data_slug)
    session_nums = []
    for path in disk_dirs:
        try:
            session_nums.append(int(path.name.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    session_nums.sort(reverse=True)

    status = "idle"
    name = strat.name or strat.slug
    if summary is not None:
        status = summary.status or status
        name = summary.name or name

    total_pnl = float(totals.get("total_pnl", 0.0))
    realized = float(totals.get("realized_pnl", 0.0))
    unrealized = float(totals.get("unrealized_pnl", 0.0))
    volume = float(totals.get("volume", 0.0))
    fees = float(totals.get("fees", 0.0))
    open_positions = int(totals.get("open_positions", 0))
    last_session_pnl = 0.0
    if pnl_by_session:
        last_session_pnl = pnl_by_session[max(pnl_by_session)]

    lines = format_strategy_overview_lines(
        slug=strat.slug,
        name=name,
        status=status,
        total_pnl=total_pnl,
        last_session_pnl=last_session_pnl,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        volume=volume,
        open_positions=open_positions,
        session_count=len(session_nums),
        fees=fees,
    )
    lines.append("")

    total = len(session_nums)
    start = page * SESSIONS_PER_PAGE
    page_items = session_nums[start : start + SESSIONS_PER_PAGE]

    if not page_items:
        lines.append("_No sessions found\\._")
    else:
        lines.append(f"_{total} session\\(s\\)\\. Select one:_")

    keyboard = []
    for num in page_items:
        live = _session_is_live(strat, num)
        label = f"{'● ' if live else ''}session_{num}"
        if num in pnl_by_session:
            label = f"{label} · {format_pnl_plain(pnl_by_session[num])}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"{prefix}:view:{strat.slug}:{num}"
                )
            ]
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"{prefix}:list:{strat.slug}:{page - 1}"
            )
        )
    if start + SESSIONS_PER_PAGE < total:
        nav.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"{prefix}:list:{strat.slug}:{page + 1}"
            )
        )
    if nav:
        keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Strategies", callback_data=f"{prefix}:menu"),
            InlineKeyboardButton(
                "🔄 Refresh", callback_data=f"{prefix}:list:{strat.slug}:{page}"
            ),
            InlineKeyboardButton("❌ Close", callback_data=f"{prefix}:close"),
        ]
    )

    await _edit_or_reply(
        update, context, "\n".join(lines), InlineKeyboardMarkup(keyboard)
    )


# ============================================
# SESSION EXECUTORS
# ============================================


async def show_session_view(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    slug: str,
    session_num: int,
    page: int = 0,
) -> None:
    """Show session-scoped executors for a deterministic strategy."""
    prefix = CALLBACK_PREFIX
    strat = get_strategy(slug)
    chat_id = update.effective_chat.id
    view_callback = session_view_callback(prefix, slug, session_num, page)

    if strat is None:
        await _edit_or_reply(
            update,
            context,
            format_error_message(f"Strategy not found: {slug}"),
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
            ),
        )
        return

    agent_id = deterministic_session_agent_id(strat.key, session_num)
    client, server_name = await _resolve_client(strat, chat_id, context)

    context.user_data["sessions_slug"] = strat.slug
    context.user_data["sessions_num"] = session_num
    context.user_data["sessions_page"] = page
    context.user_data["sessions_run_key"] = strat.key
    context.user_data["sessions_server_name"] = server_name
    context.user_data["sessions_callback_prefix"] = prefix
    context.user_data["strategies_slug"] = strat.slug
    context.user_data["strategies_run_key"] = strat.key

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
                            callback_data=f"{prefix}:list:{strat.slug}",
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
                            callback_data=f"{prefix}:list:{strat.slug}",
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
    view_callback = session_view_callback(prefix, strat.slug, session_num, page)
    context.user_data["sessions_executors"] = rows
    context.user_data["sessions_perf"] = {
        "total_pnl": perf.total_pnl,
        "volume": perf.volume,
        "fees": perf.fees,
        "open_count": perf.open_count,
        "trade_count": perf.trade_count,
    }

    live = _session_is_live(strat, session_num)
    status_label = "live" if live else "closed"
    lines = [
        f"📂 *Session* `{escape_markdown_v2(strat.slug)}` "
        f"`session\\_{session_num}`",
        f"_{escape_markdown_v2(server_name)}_ \\| "
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

    keyboard: list[list[InlineKeyboardButton]] = []
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
        strat.slug,
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

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Overview",
                callback_data=f"{prefix}:list:{strat.slug}",
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


async def show_executor_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    prefix = CALLBACK_PREFIX
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug") or context.user_data.get(
        "strategies_slug"
    )
    session_num = context.user_data.get("sessions_num")
    back_callback = stored_session_view_callback(prefix, context) or f"{prefix}:menu"

    strat = get_strategy(slug) if slug else None
    if strat is None:
        await query.answer("Session context lost", show_alert=True)
        await show_strategy_picker(update, context)
        return

    client, server_name = await _resolve_client(strat, chat_id, context)
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
            await show_session_view(
                update,
                context,
                slug,
                int(session_num),
                page=stored_session_page(context),
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


async def handle_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    await handle_stop_executor(
        update, context, executor_id, callback_prefix=CALLBACK_PREFIX
    )


async def handle_confirm_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    prefix = CALLBACK_PREFIX
    query = update.callback_query
    chat_id = update.effective_chat.id
    slug = context.user_data.get("sessions_slug") or context.user_data.get(
        "strategies_slug"
    )
    list_callback = stored_session_view_callback(prefix, context) or f"{prefix}:menu"

    await query.answer("Stopping...")

    strat = get_strategy(slug) if slug else None
    if strat is None:
        await query.message.edit_text(
            format_error_message("Session context lost"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data=f"{prefix}:menu")]]
            ),
        )
        return

    client, _ = await _resolve_client(strat, chat_id, context)
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
        logger.error("Error stopping strategy executor: %s", e, exc_info=True)
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data=list_callback)]]
            ),
        )


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    clear_strategies_state(context)
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning("Could not delete strategies message: %s", e)
