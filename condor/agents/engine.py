"""TickEngine -- main orchestrator for autonomous trading agents.

One TickEngine instance per running agent.  Each tick:
1. Pre-compute core data providers (active executors)
2. Read journal (learnings + summary + recent decisions)
3. Build prompt with strategy + data + risk state
4. Spawn a fresh ACP session, stream events, capture tool calls
5. Save full snapshot and update journal
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from condor.acp.client import (
    ACPClient,
    Heartbeat,
    PromptDone,
    TextChunk,
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
    resolve_acp,
)
from condor.acp.cursor_sdk_client import CursorSdkClient, is_cursor_sdk_model
from condor.acp.pydantic_ai_client import PydanticAIClient, is_pydantic_ai_model

from condor.agents.performance import is_running_status

from .agent import Agent
from .journal import JournalManager, next_experiment_number, next_session_number
from .prompts import build_tick_prompt
from .providers import ProviderRegistry
from .risk import RiskEngine, RiskLimits, RiskState, auto_approve_with_risk_check, resolve_risk_limits
from .strategy import Strategy

log = logging.getLogger(__name__)


_TRIPLE_BARRIER_CLOSE_TYPES = frozenset({"STOP_LOSS", "TAKE_PROFIT"})


def _normalize_close_type(close_type: str) -> str:
    return (close_type or "").upper().replace(" ", "_").replace("-", "_")


def _is_barrier_close_type(close_type: str) -> bool:
    """True only for triple-barrier SL/TP — not EARLY_STOP, manual, or agent stop."""
    return _normalize_close_type(close_type) in _TRIPLE_BARRIER_CLOSE_TYPES


def _running_executor_ids(all_executors: list[dict[str, Any]]) -> set[str]:
    return {
        str(e["id"])
        for e in all_executors
        if e.get("id") and is_running_status(str(e.get("status") or ""))
    }


async def _fetch_running_executor_ids(client: Any, agent_id: str) -> set[str]:
    """Return RUNNING executor ids for barrier-close tracking at end of tick."""
    from condor.agents.performance import fetch_agent_performance

    perf = await fetch_agent_performance(client, agent_id)
    return _running_executor_ids(perf.executors)


def _parse_tool_call_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            import json

            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _executor_id_from_tool_payload(payload: dict[str, Any]) -> str | None:
    for key in ("executor_id", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _extract_agent_created_executor_ids(tool_calls: list[dict[str, Any]]) -> set[str]:
    """Executor IDs created this tick (manage_executors action=create)."""
    created: set[str] = set()
    for tc in tool_calls:
        name = (tc.get("name") or "").lower()
        if "manage_executors" not in name:
            continue
        inp = _parse_tool_call_payload(tc.get("input"))
        if not inp or str(inp.get("action") or "").lower() != "create":
            continue
        out = _parse_tool_call_payload(tc.get("output"))
        if out:
            eid = _executor_id_from_tool_payload(out)
            if eid:
                created.add(eid)
    return created


def _extract_agent_closed_executor_ids(tool_calls: list[dict[str, Any]]) -> set[str]:
    """Executor IDs the agent stopped this tick (manage_executors action=stop)."""
    closed: set[str] = set()
    for tc in tool_calls:
        name = (tc.get("name") or "").lower()
        if "manage_executors" not in name:
            continue
        inp = _parse_tool_call_payload(tc.get("input"))
        if not inp:
            continue
        action = str(inp.get("action") or "").lower()
        if action not in ("stop", "close"):
            continue
        eid = inp.get("executor_id") or inp.get("id")
        if eid:
            closed.add(str(eid))
    return closed


def _detect_barrier_closes(
    all_executors: list[dict[str, Any]],
    last_running_ids: set[str],
    already_notified: set[str],
    agent_closed_ids: set[str],
) -> list[dict[str, Any]]:
    """Find executors that were RUNNING last tick and closed via SL/TP since then."""
    if not last_running_ids:
        return []

    running_ids = {
        e["id"]
        for e in all_executors
        if e.get("id") and is_running_status(str(e.get("status") or ""))
    }
    by_id = {e["id"]: e for e in all_executors if e.get("id")}

    closes: list[dict[str, Any]] = []
    for eid in last_running_ids:
        if eid in running_ids or eid in already_notified or eid in agent_closed_ids:
            continue
        ex = by_id.get(eid)
        if not ex or is_running_status(str(ex.get("status") or "")):
            continue
        if _is_barrier_close_type(str(ex.get("close_type") or "")):
            closes.append(ex)
    return closes


def _resolve_sl_cooldown_ticks(strategy_slug: str, config: dict[str, Any]) -> int:
    raw = config.get("strategy_params")
    if not isinstance(raw, dict):
        return 0
    from condor.agents.strategy_configs import resolve_effective_strategy_params

    frequency_sec = int(config.get("frequency_sec") or 60)
    params = resolve_effective_strategy_params(strategy_slug, raw, frequency_sec)
    return max(0, int(params.get("sl_symbol_cooldown_ticks") or 0))


def _register_sl_cooldowns(
    cooldowns: dict[str, int],
    closes: list[dict[str, Any]],
    *,
    current_tick: int,
    cooldown_ticks: int,
) -> None:
    if cooldown_ticks <= 0:
        return
    until = current_tick + cooldown_ticks
    for ex in closes:
        if _normalize_close_type(str(ex.get("close_type") or "")) != "STOP_LOSS":
            continue
        pair = str(ex.get("pair") or "").strip()
        if pair:
            cooldowns[pair] = max(cooldowns.get(pair, 0), until)


def _active_sl_cooldowns(
    cooldowns: dict[str, int], current_tick: int
) -> dict[str, int]:
    """Return pair -> remaining agent ticks for active SL cooldowns."""
    return {
        pair: until - current_tick
        for pair, until in cooldowns.items()
        if until > current_tick
    }


def _format_sl_cooldown_section(active: dict[str, int]) -> str:
    if not active:
        return ""
    lines = [
        "[SL SYMBOL COOLDOWN — engine enforced]",
        "STOP_LOSS barrier close — do not open these pairs until cooldown expires. "
        'Condor blocks manage_executors(action="create") for listed pairs:',
    ]
    for pair in sorted(active):
        lines.append(f"- {pair}: {active[pair]} agent tick(s) remaining")
    return "\n".join(lines)


def _format_barrier_closes_section(closes: list[dict[str, Any]]) -> str:
    if not closes:
        return ""
    lines = [
        "[BARRIER CLOSES SINCE LAST TICK]",
        "Triple-barrier STOP_LOSS or TAKE_PROFIT only (not EARLY_STOP / agent exits). "
        "One send_notification per row — do not duplicate for the same executor_id:",
    ]
    for ex in closes:
        close_type = ex.get("close_type") or "UNKNOWN"
        pnl = float(ex.get("pnl") or 0)
        lines.append(
            f"- {ex.get('pair', '?')} {ex.get('side', '')} | {close_type} | "
            f"PnL ${pnl:+.2f} | id={ex.get('id', '')}"
        )
    return "\n".join(lines)


async def _notify_via_telegram_bot_api(chat_id: int, text: str) -> None:
    """Send plain text using TELEGRAM_TOKEN when no python-telegram-bot handle exists."""
    from condor.telegram_notify import prepare_agent_notification_text
    from utils.config import TELEGRAM_TOKEN

    if not TELEGRAM_TOKEN or not chat_id:
        return
    payload_text = prepare_agent_notification_text(text or "", max_chars=4090)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": payload_text}
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    log.warning(
                        "Telegram notify failed for chat_id=%s: %s",
                        chat_id,
                        data.get("description", data),
                    )
    except Exception:
        log.exception("Telegram notify HTTP failed for chat_id=%s", chat_id)


# Module-level registry of running engines
_engines: dict[str, "TickEngine"] = {}
_lifecycle_locks: dict[str, asyncio.Lock] = {}


def _lifecycle_lock(agent_id: str) -> asyncio.Lock:
    lock = _lifecycle_locks.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        _lifecycle_locks[agent_id] = lock
    return lock


class EngineAlreadyRunningError(RuntimeError):
    """Raised when a lifecycle start/resume races an active engine."""


async def start_engine(engine: "TickEngine") -> None:
    """Start an engine under per-agent lock; stop any stale registry entry first."""
    async with _lifecycle_lock(engine.agent_id):
        existing = get_engine(engine.agent_id)
        if existing is not None and existing is not engine:
            if existing.is_running or existing.is_active:
                raise EngineAlreadyRunningError(
                    f"Agent '{engine.agent_id}' is already running"
                )
            await existing.stop()
        await engine.start()


async def stop_engine_by_id(agent_id: str) -> bool:
    """Stop a registered engine under per-agent lock."""
    async with _lifecycle_lock(agent_id):
        engine = get_engine(agent_id)
        if not engine:
            return False
        await engine.stop()
        return True


class _NullTracker:
    """Stub tracker for experiments (no journal)."""

    def get_total_exposure(self) -> float:
        return 0.0

    def get_open_executor_count(self) -> int:
        return 0

    def get_drawdown_pct(self) -> float:
        return 0.0


def get_engine(agent_id: str) -> TickEngine | None:
    return _engines.get(agent_id)


def get_all_engines() -> dict[str, "TickEngine"]:
    return dict(_engines)


@dataclass
class TickEngine:
    agent: Agent  # owning Agent: identity + shared brain (memory/skills)
    strategy: Strategy  # the playbook this run loops (tactics + config)
    config: dict[str, Any]
    chat_id: int
    user_id: int
    resume_session_num: int | None = field(default=None)

    # Derived identity (set in __post_init__)
    agent_id: str = field(init=False)
    session_num: int = field(init=False)
    is_experiment: bool = field(default=False, init=False)

    # Components (created in __post_init__)
    journal: JournalManager = field(init=False)
    risk: RiskEngine = field(init=False)
    provider_registry: ProviderRegistry = field(init=False)
    session_dir: "Path | None" = field(default=None, init=False)

    # Runtime state
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _shutting_down: bool = field(default=False, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_skill_data: dict[str, Any] = field(default_factory=dict, init=False)
    _pending_directives: list[str] = field(default_factory=list, init=False)
    _cached_routines_section: str | None = field(default=None, init=False, repr=False)
    _last_running_executor_ids: set[str] = field(default_factory=set, init=False)
    _live_risk_state: RiskState | None = field(default=None, init=False)
    _notified_barrier_close_ids: set[str] = field(default_factory=set, init=False)
    _agent_closed_executor_ids: set[str] = field(default_factory=set, init=False)
    _sl_cooldown_until_tick: dict[str, int] = field(default_factory=dict, init=False)
    _executing_tick: int = field(default=0, init=False)
    # The live per-tick ACP client, held so stop() can reap it if the tick's own
    # finally is skipped (e.g. cancelled mid-await). None between ticks.
    _active_client: "ACPClient | PydanticAIClient | CursorSdkClient | None" = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self):
        # The journal/sessions/learnings hang off the *strategy* dir (one level
        # below the Agent), so each playbook keeps its own operational history
        # while the Agent's brain (memory/skills) stays shared at the parent.
        strategy_dir = self.strategy.dir
        mode = self.config.get("execution_mode", "loop")
        self.is_experiment = mode in ("dry_run", "run_once")

        # agent_id == controller_id tag: "{agent_slug}.{strategy_slug}_{N}" (and
        # "..._e{N}" for experiments). The dot separates the two slugs cleanly —
        # slugs never contain a dot.
        run_key = f"{self.agent.slug}.{self.strategy.slug}"
        if self.is_experiment:
            self.session_num = next_experiment_number(strategy_dir)
            self.agent_id = f"{run_key}_e{self.session_num}"
            # Experiments: flat folder, no session dir or journal
            self.session_dir = None
            self.journal = None
        else:
            if self.resume_session_num is not None:
                self.session_num = self.resume_session_num
                self.agent_id = f"{run_key}_{self.session_num}"
                self.session_dir = strategy_dir / "sessions" / f"session_{self.session_num}"
                if not self.session_dir.is_dir():
                    raise FileNotFoundError(
                        f"Session {self.session_num} not found for {run_key}"
                    )
            else:
                self.session_num = next_session_number(strategy_dir)
                self.agent_id = f"{run_key}_{self.session_num}"
                self.session_dir = strategy_dir / "sessions" / f"session_{self.session_num}"
                self.session_dir.mkdir(parents=True, exist_ok=True)

                from .config import save_full_config

                save_full_config(self.session_dir, self.config)

            self.journal = JournalManager(
                self.agent_id,
                strategy_name=self.strategy.name,
                strategy_description=self.strategy.description,
                session_dir=self.session_dir,
                agent_dir=strategy_dir,
            )

        self.risk = RiskEngine(resolve_risk_limits(self.config))
        self.provider_registry = ProviderRegistry()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bot=None) -> None:
        """Start the tick loop as an asyncio task."""
        if self._running:
            return
        self._running = True
        self._bot = bot
        self._task = asyncio.create_task(self._loop())
        _engines[self.agent_id] = self
        log.info(
            "TickEngine %s started (freq=%ss)",
            self.agent_id,
            self.config.get("frequency_sec", 60),
        )

    async def stop(self) -> None:
        """Stop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Backstop: if the tick was cancelled mid-await, its own finally may not
        # have reaped the ACP subprocess. stop() is idempotent, so a double call
        # after a clean tick is a harmless no-op.
        client = self._active_client
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception(
                    "TickEngine %s: error reaping active client", self.agent_id
                )
            self._active_client = None
        if self.journal:
            try:
                self.journal.mark_stopped()
            except Exception:
                log.warning("TickEngine %s: failed to write stopped summary", self.agent_id)
            self.journal.close()
        _engines.pop(self.agent_id, None)
        log.info("TickEngine %s stopped", self.agent_id)

    async def _run_shutdown(self, reason: str) -> None:
        """Emergency winddown of this session's positions/executors, then self-stop.

        This is the escalation above the plain graceful :meth:`stop` (which keeps
        positions): it runs the deterministic + LLM winddown in
        :func:`condor.agents.shutdown.run_shutdown` and always ends stopped.

        Idempotent and re-entrancy-safe via ``_shutting_down`` (a concurrent auto
        trigger + manual call runs the winddown at most once). Safe from inside the
        tick task (hard auto-trigger) or outside it (manual stop): it cancels the
        in-flight tick only when called from a *different* task — cancelling our own
        task would abort the winddown.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        # Halt the loop so no next/concurrent tick fights the winddown.
        self._running = False
        self._paused = True

        current = asyncio.current_task()
        if (
            self._task is not None
            and self._task is not current
            and not self._task.done()
        ):
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Reap any live per-tick client (mirrors stop()'s backstop).
        client = self._active_client
        if client is not None:
            try:
                await client.stop()
            except Exception:
                log.exception(
                    "TickEngine %s: error reaping active client during shutdown",
                    self.agent_id,
                )
            self._active_client = None

        from .shutdown import run_shutdown

        try:
            await run_shutdown(self, reason)
        except Exception:
            log.exception("TickEngine %s: shutdown sequence error", self.agent_id)
            await self._notify(
                f"🚨 Agent {self.agent_id}: shutdown sequence errored — "
                f"verify positions manually! ({reason})"
            )
        finally:
            if self.journal:
                self.journal.close()
            _engines.pop(self.agent_id, None)
            log.info("TickEngine %s shut down (%s)", self.agent_id, reason)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def inject_directive(self, text: str) -> None:
        """Queue a user directive to be included in the next tick's prompt."""
        self._pending_directives.append(text)
        log.info("TickEngine %s: directive queued: %s", self.agent_id, text[:80])

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @property
    def is_active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def status(self) -> str:
        if not self._running:
            return "stopped"
        if self._paused:
            return "paused"
        return "running"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        freq = self.config.get("frequency_sec", 60)
        mode = self.config.get("execution_mode", "loop")
        while self._running:
            if not self._paused:
                try:
                    await self._tick()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    log.exception("TickEngine %s tick error", self.agent_id)
                    if self.journal:
                        self.journal.append_error(str(e))
                    await self._notify(f"Agent {self.agent_id} tick error: {e}")

                # Single-tick modes: stop after first tick
                if mode in ("dry_run", "run_once"):
                    label = "Dry run" if mode == "dry_run" else "Run-once"
                    log.info(
                        "TickEngine %s: %s complete, self-stopping",
                        self.agent_id,
                        label,
                    )
                    await self._notify(f"Agent {self.agent_id}: {label} complete.")
                    self._running = False
                    _engines.pop(self.agent_id, None)
                    return

                # max_ticks limit (loop mode only)
                max_ticks = self.config.get("max_ticks", 0)
                if max_ticks > 0 and self.journal.tick_count >= max_ticks:
                    log.info(
                        "TickEngine %s: reached max_ticks=%d, self-stopping",
                        self.agent_id,
                        max_ticks,
                    )
                    await self._notify(
                        f"Agent {self.agent_id}: completed {max_ticks} ticks (max_ticks limit)."
                    )
                    self._running = False
                    self.journal.close()
                    _engines.pop(self.agent_id, None)
                    return

            try:
                await asyncio.sleep(freq)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        self._last_tick_at = time.time()
        mode = self.config.get("execution_mode", "loop")

        # 1. Get API client
        client = await self._get_client()
        if not client:
            if self.journal:
                self.journal.append_error("No API client available")
            return

        # 2. Run core data providers (executors only -- agent uses MCP for market data)
        skill_results = await self.provider_registry.run_core_providers(
            client, self.config, agent_id=self.agent_id
        )

        # Extract structured data from providers for tracking
        executors_result = skill_results.get("executors")
        if executors_result:
            self._last_skill_data = executors_result.data
        positions_result = skill_results.get("positions")
        if positions_result:
            self._last_skill_data["positions"] = positions_result.data

        # Convert provider results to summary strings
        core_data_summaries: dict[str, str] = {
            name: result.summary for name, result in skill_results.items()
        }

        running_executors: list[dict[str, Any]] = []
        if executors_result:
            running_executors = executors_result.data.get("executors") or []

        if executors_result and not self.is_experiment:
            from condor.position_reconcile import audit_position_reconcile

            max_open = int(
                self.config.get("risk_limits", {}).get("max_open_executors")
                or self.risk.limits.max_open_executors
            )
            try:
                reconcile_report = await audit_position_reconcile(
                    client,
                    agent_id=self.agent_id,
                    running_executors=running_executors,
                    all_executors=executors_result.data.get("all_executors") or [],
                    tick_num=self.journal.tick_count + 1 if self.journal else 0,
                    max_open_executors=max_open,
                )
                core_data_summaries["position_reconcile"] = reconcile_report.get(
                    "summary", ""
                )
            except Exception:
                log.warning(
                    "TickEngine %s: position reconcile failed",
                    self.agent_id,
                    exc_info=True,
                )

        barrier_closes_section = ""
        barrier_closes: list[dict[str, Any]] = []
        if executors_result and not self.is_experiment:
            all_executors = executors_result.data.get("all_executors") or []
            barrier_closes = _detect_barrier_closes(
                all_executors,
                self._last_running_executor_ids,
                self._notified_barrier_close_ids,
                self._agent_closed_executor_ids,
            )
            barrier_closes_section = _format_barrier_closes_section(barrier_closes)
            for ex in barrier_closes:
                eid = ex.get("id")
                if eid:
                    self._notified_barrier_close_ids.add(eid)
            # Keep last_running until post-tick refresh — do not overwrite here.
            # Barrier detection compares end-of-previous-tick RUNNING ids to this fetch.

        # 3. Read journal context (sessions only)
        learnings = self.journal.read_learnings() if self.journal else ""
        next_tick = self.journal.tick_count + 1 if self.journal else 1
        self._executing_tick = next_tick
        if not self.is_experiment and barrier_closes:
            _register_sl_cooldowns(
                self._sl_cooldown_until_tick,
                barrier_closes,
                current_tick=next_tick,
                cooldown_ticks=_resolve_sl_cooldown_ticks(self.strategy.slug, self.config),
            )
        sl_cooldown_section = _format_sl_cooldown_section(
            _active_sl_cooldowns(self._sl_cooldown_until_tick, next_tick)
        )
        digest_interval = int(self.config.get("digest_interval_ticks", 0) or 0)
        is_digest_boundary = digest_interval > 0 and next_tick % digest_interval == 0
        recent_count = digest_interval if is_digest_boundary else 3
        recent_decisions = (
            self.journal.get_recent_decisions(count=recent_count) if self.journal else ""
        )
        summary = self.journal.read_summary() if self.journal else ""

        # 4. Get risk state (experiments pass None — returns clean state)
        risk_state = self.risk.get_state(self.journal or _NullTracker())
        if executors_result:
            risk_state.executor_count = len(running_executors)
            risk_state.total_exposure = float(
                executors_result.data.get("total_exposure") or 0
            )
        self._live_risk_state = risk_state

        # Hard kill-switch: escalate to an emergency winddown before the soft
        # pause below. Experiments never trade for real, so they never shut down.
        if risk_state.should_shutdown and not self.is_experiment:
            await self._run_shutdown(reason=risk_state.shutdown_reason)
            return

        if risk_state.is_blocked and not self.is_experiment:
            self.journal.append_action(
                self.journal.tick_count + 1,
                "tick_blocked",
                risk_state.block_reason,
            )
            self.journal.record_tick("blocked: " + risk_state.block_reason)
            await self._notify(
                f"Agent {self.agent_id} blocked: {risk_state.block_reason}"
            )
            return

        # 5. Build prompt (server credentials are injected via env into MCP process)
        # Cache routine discovery on first tick — routines rarely change mid-session
        if self._cached_routines_section is None:
            from .prompts import _build_routines_section

            try:
                self._cached_routines_section = _build_routines_section(self.strategy)
            except Exception:
                self._cached_routines_section = ""

        # User memory index (advisory) — read fresh each tick so memory written
        # by the chat or by the agent itself shows up promptly. It's a small file
        # read, like learnings/summary above; failure never blocks a tick.
        user_memory = ""
        skills_index = ""
        try:
            from condor.memory import MemoryStore, SkillStore

            slug = self.agent.slug
            user_memory = MemoryStore(self.user_id, slug).list_index()
            skills_index = SkillStore(slug).list_index()
        except Exception:
            pass

        prompt = build_tick_prompt(
            agent=self.agent,
            strategy=self.strategy,
            config=self.config,
            core_data=core_data_summaries,
            learnings=learnings,
            summary=summary,
            recent_decisions=recent_decisions,
            risk_state=risk_state.to_dict(),
            tick_number=next_tick,
            agent_id=self.agent_id,
            cached_routines_section=self._cached_routines_section or None,
            digest_boundary=is_digest_boundary,
            digest_interval=digest_interval,
            barrier_closes_section=barrier_closes_section,
            sl_cooldown_section=sl_cooldown_section,
            user_memory=user_memory,
            skills_index=skills_index,
        )

        # Inject pending user directives
        if self._pending_directives:
            directives = "\n".join(f"- {d}" for d in self._pending_directives)
            prompt += f"\n\nUSER DIRECTIVES (apply these on this tick):\n{directives}"
            self._pending_directives.clear()

        # 6. Create a fresh agent client per tick (clean context window)
        acp_client = await self._create_client(risk_state)
        self._active_client = acp_client

        response_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_call_map: dict[str, dict[str, Any]] = {}

        await acp_client.start()
        try:
            async with asyncio.timeout(300):
                async for event in self._collect_stream(acp_client, prompt):
                    if isinstance(event, TextChunk):
                        response_chunks.append(event.text)
                    elif isinstance(event, (ToolCallEvent, ToolCallUpdate)):
                        new_tc = fold_tool_call_event(tool_call_map, event)
                        if new_tc is not None:
                            tool_calls.append(new_tc)
        except asyncio.TimeoutError:
            log.warning("TickEngine %s: ACP prompt timed out", self.agent_id)
            response_chunks.append("(timed out)")
        finally:
            await acp_client.stop()
            self._active_client = None

        response_text = "".join(response_chunks)
        tick_duration = time.time() - self._last_tick_at

        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        executors_summary = core_data_summaries.get("executors", "No executor data.")

        if self.is_experiment:
            # Experiments: save a single snapshot file, no journal
            from .journal import save_experiment_snapshot

            save_experiment_snapshot(
                agent_dir=self.strategy.dir,
                experiment_num=self.session_num,
                execution_mode=mode,
                timestamp=timestamp,
                system_prompt=prompt,
                response_text=response_text,
                tool_calls=tool_calls,
                executors_data=executors_summary,
                risk_state=risk_state.to_dict(),
                duration=tick_duration,
                agent_key=self._agent_key(),
            )
            log.info(
                "TickEngine %s experiment #%d complete (tools=%d, response=%d chars)",
                self.agent_id,
                self.session_num,
                len(tool_calls),
                len(response_text),
            )
        else:
            if not self._running:
                log.info(
                    "TickEngine %s: skipping journal persist for tick (engine stopped)",
                    self.agent_id,
                )
                return
            # Sessions: full journal tracking
            tick_num = self.journal.record_tick(
                response_summary=response_text,
            )

            skill_pnl = self._last_skill_data.get("total_pnl", 0.0)
            skill_volume = self._last_skill_data.get("total_volume", 0.0)
            skill_executors = len(self._last_skill_data.get("executors", []))
            skill_exposure = self._last_skill_data.get("total_exposure", 0.0)
            self.journal.record_snapshot(
                total_pnl=skill_pnl,
                total_volume=skill_volume,
                open_count=skill_executors,
                position_size=skill_exposure,
            )

            self.journal.save_full_snapshot(
                tick=tick_num,
                timestamp=timestamp,
                system_prompt=prompt,
                response_text=response_text,
                tool_calls=tool_calls,
                executors_data=executors_summary,
                risk_state=risk_state.to_dict(),
                duration=tick_duration,
            )

            action_brief = response_text.replace("\n", " ") if response_text else "No response"
            self.journal.write_summary(
                tick=tick_num,
                status="Running",
                pnl=skill_pnl,
                open_count=skill_executors,
                last_action=action_brief,
            )

            log.info(
                "TickEngine %s tick #%d complete (tools=%d, response=%d chars)",
                self.agent_id,
                tick_num,
                len(tool_calls),
                len(response_text),
            )

            agent_closed = _extract_agent_closed_executor_ids(tool_calls)
            if agent_closed:
                self._agent_closed_executor_ids.update(agent_closed)
                self._notified_barrier_close_ids.update(agent_closed)

            created = _extract_agent_created_executor_ids(tool_calls)
            try:
                self._last_running_executor_ids = await _fetch_running_executor_ids(
                    client, self.agent_id
                )
            except Exception:
                log.warning(
                    "TickEngine %s: failed to refresh running executor ids after tick",
                    self.agent_id,
                    exc_info=True,
                )
            if created:
                self._last_running_executor_ids.update(created)
            if agent_closed:
                self._last_running_executor_ids -= agent_closed

    async def _collect_stream(
        self,
        acp_client: ACPClient | PydanticAIClient | CursorSdkClient,
        prompt: str,
    ):
        """Wrapper to make prompt_stream compatible with wait_for."""
        async for event in acp_client.prompt_stream(prompt):
            yield event
            if isinstance(event, PromptDone):
                break

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    async def _create_client(
        self, risk_state: RiskState
    ) -> "ACPClient | PydanticAIClient | CursorSdkClient":
        """Build an ACP or PydanticAI client (does NOT start it).

        ``risk_state`` is computed once in ``_tick`` and threaded through here
        (it only feeds the auto-approve callback and cannot change between the
        two points), avoiding a redundant per-tick journal re-parse.
        """
        from handlers.agents._shared import (
            build_mcp_servers_for_agent,
            build_mcp_servers_for_session,
            get_project_dir,
        )

        mode = self.config.get("execution_mode", "loop")

        server_name = self.config.get("server_name")
        if server_name:
            mcp_servers = build_mcp_servers_for_agent(
                server_name,
                self.user_id,
                self.chat_id,
                agent_slug=self.agent.slug,
                agent_id=self.agent_id,
                execution_mode=mode,
            )
        else:
            mcp_servers = build_mcp_servers_for_session(
                self.user_id,
                self.chat_id,
                execution_mode=mode,
                agent_id=self.agent_id,
            )
        permission_cb = auto_approve_with_risk_check(
            self.risk,
            risk_state,
            execution_mode=mode,
            sl_cooldown_pairs=frozenset(
                _active_sl_cooldowns(
                    self._sl_cooldown_until_tick,
                    self._executing_tick
                    or (self.journal.tick_count + 1 if self.journal else 1),
                )
            ),
        )

        agent_key = self._agent_key()

        if is_cursor_sdk_model(agent_key):
            log.info(
                "TickEngine agent_key=%s uses Cursor SDK — MCP stdio configs are forwarded; "
                "Composer does not use Condor Telegram permission_callback for MCP tools.",
                agent_key,
            )
            return CursorSdkClient(
                model=agent_key,
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
            )

        use_pydantic_ai = is_pydantic_ai_model(agent_key)

        if use_pydantic_ai:
            import os

            base_url = self.config.get("model_base_url") or None
            tool_filter_mode = (
                self.config.get("tool_filter_mode")
                or os.environ.get("PYDANTIC_AI_TOOL_FILTER")
                or None
            )
            return PydanticAIClient(
                model=agent_key,
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
                base_url=base_url,
                tool_filter_mode=tool_filter_mode,
                # Same allowlist the agent gets on consult; empty => unrestricted.
                allowed_tools=self.agent.tools or None,
            )
        else:
            # Supports a Claude model suffix, e.g. "claude-acp:opus" — selected via
            # session/set_model after handshake (the bridge ignores ANTHROPIC_MODEL).
            agent_cmd, model_env, model_pref = resolve_acp(agent_key)
            return ACPClient(
                command=agent_cmd,
                working_dir=get_project_dir(),
                mcp_servers=mcp_servers,
                permission_callback=permission_cb,
                extra_env=model_env or None,
                model=model_pref or None,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _agent_key(self) -> str:
        """Resolve the model for this run: config override > strategy override > Agent."""
        return (
            self.config.get("agent_key")
            or self.strategy.agent_key
            or self.agent.agent_key
        )

    def _resolve_server(self) -> tuple[str | None, dict | None]:
        """Resolve the server for this agent."""
        from config_manager import get_config_manager, get_effective_server

        cm = get_config_manager()
        server_name = self.config.get("server_name")

        if not server_name:
            server_name = get_effective_server(self.chat_id)
        if not server_name:
            accessible = cm.get_accessible_servers(self.user_id)
            server_name = accessible[0] if accessible else None
        if not server_name:
            return None, None

        server = cm.get_server(server_name)
        return server_name, server

    async def _get_client(self):
        """Get the Hummingbot API client for this agent."""
        try:
            server_name, server = self._resolve_server()
            if not server:
                from handlers.bots._shared import get_bots_client

                client, _ = await get_bots_client(self.chat_id)
                return client

            from config_manager import get_config_manager

            cm = get_config_manager()
            return await cm.get_client(server_name)
        except Exception:
            log.exception("Failed to get API client for agent %s", self.agent_id)
            return None

    async def _notify(self, message: str) -> None:
        """Send a notification to the user via Telegram."""
        chat_id = self.chat_id
        text = (message or "")[:4096]
        bot = getattr(self, "_bot", None)
        if bot:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                return
            except Exception:
                log.exception("Failed to send notification to chat %s", chat_id)
        await _notify_via_telegram_bot_api(chat_id, text)

    def get_info(self) -> dict[str, Any]:
        """Return a summary dict for display."""
        sd = self._last_skill_data
        risk_limits = self.config.get("risk_limits", {})

        if self.journal:
            summary = self.journal.get_summary_dict()
        else:
            summary = {
                "total_ticks": 0,
                "daily_pnl": 0,
                "total_volume": 0,
                "total_exposure": 0,
                "open_executors": 0,
            }

        return {
            "agent_id": self.agent_id,
            "strategy": self.strategy.name,
            "strategy_slug": self.strategy.slug,
            "session_num": self.session_num,
            "status": self.status,
            "tick_count": summary["total_ticks"],
            "daily_pnl": sd.get("total_pnl", summary["daily_pnl"]),
            "total_volume": sd.get("total_volume", summary.get("total_volume", 0)),
            "total_exposure": sd.get("total_exposure", summary["total_exposure"]),
            "open_executors": len(sd.get("executors", [])) or summary["open_executors"],
            "frequency_sec": self.config.get("frequency_sec", 60),
            "server_name": self.config.get("server_name", ""),
            "total_amount_quote": self.config.get("total_amount_quote", 100),
            "trading_context": self.config.get("trading_context", ""),
            "risk_limits": (
                risk_limits
                if isinstance(risk_limits, dict)
                else (
                    risk_limits.model_dump()
                    if hasattr(risk_limits, "model_dump")
                    else {}
                )
            ),
            "agent_key": self._agent_key(),
            "execution_mode": self.config.get("execution_mode", "loop"),
            "max_ticks": self.config.get("max_ticks", 0),
            "digest_interval_ticks": int(self.config.get("digest_interval_ticks", 0) or 0),
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "is_experiment": self.is_experiment,
        }
