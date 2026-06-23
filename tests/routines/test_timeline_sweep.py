"""Tests for timeline mega sweep helpers and replay presets."""

import csv

import pytest

from routines.macdbb_replay.config_sweep import SweepResult, _dynamic_sweep_base
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import (
    DYNAMIC_PRESET_OVERRIDES,
    resolve_config_with_preset,
)
from routines.macdbb_replay.timeline_sweep import (
    _should_write_checkpoint,
    merge_timeline_config,
    replay_config_to_agent_strategy_params,
    run_timeline_dynamic_sweep,
    timeline_sweep_overrides,
)


def test_should_write_checkpoint_intervals():
    assert _should_write_checkpoint(1, 25, checkpoint_every=10) is True
    assert _should_write_checkpoint(10, 25, checkpoint_every=10) is True
    assert _should_write_checkpoint(11, 25, checkpoint_every=10) is False
    assert _should_write_checkpoint(20, 25, checkpoint_every=10) is True
    assert _should_write_checkpoint(25, 25, checkpoint_every=10) is True
    assert _should_write_checkpoint(5, 25, checkpoint_every=0) is False


@pytest.mark.asyncio
async def test_run_timeline_dynamic_sweep_writes_periodic_checkpoint_csv(
    tmp_path, monkeypatch
):
    configs = [
        ("cfg_low", {}),
        ("cfg_high", {}),
        ("cfg_mid", {}),
        ("cfg_baseline_winner", {}),
    ]
    pnls = [50.0, 300.0, 150.0, 10.0]

    def fake_iter(mode, min_configs, seed, parent_overrides=None):
        return configs

    def fake_run(name, *args, **kwargs):
        idx = [name for name, _ in configs].index(name)
        return SweepResult(
            name=name,
            pnl=pnls[idx],
            trades=10,
            formal=1,
            adaptive=0,
            win_rate=0.5,
            capital_normalized_pnl=pnls[idx],
        )

    async def fake_load_sessions(config):
        return ({1: {}}, {}, {}, {}, {}, [])

    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep.iter_mega_dynamic_sweep_configs",
        fake_iter,
    )
    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep._run_dynamic_config",
        fake_run,
    )
    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep._load_sessions",
        fake_load_sessions,
    )
    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep._apply_capital_metrics",
        lambda result, benchmark: result,
    )
    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep.build_reports_by_pair",
        lambda index: {},
    )
    monkeypatch.setattr(
        "routines.macdbb_replay.timeline_sweep.load_reports_index",
        lambda: {},
    )

    stem = "test_timeline_checkpoint"
    progress_path = tmp_path / f"{stem}.progress.json"
    await run_timeline_dynamic_sweep(
        dynamic_mode="sizing_only",
        output_dir=tmp_path,
        min_configs=4,
        output_stem=stem,
        top_n=3,
        progress_path=progress_path,
        checkpoint_every=2,
    )

    checkpoint_path = tmp_path / f"{stem}.checkpoint.csv"
    final_path = tmp_path / f"{stem}.csv"
    assert checkpoint_path.is_file()
    assert final_path.is_file()

    with checkpoint_path.open(encoding="utf-8") as handle:
        checkpoint_rows = list(csv.DictReader(handle))
    assert len(checkpoint_rows) == 4
    assert checkpoint_rows[0]["name"] == "cfg_high"
    assert checkpoint_rows[0]["rank"] == "1"

    progress = progress_path.read_text(encoding="utf-8")
    assert '"status": "completed"' in progress
    assert f"{stem}.checkpoint.csv" in progress

def test_timeline_sweep_overrides_sets_backtest_mode():
    overrides = timeline_sweep_overrides()
    assert overrides["replay_mode"] == "timeline_backtest"
    assert overrides["data_source"] == "snapshots"
    assert overrides["candle_source"] == "binance_perpetual"
    assert overrides["price_source"] == "reports"
    assert "session_nums" not in overrides
    assert overrides["range_start_utc"].endswith("Z")
    assert overrides["range_end_utc"].endswith("Z")


