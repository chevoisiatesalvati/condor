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
    REFINE_PRESET_NAME_PREFIX,
    PromoteAutomationConfig,
    PromoteJob,
    PromoteQueue,
    RefineSubprocessManager,
    SweepLeaderState,
    build_full_config_from_result,
    is_refine_cancel_requested,
    refine_phase_csv_path,
    register_sweep_lead_preset,
)

REAL_PRESETS_YAML = (
    Path(__file__).resolve().parents[1]
    / "strategies"
    / "macdbb_scanner_aggressive_hl"
    / "presets.yaml"
)


@pytest.fixture(autouse=True)
def _isolate_strategy_presets(tmp_path, monkeypatch):
    """Never read/write the repo strategies submodule presets.yaml during tests."""
    isolated = tmp_path / "strategies"
    isolated.mkdir()
    monkeypatch.setenv("CONDOR_STRATEGIES_DIR", str(isolated))
    baseline = (
        REAL_PRESETS_YAML.read_text(encoding="utf-8")
        if REAL_PRESETS_YAML.is_file()
        else None
    )
    yield
    if baseline is not None:
        assert REAL_PRESETS_YAML.read_text(encoding="utf-8") == baseline, (
            "tests must not mutate strategies/macdbb_scanner_aggressive_hl/presets.yaml"
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


def test_build_full_config_roundtrip_preserves_sweep_config():
    from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
        resolve_sweep_config_iterator,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
        merge_timeline_config,
    )

    name = (
        "dyn_both_on_entry_sltp_sl3.8_slmin1.4_tpmin10.0_L76_S78_sc3.5_td44_eps0.22"
    )
    items = dict(
        resolve_sweep_config_iterator(
            "entry_sltp",
            "both_on",
            min_configs=50000,
            seed=42,
        )
    )
    merged = merge_timeline_config(
        items[name],
        range_start_utc="2026-04-10T00:00:00Z",
        range_end_utc="2026-07-10T23:59:59Z",
        sweep_grid="entry_sltp",
    )
    result = SweepResult(
        name=name,
        pnl=1.0,
        trades=680,
        formal=1,
        adaptive=0,
        win_rate=0.5,
        overrides=merged,
        capital_normalized_pnl=66.82,
        avg_notional=250.0,
    )
    full = build_full_config_from_result(
        result,
        dynamic_mode="both_on",
        sweep_grid="entry_sltp",
        range_start_utc="2026-04-10T00:00:00Z",
        range_end_utc="2026-07-10T23:59:59Z",
        snapshot_dir="data/replay_snapshots_binance_1y",
    )
    assert full["adaptive_short_bb_pos_min"] == merged["adaptive_short_bb_pos_min"]
    assert full["sl_pct"] == merged["sl_pct"]
    assert full["replay_mode"] == "timeline_backtest"


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
            "condor.agents.strategy_paths.private_strategy_dir",
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


@pytest.mark.asyncio
async def test_promote_queue_submit_from_worker_thread(tmp_path: Path):
    """Sweep on_result runs in a thread; promote jobs must still enqueue."""
    import asyncio

    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)
    tracker.consider(_result("anchor", 5.0))

    config = PromoteAutomationConfig(
        enabled=True,
        telegram_chat_id=None,
        run_refine=False,
        dynamic_mode="both_on",
        sweep_grid="entry_sltp",
        output_dir=tmp_path,
        automation_state_path=state_path,
        repo_root=tmp_path,
    )
    queue = PromoteQueue(tracker, config)
    queue.start()

    job = tracker.consider(_result("leader", 25.0))
    assert job is not None

    processed: asyncio.Event = asyncio.Event()

    async def fake_process(job: PromoteJob) -> None:
        processed.set()

    with patch.object(queue, "_process_job", side_effect=fake_process):
        await asyncio.to_thread(queue.submit, job)
        await asyncio.wait_for(processed.wait(), timeout=2.0)

    await queue.shutdown()


def test_refine_subprocess_manager_cancel(tmp_path: Path, monkeypatch):
    manager = RefineSubprocessManager(
        repo_root=tmp_path,
        output_dir=tmp_path,
        snapshot_dir=tmp_path,
        automation_state_path=tmp_path / "automation.json",
    )

    class FakeProcess:
        pid = 4242
        _alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self._alive = False
            return None

        def kill(self):
            self._alive = False
            return None

        def wait(self, timeout=None):
            self._alive = False
            return -15

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
        lambda pgid, sig: fake.terminate(),
    )

    parent_json = tmp_path / "lead_001_parent_overrides.json"
    parent_json.write_text("{}", encoding="utf-8")
    process = manager.start(parent_json, "lead_001")
    assert manager.is_running()
    assert process is fake
    manager.cancel()
    assert fake.wait() == -15
    assert fake.poll() == 0


