import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from condor.persistence import SafePicklePersistence
from handlers import clear_all_input_states
from utils.auth import restricted
from utils.config import TELEGRAM_TOKEN, WEB_PORT, WEB_URL, is_dev_mode

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Suppress httpx INFO-level request logging — python-telegram-bot embeds the
# bot token in the request path (api.telegram.org/bot<TOKEN>/getUpdates), and
# httpx's default INFO logs the full URL on every call. With long-poll firing
# every ~10s, the token ends up in every log handler (journald, files, etc.),
# which makes safe log sharing impossible. Suppressing to WARNING preserves
# real HTTP errors while removing the token leak.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _get_start_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Build the start menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🔌 Servers", callback_data="start:config_servers"),
            InlineKeyboardButton("🔑 Keys", callback_data="start:config_keys"),
            InlineKeyboardButton("🌐 Gateway", callback_data="start:config_gateway"),
        ],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="start:admin")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="start:cancel")])
    return InlineKeyboardMarkup(keyboard)


@restricted
async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a one-time login link for the web dashboard."""
    from condor.web.auth import create_login_token

    user = update.effective_user
    token = create_login_token(user.id, user.username or "", user.first_name or "")

    url = f"{WEB_URL}/login?token={token}"
    _hostname = urlparse(WEB_URL).hostname or ""
    is_localhost = (
        "localhost" in WEB_URL or "127.0.0.1" in WEB_URL or "." not in _hostname
    )

    if is_localhost:
        await update.message.reply_text(
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._",
            parse_mode="MarkdownV2",
        )
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌐 Open Dashboard", url=url)]]
        )
        await update.message.reply_text(
            "🌐 *Web Dashboard*\n\n"
            "Tap the button below to open the dashboard\\.\n"
            "_Link valid for 5 minutes\\._",
            reply_markup=keyboard,
            parse_mode="MarkdownV2",
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display available commands (BotFather style)."""
    from config_manager import UserRole, get_config_manager
    from utils.auth import _notify_admin_new_user

    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"

    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    # Handle blocked users
    if role == UserRole.BLOCKED:
        await update.message.reply_text("Access denied.")
        return

    # Handle pending users
    if role == UserRole.PENDING:
        reply_text = f"""Access Pending

Your access request is awaiting admin approval.

Your Info:
User ID: {user_id}
Username: @{username}

You will be notified when approved."""
        await update.message.reply_text(reply_text)
        return

    # Handle new users - register as pending
    if role is None:
        is_new = cm.register_pending(user_id, username)
        if is_new:
            await _notify_admin_new_user(context, user_id, username)

        reply_text = f"""Access Request Submitted

Your request has been sent to the admin for approval.

Your Info:
User ID: {user_id}
Username: @{username}

You will be notified when approved."""
        await update.message.reply_text(reply_text)
        return

    # User is approved (USER or ADMIN role)
    clear_all_input_states(context)

    reply_text = """I can help you create and manage trading bots on any CEX or DEX using Hummingbot API servers\\.

See [this manual](https://condor.hummingbot.org/introduction) if you're new to Condor\\.

You can control me by sending these commands:

/keys \\- add exchange API keys
/portfolio \\- view balances across exchanges
/bots \\- deploy and manage trading bots
/trade \\- place CEX and DEX orders
/agent \\- AI trading assistant
/performance \\- trading agent performance stats
/web \\- open the web dashboard"""

    await update.message.reply_text(
        reply_text, parse_mode="MarkdownV2", disable_web_page_preview=True
    )


@restricted
async def start_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle callbacks from the start menu."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1] if ":" in data else data

    # Handle cancel - delete the message
    if action == "cancel":
        await query.message.delete()
        return

    # Handle navigation to config options
    if data.startswith("start:"):
        if action == "config_servers":
            from handlers import clear_all_input_states
            from handlers.config.servers import show_api_servers

            clear_all_input_states(context)
            await show_api_servers(query, context)
        elif action == "config_keys":
            from handlers import clear_all_input_states
            from handlers.config.api_keys import show_api_keys

            clear_all_input_states(context)
            await show_api_keys(query, context)
        elif action == "config_gateway":
            from handlers import clear_all_input_states
            from handlers.config.gateway import show_gateway_menu

            clear_all_input_states(context)
            context.user_data.pop("dex_state", None)
            context.user_data.pop("cex_state", None)
            await show_gateway_menu(query, context)
        elif action == "admin":
            from handlers import clear_all_input_states
            from handlers.admin import _show_admin_menu

            clear_all_input_states(context)
            await _show_admin_menu(query, context)