def test_timeline_preset_static_dict_has_default_snapshot_infra():
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_timeline_mega_best"]
    assert "session_nums" not in preset
    assert preset["replay_mode"] == "timeline_backtest"
    assert preset["snapshot_dir"] == "data/replay_snapshots_binance_1y"
    assert preset["range_start_utc"].endswith("Z")
    assert preset["range_end_utc"].endswith("Z")


def test_session_preset_static_dict_has_no_timeline_range():
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_session_parity"]
    assert preset["replay_mode"] == "session_parity"
    assert "session_nums" in preset
    assert "range_start_utc" not in preset
    assert "range_end_utc" not in preset
    assert "frequency_sec" not in preset


def test_session_parity_preset_has_no_tick_schedule():
    preset = DYNAMIC_PRESET_OVERRIDES["hl_dynamic_session_parity"]
    assert "tick_schedule" not in preset


def test_merge_timeline_config_includes_fixed_activation():
    merged = merge_timeline_config(_dynamic_sweep_base("both_on"))
    assert merged["activation_ticks"] == 0
    assert merged["replay_mode"] == "timeline_backtest"


def test_timeline_preset_loads_and_fills_range():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset="hl_dynamic_timeline_mega_best",
            range_start_utc=None,
            range_end_utc=None,
        )
    )
    assert config.replay_mode == "timeline_backtest"
    assert config.sl_pct == 4.5
    assert config.tp_pct == 6.2
    assert config.range_start_utc
    assert config.range_end_utc
    assert config.range_start_utc.endswith("Z")
    assert config.range_end_utc.endswith("Z")


def test_timeline_preset_respects_user_range():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset="hl_dynamic_timeline_mega_best",
            range_start_utc="2026-06-01T00:00:00Z",
            range_end_utc="2026-06-10T23:59:59Z",
        )
    )
    assert config.range_start_utc == "2026-06-01T00:00:00Z"
    assert config.range_end_utc == "2026-06-10T23:59:59Z"


def test_timeline_preset_respects_user_snapshot_dir():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset="hl_dynamic_timeline_mega_best",
            snapshot_dir="data/replay_snapshots_binance_1y",
        )
    )
    assert config.snapshot_dir == "data/replay_snapshots_binance_1y"
    assert config.replay_mode == "timeline_backtest"


def test_timeline_range_from_snapshots_uses_manifest():
    from routines.macdbb_replay.replay_range import timeline_range_from_snapshots

    start, end = timeline_range_from_snapshots("data/replay_snapshots_binance_1y")
    assert start.endswith("Z")
    assert end.endswith("Z")
    assert start < end


def test_dynamic_replay_field_groups():
    groups = DynamicStrategyReplayConfig.get_routine_groups()
    assert "Preset & mode" in groups
    assert "Timeline" in groups
    assert "Entry barriers" in groups


def test_dynamic_replay_field_metadata_has_groups_and_visibility():
    fields = DynamicStrategyReplayConfig.get_routine_fields()
    assert fields["range_start_utc"]["group"] == "Timeline"
    assert fields["range_start_utc"]["visible_when"]["replay_mode"] == "timeline_backtest"
    assert fields["session_nums"]["visible_when"]["replay_mode"] == "session_parity"
    assert fields["snapshot_dir"]["visible_when"]["data_source"] == "snapshots"
    assert fields["snapshot_dir"]["options_from"] == "replay_snapshot_dirs"
    assert fields["tick_schedule"]["hidden"] is True
    assert fields["config_source"]["hidden"] is True
    assert fields["price_source"]["hidden_when"]["data_source"] == "snapshots"


def test_replay_config_to_agent_strategy_params_hours():
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_timeline_mega_best")
    )
    params = replay_config_to_agent_strategy_params(config, frequency_sec=1800)
    assert params["thesis_decay_exit_hours"] == 14.0
    assert params["sl_pct"] == 4.5