@pytest.mark.asyncio
async def test_refine_completion_registers_preset_and_backtest(tmp_path: Path):
    state_path = tmp_path / "automation.json"
    tracker = LeaderTracker(state_path)
    snapshot_dir = tmp_path / "replay_snapshots_binance_1y"
    snapshot_dir.mkdir()

    merged = {
        "replay_mode": "timeline_backtest",
        "enable_dynamic_sizing": True,
        "enable_dynamic_barriers": True,
        "sl_pct": 3.8,
        "range_start_utc": "2026-04-10T00:00:00Z",
        "range_end_utc": "2026-07-10T23:59:59Z",
    }
    winner = _result("refine_D_winner", 900.0, pnl=500.0, trades=120)
    winner.overrides = merged

    phase_d = refine_phase_csv_path(tmp_path, snapshot_dir, "lead_007", "D")
    phase_d.parent.mkdir(parents=True, exist_ok=True)
    import csv
    import json

    with phase_d.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "name",
                "pnl",
                "capital_normalized_pnl",
                "pnl_per_exposure",
                "trades",
                "formal",
                "adaptive",
                "win_rate_pct",
                "exit_tp",
                "exit_sl",
                "exit_thesis_decay",
                "exit_session_end",
                "exit_flip",
                "exit_other",
                "total_exposure",
                "avg_notional",
                "avg_size_mult",
                "avg_sl_pct",
                "avg_tp_pct",
                "sl_saturation_pct",
                "tp_saturation_pct",
                "dynamic_mode",
                "snapshot_dir",
                "overrides_json",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "rank": "1",
                "name": winner.name,
                "pnl": str(winner.pnl),
                "capital_normalized_pnl": str(winner.capital_normalized_pnl),
                "pnl_per_exposure": "0.01",
                "trades": str(winner.trades),
                "formal": "1",
                "adaptive": "0",
                "win_rate_pct": "50.0",
                "exit_tp": "1",
                "exit_sl": "0",
                "exit_thesis_decay": "0",
                "exit_session_end": "0",
                "exit_flip": "0",
                "exit_other": "0",
                "total_exposure": "1000",
                "avg_notional": "250",
                "avg_size_mult": "1.0",
                "avg_sl_pct": "2.0",
                "avg_tp_pct": "5.0",
                "sl_saturation_pct": "0",
                "tp_saturation_pct": "0",
                "dynamic_mode": "both_on",
                "snapshot_dir": str(snapshot_dir),
                "overrides_json": json.dumps(merged),
            }
        )

    config = PromoteAutomationConfig(
        telegram_chat_id="12345",
        dynamic_mode="both_on",
        sweep_grid="entry_sltp",
        range_start_utc="2026-04-10T00:00:00Z",
        range_end_utc="2026-07-10T23:59:59Z",
        snapshot_dir=str(snapshot_dir),
        output_dir=tmp_path,
        automation_state_path=state_path,
        repo_root=tmp_path,
    )
    queue = PromoteQueue(tracker, config)
    presets_path = tmp_path / "strategies" / "macdbb_scanner_aggressive_hl" / "presets.yaml"

    with (
        patch(
            "condor.agents.strategy_paths.private_strategy_dir",
            return_value=tmp_path / "strategies" / "macdbb_scanner_aggressive_hl",
        ),
        patch(
            "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.run_backtest_for_preset",
            new_callable=AsyncMock,
            return_value=("report-refine", "ok"),
        ) as backtest_mock,
        patch(
            "routines.macdbb_scanner_aggressive_hl_replay.sweep_automation.send_refine_complete_telegram",
            new_callable=AsyncMock,
        ) as telegram_mock,
    ):
        await queue._process_refine_completion("lead_007")

    preset_name = f"{REFINE_PRESET_NAME_PREFIX}lead_007"
    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert preset_name in bundle["dynamic_preset_overrides"]
    backtest_mock.assert_awaited_once()
    telegram_mock.assert_awaited_once()
    assert (tmp_path / "lead_007_refine_winner_overrides.json").is_file()
