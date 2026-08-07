"""Tests for routine progress JSON and log-tail API."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routines_module
from condor.routine_progress import (
    read_log_tail,
    read_progress,
    resolve_safe_log_path,
    write_progress,
)
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    TickMeta,
)
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

USER = WebUser(id=1, username="u", first_name="U", role="user")


def test_write_and_read_progress(tmp_path: Path):
    path = tmp_path / "inst.progress.json"
    written = write_progress(
        phase="simulate",
        message="tick 10/100",
        current=10,
        total=100,
        path=path,
    )
    assert written == path
    data = read_progress(path)
    assert data is not None
    assert data["phase"] == "simulate"
    assert data["current"] == 10
    assert data["total"] == 100
    assert data["percent"] == 10.0
    assert data["message"] == "tick 10/100"
    assert "updated_at" in data


def test_read_log_tail_offset_and_initial_tail(tmp_path: Path):
    log_path = tmp_path / "run.log"
    lines = [f"line-{i}" for i in range(50)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = read_log_tail(log_path, offset=0, tail=10)
    assert first["lines"] == lines[-10:]
    assert first["next_offset"] == log_path.stat().st_size
    assert first["truncated"] is True

    # Append more content; poll from next_offset.
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("line-new\n")
    second = read_log_tail(log_path, offset=first["next_offset"], tail=200)
    assert second["lines"] == ["line-new"]
    assert second["next_offset"] > first["next_offset"]


def test_resolve_safe_log_path_rejects_traversal(tmp_path: Path):
    runs = tmp_path / "routine_runs"
    runs.mkdir()
    safe = runs / "abc.log"
    safe.write_text("ok\n", encoding="utf-8")
    assert resolve_safe_log_path(str(safe), runs_dir=runs) == safe.resolve()
    # Relative paths are basename-scoped into runs_dir (``..`` cannot escape).
    assert resolve_safe_log_path("../etc/passwd", runs_dir=runs) == (
        runs / "passwd"
    ).resolve()
    outside = tmp_path / "outside.log"
    outside.write_text("nope\n", encoding="utf-8")
    assert resolve_safe_log_path(str(outside), runs_dir=runs) is None


class _LogStore:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get_instance_logs(self, instance_id, *, offset=0, tail=200):
        self.calls.append((instance_id, offset, tail))
        if instance_id == "missing":
            return None
        return {**self._payload, "instance_id": instance_id}


@pytest.fixture
def logs_client(monkeypatch):
    store = _LogStore(
        {
            "lines": ["hello"],
            "next_offset": 6,
            "truncated": False,
            "complete": False,
            "size": 6,
            "progress": {"phase": "prefetch_candles", "percent": 25.0},
        }
    )
    monkeypatch.setattr(routines_module, "get_routine_store", lambda: store)
    app = FastAPI()
    app.include_router(routines_module.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app), store


def test_logs_api_returns_tail(logs_client):
    client, store = logs_client
    resp = client.get("/routines/instances/inst-1/logs?tail=50&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"] == ["hello"]
    assert body["progress"]["phase"] == "prefetch_candles"
    assert store.calls == [("inst-1", 0, 50)]


def test_logs_api_404_for_unknown(logs_client):
    client, _store = logs_client
    resp = client.get("/routines/instances/missing/logs")
    assert resp.status_code == 404


def test_store_get_instance_logs_uses_runs_dir(tmp_path: Path, monkeypatch):
    from condor import routine_store as store_mod
    from condor.routine_store import RoutineStore

    runs = tmp_path / "routine_runs"
    runs.mkdir()
    monkeypatch.setattr(store_mod, "RUNS_DIR", runs)

    instance_id = "live-1"
    log_path = runs / f"{instance_id}.log"
    log_path.write_text("a\nb\nc\n", encoding="utf-8")
    write_progress(
        phase="hydrate",
        message="loading",
        current=1,
        total=4,
        path=runs / f"{instance_id}.progress.json",
    )

    store = RoutineStore.__new__(RoutineStore)
    store._instances = {
        instance_id: {
            "status": "running",
            "log_path": str(log_path),
            "routine_name": "demo",
        }
    }
    store._results = {}

    payload = store.get_instance_logs(instance_id, offset=0, tail=2)
    assert payload is not None
    assert payload["lines"] == ["b", "c"]
    assert payload["complete"] is False
    assert payload["progress"]["phase"] == "hydrate"
    assert payload["progress"]["percent"] == 25.0

    detail = store.get_instance(instance_id)
    assert detail is not None
    assert detail["progress"]["phase"] == "hydrate"


def test_simulator_on_progress_callback_fires():
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    tick_meta_map = {
        i: TickMeta(
            tick=i,
            timestamp=base + dt.timedelta(seconds=i),
            macd_pairs=[],
        )
        for i in range(8)
    }
    config = DynamicStrategyReplayConfig(
        require_price_data=False,
        write_csv=False,
        data_source="html_only",
        replay_mode="timeline_backtest",
    )
    calls: list[tuple[int, int]] = []
    simulate_strategy_session(
        session_num=1,
        tick_meta_map=tick_meta_map,
        reports_by_pair={},
        config=config,
        on_progress=lambda current, total: calls.append((current, total)),
    )
    assert calls, "on_progress should fire at least once"
    assert calls[0][1] == 8
    assert calls[-1] == (8, 8)