def reload_handlers():
    """Reload all handler modules."""
    modules_to_reload = [
        "handlers.portfolio",
        "handlers.bots",
        "handlers.bots.menu",
        "handlers.bots.controllers",
        "handlers.bots._shared",
        "handlers.executors",
        "handlers.executors.menu",
        "handlers.executors.grid",
        "handlers.executors.position",
        "handlers.executors._shared",
        "handlers.trading",
        "handlers.trading.router",
        "handlers.cex",
        "handlers.cex.menu",
        "handlers.cex.trade",
        "handlers.cex.orders",
        "handlers.cex.positions",
        "handlers.cex._shared",
        "handlers.dex",
        "handlers.dex.menu",
        "handlers.dex.swap_quote",
        "handlers.dex.swap_execute",
        "handlers.dex.swap_history",
        "handlers.dex.pools",
        "handlers.dex._shared",
        "handlers.config",
        "handlers.config.servers",
        "handlers.config.api_keys",
        "handlers.config.gateway",
        "handlers.config.user_preferences",
        "routines.base",
        "handlers.routines",
        "handlers.agents",
        "handlers.agents.menu",
        "handlers.agents.session",
        "handlers.agents.stream",
        "handlers.agents.confirmation",
        "handlers.agents._shared",
        "handlers.memory",
        "handlers.admin",
        "handlers.admin.update",
        "utils.auth",
        "utils.telegram_formatters",
        "config_manager",
    ]

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info(f"Reloaded module: {module_name}")

    from routines.base import reload_routine_modules

    reload_routine_modules()

    # Re-register fetch functions after reload (preserves in-memory cache)
    try:
        from condor.server_data_service import register_default_fetches as sds_register

        sds_register()
    except Exception as e:
        logger.warning(f"Failed to re-register SDS fetches: {e}")


