"""Tests for subprocess routine worker pool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from condor.routine_runner import (
    RoutineJob,
    reset_routine_worker_pool,
    resolve_worker_count,
)
from condor.routine_store import RoutineStore
from routines.base import discover_routines_from_path

FIXTURES_ROUTINES = Path(__file__).resolve().parent.parent / "fixtures" / "routines"
STUB_NAME = "subprocess_echo"


@pytest.fixture
def stub_routine():
    routines = discover_routines_from_path(FIXTURES_ROUTINES)
    assert STUB_NAME in routines
    return routines[STUB_NAME]


@pytest.fixture
def patch_stub_routine(stub_routine, monkeypatch):
    def store_resolve(self, routine_name: str):
        if routine_name == STUB_NAME:
            return stub_routine
        return None

    monkeypatch.setattr(RoutineStore, "_resolve_routine", store_resolve)


@pytest.fixture
def extra_routines_dir():
    return str(FIXTURES_ROUTINES)


def _job(instance_id: str, extra_routines_dir: str, **config) -> RoutineJob:
    return RoutineJob(
        instance_id=instance_id,
        routine_name=STUB_NAME,
        config={"delay_sec": 0.05, "message": "x", **config},
        server_name="local",
        extra_routines_dir=extra_routines_dir,
    )


@pytest.fixture(autouse=True)
def reset_pool(monkeypatch):
    monkeypatch.setenv("CONDOR_EXTRA_ROUTINES_DIR", str(FIXTURES_ROUTINES))
    reset_routine_worker_pool()
    yield
    reset_routine_worker_pool()


def test_resolve_worker_count_respects_cpu_and_ram(monkeypatch):
    monkeypatch.setattr("condor.routine_runner.os.cpu_count", lambda: 8)
    monkeypatch.setattr("condor.routine_runner._available_ram_gb", lambda: 6.0)
    monkeypatch.delenv("CONDOR_ROUTINE_MAX_WORKERS", raising=False)
    assert resolve_worker_count(worker_ram_gb=3.0) == 2

    monkeypatch.setenv("CONDOR_ROUTINE_MAX_WORKERS", "4")
    assert resolve_worker_count(worker_ram_gb=3.0) == 2


@pytest.mark.asyncio
async def test_subprocess_round_trip(patch_stub_routine, extra_routines_dir):
    from condor.routine_runner import RoutineWorkerPool

    pool = RoutineWorkerPool(max_workers=1)
    job = _job("t1", extra_routines_dir, delay_sec=0.05, message="world")
    loop_free = asyncio.Event()

    async def probe():
        await asyncio.sleep(0)
        loop_free.set()

    probe_task = asyncio.create_task(probe())
    outcome = await pool.run_job(job)
    await probe_task
    assert loop_free.is_set()
    assert outcome.ok is True
    assert outcome.result.text == "echo:world"


@pytest.mark.asyncio
async def test_queue_serial_with_one_worker(patch_stub_routine, extra_routines_dir):
    from condor.routine_runner import RoutineWorkerPool

    pool = RoutineWorkerPool(max_workers=1)
    jobs = [
        _job(f"q{i}", extra_routines_dir, delay_sec=0.08, message=str(i))
        for i in range(3)
    ]
    futures = [await pool.submit(job) for job in jobs]
    assert pool.queue_position("q0") in (None, 1)
    results = [await f for f in futures]
    assert [r.result.text for r in results] == [
        "echo:0",
        "echo:1",
        "echo:2",
    ]


@pytest.mark.asyncio
async def test_parallel_workers(patch_stub_routine, extra_routines_dir):
    from condor.routine_runner import RoutineWorkerPool

    pool = RoutineWorkerPool(max_workers=2)
    jobs = [
        _job(f"p{i}", extra_routines_dir, delay_sec=0.2, message=str(i))
        for i in range(2)
    ]
    futures = [await pool.submit(job) for job in jobs]
    await asyncio.sleep(0.05)
    active = sum(1 for i in range(2) if pool.is_active(f"p{i}"))
    assert active == 2
    results = await asyncio.gather(*futures)
    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_stop_running_worker(patch_stub_routine, extra_routines_dir):
    from condor.routine_runner import RoutineWorkerPool

    pool = RoutineWorkerPool(max_workers=1)
    job = _job("stop1", extra_routines_dir, delay_sec=5.0, message="slow")
    future = await pool.submit(job)
    for _ in range(50):
        if pool.is_active("stop1"):
            break
        await asyncio.sleep(0.05)
    assert pool.is_active("stop1")
    stopped = await pool.stop_instance("stop1")
    assert stopped is True
    outcome = await future
    assert outcome.stopped is True


@pytest.mark.asyncio
async def test_worker_failure_invalid_config(patch_stub_routine, extra_routines_dir):
    from condor.routine_runner import RoutineWorkerPool

    pool = RoutineWorkerPool(max_workers=1)
    job = RoutineJob(
        instance_id="bad1",
        routine_name=STUB_NAME,
        config={"delay_sec": "not-a-float"},
        server_name="local",
        extra_routines_dir=extra_routines_dir,
    )
    outcome = await pool.run_job(job)
    assert outcome.ok is False
    assert outcome.error


@pytest.mark.asyncio
async def test_routine_store_execute_subprocess(patch_stub_routine):
    store = RoutineStore()
    instance_id = await store.execute(
        STUB_NAME,
        {"delay_sec": 0.05, "message": "store"},
        server_name="local",
    )
    assert instance_id
    inst = store.get_instance(instance_id)
    assert inst is not None
    assert inst["execution_mode"] == "subprocess"

    for _ in range(100):
        inst = store.get_instance(instance_id)
        if inst and inst["status"] in ("completed", "failed", "stopped"):
            break
        await asyncio.sleep(0.05)

    inst = store.get_instance(instance_id)
    assert inst["status"] == "completed"
    result = store.get_result(instance_id)
    assert result is not None
    assert result.text == "echo:store"


@pytest.mark.asyncio
async def test_routine_store_stop_keeps_instance(patch_stub_routine):
    store = RoutineStore()
    instance_id = await store.execute(
        STUB_NAME,
        {"delay_sec": 5.0, "message": "slow"},
        server_name="local",
    )
    for _ in range(50):
        inst = store.get_instance(instance_id)
        if inst and inst["status"] == "running":
            break
        await asyncio.sleep(0.05)

    stopped = await store.stop(instance_id)
    assert stopped is True
    inst = store.get_instance(instance_id)
    assert inst is not None
    assert inst["status"] == "stopped"
