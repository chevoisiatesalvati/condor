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
from condor.strategy_runners.macdbb.sessions import create_session, load_default_config
from condor.strategy_runners.macdbb.tick_log import maybe_cleanup, write_tick_log
from condor.strategy_runners.promote import assert_promoted_or_raise

log = logging.getLogger(__name__)


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


@dataclass
class DeterministicRunner:
    """Runs a catalog strategy with ``decide()`` — no LLM on the tick path."""

    strategy: DeterministicStrategy
    config: dict[str, Any]
    chat_id: int
    user_id: int

    agent_id: str = field(init=False)
    session_num: int = field(init=False)
    session_dir: Path | None = field(default=None, init=False)
    journal: JournalManager | None = field(default=None, init=False)
    risk: RiskEngine = field(init=False)

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _macdbb_state: MacdbbState = field(default_factory=MacdbbState, init=False)
    _last_tick_at: float = field(default=0.0, init=False)
    _last_error: str = field(default="", init=False)
    _last_tick_summary: str = field(default="", init=False)
    _tick_count: int = field(default=0, init=False)
    _bot: Any = field(default=None, init=False, repr=False)

    # Duck-type attributes expected by LoopSupervisor.record / reconcile.
    @property
    def agent(self) -> Any:
        return type("A", (), {"slug": self.strategy.data_slug})()

    def __post_init__(self) -> None:
        run_key = self.strategy.key
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

    def resume(self) -> None:
        self._paused = False

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

    async def _tick(self) -> None:
        """One decision cycle. Market fetch is best-effort; empty signals → hold."""
        self._last_tick_at = time.time()
        tick_num = (self.journal.tick_count + 1) if self.journal else 1
        params = dict(self.config.get("strategy_params") or {})
        formal = float(self.config.get("total_amount_quote") or 500)
        risk_limits = self.config.get("risk_limits") or {}
        max_open = int(risk_limits.get("max_open_executors") or 10)

        open_positions = await self._load_open_positions()
        signals, scanner_regime, tradeable_count = await self._load_signals(params)

        tick = MacdbbTickInput(
            tick_number=tick_num,
            scanner_regime=scanner_regime,  # type: ignore[arg-type]
            tradeable_count=tradeable_count,
            signals=signals,
            open_positions=open_positions,
            barrier_closes=[],
            formal_notional_quote=formal,
            strategy_params=params,
            max_open_executors=max_open,
            fee_bps=float(params.get("fee_bps") or 0),
            slippage_bps=float(params.get("slippage_bps") or 0),
            amount_step=float(params.get("amount_step") or 0),
        )
        decision = decide(tick, self._macdbb_state)
        self._macdbb_state = decision.state

        apply_result = await self._apply_decision(decision)

        journal_fields = dict(decision.journal_fields)
        journal_fields["apply_ok"] = apply_result.ok
        journal_fields["apply_error"] = apply_result.error
        journal_fields["created_ids"] = ",".join(apply_result.created_ids)
        journal_fields["stopped_ids"] = ",".join(apply_result.stopped_ids)

        if apply_result.ok:
            if decision.creates or decision.stops:
                summary = (
                    f"creates={len(apply_result.created_ids)} "
                    f"stops={len(apply_result.stopped_ids)}"
                )
            else:
                summary = decision.hold_reason or "hold"
            self._last_error = ""
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

    async def _load_open_positions(self) -> list[OpenPosition]:
        """Best-effort: empty when no API client is available."""
        try:
            from condor.agents.performance import fetch_agent_performance

            client = await self._get_client()
            if client is None:
                return []
            perf = await fetch_agent_performance(client, self.agent_id)
            out: list[OpenPosition] = []
            for ex in perf.executors:
                status = str(ex.get("status") or "").lower()
                if status not in ("running", "active"):
                    continue
                side = str(ex.get("side") or "long").lower()
                if side not in ("long", "short"):
                    side = "long"
                out.append(
                    OpenPosition(
                        executor_id=str(ex.get("id") or ""),
                        pair=str(ex.get("pair") or ""),
                        side=side,  # type: ignore[arg-type]
                        entry_class="formal",
                        pnl=float(ex.get("pnl") or 0),
                    )
                )
            return out
        except Exception:
            log.warning(
                "DeterministicRunner %s: open position load failed",
                self.agent_id,
                exc_info=True,
            )
            return []

    async def _load_signals(
        self, params: dict[str, Any]
    ) -> tuple[list[SignalSnapshot], str | None, int]:
        """Load scanner + MACD/BB signals for this tick (no LLM)."""
        try:
            from condor.strategy_runners.macdbb.market_data import load_macdbb_signals

            return await load_macdbb_signals(params)
        except Exception:
            log.warning(
                "DeterministicRunner %s: signal load failed",
                self.agent_id,
                exc_info=True,
            )
            return [], None, 0

    async def _apply_decision(self, decision) -> ApplyResult:
        if not decision.creates and not decision.stops:
            # Barrier-close notifications are informational (already closed on venue).
            closes = [
                n.text
                for n in decision.notifications
                if "CLOSED" in n.text.upper()
            ]
            return ApplyResult(ok=True, notified_closes=closes)

        client = await self._get_client()
        if client is None:
            return ApplyResult(
                ok=False,
                error=self._last_error or "No API client available",
            )

        from condor.fetchers.executors import create_executor, stop_executor

        result = ApplyResult(ok=True)
        for stop in decision.stops:
            try:
                stop_res = await stop_executor(client, stop.executor_id)
                if isinstance(stop_res, dict) and stop_res.get("status") == "error":
                    msg = str(stop_res.get("message") or "stop failed")
                    result.stop_failures.append(f"{stop.executor_id}:{msg}")
                    result.ok = False
                else:
                    result.stopped_ids.append(stop.executor_id)
                    result.notified_closes.append(
                        f"⚡ CLOSED {stop.pair} | {stop.reason} | id: {stop.executor_id}"
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
        for create in decision.creates:
            try:
                create_res = await create_executor(
                    client,
                    {
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
                        },
                    },
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
                note = (
                    f"⚡ OPEN {create.side.upper()} {create.pair} | "
                    f"{create.entry_class} | notional ${create.notional_quote:.2f} | "
                    f"SL {create.sl_pct:.2f}% TP {create.tp_pct:.2f}%"
                )
                for candidate in decision.notifications:
                    text = candidate.text
                    if (
                        "OPEN" in text.upper()
                        and create.pair in text
                        and create.side.upper() in text.upper()
                    ):
                        note = text
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
    merged.update(config or {})
    preset = str(merged.get("strategy_preset") or "")
    freq = int(merged.get("frequency_sec") or 1800)
    if preset and not merged.get("strategy_params"):
        try:
            from condor.strategy_runners.macdbb.presets import strategy_params_from_preset

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
    )
    await runner.start(bot=bot)
    return runner
