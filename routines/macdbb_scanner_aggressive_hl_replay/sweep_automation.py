"""Auto-promote sweep leaders: preset, backtest report, Telegram, refine subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    SweepResult,
    _merge,
    finalize_sweep_config,
    reconstruct_sweep_overrides,
    sweep_base_config,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.presets import resolve_config_with_preset
from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
    AGENT_SLUG,
    DEFAULT_FREQUENCY_SEC,
    DEFAULT_TIME_WINDOW_MIN,
    PRESET_STRIP_KEYS,
    merge_timeline_config,
)

logger = logging.getLogger(__name__)

PRESET_NAME_PREFIX = "hl_dynamic_timeline_sweep_lead_"


@dataclass
class SweepLeaderState:
    anchor_established: bool = False
    best_cap_norm: float = float("-inf")
    best_name: str = ""
    promote_count: int = 0
    refine_pid: int | None = None
    refine_output_tag: str = ""
    refine_cancel_requested: bool = False

    @classmethod
    def load(cls, path: Path) -> SweepLeaderState:
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            return cls(
                anchor_established=bool(raw.get("anchor_established")),
                best_cap_norm=float(raw.get("best_cap_norm", float("-inf"))),
                best_name=str(raw.get("best_name") or ""),
                promote_count=int(raw.get("promote_count") or 0),
                refine_pid=(
                    int(raw["refine_pid"]) if raw.get("refine_pid") is not None else None
                ),
                refine_output_tag=str(raw.get("refine_output_tag") or ""),
                refine_cancel_requested=bool(raw.get("refine_cancel_requested")),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            logger.warning("Could not load automation state from %s: %s", path, error)
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


@dataclass(frozen=True)
class PromoteJob:
    result: SweepResult
    preset_name: str
    output_tag: str


class LeaderTracker:
    """Track cap-norm leader; emit promote jobs after first positive anchor."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._state = SweepLeaderState.load(state_path)
        self._lock = threading.Lock()

    @property
    def state(self) -> SweepLeaderState:
        return self._state

    def _save(self) -> None:
        self._state.save(self._state_path)

    def consider(self, result: SweepResult) -> PromoteJob | None:
        cap_norm = float(result.capital_normalized_pnl)
        with self._lock:
            if not self._state.anchor_established:
                if cap_norm <= 0:
                    if cap_norm > self._state.best_cap_norm:
                        self._state.best_cap_norm = cap_norm
                        self._state.best_name = result.name
                        self._save()
                    return None
                self._state.anchor_established = True
                self._state.best_cap_norm = cap_norm
                self._state.best_name = result.name
                self._save()
                logger.info(
                    "Sweep anchor established: %s cap_norm=%+.2f (no promote yet)",
                    result.name,
                    cap_norm,
                )
                return None

            if cap_norm <= self._state.best_cap_norm:
                return None

            self._state.promote_count += 1
            self._state.best_cap_norm = cap_norm
            self._state.best_name = result.name
            lead_num = self._state.promote_count
            self._save()

        preset_name = f"{PRESET_NAME_PREFIX}{lead_num:03d}"
        output_tag = f"lead_{lead_num:03d}"
        logger.info(
            "New sweep leader #%d: %s cap_norm=%+.2f -> preset %s",
            lead_num,
            result.name,
            cap_norm,
            preset_name,
        )
        return PromoteJob(
            result=result,
            preset_name=preset_name,
            output_tag=output_tag,
        )

    def set_refine_pid(self, pid: int | None, output_tag: str = "") -> None:
        with self._lock:
            self._state.refine_pid = pid
            self._state.refine_output_tag = output_tag
            self._state.refine_cancel_requested = False
            self._save()

    def request_refine_cancel(self) -> None:
        with self._lock:
            self._state.refine_cancel_requested = True
            self._save()

    def clear_refine_cancel(self) -> None:
        with self._lock:
            self._state.refine_cancel_requested = False
            self._save()


def build_full_config_from_result(
    result: SweepResult,
    *,
    dynamic_mode: str,
    sweep_grid: str,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    snapshot_dir: str | None = None,
) -> dict[str, Any]:
    if result.overrides.get("replay_mode") == "timeline_backtest":
        merged = dict(result.overrides)
    else:
        merged = reconstruct_sweep_overrides(
            dynamic_mode,
            dict(result.overrides),
            sweep_grid=sweep_grid,
            config_name=result.name,
        )
        merged = merge_timeline_config(
            merged,
            frequency_sec=frequency_sec,
            time_window_min=time_window_min,
            range_start_utc=range_start_utc,
            range_end_utc=range_end_utc,
            sweep_grid=sweep_grid,
        )
    if snapshot_dir:
        merged["snapshot_dir"] = snapshot_dir
    if range_start_utc and not merged.get("range_start_utc"):
        merged["range_start_utc"] = range_start_utc
    if range_end_utc and not merged.get("range_end_utc"):
        merged["range_end_utc"] = range_end_utc
    return merged


