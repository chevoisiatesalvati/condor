"""Bounded subprocess pool for heavy routine execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from routines.base import RoutineResult

from condor.routine_worker import read_envelope, routine_result_from_dict

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "data" / "routine_runs"
DEFAULT_WORKER_RAM_GB = 3.0
STOP_GRACE_SEC = 5.0
LOG_TAIL_LINES = 40


def _tail_log_file(log_path: Path, *, max_lines: int = LOG_TAIL_LINES) -> str:
    if not log_path.is_file():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    tail = lines[-max_lines:]
    return "\n".join(tail)

CompletionCallback = Callable[["RoutineRunOutcome"], Awaitable[None]]


def _available_ram_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except OSError:
        pass
    return float(os.cpu_count() or 1) * 2.0


def resolve_worker_count(
    requested: int = 0,
    *,
    worker_ram_gb: float = DEFAULT_WORKER_RAM_GB,
) -> int:
    """Resolve parallel worker cap from CPU, RAM, and optional env override."""
    env_raw = os.environ.get("CONDOR_ROUTINE_MAX_WORKERS", "0").strip()
    try:
        env_cap = int(env_raw)
    except ValueError:
        env_cap = 0

    cpu = os.cpu_count() or 1
    ram_cap = max(1, int(_available_ram_gb() // max(worker_ram_gb, 0.5)))

    if requested <= 0 and env_cap <= 0:
        resolved = min(cpu, ram_cap)
    elif env_cap > 0 and requested <= 0:
        resolved = min(env_cap, cpu, ram_cap)
    elif requested > 0 and env_cap <= 0:
        resolved = min(requested, cpu, ram_cap)
    else:
        resolved = min(requested, env_cap, cpu, ram_cap)

    resolved = max(1, resolved)
    logger.info(
        "Routine worker pool: max_workers=%d (cpu=%d ram_cap=%d env=%s requested=%d)",
        resolved,
        cpu,
        ram_cap,
        env_raw or "auto",
        requested,
    )
    return resolved


@dataclass
class RoutineJob:
    instance_id: str
    routine_name: str
    config: dict[str, Any]
    server_name: str
    user_id: int = 0
    extra_routines_dir: str | None = None


@dataclass
class RoutineRunOutcome:
    instance_id: str
    ok: bool
    result: RoutineResult
    report_id: str | None
    duration_sec: float
    error: str | None = None
    stopped: bool = False


@dataclass
class _ActiveRun:
    job: RoutineJob
    process: asyncio.subprocess.Process
    config_path: Path
    result_path: Path
    log_path: Path
    future: asyncio.Future


class RoutineWorkerPool:
    """Queue + semaphore-limited subprocess workers."""

    def __init__(
        self,
        max_workers: int | None = None,
        *,
        worker_ram_gb: float = DEFAULT_WORKER_RAM_GB,
    ) -> None:
        self._max_workers = max_workers or resolve_worker_count(worker_ram_gb=worker_ram_gb)
        self._semaphore = asyncio.Semaphore(self._max_workers)
        self._queue: deque[tuple[RoutineJob, asyncio.Future]] = deque()
        self._queued_ids: list[str] = []
        self._active: dict[str, _ActiveRun] = {}
        self._pump_task: asyncio.Task | None = None
        self._stopped_ids: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def queue_position(self, instance_id: str) -> int | None:
        try:
            return self._queued_ids.index(instance_id) + 1
        except ValueError:
            return None

    def is_active(self, instance_id: str) -> bool:
        return instance_id in self._active

    def worker_pid(self, instance_id: str) -> int | None:
        run = self._active.get(instance_id)
        if run and run.process.pid:
            return run.process.pid
        return None

    async def start(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump_loop())

    async def submit(self, job: RoutineJob) -> asyncio.Future:
        """Enqueue a job; returns a Future that completes with RoutineRunOutcome."""
        await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        async with self._lock:
            if job.instance_id in self._stopped_ids:
                self._stopped_ids.discard(job.instance_id)
            self._queue.append((job, future))
            self._queued_ids.append(job.instance_id)
        logger.info(
            "Routine queued: %s[%s] server=%s queue_depth=%d",
            job.routine_name,
            job.instance_id,
            job.server_name,
            len(self._queued_ids),
        )
        return future

    async def run_job(self, job: RoutineJob) -> RoutineRunOutcome:
        future = await self.submit(job)
        return await future

    async def _pump_loop(self) -> None:
        while True:
            while await self._schedule_one():
                pass
            await asyncio.sleep(0.05)

    async def _schedule_one(self) -> bool:
        if self._semaphore.locked():
            return False
        async with self._lock:
            if not self._queue:
                return False
            job, future = self._queue[0]
            if job.instance_id in self._stopped_ids:
                self._queue.popleft()
                if job.instance_id in self._queued_ids:
                    self._queued_ids.remove(job.instance_id)
                if not future.done():
                    future.set_result(
                        RoutineRunOutcome(
                            instance_id=job.instance_id,
                            ok=False,
                            result=RoutineResult(text="Stopped by user"),
                            report_id=None,
                            duration_sec=0.0,
                            error="Stopped by user",
                            stopped=True,
                        )
                    )
                return True
            if self._semaphore.locked():
                return False
            self._queue.popleft()
            if job.instance_id in self._queued_ids:
                self._queued_ids.remove(job.instance_id)

        await self._semaphore.acquire()
        if job.instance_id in self._stopped_ids:
            self._semaphore.release()
            if not future.done():
                future.set_result(
                    RoutineRunOutcome(
                        instance_id=job.instance_id,
                        ok=False,
                        result=RoutineResult(text="Stopped by user"),
                        report_id=None,
                        duration_sec=0.0,
                        error="Stopped by user",
                        stopped=True,
                    )
                )
            return True

        asyncio.create_task(self._execute_job(job, future))
        return True

    async def _execute_job(self, job: RoutineJob, future: asyncio.Future) -> None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        config_path = RUNS_DIR / f"{job.instance_id}.config.json"
        result_path = RUNS_DIR / f"{job.instance_id}.result.json"
        log_path = RUNS_DIR / f"{job.instance_id}.log"

        config_path.write_text(
            json.dumps(job.config, ensure_ascii=False),
            encoding="utf-8",
        )

        python = sys.executable
        cmd = [
            python,
            "-m",
            "condor.routine_worker",
            "--routine",
            job.routine_name,
            "--config-json",
            str(config_path),
            "--server",
            job.server_name,
            "--user-id",
            str(job.user_id),
            "--result-file",
            str(result_path),
        ]
        if job.extra_routines_dir:
            cmd.extend(["--extra-routines-dir", job.extra_routines_dir])

        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(REPO_ROOT))

        logger.info(
            "Routine worker starting: %s[%s] pid=pending log=%s",
            job.routine_name,
            job.instance_id,
            log_path,
        )

        log_file = log_path.open("w", encoding="utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            log_file.close()
            self._semaphore.release()
            logger.error(
                "Routine worker spawn failed: %s[%s]: %s",
                job.routine_name,
                job.instance_id,
                exc,
            )
            if not future.done():
                future.set_result(
                    RoutineRunOutcome(
                        instance_id=job.instance_id,
                        ok=False,
                        result=RoutineResult(text=f"Error: {exc}"),
                        report_id=None,
                        duration_sec=0.0,
                        error=str(exc),
                    )
                )
            return

        logger.info(
            "Routine worker started: %s[%s] pid=%s log=%s",
            job.routine_name,
            job.instance_id,
            process.pid,
            log_path,
        )

        self._active[job.instance_id] = _ActiveRun(
            job=job,
            process=process,
            config_path=config_path,
            result_path=result_path,
            log_path=log_path,
            future=future,
        )

        try:
            returncode = await process.wait()
        finally:
            log_file.close()
            self._active.pop(job.instance_id, None)
            self._semaphore.release()

        stopped = job.instance_id in self._stopped_ids
        if stopped:
            self._stopped_ids.discard(job.instance_id)

        outcome = self._build_outcome(job, result_path, returncode, stopped=stopped)
        if outcome.ok:
            if outcome.report_id:
                logger.info(
                    "Routine finished: %s[%s] ok=true duration=%.1fs report_id=%s exit=%s",
                    job.routine_name,
                    job.instance_id,
                    outcome.duration_sec,
                    outcome.report_id,
                    returncode,
                )
            else:
                logger.warning(
                    "Routine finished: %s[%s] ok=true but no report_id duration=%.1fs exit=%s result=%s log=%s",
                    job.routine_name,
                    job.instance_id,
                    outcome.duration_sec,
                    returncode,
                    (outcome.result.text[:160] + "…")
                    if len(outcome.result.text) > 160
                    else outcome.result.text,
                    log_path,
                )
        elif outcome.stopped:
            logger.info(
                "Routine stopped: %s[%s] exit=%s",
                job.routine_name,
                job.instance_id,
                returncode,
            )
        else:
            log_tail = _tail_log_file(log_path)
            logger.error(
                "Routine failed: %s[%s] exit=%s error=%s log=%s\n--- worker log tail ---\n%s",
                job.routine_name,
                job.instance_id,
                returncode,
                outcome.error or "unknown",
                log_path,
                log_tail or "(empty)",
            )
        if not future.done():
            future.set_result(outcome)

    def _build_outcome(
        self,
        job: RoutineJob,
        result_path: Path,
        returncode: int,
        *,
        stopped: bool,
    ) -> RoutineRunOutcome:
        if stopped:
            return RoutineRunOutcome(
                instance_id=job.instance_id,
                ok=False,
                result=RoutineResult(text="Stopped by user"),
                report_id=None,
                duration_sec=0.0,
                error="Stopped by user",
                stopped=True,
            )

        if result_path.is_file():
            try:
                envelope = read_envelope(result_path)
                result = routine_result_from_dict(envelope.get("result") or {})
                return RoutineRunOutcome(
                    instance_id=job.instance_id,
                    ok=bool(envelope.get("ok")),
                    result=result,
                    report_id=envelope.get("report_id"),
                    duration_sec=float(envelope.get("duration_sec") or 0.0),
                    error=envelope.get("error"),
                    stopped=False,
                )
            except Exception as exc:
                return RoutineRunOutcome(
                    instance_id=job.instance_id,
                    ok=False,
                    result=RoutineResult(text=f"Error reading worker result: {exc}"),
                    report_id=None,
                    duration_sec=0.0,
                    error=str(exc),
                )

        return RoutineRunOutcome(
            instance_id=job.instance_id,
            ok=False,
            result=RoutineResult(
                text=f"Worker exited with code {returncode} and no result file"
            ),
            report_id=None,
            duration_sec=0.0,
            error=f"exit code {returncode}",
        )

    async def stop_instance(self, instance_id: str) -> bool:
        """Cancel queued or kill running subprocess."""
        found = False
        self._stopped_ids.add(instance_id)

        async with self._lock:
            if instance_id in self._queued_ids:
                found = True
                self._queued_ids.remove(instance_id)
                remaining = []
                while self._queue:
                    job, future = self._queue.popleft()
                    if job.instance_id == instance_id:
                        if not future.done():
                            future.set_result(
                                RoutineRunOutcome(
                                    instance_id=instance_id,
                                    ok=False,
                                    result=RoutineResult(text="Stopped by user"),
                                    report_id=None,
                                    duration_sec=0.0,
                                    error="Stopped by user",
                                    stopped=True,
                                )
                            )
                    else:
                        remaining.append((job, future))
                self._queue.extend(remaining)

        run = self._active.get(instance_id)
        if run:
            found = True
            process = run.process
            if process.returncode is None:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_SEC)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            if not run.future.done():
                run.future.set_result(
                    RoutineRunOutcome(
                        instance_id=instance_id,
                        ok=False,
                        result=RoutineResult(text="Stopped by user"),
                        report_id=None,
                        duration_sec=0.0,
                        error="Stopped by user",
                        stopped=True,
                    )
                )

        return found


_pool: RoutineWorkerPool | None = None


def get_routine_worker_pool(
    max_workers: int | None = None,
    *,
    worker_ram_gb: float = DEFAULT_WORKER_RAM_GB,
) -> RoutineWorkerPool:
    global _pool
    if _pool is None:
        _pool = RoutineWorkerPool(max_workers=max_workers, worker_ram_gb=worker_ram_gb)
    return _pool


def reset_routine_worker_pool() -> None:
    """Test helper to reset singleton."""
    global _pool
    _pool = None
