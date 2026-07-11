"""Tests for sweep auto-promote pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import SweepResult
from routines.macdbb_scanner_aggressive_hl_replay.sweep_automation import (
    LeaderTracker,
    PRESET_NAME_PREFIX,
    PromoteAutomationConfig,
    PromoteJob,
    PromoteQueue,
    RefineSubprocessManager,
    SweepLeaderState,
    build_full_config_from_result,
    is_refine_cancel_requested,
    register_sweep_lead_preset,
)


def _result(name: str, cap_norm: float, pnl: float = 0.0, trades: int = 10) -> SweepResult:
    return SweepResult(
        name=name,
        pnl=pnl,
        trades=trades,
        formal=1,
        adaptive=0,
        win_rate=0.5,
        overrides={"sl_pct": 3.8, "tp_min_pct": 10.0},
        capital_normalized_pnl=cap_norm,
        avg_notional=250.0,
    )


def test_leader_tracker_first_positive_sets_anchor_only(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)

    assert tracker.consider(_result("cfg_a", -5.0)) is None
    assert tracker.state.anchor_established is False

    assert tracker.consider(_result("cfg_b", 12.0)) is None
    assert tracker.state.anchor_established is True
    assert tracker.state.best_cap_norm == 12.0
    assert tracker.state.promote_count == 0


def test_leader_tracker_improvement_after_anchor_emits_job(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)

    tracker.consider(_result("anchor", 8.0))
    job = tracker.consider(_result("better", 15.0))

    assert job is not None
    assert job.preset_name == f"{PRESET_NAME_PREFIX}001"
    assert job.output_tag == "lead_001"
    assert tracker.state.best_cap_norm == 15.0


def test_leader_tracker_ignores_non_improving_results(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)

    tracker.consider(_result("anchor", 10.0))
    assert tracker.consider(_result("worse", 5.0)) is None
    assert tracker.consider(_result("same", 10.0)) is None


def test_leader_state_persists_across_reload(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)
    tracker.consider(_result("anchor", 4.0))
    tracker.consider(_result("better", 9.0))

    reloaded = LeaderTracker(state_path)
    assert reloaded.state.anchor_established is True
    assert reloaded.state.best_cap_norm == 9.0
    assert reloaded.state.promote_count == 1


def test_register_sweep_lead_preset_appends_without_winner_defaults(tmp_path: Path):
    presets_path = tmp_path / "presets.yaml"
    presets_path.write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "old_winner",
                "default_agent_strategy_preset": "old_winner",
                "dynamic_preset_overrides": {"old_winner": {"sl_pct": 1.0}},
            }
        ),
        encoding="utf-8",
    )

    register_sweep_lead_preset(
        f"{PRESET_NAME_PREFIX}001",
        {"sl_pct": 3.8, "tp_min_pct": 10.0, "preset": "custom"},
        presets_path=presets_path,
    )

    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert f"{PRESET_NAME_PREFIX}001" in bundle["dynamic_preset_overrides"]
    assert bundle["current_winner_preset"] == "old_winner"
    assert bundle["default_agent_strategy_preset"] == "old_winner"


def test_build_full_config_from_result_uses_entry_sltp_base():
    result = _result("cfg", 20.0)
    full = build_full_config_from_result(
        result,
        dynamic_mode="both_on",
        sweep_grid="entry_sltp",
        range_start_utc="2026-07-01T00:00:00+00:00",
        range_end_utc="2026-07-10T00:00:00+00:00",
    )
    assert full["enable_dynamic_sizing"] is True
    assert full["enable_dynamic_barriers"] is True
    assert full["sl_pct"] == 3.8
    assert full["range_start_utc"] == "2026-07-01T00:00:00+00:00"


def test_is_refine_cancel_requested(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    assert is_refine_cancel_requested(state_path) is False

    state_path.write_text(
        json.dumps({"refine_cancel_requested": True}),
        encoding="utf-8",
    )
    assert is_refine_cancel_requested(state_path) is True


@pytest.mark.asyncio
async def test_promote_queue_processes_job(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)
    tracker.consider(_result("anchor", 5.0))

    config = PromoteAutomationConfig(
        enabled=True,
        telegram_chat_id=None,
        run_refine=False,
        dynamic_mode="both_on",
        sweep_grid="entry_sltp",
        range_start_utc="2026-07-01T00:00:00+00:00",
        range_end_utc="2026-07-10T00:00:00+00:00",
        output_dir=tmp_path,
        automation_state_path=state_path,
        repo_root=tmp_path,
    )
    queue = PromoteQueue(tracker, config)
    queue.start()

    job = tracker.consider(_result("leader", 25.0))
    assert job is not None

    presets_path = tmp_path / "strategies" / "macdbb_scanner_aggressive_hl" / "presets.yaml"
    with (
        patch(
            "condor.trading_agent.strategy_paths.private_strategy_dir",
            return_value=tmp_path / "strategies" / "macdbb_scanner_aggressive_hl",
        ),
        patch(
            "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.run_backtest_for_preset",
            new_callable=AsyncMock,
            return_value=("report-1", "ok"),
        ),
        patch(
            "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.send_promote_telegram",
            new_callable=AsyncMock,
        ),
    ):
        await queue._queue.put(job)
        await queue._queue.put(None)
        await queue._worker_task

    assert presets_path.is_file()
    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert f"{PRESET_NAME_PREFIX}001" in bundle["dynamic_preset_overrides"]


def test_refine_subprocess_manager_cancel(tmp_path: Path, monkeypatch):
    manager = RefineSubprocessManager(
        repo_root=tmp_path,
        output_dir=tmp_path,
        snapshot_dir=tmp_path,
        automation_state_path=tmp_path / "automation.json",
    )

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.subprocess.Popen",
        lambda *a, **k: fake,
    )
    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.os.getpgid",
        lambda pid: pid,
    )
    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.os.killpg",
        lambda pgid, sig: None,
    )

    parent_json = tmp_path / "lead_001_parent_overrides.json"
    parent_json.write_text("{}", encoding="utf-8")
    manager.start(parent_json, "lead_001")
    assert manager.is_running()
    manager.cancel()
    assert not manager.is_running()