def register_sweep_lead_preset(
    preset_name: str,
    preset_overrides: dict[str, Any],
    *,
    presets_path: Path | None = None,
) -> None:
    """Append a sweep-lead preset without touching agent.md or winner defaults."""
    from condor.trading_agent.strategy_paths import private_strategy_dir

    yaml_path = presets_path or (private_strategy_dir(AGENT_SLUG) / "presets.yaml")
    bundle: dict[str, Any] = {}
    if yaml_path.is_file():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            bundle = loaded

    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_name in dynamic_overrides:
        raise ValueError(f"Preset {preset_name!r} already exists in {yaml_path}")

    filtered = {
        key: value
        for key, value in preset_overrides.items()
        if key not in PRESET_STRIP_KEYS
    }
    dynamic_overrides[preset_name] = filtered

    labels = bundle.setdefault("labels", {})
    cap_label = f"Sweep lead {preset_name.removeprefix(PRESET_NAME_PREFIX)}"
    labels.setdefault(preset_name, cap_label)

    names = list(bundle.get("agent_strategy_preset_names") or [])
    if preset_name not in names:
        names.append(preset_name)
    bundle["agent_strategy_preset_names"] = names

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(bundle, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


async def run_backtest_for_preset(
    preset_name: str,
    *,
    full_overrides: dict[str, Any] | None = None,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    snapshot_dir: str | None = None,
    candle_prefetch_mode: str | None = None,
) -> tuple[str | None, str]:
    from condor.reports import get_last_report_id
    from routines.macdbb_scanner_aggressive_hl_backtest import run as run_dynamic_replay

    if full_overrides is not None:
        payload = dict(full_overrides)
        payload["preset"] = "custom"
    else:
        payload = {"preset": preset_name}
    if range_start_utc:
        payload["range_start_utc"] = range_start_utc
    if range_end_utc:
        payload["range_end_utc"] = range_end_utc
    if snapshot_dir:
        payload["snapshot_dir"] = snapshot_dir
    if candle_prefetch_mode:
        payload["candle_prefetch_mode"] = candle_prefetch_mode

    config = resolve_config_with_preset(DynamicStrategyReplayConfig(**payload))
    result = await run_dynamic_replay(config, None)
    text = result.text if hasattr(result, "text") else str(result)
    return get_last_report_id(), text


async def send_promote_telegram(
    chat_id: str,
    job: PromoteJob,
    *,
    report_id: str | None = None,
    refine_started: bool = False,
) -> None:
    from condor.routine_hooks import _resolve_report_html
    from condor.routine_store import _http_bot

    result = job.result
    lines = [
        "New sweep leader",
        f"Config: {result.name}",
        f"Sweep cap-norm: ${result.capital_normalized_pnl:+.2f}",
        f"Raw PnL: ${result.pnl:+.2f}",
        f"Trades: {result.trades}",
        f"Preset: {job.preset_name}",
    ]
    if refine_started:
        lines.append(f"Refine: started (tag {job.output_tag})")
    elif refine_started is False:
        lines.append("Refine: skipped")

    await _http_bot.send_message(chat_id=chat_id, text="\n".join(lines))

    if report_id:
        html, filename = _resolve_report_html(report_id, None)
        await _http_bot.send_document(
            chat_id=chat_id,
            document=html.encode("utf-8"),
            caption=f"Backtest report: {job.preset_name}",
            filename=filename or f"{job.preset_name}.html",
        )


class RefineSubprocessManager:
    """Run refine A→D in a subprocess; cancel via SIGTERM on process group."""

    def __init__(
        self,
        *,
        repo_root: Path,
        output_dir: Path,
        snapshot_dir: Path,
        automation_state_path: Path,
        refine_workers: int = 2,
        checkpoint_every: int = 10,
        seed: int = 42,
    ) -> None:
        self._repo_root = repo_root
        self._output_dir = output_dir
        self._snapshot_dir = snapshot_dir
        self._automation_state_path = automation_state_path
        self._refine_workers = refine_workers
        self._checkpoint_every = checkpoint_every
        self._seed = seed
        self._process: subprocess.Popen[Any] | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def cancel(self) -> None:
        if not self.is_running():
            return
        assert self._process is not None
        pid = self._process.pid
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                self._process.terminate()
            except OSError:
                pass
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                self._process.kill()
        self._process = None

    def start(
        self,
        parent_overrides_path: Path,
        output_tag: str,
        *,
        tracker: LeaderTracker | None = None,
    ) -> int:
        self.cancel()
        if tracker is not None:
            tracker.request_refine_cancel()

        cmd = [
            sys.executable,
            "scripts/run_refine_sweep.py",
            "all",
            "--parent-overrides-json",
            str(parent_overrides_path),
            "--output-tag",
            output_tag,
            "--snapshot-dir",
            str(self._snapshot_dir),
            "--output-dir",
            str(self._output_dir),
            "--workers",
            str(self._refine_workers),
            "--checkpoint-every",
            str(self._checkpoint_every),
            "--seed",
            str(self._seed),
            "--automation-state-path",
            str(self._automation_state_path),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self._repo_root)

        self._process = subprocess.Popen(
            cmd,
            cwd=str(self._repo_root),
            env=env,
            start_new_session=True,
        )
        if tracker is not None:
            tracker.set_refine_pid(self._process.pid, output_tag)
            tracker.clear_refine_cancel()
        logger.info(
            "Started refine subprocess pid=%d tag=%s",
            self._process.pid,
            output_tag,
        )
        return self._process.pid


@dataclass
class PromoteAutomationConfig:
    enabled: bool = False
    telegram_chat_id: str | None = None
    run_refine: bool = True
    refine_workers: int = 2
    dynamic_mode: str = "both_on"
    sweep_grid: str = "entry_sltp"
    frequency_sec: int = DEFAULT_FREQUENCY_SEC
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN
    range_start_utc: str = ""
    range_end_utc: str = ""
    snapshot_dir: str = ""
    candle_prefetch_mode: str = "full"
    output_dir: Path = field(default_factory=lambda: Path("data/strategy_replay_sweeps"))
    automation_state_path: Path = field(
        default_factory=lambda: Path("data/strategy_replay_sweeps/automation.json")
    )
    repo_root: Path = field(default_factory=lambda: Path(".").resolve())


class PromoteQueue:
    """Serial async worker: preset -> backtest -> telegram -> refine."""

    def __init__(
        self,
        tracker: LeaderTracker,
        config: PromoteAutomationConfig,
    ) -> None:
        self._tracker = tracker
        self._config = config
        self._queue: asyncio.Queue[PromoteJob | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refine_manager = RefineSubprocessManager(
            repo_root=config.repo_root,
            output_dir=config.output_dir,
            snapshot_dir=Path(config.snapshot_dir),
            automation_state_path=config.automation_state_path,
            refine_workers=config.refine_workers,
        )

    def start(self) -> None:
        if self._worker_task is None:
            self._loop = asyncio.get_running_loop()
            self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(None)
            await self._worker_task
            self._worker_task = None
        self._refine_manager.cancel()

    def submit(self, job: PromoteJob | None) -> None:
        if job is None:
            return
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            logger.warning("Promote job dropped — promote worker not started")
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, job)

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                break
            try:
                await self._process_job(job)
            except Exception as error:
                logger.exception("Promote pipeline failed for %s: %s", job.result.name, error)
            finally:
                self._queue.task_done()

    async def _process_job(self, job: PromoteJob) -> None:
        cfg = self._config
        print(
            f"Auto-promote: processing {job.preset_name} ({job.result.name}) "
            f"cap_norm=${job.result.capital_normalized_pnl:+.2f}",
            flush=True,
        )
        full_overrides = build_full_config_from_result(
            job.result,
            dynamic_mode=cfg.dynamic_mode,
            sweep_grid=cfg.sweep_grid,
            frequency_sec=cfg.frequency_sec,
            time_window_min=cfg.time_window_min,
            range_start_utc=cfg.range_start_utc or None,
            range_end_utc=cfg.range_end_utc or None,
            snapshot_dir=cfg.snapshot_dir or None,
        )
        full_overrides["preset"] = "custom"

        register_sweep_lead_preset(job.preset_name, full_overrides)

        report_id, _text = await run_backtest_for_preset(
            job.preset_name,
            full_overrides=full_overrides,
            range_start_utc=cfg.range_start_utc or None,
            range_end_utc=cfg.range_end_utc or None,
            snapshot_dir=cfg.snapshot_dir or None,
            candle_prefetch_mode=cfg.candle_prefetch_mode,
        )

        refine_started = False
        if cfg.run_refine:
            if self._refine_manager.is_running():
                logger.info("Cancelling prior refine for new leader %s", job.output_tag)
                self._refine_manager.cancel()
                self._tracker.set_refine_pid(None)

            parent_json = (
                cfg.output_dir / f"{job.output_tag}_parent_overrides.json"
            )
            parent_json.write_text(
                json.dumps(full_overrides, indent=2, default=str),
                encoding="utf-8",
            )
            self._refine_manager.start(
                parent_json,
                job.output_tag,
                tracker=self._tracker,
            )
            refine_started = True

        chat_id = cfg.telegram_chat_id
        if chat_id:
            await send_promote_telegram(
                chat_id,
                job,
                report_id=report_id,
                refine_started=refine_started,
            )


def default_telegram_chat_id() -> str | None:
    raw = os.environ.get("ADMIN_USER_ID") or os.environ.get("SWEEP_TELEGRAM_CHAT_ID")
    return str(raw).strip() if raw else None


def is_refine_cancel_requested(state_path: Path) -> bool:
    return SweepLeaderState.load(state_path).refine_cancel_requested