def register_handlers(application: Application) -> None:
    """Register all command handlers."""
    # Import fresh versions after reload
    from handlers.admin import admin_command
    from handlers.admin.update import update_command
    from handlers.agents import (
        agent_callback_handler,
        agent_command,
        agent_voice_handler,
    )
    from handlers.agents.performance import (
        performance_callback_handler,
        performance_command,
    )
    from handlers.bots import (
        bots_callback_handler,
        bots_command,
        get_bots_document_handler,
        new_bot_command,
    )
    from handlers.cex import cex_callback_handler
    from handlers.config import get_config_callback_handler, get_modify_value_handler
    from handlers.config.api_keys import keys_command
    from handlers.config.gateway import gateway_command
    from handlers.config.servers import servers_command
    from handlers.delegations import delegations_callback_handler, delegations_command
    from handlers.dex import dex_callback_handler, lp_command
    from handlers.executors import executors_callback_handler, executors_command
    from handlers.memory import memory_callback_handler, memory_command
    from handlers.portfolio import get_portfolio_callback_handler, portfolio_command
    from handlers.routines import routines_callback_handler, routines_command
    from handlers.sessions import sessions_callback_handler, sessions_command
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler

    # Clear existing handlers
    application.handlers.clear()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("new_bot", new_bot_command))
    application.add_handler(
        CommandHandler("trade", unified_trade_command)
    )  # Unified trade (CEX + DEX)
    application.add_handler(
        CommandHandler("swap", unified_trade_command)
    )  # Alias for /trade
    application.add_handler(CommandHandler("lp", lp_command))
    application.add_handler(CommandHandler("routines", routines_command))
    application.add_handler(CommandHandler("executors", executors_command))
    application.add_handler(CommandHandler("sessions", sessions_command))
    application.add_handler(CommandHandler("agent", agent_command))
    application.add_handler(CommandHandler("performance", performance_command))
    application.add_handler(CommandHandler("delegations", delegations_command))
    application.add_handler(CommandHandler("memory", memory_command))

    # Add configuration commands (direct access)
    application.add_handler(CommandHandler("servers", servers_command))
    application.add_handler(CommandHandler("keys", keys_command))
    application.add_handler(CommandHandler("gateway", gateway_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("update", update_command))
    application.add_handler(CommandHandler("web", web_command))

    # Add callback query handler for start menu navigation
    application.add_handler(
        CallbackQueryHandler(start_callback_handler, pattern="^start:")
    )

    # Add unified trade callback handler BEFORE cex/dex handlers (for connector switching)
    application.add_handler(
        CallbackQueryHandler(unified_trade_callback_handler, pattern="^trade:")
    )

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(cex_callback_handler, pattern="^cex:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))
    application.add_handler(
        CallbackQueryHandler(bots_callback_handler, pattern="^bots:")
    )
    application.add_handler(
        CallbackQueryHandler(routines_callback_handler, pattern="^routines:")
    )
    application.add_handler(
        CallbackQueryHandler(executors_callback_handler, pattern="^executors:")
    )
    application.add_handler(
        CallbackQueryHandler(sessions_callback_handler, pattern="^sessions:")
    )

    # Add agent callback handler
    application.add_handler(
        CallbackQueryHandler(agent_callback_handler, pattern="^agent:")
    )
    application.add_handler(
        CallbackQueryHandler(performance_callback_handler, pattern="^perf:")
    )

    # Add delegations callback handler (/delegations list + view + stop)
    application.add_handler(
        CallbackQueryHandler(delegations_callback_handler, pattern="^deleg:")
    )

    # Add memory callback handler (/memory review + delete)
    application.add_handler(
        CallbackQueryHandler(memory_callback_handler, pattern="^memory:")
    )

    # Add admin callback handler
    from handlers.admin import admin_callback_handler

    application.add_handler(
        CallbackQueryHandler(admin_callback_handler, pattern="^admin:")
    )

    # Add callback query handler for portfolio settings
    application.add_handler(get_portfolio_callback_handler())

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add UNIFIED message handler for ALL text input
    # This single handler routes to: CLOB trading, DEX trading, and Config flows
    # based on context state. This avoids issues with multiple MessageHandlers
    # competing for the same filter.
    application.add_handler(get_modify_value_handler())

    # Add voice message handler for agent transcription
    application.add_handler(MessageHandler(filters.VOICE, agent_voice_handler))

    # Add document handler for file uploads (e.g., config files in /bots)
    application.add_handler(get_bots_document_handler())

    logger.info("Handlers registered successfully")


async def sync_server_permissions() -> None:
    """
    Ensure all servers in config have permission entries.
    Registers any unregistered servers with admin as owner.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    for server_name in cm.list_servers():
        cm.ensure_server_registered(server_name)

    logger.info("Synced server permissions")


async def register_bot_commands(application: Application) -> None:
    """Register the Telegram command menus (public for everyone, admin overlay).

    Extracted from ``post_init`` so it can also run on hot-reload — otherwise a
    newly added command (e.g. /delegations) gets its dispatch handler reloaded
    but never shows up in the menu until a full process restart.
    """
    from telegram import (
        BotCommand,
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
    )

    from utils.config import ADMIN_USER_ID

    # Clear any previously set commands for all scopes to avoid stale overrides
    for scope in [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ]:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    if ADMIN_USER_ID:
        try:
            await application.bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception:
            pass

    # 1) Public commands — registered by default for ALL users (default scope is
    #    the universal fallback every user resolves to unless a more specific
    #    scope overrides it). Wrapped independently so a transient failure here
    #    never blocks the admin step (or the rest of post_init) from running.
    commands = [
        BotCommand("start", "Welcome message and setup"),
        BotCommand("portfolio", "View balances across exchanges"),
        BotCommand("agent", "AI trading assistant"),
        BotCommand("performance", "Trading agent performance stats"),
        BotCommand("sessions", "Browse strategy session executors"),
        BotCommand("delegations", "Monitor background agent tasks"),
        BotCommand("memory", "Review what the assistant remembers about you"),
        BotCommand("executors", "Deploy and manage trading executors"),
        BotCommand("bots", "Deploy and manage trading bots"),
        BotCommand("new_bot", "Create bot configurations"),
        BotCommand("routines", "Run configurable Python scripts"),
        BotCommand("trade", "Place CEX and DEX orders"),
        BotCommand("lp", "Liquidity pool management"),
        BotCommand("servers", "Manage Hummingbot API servers"),
        BotCommand("keys", "Configure exchange API credentials"),
        BotCommand("gateway", "Gateway for DEX trading"),
        BotCommand("web", "Open the web dashboard"),
    ]
    # Set default + private-chat scopes. Some Telegram clients resolve the Menu
    # button from AllPrivateChats rather than falling back to Default alone.
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        try:
            await application.bot.set_my_commands(commands, scope=scope)
        except Exception as e:
            logger.warning(
                "Failed to set public commands (scope=%s): %s", scope, e, exc_info=True
            )

    # 2) Admin-only commands — layered on top of the public ones, visible only in
    #    the admin user's own command menu (chat scope overrides the default).
    if ADMIN_USER_ID:
        admin_commands = commands + [
            BotCommand("admin", "Admin panel - manage users and access"),
            BotCommand("update", "Check for updates and restart"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception as e:
            logger.warning(f"Failed to set admin-specific commands: {e}", exc_info=True)


async def _boot_web_services() -> None:
    """Start shared web/API services (safe without a Telegram Application)."""
    await sync_server_permissions()

    import asyncio

    from utils.transcribe import DEFAULT_MODEL, _get_model

    asyncio.get_event_loop().run_in_executor(None, _get_model, DEFAULT_MODEL)

    from condor.server_data_service import get_server_data_service
    from condor.server_data_service import register_default_fetches as sds_register

    sds_register()
    sds = get_server_data_service()
    sds.start()
    await sds.auto_subscribe_servers()


async def _shutdown_services() -> None:
    """Tear down agents, web sockets, API clients, and background services."""
    from handlers.agents.session import destroy_all_sessions, stop_health_monitor

    await stop_health_monitor()
    await destroy_all_sessions()

    from condor.agents.engine import get_all_engines

    for engine in list(get_all_engines().values()):
        try:
            await engine.stop()
        except Exception:
            pass

    from condor.web.ws_manager import get_ws_manager

    get_ws_manager().stop()

    from condor.server_data_service import get_server_data_service

    get_server_data_service().stop()

    from config_manager import get_config_manager

    await get_config_manager().close_all_clients()

    from mcp_servers.hummingbot_api.hummingbot_client import hummingbot_client

    await hummingbot_client.close()


async def post_init(application: Application) -> None:
    """Register bot commands after initialization."""
    from handlers import scrub_all_user_agent_llm_typing_states

    cleared = scrub_all_user_agent_llm_typing_states(application)
    if cleared:
        logger.info(
            "Cleared %d persisted agent LLM typing state(s) from user_data",
            cleared,
        )

    await _boot_web_services()

    # Register command menus (public + admin overlay)
    await register_bot_commands(application)

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs

    await restore_scheduled_jobs(application)

    # Inject Telegram bot into routine store so web-triggered routines can send messages
    from condor.routine_store import get_routine_store

    get_routine_store().set_bot(application.bot)

    # Start agent session health monitor
    from handlers.agents.session import start_health_monitor

    await start_health_monitor(application.bot)

    # Schedule periodic update checks (notifies admin)
    from handlers.admin.update import schedule_update_checks

    schedule_update_checks(application)

    # Start file watcher (dev only)
    if is_dev_mode():
        asyncio.create_task(watch_and_reload(application))
    else:
        logger.info("Auto-reload disabled (production mode)")


# ── Web server hot-reload (dev) ──

_WEB_RELOAD_SKIP = frozenset({"condor.web.ws_manager"})
_web_reload_event: asyncio.Event | None = None


def request_web_reload() -> None:
    """Signal the running web server to restart (reload route modules)."""
    if _web_reload_event is not None:
        _web_reload_event.set()


def reload_web_modules(extra_modules: list[str] | None = None) -> None:
    """Reload FastAPI route modules without touching the WS manager singleton."""
    if extra_modules:
        for name in extra_modules:
            if name in _WEB_RELOAD_SKIP:
                continue
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)
                logger.info("Reloaded module: %s", name)

    to_reload = sorted(
        (
            name
            for name in sys.modules
            if name.startswith("condor.web.") and name not in _WEB_RELOAD_SKIP
        ),
        key=lambda n: n.count("."),
        reverse=True,
    )
    for name in to_reload:
        importlib.reload(sys.modules[name])
        logger.info("Reloaded module: %s", name)


def _path_to_module(path: Path, project_root: Path) -> str | None:
    """Map a .py file path under project_root to a dotted module name."""
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts) if parts else None


class WebServerRunner:
    """Restartable uvicorn server for dev hot-reload."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self.server = None
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        import uvicorn

        from condor.web.app import create_app

        web_app = create_app()
        config = uvicorn.Config(
            web_app,
            host=self._host,
            port=self._port,
            log_level="info",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            await self.task
        self.server = None
        self.task = None

    async def restart(self, extra_modules: list[str] | None = None) -> None:
        modules = extra_modules or []
        if "condor.agents.engine" in modules:
            from condor.agents.engine import get_all_engines, stop_engine_by_id

            for engine in list(get_all_engines().values()):
                try:
                    await stop_engine_by_id(engine.agent_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to stop %s before hot-reload: %s", engine.agent_id, exc
                    )
        await self.stop()
        reload_web_modules(extra_modules)
        await self.start()


async def web_reload_loop(runner: WebServerRunner) -> None:
    """Process web reload requests from the file watcher."""
    global _web_reload_event, _pending_web_modules
    if _web_reload_event is None:
        _web_reload_event = asyncio.Event()

    while True:
        await _web_reload_event.wait()
        _web_reload_event.clear()
        modules = list(dict.fromkeys(_pending_web_modules))
        _pending_web_modules.clear()
        try:
            await runner.restart(modules or None)
            logger.info("✅ Auto-reloaded web server successfully")
        except Exception as e:
            logger.error("❌ Web server reload failed: %s", e, exc_info=True)


_pending_web_modules: list[str] = []


def queue_web_reload(extra_modules: list[str] | None = None) -> None:
    """Request a web server restart, optionally reloading shared condor modules first."""
    global _pending_web_modules
    if extra_modules:
        for name in extra_modules:
            if name not in _pending_web_modules:
                _pending_web_modules.append(name)
    request_web_reload()


def _classify_changes(
    changes: set[tuple[int, str]],
    *,
    handlers_path: Path,
    routines_path: Path,
    assistants_path: Path,
    condor_path: Path,
    condor_web_path: Path,
    main_py: Path,
    project_root: Path,
) -> tuple[bool, bool, bool, list[str]]:
    """Return (reload_assistants, reload_handlers, reload_web, extra_condor_modules)."""
    reload_assistants = False
    reload_handlers = False
    reload_web = False
    extra_modules: list[str] = []

    handlers_str = str(handlers_path.resolve())
    routines_str = str(routines_path.resolve())
    assistants_str = str(assistants_path.resolve())
    condor_str = str(condor_path.resolve())
    condor_web_str = str(condor_web_path.resolve())
    main_py_str = str(main_py.resolve())

    for _change_type, raw_path in changes:
        path = Path(raw_path).resolve()
        path_str = str(path)

        if path_str == main_py_str:
            logger.warning("main.py changed — full manual restart required")
            continue

        if assistants_str in path_str and path.suffix == ".md":
            reload_assistants = True

        if handlers_str in path_str or routines_str in path_str:
            reload_handlers = True
            continue

        if condor_web_str in path_str:
            reload_web = True
            continue

        if condor_str in path_str:
            reload_web = True
            mod = _path_to_module(path, project_root)
            if mod and mod not in _WEB_RELOAD_SKIP:
                extra_modules.append(mod)

    return reload_assistants, reload_handlers, reload_web, extra_modules


async def watch_and_reload(application: Application) -> None:
    """Watch for file changes and reload handlers or web server automatically."""
    try:
        from watchfiles import DefaultFilter, awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    project_root = Path(__file__).parent
    handlers_path = project_root / "handlers"
    routines_path = project_root / "routines"
    assistants_path = project_root / "assistants"
    condor_path = project_root / "condor"
    condor_web_path = condor_path / "web"
    main_py = Path(__file__)

    watch_paths: list[Path] = [handlers_path, routines_path, condor_path, main_py]
    if assistants_path.exists():
        watch_paths.append(assistants_path)

    logger.info(
        "👀 Watching for changes in: %s",
        ", ".join(str(p) for p in watch_paths),
    )

    class _ReloadFilter(DefaultFilter):
        """Ignore per-assistant runtime stores (FEAT-003)."""

        def __call__(self, change, path: str) -> bool:
            if f"{os.sep}store{os.sep}" in path:
                return False
            return super().__call__(change, path)

    async for changes in awatch(*watch_paths, watch_filter=_ReloadFilter()):
        logger.info("📝 Detected changes: %s", changes)
        try:
            (
                reload_assistants_flag,
                needs_handler_reload,
                needs_web_reload,
                extra_modules,
            ) = _classify_changes(
                changes,
                handlers_path=handlers_path,
                routines_path=routines_path,
                assistants_path=assistants_path,
                condor_path=condor_path,
                condor_web_path=condor_web_path,
                main_py=main_py,
                project_root=project_root,
            )

            if reload_assistants_flag:
                from handlers.agents._shared import reload_assistants

                reload_assistants()
                logger.info("✅ Auto-reloaded assistants")
            if needs_handler_reload:
                reload_handlers()
                register_handlers(application)
                await register_bot_commands(application)
                logger.info("✅ Auto-reloaded handlers successfully")

            if needs_web_reload:
                queue_web_reload(extra_modules or None)
        except Exception as e:
            logger.error("❌ Auto-reload failed: %s", e, exc_info=True)


async def watch_and_reload_web() -> None:
    """Watch condor/ for changes and hot-reload the web server (web-only dev mode)."""
    try:
        from watchfiles import DefaultFilter, awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    project_root = Path(__file__).parent
    condor_path = project_root / "condor"
    condor_web_path = condor_path / "web"
    main_py = Path(__file__)

    watch_paths: list[Path] = [condor_path, main_py]
    logger.info(
        "👀 Watching for web changes in: %s",
        ", ".join(str(p) for p in watch_paths),
    )

    async for changes in awatch(*watch_paths, watch_filter=DefaultFilter()):
        logger.info("📝 Detected web changes: %s", changes)
        try:
            _reload_assistants, _reload_handlers, needs_web_reload, extra_modules = _classify_changes(
                changes,
                handlers_path=project_root / "handlers",
                routines_path=project_root / "routines",
                assistants_path=project_root / "assistants",
                condor_path=condor_path,
                condor_web_path=condor_web_path,
                main_py=main_py,
                project_root=project_root,
            )
            if needs_web_reload:
                queue_web_reload(extra_modules or None)
        except Exception as e:
            logger.error("❌ Web auto-reload failed: %s", e, exc_info=True)


def get_persistence() -> SafePicklePersistence:
    """
    Build a persistence object that works both locally and in Docker.
    - Uses an env var override if provided.
    - Defaults to <project_root>/data/condor_bot_data.pickle.
    - Ensures the parent directory exists, but does NOT create the file.
    - Uses SafePicklePersistence for atomic writes, backup recovery,
      and ephemeral key filtering.
    """
    base_dir = Path(__file__).parent
    default_path = base_dir / "data" / "condor_bot_data.pickle"

    persistence_path = Path(os.getenv("CONDOR_PERSISTENCE_FILE", default_path))

    # Make sure the directory exists; the file will be created by PTB
    persistence_path.parent.mkdir(parents=True, exist_ok=True)

    return SafePicklePersistence(filepath=persistence_path, update_interval=10)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors gracefully."""
    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error (will retry): {context.error}")
        return

    logger.exception("Exception while handling an update:", exc_info=context.error)


async def send_to_telegram(
    self, chat_id: int, message: str, parse_mode: str = "Markdown"
):
    """Sends a message to a specific Telegram chat."""
    await self.bot.send_message(chat_id=chat_id, text=message, parse_mode=parse_mode)


async def send_to_all(self, message: str, parse_mode: str = "Markdown"):
    """Sends a message to all users who have started the bot."""
    for chat_id in self.user_data:
        try:
            await self.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=parse_mode
            )
        except Exception as e:
            logger.warning(f"Failed to send message to chat {chat_id}: {e}")


def main() -> None:
    """Run the bot."""
    if os.environ.get("CONDOR_WEB_ONLY"):
        asyncio.run(_run_web_only())
        return

    # Reap any ACP/MCP subprocess trees orphaned by a prior hard kill (kill -9,
    # OOM, power loss) before we spawn our own — those bypass post_shutdown.
    try:
        from condor.acp.client import reap_stale_acp_trees

        reaped = reap_stale_acp_trees(TELEGRAM_TOKEN)
        if reaped:
            logger.info("Reaped %d stale ACP/MCP process(es) from a prior run", reaped)
    except Exception:
        logger.exception("Startup ACP reaper failed (continuing)")

    # Setup persistence to save user data, chat data, and bot data
    # This will save trading context, last used parameters, etc.
    persistence = get_persistence()

    async def post_shutdown(application: Application) -> None:
        """Clean up agent subprocesses on shutdown."""
        await _shutdown_services()

    # Create the Application with persistence enabled
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )

    # Register all handlers
    register_handlers(application)

    # Register error handler
    application.add_error_handler(error_handler)

    # Run TG bot + web server concurrently in a manual event loop
    asyncio.run(_run_dual(application))


async def _run_dual(application: Application) -> None:
    """Run the Telegram bot and FastAPI web server concurrently."""
    import signal

    from condor.web.ws_manager import get_ws_manager

    # Initialize and start the Telegram application.
    # NOTE: Application.initialize() does *not* call post_init (only run_polling/run_webhook do).
    # We drive startup manually because uvicorn runs alongside polling, so we must invoke
    # post_init ourselves — that is where BotCommand menu sync and other boot hooks run.
    await application.initialize()

    global _web_reload_event
    reload_task: asyncio.Task | None = None
    if is_dev_mode():
        _web_reload_event = asyncio.Event()

    web_runner = WebServerRunner(host="0.0.0.0", port=WEB_PORT)
    await web_runner.start()
    if is_dev_mode():
        reload_task = asyncio.create_task(web_reload_loop(web_runner))

    if application.post_init:
        await application.post_init(application)

    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await application.start()

    # Start WebSocket manager
    get_ws_manager().start()

    # Notify admin that Condor has started
    from utils.config import ADMIN_USER_ID

    if ADMIN_USER_ID:
        try:
            await application.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text="Condor is online and ready.",
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notification to admin: {e}")

    if is_dev_mode():
        logger.info(
            "Starting Condor (dev): Telegram bot + API on port %s — UI at %s",
            WEB_PORT,
            WEB_URL,
        )
    else:
        logger.info(
            "Starting Condor: Telegram bot + web dashboard on port %s", WEB_PORT
        )

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def _signal_handler():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Wait until shutdown signal
    await shutdown_event.wait()

    logger.info("Shutting down...")
    if reload_task is not None:
        reload_task.cancel()
        try:
            await reload_task
        except asyncio.CancelledError:
            pass
    await web_runner.stop()

    await application.updater.stop()
    await application.stop()
    if application.post_stop:
        await application.post_stop(application)
    await application.shutdown()
    if application.post_shutdown:
        await application.post_shutdown(application)


async def _run_web_only() -> None:
    """Run FastAPI + background services without Telegram polling (dev worktree)."""
    import signal

    from condor.web.ws_manager import get_ws_manager

    global _web_reload_event
    _web_reload_event = asyncio.Event()

    web_runner = WebServerRunner(host="0.0.0.0", port=WEB_PORT)
    await web_runner.start()
    reload_task = asyncio.create_task(web_reload_loop(web_runner))
    watcher_task = asyncio.create_task(watch_and_reload_web())

    await _boot_web_services()
    get_ws_manager().start()

    logger.info(
        "Starting Condor (web-only dev): API on port %s — UI at %s",
        WEB_PORT,
        WEB_URL,
    )

    shutdown_event = asyncio.Event()

    def _signal_handler():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await shutdown_event.wait()

    logger.info("Shutting down...")
    watcher_task.cancel()
    reload_task.cancel()
    for task in (watcher_task, reload_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await web_runner.stop()
    await _shutdown_services()


if __name__ == "__main__":
    # Add custom methods to the application object
    Application.send_to_telegram = send_to_telegram
    Application.send_to_all = send_to_all
    main()
