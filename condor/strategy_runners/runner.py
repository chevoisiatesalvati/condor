"""Deterministic strategy runner — supervisor-backed loop without ACP/LLM."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from condor.agents.journal import JournalManager
from condor.agents.risk import RiskEngine, resolve_risk_limits
from condor.runtime.registry_file import LoopState
from condor.strategy_runners.catalog import DeterministicStrategy, get_strategy
from condor.strategy_runners.macdbb import (
    MacdbbState,
    MacdbbTickInput,
    OpenPosition,
    SignalSnapshot,
    decide,
)
from condor.strategy_runners.macdbb.sessions import (
    create_session,
    find_session_dir,
    load_default_config,
    open_existing_session,
)
from condor.strategy_runners.macdbb.state_store import (
    PENDING_OPEN_TTL_TICKS,
    load_runner_state,
    save_runner_state,
)
from condor.strategy_runners.macdbb.tick_log import maybe_cleanup, write_tick_log
from condor.strategy_runners.macdbb.types import EntryClass, Side
from condor.strategy_runners.macdbb_pullback.types import (
    MacdbbPullbackState,
    PullbackTickInput,
)
from condor.strategy_runners.promote import assert_promoted_or_raise

_PULLBACK_SLUG = "macdbb_pullback_hl"

log = logging.getLogger(__name__)

# Hummingbot OrderType: MARKET=1, LIMIT=2, LIMIT_MAKER=3
_OPEN_ORDER_TYPE_MARKET = 1
_NEVER_FILLED_CLOSE_TYPES = frozenset(
    {
        "INSUFFICIENT_BALANCE",
        "FAILED",
        "EXPIRED",
        "CANCELED",
        "CANCELLED",
    }
)


def _normalize_close_type(close_type: Any) -> str:
    text = str(close_type or "").strip().upper().replace(" ", "_")
    if text.startswith("CLOSETYPE."):
        text = text.split(".", 1)[-1]
    return text


def _executor_never_filled(ex: dict[str, Any]) -> bool:
    """True when a terminated executor never established a live position."""
    from condor.agents.performance import is_running_status
    from condor.fetchers.executors import get_executor_volume

    if is_running_status(str(ex.get("status") or "")):
        return False
    close_type = _normalize_close_type(ex.get("close_type"))
    if close_type in _NEVER_FILLED_CLOSE_TYPES:
        return True
    try:
        volume = float(get_executor_volume(ex) or ex.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return volume <= 0.0


def _supervisor():
    from condor.runtime.loops import get_supervisor

    return get_supervisor()


@dataclass
class ApplyResult:
    """Outcome of applying a decision to the venue."""

    ok: bool
    error: str = ""
    created_ids: list[str] = field(default_factory=list)
    stopped_ids: list[str] = field(default_factory=list)
    create_failures: list[str] = field(default_factory=list)
    stop_failures: list[str] = field(default_factory=list)
    notified_opens: list[str] = field(default_factory=list)
    notified_closes: list[str] = field(default_factory=list)
    created_pairs: list[tuple[str, str, Side, EntryClass]] = field(default_factory=list)


@dataclass
class InventoryResult:
    """Venue inventory for one tick — fail closed when ``available`` is False."""

    positions: list[OpenPosition]
    available: bool
    all_executors: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def _position_side_from_executor(raw_side: Any, *, fallback: Side | None) -> Side | None:
    from condor.fetchers.executors import normalize_executor_side

    normalized = normalize_executor_side(raw_side)
    if normalized == "BUY":
        return "long"
    if normalized == "SELL":
        return "short"
    label = str(raw_side or "").strip().lower()
    if label in {"long", "short"}:
        return label  # type: ignore[return-value]
    return fallback


@dataclass
class DeterministicRunner:
    """Runs a catalog strategy with ``decide()`` — no LLM on the tick path."""

    strategy: DeterministicStrategy
    config: dict[str, Any]
    chat_id: int
    user_id: int
    resume_session_num: int | None = None

    # Stable marker for LoopSupervisor.for_deterministic_slug (survives class reload).
    runner_kind: str = field(default="deterministic", init=False)

    agent_id: str = field(init=False)
    session_num: int = field(init=False)
    session_dir: Path | None = field(default=None, init=False)
    journal: JournalManager | None = field(default=None, init=False)
    risk: RiskEngine = field(init=False)

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _macdbb_state: MacdbbState | MacdbbPullbackState = field(
        default_factory=MacdbbState, init=False
    )
    _pending_opens: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _last_running_executor_ids: set[str] = field(default_factory=set, init=False)
    _barrier_notified_ids: set[str] = field(default_factory=set, init=False)
    _agent_closed_ids: set[str] = field(default_factory=set, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_tick_summary: str = field(default="", init=False)
    _tick_count: int = field(default=0, init=False)
    _bot: Any = field(default=None, init=False, repr=False)

    # Duck-type attributes expected by LoopSupervisor.record / reconcile.
    @property
    def agent(self) -> Any:
        return type("A", (), {"slug": self.strategy.data_slug})()

    @property
    def _is_pullback(self) -> bool:
        return self.strategy.slug == _PULLBACK_SLUG

    def __post_init__(self) -> None:
        if self._is_pullback:
            self._macdbb_state = MacdbbPullbackState()
        run_key = self.strategy.key
        if self.resume_session_num is not None:
            self.session_num, self.session_dir, self.journal = open_existing_session(
                slug=self.strategy.data_slug,
                session_num=int(self.resume_session_num),
                strategy_name=self.strategy.name,
                strategy_description=self.strategy.description,
                run_key=run_key,
            )
        else:
            self.session_num, self.session_dir, self.journal = create_session(
                slug=self.strategy.data_slug,
                strategy_name=self.strategy.name,
                strategy_description=self.strategy.description,
                config=self.config,
                run_key=run_key,
            )
        self.agent_id = f"{run_key}_{self.session_num}"
        self.risk = RiskEngine(resolve_risk_limits(self.config))
        maybe_cleanup(self.strategy.data_slug, config=self.config, force=True)
        self._restore_persisted_state()

    def _restore_persisted_state(self) -> None:
        state_cls = MacdbbPullbackState if self._is_pullback else MacdbbState
        stored = load_runner_state(self.session_dir, state_cls=state_cls)
        self._macdbb_state = stored["macdbb_state"]
        self._pending_opens = {
            str(k): dict(v) if isinstance(v, dict) else {"executor_id": str(v), "tick": 0}
            for k, v in (stored.get("pending_opens") or {}).items()
        }
        self._last_running_executor_ids = set(stored.get("last_running_ids") or [])
        self._barrier_notified_ids = set(stored.get("barrier_notified_ids") or [])

    def _persist_state(self) -> None:
        # state_store expects MacdbbState duck-type with to_dict(); pullback state matches.
        save_runner_state(
            self.session_dir,
            macdbb_state=self._macdbb_state,  # type: ignore[arg-type]
            pending_opens=self._pending_opens,
            last_running_ids=self._last_running_executor_ids,
            barrier_notified_ids=self._barrier_notified_ids,
        )

    @property
    def is_running(self) -> bool:
        return self._running and not self._paused

    @property
    def is_active(self) -> bool:
        return self._running

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def last_tick_summary(self) -> str:
        return self._last_tick_summary

    @property
    def tick_count(self) -> int:
        if self.journal:
            return int(self.journal.tick_count)
        return self._tick_count

    def pause(self) -> None:
        self._paused = True
        if self._running:
            _supervisor().record(self, LoopState.PAUSED)

    def resume(self) -> None:
        self._paused = False
        if self._running:
            _supervisor().record(self, LoopState.RUNNING)

    async def start(self, bot=None) -> None:
        if self._running:
            return
        assert_promoted_or_raise(
            self.strategy.slug,
            preset=str(self.config.get("strategy_preset") or ""),
            strategy_params=dict(self.config.get("strategy_params") or {}),
            require_promoted=self.strategy.require_promoted,
        )
        self._running = True
        self._bot = bot
        self._last_error = ""
        self._task = asyncio.create_task(self._loop())
        _supervisor().register(self)
        log.info(
            "DeterministicRunner %s started (freq=%ss)",
            self.agent_id,
            self.config.get("frequency_sec", 1800),
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._persist_state()
        if self.journal:
            try:
                self.journal.mark_stopped()
            except Exception:
                log.warning(
                    "DeterministicRunner %s: failed to write stopped summary",
                    self.agent_id,
                )
            self.journal.close()
        _supervisor().unregister(self.agent_id, LoopState.STOPPED)
        log.info("DeterministicRunner %s stopped", self.agent_id)

    async def _get_client(self):
        """Resolve Hummingbot API client for configured server (same as TickEngine)."""
        try:
            from config_manager import get_config_manager

            server_name = str(self.config.get("server_name") or "").strip() or None
            cm = get_config_manager()
            if not server_name:
                server_name = cm.get_default_server()
            if not server_name:
                self._last_error = "No server configured for DeterministicRunner"
                log.warning(
                    "DeterministicRunner %s: %s", self.agent_id, self._last_error
                )
                return None
            return await cm.get_client(server_name)
        except Exception as exc:
            self._last_error = f"get_client failed: {exc}"
            log.exception(
                "DeterministicRunner %s: failed to get API client", self.agent_id
            )
            return None

    async def _loop(self) -> None:
        freq = int(self.config.get("frequency_sec") or 1800)
        try:
            while self._running:
                if not self._paused:
                    try:
                        await self._tick()
                    except Exception as exc:
                        self._last_error = f"tick failed: {exc}"
                        log.exception(
                            "DeterministicRunner %s tick failed", self.agent_id
                        )
                await asyncio.sleep(freq)
        except asyncio.CancelledError:
            raise

    def _prune_pending_opens(self, tick_num: int, confirmed_pairs: set[str]) -> None:
        expired: list[str] = []
        for pair, meta in list(self._pending_opens.items()):
            if pair in confirmed_pairs:
                expired.append(pair)
                continue
            opened_tick = int(meta.get("tick") or 0)
            if tick_num - opened_tick > PENDING_OPEN_TTL_TICKS:
                expired.append(pair)
        for pair in expired:
            self._pending_opens.pop(pair, None)

    def _clear_never_filled_pendings(
        self, all_executors: list[dict[str, Any]], tick_num: int
    ) -> list[dict[str, Any]]:
        """Drop pending opens that terminated without a fill (e.g. IB).

        Never-filled legs must not occupy thesis monitoring or block re-entry
        until PENDING_OPEN_TTL expires.
        """
        by_id = {
            str(ex.get("id") or ""): ex
            for ex in all_executors
            if isinstance(ex, dict) and ex.get("id")
        }
        cleared: list[dict[str, Any]] = []
        params = dict(self.config.get("strategy_params") or {})
        cooldown_ticks = int(params.get("sl_cooldown_ticks") or 2)
        for pair, meta in list(self._pending_opens.items()):
            eid = str(meta.get("executor_id") or "")
            ex = by_id.get(eid)
            if ex is None or not _executor_never_filled(ex):
                continue
            self._pending_opens.pop(pair, None)
            self._macdbb_state.entry_meta_by_pair.pop(pair, None)
            for attr in (
                "thesis_decay_by_pair",
                "flip_streak_by_pair",
                "thesis_decay_extra_pending_by_pair",
                "monitor_state_by_pair",
                "armed_by_pair",
            ):
                store = getattr(self._macdbb_state, attr, None)
                if isinstance(store, dict):
                    store.pop(pair, None)
            if cooldown_ticks > 0:
                self._macdbb_state.sl_cooldown_until_tick[pair] = max(
                    self._macdbb_state.sl_cooldown_until_tick.get(pair, 0),
                    tick_num + cooldown_ticks,
                )
            cleared.append(
                {
                    "pair": pair,
                    "executor_id": eid,
                    "close_type": _normalize_close_type(ex.get("close_type")),
                    "volume": ex.get("volume"),
                }
            )
        if cleared:
            log.info(
                "DeterministicRunner %s: cleared never-filled pendings %s",
                self.agent_id,
                ",".join(f"{c['pair']}:{c['close_type']}" for c in cleared),
            )
        return cleared

    def _merge_pending_into_positions(
        self, positions: list[OpenPosition], tick_num: int
    ) -> list[OpenPosition]:
        """Union pending opens so decide() cannot re-enter before API confirms."""
        confirmed = {p.pair for p in positions}
        self._prune_pending_opens(tick_num, confirmed)
        by_pair = {p.pair: p for p in positions if p.pair}
        for pair, meta in self._pending_opens.items():
            if pair in by_pair:
                continue
            entry_meta = self._macdbb_state.entry_meta_by_pair.get(pair)
            side: Side = entry_meta.side if entry_meta else "long"
            entry_class: EntryClass = (
                entry_meta.entry_class
                if entry_meta
                else ("immediate" if self._is_pullback else "formal")
            )
            by_pair[pair] = OpenPosition(
                executor_id=str(meta.get("executor_id") or f"pending:{pair}"),
                pair=pair,
                side=side,
                entry_class=entry_class,
                pnl=0.0,
                entry_bb_pos_pct=(
                    float(entry_meta.entry_bb_pos_pct) if entry_meta else 0.0
                ),
                filled=False,
            )
        return list(by_pair.values())

    async def _tick(self) -> None:
        """One decision cycle. Market fetch is best-effort; empty signals → hold."""
        self._last_tick_at = time.time()
        tick_num = (self.journal.tick_count + 1) if self.journal else 1
        raw_params = dict(self.config.get("strategy_params") or {})
        frequency_sec = max(1, int(self.config.get("frequency_sec") or 1800))
        from condor.agents.strategy_configs.registry import (
            resolve_effective_strategy_params,
        )

        params = resolve_effective_strategy_params(
            self.strategy.strategy_slug,
            raw_params,
            frequency_sec,
        )
        formal = float(self.config.get("total_amount_quote") or 500)
        risk_limits = self.config.get("risk_limits") or {}
        max_open = int(risk_limits.get("max_open_executors") or 10)

        inventory = await self._load_open_positions()
        if inventory.available:
            self._clear_never_filled_pendings(inventory.all_executors, tick_num)
        open_positions = self._merge_pending_into_positions(
            inventory.positions, tick_num
        )
        open_pairs = [p.pair for p in open_positions if p.pair]

        signals, scanner_regime, tradeable_count = await self._load_signals(
            params, extra_pairs=open_pairs
        )

        barrier_closes: list[dict[str, Any]] = []
        if inventory.available and self._last_running_executor_ids:
            from condor.agents.engine import _detect_barrier_closes

            barrier_closes = _detect_barrier_closes(
                inventory.all_executors,
                self._last_running_executor_ids,
                self._barrier_notified_ids,
                self._agent_closed_ids,
            )
            for close in barrier_closes:
                eid = str(close.get("id") or "")
                if eid:
                    self._barrier_notified_ids.add(eid)

        if self._is_pullback:
            from condor.strategy_runners.macdbb_pullback import decide as pullback_decide

            # Map macdbb OpenPosition rows into pullback OpenPosition if needed.
            from condor.strategy_runners.macdbb_pullback.types import (
                OpenPosition as PullbackOpenPosition,
            )

            pullback_positions = []
            for pos in open_positions:
                entry_class = str(pos.entry_class or "immediate")
                if entry_class not in {"immediate", "pullback"}:
                    entry_class = "immediate"
                pullback_positions.append(
                    PullbackOpenPosition(
                        executor_id=pos.executor_id,
                        pair=pos.pair,
                        side=pos.side,
                        entry_class=entry_class,  # type: ignore[arg-type]
                        pnl=pos.pnl,
                        entry_bb_pos_pct=pos.entry_bb_pos_pct,
                        filled=pos.filled,
                    )
                )
            tick = PullbackTickInput(
                tick_number=tick_num,
                tradeable_count=tradeable_count,
                signals=signals,  # type: ignore[arg-type]
                open_positions=pullback_positions,
                barrier_closes=barrier_closes,
                total_amount_quote=formal,
                strategy_params=params,
                max_open_executors=max_open,
                fee_bps=float(params.get("fee_bps") or 0),
                slippage_bps=float(params.get("slippage_bps") or 0),
                amount_step=float(params.get("amount_step") or 0),
                inventory_available=inventory.available,
                frequency_sec=int(self.config.get("frequency_sec") or 60),
            )
            decision = pullback_decide(tick, self._macdbb_state)  # type: ignore[arg-type]
        else:
            tick = MacdbbTickInput(
                tick_number=tick_num,
                scanner_regime=scanner_regime,  # type: ignore[arg-type]
                tradeable_count=tradeable_count,
                signals=signals,
                open_positions=open_positions,
                barrier_closes=barrier_closes,
                formal_notional_quote=formal,
                strategy_params=params,
                max_open_executors=max_open,
                fee_bps=float(params.get("fee_bps") or 0),
                slippage_bps=float(params.get("slippage_bps") or 0),
                amount_step=float(params.get("amount_step") or 0),
                inventory_available=inventory.available,
            )
            decision = decide(tick, self._macdbb_state)  # type: ignore[arg-type]
        self._macdbb_state = decision.state

        # Fail closed: never apply creates when inventory is unknown.
        if not inventory.available and decision.creates:
            decision.creates = []
            decision.hold = not decision.stops
            decision.hold_reason = "inventory_unavailable"
            decision.journal_fields["hold_reason"] = "inventory_unavailable"
            decision.journal_fields["inventory_available"] = False

        apply_result = await self._apply_decision(
            decision, open_positions=open_positions
        )

        for pair, executor_id, side, entry_class in apply_result.created_pairs:
            self._pending_opens[pair] = {
                "executor_id": executor_id,
                "tick": tick_num,
                "side": side,
                "entry_class": entry_class,
            }
        for stop_id in apply_result.stopped_ids:
            self._agent_closed_ids.add(stop_id)
            for pair, meta in list(self._pending_opens.items()):
                if str(meta.get("executor_id") or "") == stop_id:
                    self._pending_opens.pop(pair, None)

        # Refresh last-running snapshot for next-tick barrier detection.
        confirmed_ids = {
            p.executor_id for p in inventory.positions if p.executor_id
        }
        pending_ids = {
            str(meta.get("executor_id") or "")
            for meta in self._pending_opens.values()
            if meta.get("executor_id")
        }
        if inventory.available:
            self._last_running_executor_ids = {
                eid for eid in (confirmed_ids | pending_ids | set(apply_result.created_ids))
                if eid and not eid.startswith("pending:")
            }
            # Drop stopped / barrier-closed ids.
            self._last_running_executor_ids -= set(apply_result.stopped_ids)
            self._last_running_executor_ids -= {
                str(c.get("id") or "") for c in barrier_closes if c.get("id")
            }

        self._persist_state()

        journal_fields = dict(decision.journal_fields)
        journal_fields["apply_ok"] = apply_result.ok
        journal_fields["apply_error"] = apply_result.error
        journal_fields["created_ids"] = ",".join(apply_result.created_ids)
        journal_fields["stopped_ids"] = ",".join(apply_result.stopped_ids)
        journal_fields["pending_pairs"] = ",".join(sorted(self._pending_opens))
        journal_fields["barrier_closes"] = ",".join(
            str(c.get("pair") or "") for c in barrier_closes
        )
        if not inventory.available:
            journal_fields["inventory_error"] = inventory.error

        if apply_result.ok:
            if decision.creates or decision.stops:
                summary = (
                    f"creates={len(apply_result.created_ids)} "
                    f"stops={len(apply_result.stopped_ids)}"
                )
            else:
                summary = decision.hold_reason or "hold"
            self._last_error = "" if inventory.available else inventory.error
        else:
            summary = f"apply_error={apply_result.error}"
            self._last_error = apply_result.error

        self._last_tick_summary = summary
        self._tick_count = tick_num

        if self.journal:
            self.journal.record_tick(summary)
            self.journal.append_action(
                tick_num,
                "deterministic_tick",
                " ".join(f"{k}={v}" for k, v in journal_fields.items()),
            )
            _supervisor().record_tick(self)

        try:
            write_tick_log(
                slug=self.strategy.data_slug,
                session_num=self.session_num,
                tick_number=tick_num,
                config=self.config,
                payload={
                    "scanner_regime": scanner_regime,
                    "tradeable_count": tradeable_count,
                    "signal_count": len(signals),
                    "signals": [
                        {
                            "pair": s.pair,
                            "price": s.price,
                            "bb_pos_pct": s.bb_pos_pct,
                            "macd": s.macd,
                            "signal_line": s.signal_line,
                            "histogram": s.histogram,
                            "trend": s.trend,
                            "momentum": s.momentum,
                        }
                        for s in signals[:40]
                    ],
                    "open_count": len(open_positions),
                    "open_pairs": open_pairs,
                    "pending_pairs": sorted(self._pending_opens),
                    "inventory_available": inventory.available,
                    "barrier_closes": [
                        {
                            "id": c.get("id"),
                            "pair": c.get("pair"),
                            "close_type": c.get("close_type"),
                            "pnl": c.get("pnl"),
                        }
                        for c in barrier_closes
                    ],
                    "decide": {
                        "hold_reason": decision.hold_reason,
                        "creates": len(decision.creates),
                        "stops": len(decision.stops),
                        "scores": {
                            c.pair: getattr(c, "score", None)
                            for c in decision.creates
                        },
                    },
                    "apply": {
                        "ok": apply_result.ok,
                        "error": apply_result.error,
                        "created_ids": apply_result.created_ids,
                        "stopped_ids": apply_result.stopped_ids,
                    },
                    "summary": summary,
                },
            )
        except Exception:
            log.debug(
                "DeterministicRunner %s: tick_log write failed",
                self.agent_id,
                exc_info=True,
            )

        # Only emit OPEN/CLOSE notifications for venue-confirmed actions.
        for text in apply_result.notified_opens + apply_result.notified_closes:
            await self._notify(text)
        if not apply_result.ok and (decision.creates or decision.stops):
            await self._notify(
                f"⚠️ APPLY FAILED {self.agent_id}: {apply_result.error}"
            )

    async def _load_open_positions(self) -> InventoryResult:
        """Load RUNNING executors; fail closed when API/client is unavailable."""
        try:
            from condor.agents.performance import (
                fetch_agent_performance,
                is_running_status,
            )

            client = await self._get_client()
            if client is None:
                return InventoryResult(
                    positions=[],
                    available=False,
                    error=self._last_error or "No API client available",
                )
            perf = await fetch_agent_performance(client, self.agent_id)
            out: list[OpenPosition] = []
            for ex in perf.executors:
                status = str(ex.get("status") or "")
                if not is_running_status(status):
                    continue
                pair = str(ex.get("pair") or "").strip()
                if not pair:
                    continue
                entry_meta = self._macdbb_state.entry_meta_by_pair.get(pair)
                fallback_side = entry_meta.side if entry_meta else None
                side = _position_side_from_executor(
                    ex.get("side"), fallback=fallback_side
                )
                if side is None:
                    log.warning(
                        "DeterministicRunner %s: unknown side for %s (%r); "
                        "keeping pair for dedup as long",
                        self.agent_id,
                        pair,
                        ex.get("side"),
                    )
                    side = "long"
                entry_class: EntryClass = (
                    entry_meta.entry_class
                    if entry_meta
                    else ("immediate" if self._is_pullback else "formal")
                )
                out.append(
                    OpenPosition(
                        executor_id=str(ex.get("id") or ""),
                        pair=pair,
                        side=side,
                        entry_class=entry_class,
                        pnl=float(ex.get("pnl") or 0),
                        entry_bb_pos_pct=(
                            float(entry_meta.entry_bb_pos_pct) if entry_meta else 0.0
                        ),
                    )
                )
            return InventoryResult(
                positions=out,
                available=True,
                all_executors=list(perf.executors),
            )
        except Exception as exc:
            log.warning(
                "DeterministicRunner %s: open position load failed",
                self.agent_id,
                exc_info=True,
            )
            return InventoryResult(
                positions=[],
                available=False,
                error=f"open position load failed: {exc}",
            )

    async def _load_signals(
        self,
        params: dict[str, Any],
        *,
        extra_pairs: list[str] | None = None,
    ) -> tuple[list[Any], str | None, int]:
        """Load scanner + MACD/BB signals for this tick (no LLM)."""
        try:
            if self._is_pullback:
                from condor.strategy_runners.macdbb_pullback.market_data import (
                    load_pullback_signals,
                )

                return await load_pullback_signals(params, extra_pairs=extra_pairs)
            from condor.strategy_runners.macdbb.market_data import load_macdbb_signals

            return await load_macdbb_signals(params, extra_pairs=extra_pairs)
        except Exception:
            log.warning(
                "DeterministicRunner %s: signal load failed",
                self.agent_id,
                exc_info=True,
            )
            return [], None, 0

    def _pnl_snapshot_for_stop(
        self,
        stop,
        open_positions: list[OpenPosition],
        executors_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Best-effort PnL/side for a stop notification."""
        ex = executors_by_id.get(stop.executor_id) or {}
        pnl = ex.get("pnl")
        net_pnl_pct = ex.get("net_pnl_pct")
        side = str(ex.get("side") or "")
        volume = ex.get("volume")
        if pnl is None:
            for pos in open_positions:
                if pos.executor_id == stop.executor_id or pos.pair == stop.pair:
                    pnl = pos.pnl
                    if not side:
                        side = pos.side
                    break
        return {
            "pnl": float(pnl) if pnl is not None else None,
            "net_pnl_pct": float(net_pnl_pct) if net_pnl_pct is not None else None,
            "side": side,
            "volume": float(volume) if volume is not None else None,
        }

    async def _apply_decision(
        self,
        decision,
        *,
        open_positions: list[OpenPosition] | None = None,
    ) -> ApplyResult:
        from condor.strategy_runners.macdbb.notifications import (
            format_close_notification,
            format_open_notification,
        )

        positions = list(open_positions or [])
        barrier_closes = [
            n.text
            for n in decision.notifications
            if "CLOSED" in n.text.upper()
        ]

        if not decision.creates and not decision.stops:
            return ApplyResult(ok=True, notified_closes=barrier_closes)

        client = await self._get_client()
        if client is None:
            return ApplyResult(
                ok=False,
                error=self._last_error or "No API client available",
                notified_closes=barrier_closes,
            )

        from condor.fetchers.executors import create_executor, stop_executor

        executors_by_id: dict[str, dict[str, Any]] = {}
        try:
            from condor.agents.performance import fetch_agent_performance

            perf = await fetch_agent_performance(client, self.agent_id)
            for ex in perf.executors:
                eid = str(ex.get("id") or "")
                if eid:
                    executors_by_id[eid] = ex
        except Exception:
            log.debug(
                "DeterministicRunner %s: pre-stop perf lookup failed",
                self.agent_id,
                exc_info=True,
            )

        result = ApplyResult(ok=True, notified_closes=list(barrier_closes))
        for stop in decision.stops:
            snap = self._pnl_snapshot_for_stop(stop, positions, executors_by_id)
            try:
                stop_res = await stop_executor(client, stop.executor_id)
                if isinstance(stop_res, dict) and stop_res.get("status") == "error":
                    msg = str(stop_res.get("message") or "stop failed")
                    result.stop_failures.append(f"{stop.executor_id}:{msg}")
                    result.ok = False
                else:
                    result.stopped_ids.append(stop.executor_id)
                    # Prefer post-stop executor row when available.
                    refreshed = executors_by_id.get(stop.executor_id) or {}
                    if isinstance(stop_res, dict):
                        data = stop_res.get("data")
                        if isinstance(data, dict):
                            refreshed = {**refreshed, **data}
                        for key in ("pnl", "net_pnl_pct", "side", "volume", "close_type"):
                            if stop_res.get(key) is not None:
                                refreshed[key] = stop_res[key]
                    if refreshed.get("pnl") is not None:
                        snap["pnl"] = float(refreshed["pnl"])
                    if refreshed.get("net_pnl_pct") is not None:
                        snap["net_pnl_pct"] = float(refreshed["net_pnl_pct"])
                    if refreshed.get("side"):
                        snap["side"] = str(refreshed["side"])
                    if refreshed.get("volume") is not None:
                        snap["volume"] = float(refreshed["volume"])
                    close_type = str(
                        refreshed.get("close_type") or stop.close_type or ""
                    )
                    result.notified_closes.append(
                        format_close_notification(
                            pair=stop.pair,
                            reason=stop.reason,
                            close_type=close_type,
                            side=str(snap.get("side") or ""),
                            pnl=snap.get("pnl"),
                            net_pnl_pct=snap.get("net_pnl_pct"),
                            executor_id=stop.executor_id,
                            session_num=self.session_num,
                            volume=snap.get("volume"),
                        )
                    )
            except Exception as exc:
                log.exception(
                    "DeterministicRunner %s: stop %s failed",
                    self.agent_id,
                    stop.executor_id,
                )
                result.stop_failures.append(f"{stop.executor_id}:{exc}")
                result.ok = False

        account = str(self.config.get("account_name") or "master_account")
        params = dict(self.config.get("strategy_params") or {})
        raw_leverage = params.get("leverage")
        for create in decision.creates:
            try:
                from condor.hyperliquid_leverage import apply_hyperliquid_leverage_cap

                create_cfg: dict[str, Any] = {
                    "type": "position_executor",
                    "controller_id": self.agent_id,
                    "connector_name": self.strategy.connector,
                    "trading_pair": create.pair,
                    "side": create.side,
                    "amount": create.base_amount,
                    "notional_usd": create.notional_quote,
                    "triple_barrier_config": {
                        "stop_loss": create.sl_pct / 100.0,
                        "take_profit": create.tp_pct / 100.0,
                        # MARKET — LIMIT entries often sit unfilled on HL
                        "open_order_type": _OPEN_ORDER_TYPE_MARKET,
                    },
                }
                # Explicit strategy leverage is optional; unset → per-pair HL max.
                if raw_leverage is not None and str(raw_leverage).strip() != "":
                    create_cfg["leverage"] = raw_leverage
                apply_hyperliquid_leverage_cap(create_cfg)
                leverage_used = create_cfg.get("leverage")
                create_res = await create_executor(
                    client,
                    create_cfg,
                    account_name=account,
                )
                if isinstance(create_res, dict) and create_res.get("status") == "error":
                    msg = str(create_res.get("message") or "create failed")
                    log.warning(
                        "DeterministicRunner %s: create %s error: %s",
                        self.agent_id,
                        create.pair,
                        msg,
                    )
                    result.create_failures.append(f"{create.pair}:{msg}")
                    result.ok = False
                    continue
                executor_id = ""
                if isinstance(create_res, dict):
                    data = create_res.get("data")
                    if isinstance(data, dict) and data.get("id"):
                        executor_id = str(data["id"])
                    else:
                        executor_id = str(
                            create_res.get("id")
                            or create_res.get("executor_id")
                            or ""
                        )
                result.created_ids.append(executor_id or create.pair)
                result.created_pairs.append(
                    (create.pair, executor_id or create.pair, create.side, create.entry_class)
                )
                note = format_open_notification(
                    side=create.side,
                    pair=create.pair,
                    entry_class=create.entry_class,
                    notional_quote=create.notional_quote,
                    sl_pct=create.sl_pct,
                    tp_pct=create.tp_pct,
                    session_num=self.session_num,
                    leverage=leverage_used,
                    score=float(create.score or 0) or None,
                    base_amount=create.base_amount,
                )
                for candidate in decision.notifications:
                    text = candidate.text
                    if (
                        "OPEN" in text.upper()
                        and create.pair in text
                        and create.side.upper() in text.upper()
                    ):
                        # Prefer engine text; append leverage/session if missing.
                        note = text
                        if leverage_used is not None and "x |" not in note and f"{leverage_used}" not in note:
                            note = f"{note} | {float(leverage_used):.0f}x"
                        if f"session_{self.session_num}" not in note:
                            note = f"{note} | session_{self.session_num}"
                        break
                result.notified_opens.append(note)
                log.info(
                    "DeterministicRunner %s: created %s %s notional=%.2f id=%s",
                    self.agent_id,
                    create.side,
                    create.pair,
                    create.notional_quote,
                    executor_id or "?",
                )
            except Exception as exc:
                log.exception(
                    "DeterministicRunner %s: create %s failed",
                    self.agent_id,
                    create.pair,
                )
                result.create_failures.append(f"{create.pair}:{exc}")
                result.ok = False

        if not result.ok:
            parts = result.create_failures + result.stop_failures
            result.error = "; ".join(parts) or "apply failed"
        return result

    async def _notify(self, text: str) -> None:
        if self._bot and self.chat_id:
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=text)
                return
            except Exception:
                pass
        try:
            from condor.telegram_notify import prepare_agent_notification_text
            from utils.config import TELEGRAM_TOKEN

            if not TELEGRAM_TOKEN or not self.chat_id:
                return
            import aiohttp

            payload_text = prepare_agent_notification_text(text or "", max_chars=4090)
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(
                    url,
                    json={"chat_id": self.chat_id, "text": payload_text},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
        except Exception:
            log.warning(
                "DeterministicRunner %s: notify failed", self.agent_id, exc_info=True
            )


async def start_deterministic_strategy(
    slug: str,
    *,
    config: dict[str, Any],
    user_id: int,
    chat_id: int = 0,
    bot=None,
    resume_session_num: int | None = None,
) -> DeterministicRunner:
    """Factory used by Strategies API."""
    strategy = get_strategy(slug)
    if strategy is None:
        raise KeyError(f"Unknown deterministic strategy '{slug}'")

    merged = dict(strategy.default_config)
    try:
        persisted = load_default_config(strategy.data_slug)
        if persisted:
            merged.update(persisted)
    except Exception:
        log.debug("Could not load strategy.yaml defaults for %s", slug, exc_info=True)

    if resume_session_num is not None:
        session_dir = find_session_dir(strategy.data_slug, int(resume_session_num))
        if session_dir is None:
            raise FileNotFoundError(
                f"Session {resume_session_num} not found for '{slug}'"
            )
        from condor.agents.config import load_session_config

        session_cfg = load_session_config(session_dir)
        if session_cfg:
            merged.update(session_cfg)

    merged.update(config or {})
    preset = str(merged.get("strategy_preset") or "")
    freq = int(merged.get("frequency_sec") or 1800)
    if preset and not merged.get("strategy_params"):
        try:
            if slug == _PULLBACK_SLUG:
                from condor.strategy_runners.macdbb_pullback.presets import (
                    strategy_params_from_preset as pullback_params_from_preset,
                )

                expanded = pullback_params_from_preset(preset, frequency_sec=freq)
            else:
                from condor.strategy_runners.macdbb.presets import (
                    strategy_params_from_preset,
                )

                expanded = strategy_params_from_preset(preset, frequency_sec=freq)
            if expanded:
                merged["strategy_params"] = expanded
        except Exception:
            log.warning("Could not expand preset %s", preset, exc_info=True)

    runner = DeterministicRunner(
        strategy=strategy,
        config=merged,
        chat_id=chat_id or user_id,
        user_id=user_id,
        resume_session_num=resume_session_num,
    )
    await runner.start(bot=bot)
    return runner
