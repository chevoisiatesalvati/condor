"""Pullback sweep auto-promote: preset append, backtest report, Telegram."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from condor.strategy_runners.macdbb_pullback.presets import (
    invalidate_preset_cache,
    known_preset_names,
    strategy_params_from_preset,
)
from routines.macdbb_pullback_hl_replay.sweep_automation import (
    PRESET_NAME_PREFIX,
    LeaderTracker,
    PromoteJob,
    PullbackSweepResult,
    consider_and_promote,
    default_telegram_chat_id,
    latest_sweep_lead_number,
    latest_sweep_lead_preset,
    promote_telegram_text,
    register_sweep_lead_preset,
    send_promote_telegram_sync,
    strategy_overrides_from_result,
)

REAL_PRESETS_YAML = (
    Path(__file__).resolve().parents[2]
    / "strategies"
    / "macdbb_pullback_hl"
    / "presets.yaml"
)


@pytest.fixture(autouse=True)
def _isolate_strategy_presets(tmp_path, monkeypatch):
    isolated = tmp_path / "strategies"
    isolated.mkdir()
    monkeypatch.setenv("CONDOR_STRATEGIES_DIR", str(isolated))
    invalidate_preset_cache()
    baseline = (
        REAL_PRESETS_YAML.read_text(encoding="utf-8")
        if REAL_PRESETS_YAML.is_file()
        else None
    )
    yield
    invalidate_preset_cache()
    if baseline is not None:
        assert REAL_PRESETS_YAML.read_text(encoding="utf-8") == baseline, (
            "tests must not mutate strategies/macdbb_pullback_hl/presets.yaml"
        )


def _install_named_preset(tmp_path: Path, name: str) -> None:
    dest = tmp_path / "strategies" / "macdbb_pullback_hl"
    dest.mkdir(parents=True, exist_ok=True)
    dest.joinpath("presets.yaml").write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "agent_strategy_preset_names": ["pullback_decay_2h_60s", name],
                "dynamic_preset_overrides": {
                    "pullback_decay_2h_60s": {"sl_pct": 3.0, "tp_pct": 6.0},
                    name: {"sl_pct": 3.8, "tp_pct": 9.0},
                },
            }
        ),
        encoding="utf-8",
    )
    invalidate_preset_cache()


def _result(
    name: str,
    pnl: float,
    trades: int = 5,
    *,
    annualized_cap_norm: float | None = None,
    **overrides,
) -> PullbackSweepResult:
    config = {
        "impulse_atr_mult": 1.25,
        "pullback_epsilon_pct": 0.75,
        "sl_pct": 3.8,
        "tp_pct": 5.0,
        "chase_long_bb_pos_max": 70.0,
        "chase_short_bb_pos_min": 30.0,
        "frequency_sec": 60,
        "total_amount_quote": 100.0,
        "snapshot_dir": "data/replay_snapshots_binance_60s",
        **overrides,
    }
    stats = {"net_pnl_quote": pnl, "trades": trades, "immediate": 1, "pullback": 2}
    if annualized_cap_norm is not None:
        stats["annualized_cap_norm"] = annualized_cap_norm
        stats["window_days"] = 365.25
        stats["capital_normalized_pnl"] = pnl
    return PullbackSweepResult(
        name=name,
        pnl=pnl,
        trades=trades,
        overrides=config,
        stats=stats,
        annualized_cap_norm=annualized_cap_norm,
    )


def test_leader_tracker_first_positive_sets_anchor_only(tmp_path: Path):
    tracker = LeaderTracker(tmp_path / "automation.json")
    assert tracker.consider(_result("cfg_a", -5.0)) is None
    assert tracker.state.anchor_established is False
    assert tracker.consider(_result("cfg_b", 12.0)) is None
    assert tracker.state.anchor_established is True
    assert tracker.state.best_pnl == 12.0
    assert tracker.state.promote_count == 0


def test_leader_tracker_improvement_after_anchor_emits_job(tmp_path: Path):
    tracker = LeaderTracker(tmp_path / "automation.json")
    tracker.consider(_result("anchor", 8.0))
    job = tracker.consider(_result("better", 15.0))
    assert job is not None
    assert job.preset_name == f"{PRESET_NAME_PREFIX}001"
    assert job.output_tag == "lead_001"
    assert tracker.state.best_pnl == 15.0


def test_leader_tracker_ignores_non_improving_results(tmp_path: Path):
    tracker = LeaderTracker(tmp_path / "automation.json")
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
    assert reloaded.state.best_pnl == 9.0
    assert reloaded.state.promote_count == 1


def test_consider_screen_queues_anchor_and_improvements(tmp_path: Path):
    tracker = LeaderTracker(tmp_path / "automation.json")
    assert tracker.consider_screen(_result("neg", -3.0)) is False
    assert tracker.consider_screen(_result("anchor", 8.0)) is True
    assert tracker.state.anchor_established is True
    assert tracker.state.pending_verify_name == "anchor"
    assert tracker.state.promote_count == 0
    assert tracker.consider_screen(_result("worse", 4.0)) is False
    assert tracker.consider_screen(_result("better", 12.0)) is True
    assert tracker.state.best_pnl == 12.0
    assert tracker.state.pending_verify_name == "better"
    assert tracker.state.promote_count == 0


def test_consider_verified_promotes_only_after_positive_1y_improvement(tmp_path: Path):
    tracker = LeaderTracker(tmp_path / "automation.json")
    tracker.consider_screen(_result("anchor", 8.0))
    assert tracker.consider_verified(
        _result("anchor", 8.0, annualized_cap_norm=20.0)
    ) is None
    assert tracker.state.verify_anchor_established is True
    assert tracker.state.best_annual_cap_norm == 20.0
    assert tracker.state.pending_verify_name == ""
    assert tracker.consider_verified(
        _result("lucky_month", 40.0, annualized_cap_norm=15.0)
    ) is None
    assert tracker.state.best_annual_cap_norm == 20.0
    assert tracker.consider_verified(
        _result("still_red", 50.0, annualized_cap_norm=-1.0)
    ) is None
    job = tracker.consider_verified(
        _result("consistent", 14.0, annualized_cap_norm=45.0, pullback_epsilon_pct=1.25)
    )
    assert job is not None
    assert job.preset_name == f"{PRESET_NAME_PREFIX}001"
    assert job.result.overrides["frequency_sec"] == 60
    assert job.result.annualized_cap_norm == 45.0
    assert "range_start_utc" not in strategy_overrides_from_result(job.result)
    assert strategy_overrides_from_result(job.result)["frequency_sec"] == 60


def test_verify_json_roundtrip_skips_rerun(tmp_path: Path):
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        load_verify_result,
        save_verify_result,
    )

    payload = {
        "name": "cfg_a",
        "config": {"sl_pct": 3.8, "frequency_sec": 60},
        "stats": {"annualized_cap_norm": 33.0, "window_days": 365.0},
    }
    saved = save_verify_result(tmp_path, payload)
    assert saved.is_file()
    loaded = load_verify_result(tmp_path, "cfg_a")
    assert loaded is not None
    assert loaded["stats"]["annualized_cap_norm"] == 33.0
    assert load_verify_result(tmp_path, "missing") is None


def test_default_verify_range_start_is_one_calendar_year():
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        default_verify_range_start,
    )

    assert default_verify_range_start("2026-08-17T10:20:00Z") == "2025-08-17T10:20:00Z"


def test_verify_range_coverage_gap_detects_short_store(tmp_path: Path):
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        verify_range_coverage_gap,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import write_manifest

    write_manifest(
        {
            "range_start_utc": "2026-05-06T00:00:00Z",
            "range_end_utc": "2026-08-17T10:20:00Z",
            "tick_count": 134209,
            "frequency_sec": 60,
        },
        snapshot_dir=tmp_path,
    )
    gap = verify_range_coverage_gap(
        str(tmp_path),
        "2025-08-17T00:00:00Z",
        "2026-08-17T10:20:00Z",
    )
    assert gap is not None
    assert gap.gap_start_utc.startswith("2025-08-17")


def test_promote_telegram_includes_annualized():
    job = PromoteJob(
        result=_result(
            "cfg_y",
            12.0,
            annualized_cap_norm=146.1,
        ),
        preset_name=f"{PRESET_NAME_PREFIX}002",
        output_tag="lead_002",
    )
    text = promote_telegram_text(job)
    assert "Annualized cap-norm: $+146.10 (365.2d window)" in text
    assert "cfg_y" in text


def test_register_sweep_lead_does_not_flip_winner(tmp_path: Path):
    presets_path = tmp_path / "presets.yaml"
    presets_path.write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "agent_strategy_preset_names": ["pullback_decay_2h_60s"],
                "dynamic_preset_overrides": {
                    "pullback_decay_2h_60s": {"sl_pct": 3.0, "tp_pct": 6.0}
                },
            }
        ),
        encoding="utf-8",
    )
    register_sweep_lead_preset(
        f"{PRESET_NAME_PREFIX}001",
        {
            "sl_pct": 3.8,
            "tp_pct": 5.0,
            "preset": "custom",
            "total_amount_quote": 100.0,
            "snapshot_dir": "data/replay_snapshots_binance_60s",
        },
        presets_path=presets_path,
    )
    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    lead = bundle["dynamic_preset_overrides"][f"{PRESET_NAME_PREFIX}001"]
    assert lead["sl_pct"] == 3.8
    assert "total_amount_quote" not in lead
    assert "snapshot_dir" not in lead
    assert bundle["current_winner_preset"] == "pullback_decay_2h_60s"
    assert bundle["default_agent_strategy_preset"] == "pullback_decay_2h_60s"
    assert REAL_PRESETS_YAML.is_file()


def test_consider_and_promote_writes_lead(tmp_path: Path):
    presets_path = tmp_path / "presets.yaml"
    presets_path.write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "dynamic_preset_overrides": {},
            }
        ),
        encoding="utf-8",
    )
    tracker = LeaderTracker(tmp_path / "automation.json")
    assert consider_and_promote(
        tracker,
        _result("anchor", 2.0),
        presets_path=presets_path,
    ) is None
    job = consider_and_promote(
        tracker,
        _result("better", 9.5, pullback_epsilon_pct=0.75),
        presets_path=presets_path,
    )
    assert job is not None
    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert job.preset_name in bundle["dynamic_preset_overrides"]
    assert bundle["current_winner_preset"] == "pullback_decay_2h_60s"
    overrides = strategy_overrides_from_result(_result("x", 1.0, pullback_epsilon_pct=1.25))
    assert overrides["pullback_epsilon_pct"] == 1.25
    assert "total_amount_quote" not in overrides


def test_consider_and_promote_skips_existing_lead_numbers(tmp_path: Path):
    presets_path = tmp_path / "presets.yaml"
    existing = {
        f"{PRESET_NAME_PREFIX}{n:03d}": {"sl_pct": 3.0}
        for n in (1, 2, 3, 4, 5)
    }
    presets_path.write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "agent_strategy_preset_names": list(existing),
                "dynamic_preset_overrides": existing,
            }
        ),
        encoding="utf-8",
    )
    tracker = LeaderTracker(tmp_path / "automation.json", presets_path=presets_path)
    assert consider_and_promote(
        tracker,
        _result("anchor", 2.0),
        presets_path=presets_path,
    ) is None
    job = consider_and_promote(
        tracker,
        _result("better", 47.15),
        presets_path=presets_path,
    )
    assert job is not None
    assert job.preset_name == f"{PRESET_NAME_PREFIX}006"
    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert f"{PRESET_NAME_PREFIX}006" in bundle["dynamic_preset_overrides"]
    assert bundle["current_winner_preset"] == "pullback_decay_2h_60s"
    assert f"{PRESET_NAME_PREFIX}001" in bundle["dynamic_preset_overrides"]


def test_latest_sweep_lead_is_highest_index(tmp_path: Path):
    presets_path = tmp_path / "presets.yaml"
    existing = {
        f"{PRESET_NAME_PREFIX}{n:03d}": {"sl_pct": 3.0}
        for n in (1, 8, 12)
    }
    presets_path.write_text(
        yaml.safe_dump(
            {
                "agent_strategy_preset_names": list(existing),
                "dynamic_preset_overrides": existing,
            }
        ),
        encoding="utf-8",
    )
    assert latest_sweep_lead_number(presets_path) == 12
    assert latest_sweep_lead_preset(presets_path) == f"{PRESET_NAME_PREFIX}012"
    empty = tmp_path / "empty.yaml"
    empty.write_text(yaml.safe_dump({"dynamic_preset_overrides": {}}), encoding="utf-8")
    assert latest_sweep_lead_number(empty) is None
    assert latest_sweep_lead_preset(empty) is None


def test_yaml_lead_overlays_winner_strategy_params(tmp_path: Path):
    isolated = tmp_path / "strategies" / "macdbb_pullback_hl"
    isolated.mkdir(parents=True)
    isolated.joinpath("presets.yaml").write_text(
        yaml.safe_dump(
            {
                "current_winner_preset": "pullback_decay_2h_60s",
                "default_agent_strategy_preset": "pullback_decay_2h_60s",
                "agent_strategy_preset_names": [
                    "pullback_decay_2h_60s",
                    f"{PRESET_NAME_PREFIX}001",
                ],
                "dynamic_preset_overrides": {
                    f"{PRESET_NAME_PREFIX}001": {
                        "sl_pct": 3.8,
                        "tp_pct": 5.0,
                        "pullback_epsilon_pct": 0.75,
                        "impulse_atr_mult": 1.5,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    invalidate_preset_cache()
    params = strategy_params_from_preset(f"{PRESET_NAME_PREFIX}001")
    assert params["sl_pct"] == 3.8
    assert params["tp_pct"] == 5.0
    assert params["pullback_epsilon_pct"] == 0.75
    assert params["impulse_atr_mult"] == 1.5
    assert params["enable_thesis_decay_exit"] is True
    assert params["enable_flip_exit"] is False
    assert f"{PRESET_NAME_PREFIX}001" in known_preset_names()


def test_default_telegram_chat_id_reads_admin_user(monkeypatch):
    monkeypatch.delenv("SWEEP_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("ADMIN_USER_ID", "1089320799")
    assert default_telegram_chat_id() == "1089320799"


def test_lead_presets_missing_reports_skips_existing(tmp_path: Path, monkeypatch):
    _install_named_preset(tmp_path, f"{PRESET_NAME_PREFIX}001")

    def _list_reports(**kwargs):
        if kwargs.get("tag") == f"{PRESET_NAME_PREFIX}001":
            return ([{"id": "rep1", "tags": [f"{PRESET_NAME_PREFIX}001"]}], 1)
        return ([], 0)

    monkeypatch.setattr("condor.reports.list_reports", _list_reports)
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        lead_presets_missing_reports,
    )

    assert f"{PRESET_NAME_PREFIX}001" not in lead_presets_missing_reports()


def test_lead_presets_missing_reports_includes_new_leads(tmp_path: Path, monkeypatch):
    _install_named_preset(tmp_path, f"{PRESET_NAME_PREFIX}001")
    monkeypatch.setattr("condor.reports.list_reports", lambda **kwargs: ([], 0))
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        lead_presets_missing_reports,
    )

    assert f"{PRESET_NAME_PREFIX}001" in lead_presets_missing_reports()


def test_lead_presets_missing_reports_tracks_30d_and_1y_windows(
    tmp_path: Path, monkeypatch
):
    _install_named_preset(tmp_path, f"{PRESET_NAME_PREFIX}001")
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        REPORT_WINDOW_SWEEP_30D,
        REPORT_WINDOW_VERIFY_1Y,
        lead_presets_missing_reports,
    )

    name = f"{PRESET_NAME_PREFIX}001"

    monkeypatch.setattr("condor.reports.list_reports", lambda **kwargs: ([], 0))
    assert name in lead_presets_missing_reports(window_tag=REPORT_WINDOW_SWEEP_30D)
    assert name in lead_presets_missing_reports(window_tag=REPORT_WINDOW_VERIFY_1Y)

    monkeypatch.setattr(
        "condor.reports.list_reports",
        lambda **kwargs: (
            [{"id": "rep1", "tags": [name, REPORT_WINDOW_VERIFY_1Y]}],
            1,
        ),
    )
    assert name in lead_presets_missing_reports(window_tag=REPORT_WINDOW_SWEEP_30D)
    assert name not in lead_presets_missing_reports(window_tag=REPORT_WINDOW_VERIFY_1Y)

    monkeypatch.setattr(
        "condor.reports.list_reports",
        lambda **kwargs: ([{"id": "rep-legacy", "tags": [name]}], 1),
    )
    assert name not in lead_presets_missing_reports(window_tag=REPORT_WINDOW_SWEEP_30D)
    assert name not in lead_presets_missing_reports(window_tag=REPORT_WINDOW_VERIFY_1Y)


def test_start_lead_report_process_spawns_backtest_script(tmp_path: Path, monkeypatch):
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        start_lead_report_process,
    )

    posted: dict[str, object] = {}

    class _Proc:
        pid = 4242

    def _popen(command, **kwargs):
        posted["command"] = command
        posted["cwd"] = kwargs.get("cwd")
        return _Proc()

    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_replay.sweep_automation.subprocess.Popen",
        _popen,
    )

    pid = start_lead_report_process(
        preset_name="pullback_sweep_lead_006",
        range_start_utc="2026-07-18T00:00:00Z",
        range_end_utc="2026-08-17T10:20:00Z",
        snapshot_dir="data/replay_snapshots_binance_60s",
        telegram_chat_id="1089320799",
        log_dir=tmp_path,
        repo_root=tmp_path,
    )
    assert pid == 4242
    command = posted["command"]
    assert command[1].endswith("run_macdbb_pullback_lead_report.py")
    assert "pullback_sweep_lead_006" in command
    assert "--telegram-chat-id" in command
    assert posted["cwd"] == str(tmp_path)


def test_run_backtest_for_preset_calls_pullback_routine(tmp_path: Path, monkeypatch):
    from routines.base import RoutineResult

    _install_named_preset(tmp_path, "pullback_sweep_lead_006")

    async def _fake_run(config, _context):
        assert config.preset == "pullback_sweep_lead_006"
        assert config.total_amount_quote == 100.0
        return RoutineResult(text="ok")

    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_backtest.run",
        _fake_run,
    )
    monkeypatch.setattr(
        "condor.reports.reset_last_report_id",
        lambda: None,
    )
    monkeypatch.setattr(
        "condor.reports.get_last_report_id",
        lambda: "abc123",
    )

    import asyncio

    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        run_backtest_for_preset,
    )

    report_id, text = asyncio.run(
        run_backtest_for_preset(
            "pullback_sweep_lead_006",
            range_start_utc="2026-07-18T00:00:00Z",
            range_end_utc="2026-08-17T10:20:00Z",
        )
    )
    assert report_id == "abc123"
    assert text == "ok"


def test_save_lead_reports_from_shared_reuses_tape(tmp_path: Path, monkeypatch):
    _install_named_preset(tmp_path, "pullback_sweep_lead_006")
    posted: dict[str, object] = {}

    class _Trade:
        pnl_quote = 1.0
        notional_quote = 100.0
        entry_class = "pullback"
        exit_reason = "take_profit"
        sl_pct_used = 3.8
        tp_pct_used = 9.0
        pair = "BTC-USDT"
        side = "long"
        entry_time_utc = None
        exit_time_utc = None
        return_pct = 0.01

    def _simulate(**kwargs):
        posted["config_preset"] = kwargs["config"].preset
        posted["used_tape"] = kwargs.get("signal_tape")
        return [], [], [_Trade()], {
            "status": "ok",
            "total_trades": 1,
            "immediate_trades": 0,
            "pullback_trades": 1,
            "win_rate_pct": 100.0,
            "sl_before_tp_rate": 0.0,
            "net_pnl_quote": 1.0,
        }

    async def _save(config, *, all_trades, session_rows, extra_tags=None, title_suffix=None):
        posted["saved_preset"] = config.preset
        posted["n_trades"] = len(all_trades)
        posted["extra_tags"] = extra_tags
        posted["title_suffix"] = title_suffix
        return "ok", "rep-shared"

    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_replay.simulator.simulate_pullback_session",
        _simulate,
    )
    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_backtest.save_pullback_backtest_report",
        _save,
    )

    import asyncio

    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        save_lead_reports_from_shared,
    )

    shared = {
        "base_kwargs": {"preset": "pullback_decay_2h_60s", "total_amount_quote": 100.0},
        "loader": object(),
        "signal_tapes": {0: "tape0"},
        "parsed_sessions": {0: {1: object()}},
        "reports_by_pair": {},
        "hl_caches_by_session": {},
        "hl_candle_cache": {},
        "hl_barrier_candle_cache": {},
        "hl_vol_candle_cache": {},
    }
    saved = asyncio.run(
        save_lead_reports_from_shared(shared, ["pullback_sweep_lead_006"])
    )
    assert saved == [("pullback_sweep_lead_006", "rep-shared")]
    assert posted["saved_preset"] == "pullback_sweep_lead_006"
    assert posted["used_tape"] == "tape0"
    assert posted["n_trades"] == 1
    assert posted["extra_tags"] == []
    assert posted["title_suffix"] is None


def test_save_lead_reports_from_shared_tags_window(tmp_path: Path, monkeypatch):
    _install_named_preset(tmp_path, "pullback_sweep_lead_006")
    posted: dict[str, object] = {}

    class _Trade:
        pnl_quote = 1.0
        notional_quote = 100.0
        entry_class = "pullback"
        exit_reason = "take_profit"
        sl_pct_used = 3.8
        tp_pct_used = 9.0
        pair = "BTC-USDT"
        side = "long"
        entry_time_utc = None
        exit_time_utc = None
        return_pct = 0.01

    def _simulate(**kwargs):
        return [], [], [_Trade()], {
            "status": "ok",
            "total_trades": 1,
            "immediate_trades": 0,
            "pullback_trades": 1,
            "win_rate_pct": 100.0,
            "sl_before_tp_rate": 0.0,
            "net_pnl_quote": 1.0,
        }

    async def _save(config, *, all_trades, session_rows, extra_tags=None, title_suffix=None):
        posted["extra_tags"] = extra_tags
        posted["title_suffix"] = title_suffix
        return "ok", "rep-30d"

    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_replay.simulator.simulate_pullback_session",
        _simulate,
    )
    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_backtest.save_pullback_backtest_report",
        _save,
    )

    import asyncio

    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        REPORT_WINDOW_SWEEP_30D,
        save_lead_reports_from_shared,
    )

    shared = {
        "base_kwargs": {"preset": "pullback_decay_2h_60s", "total_amount_quote": 100.0},
        "loader": object(),
        "signal_tapes": {0: "tape0"},
        "parsed_sessions": {0: {1: object()}},
        "reports_by_pair": {},
        "hl_caches_by_session": {},
        "hl_candle_cache": {},
        "hl_barrier_candle_cache": {},
        "hl_vol_candle_cache": {},
    }
    saved = asyncio.run(
        save_lead_reports_from_shared(
            shared,
            ["pullback_sweep_lead_006"],
            window_tag=REPORT_WINDOW_SWEEP_30D,
        )
    )
    assert saved == [("pullback_sweep_lead_006", "rep-30d")]
    assert posted["extra_tags"] == [REPORT_WINDOW_SWEEP_30D]
    assert posted["title_suffix"] == "30d"


def test_save_pullback_backtest_report_sets_routine_source(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    class _FakeBuilder:
        def __init__(self, title):
            captured["title"] = title

        def source(self, source_type, source_name):
            captured["source"] = (source_type, source_name)
            return self

        def tags(self, tags):
            captured["tags"] = tags
            return self

        def manual_order(self):
            return self

        def kpi(self, *args, **kwargs):
            return self

        def markdown(self, *args, **kwargs):
            return self

        def table(self, *args, **kwargs):
            return self

        async def save(self):
            return "rep1"

    monkeypatch.setattr("condor.reports.ReportBuilder", _FakeBuilder)
    monkeypatch.setattr("condor.reports.get_last_report_id", lambda: "rep1")
    monkeypatch.setattr("condor.reports.reset_last_report_id", lambda: None)

    import asyncio

    from routines.macdbb_pullback_hl_backtest import Config, save_pullback_backtest_report

    _install_named_preset(tmp_path, "pullback_sweep_lead_001")

    text, report_id = asyncio.run(
        save_pullback_backtest_report(
            Config(preset="pullback_sweep_lead_001"),
            all_trades=[],
            session_rows=[],
        )
    )
    assert captured["source"] == ("routine", "macdbb_pullback_hl_backtest")
    assert "pullback_sweep_lead_001" in captured["tags"]
    assert report_id == "rep1"
    assert "Preset: pullback_sweep_lead_001" in text


def test_save_pullback_backtest_report_accepts_window_tag(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    class _FakeBuilder:
        def __init__(self, title):
            captured["title"] = title

        def source(self, source_type, source_name):
            return self

        def tags(self, tags):
            captured["tags"] = tags
            return self

        def manual_order(self):
            return self

        def kpi(self, *args, **kwargs):
            return self

        def markdown(self, *args, **kwargs):
            return self

        def table(self, *args, **kwargs):
            return self

        async def save(self):
            return "rep-30d"

    monkeypatch.setattr("condor.reports.ReportBuilder", _FakeBuilder)
    monkeypatch.setattr("condor.reports.get_last_report_id", lambda: "rep-30d")
    monkeypatch.setattr("condor.reports.reset_last_report_id", lambda: None)

    import asyncio

    from routines.macdbb_pullback_hl_backtest import Config, save_pullback_backtest_report
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        REPORT_WINDOW_SWEEP_30D,
    )

    _install_named_preset(tmp_path, "pullback_sweep_lead_001")

    _text, report_id = asyncio.run(
        save_pullback_backtest_report(
            Config(preset="pullback_sweep_lead_001"),
            all_trades=[],
            session_rows=[],
            extra_tags=[REPORT_WINDOW_SWEEP_30D],
            title_suffix="30d",
        )
    )
    assert report_id == "rep-30d"
    assert REPORT_WINDOW_SWEEP_30D in captured["tags"]
    assert str(captured["title"]).endswith("(30d)")


def _report_trade(**overrides):
    from types import SimpleNamespace

    payload = {
        "pair": "BTC-USDT",
        "side": "long",
        "entry_class": "pullback",
        "notional_quote": 100.0,
        "sl_pct_used": 3.8,
        "tp_pct_used": 9.0,
        "entry_time_utc": None,
        "exit_time_utc": None,
        "exit_reason": "thesis_decay",
        "return_pct": 0.01,
        "pnl_quote": 1.0,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_summarize_pullback_trades_counts_exit_mix():
    from routines.macdbb_pullback_hl_backtest import summarize_pullback_trades

    stats = summarize_pullback_trades(
        [
            _report_trade(
                exit_reason="stop_loss_close_proxy",
                pnl_quote=-3.8,
                entry_class="immediate",
            ),
            _report_trade(exit_reason="take_profit_close_proxy", pnl_quote=9.0),
            _report_trade(exit_reason="thesis_decay", pnl_quote=0.4),
            _report_trade(exit_reason="session_end", pnl_quote=1.5),
            _report_trade(exit_reason="flip_confirm", pnl_quote=-0.2),
        ]
    )
    assert stats["total_trades"] == 5
    assert stats["immediate_n"] == 1
    assert stats["pullback_n"] == 4
    assert stats["sl_n"] == 1
    assert stats["tp_n"] == 1
    assert stats["decay_n"] == 1
    assert stats["session_end_n"] == 1
    assert stats["flip_n"] == 1
    assert stats["wins"] == 3
    assert stats["avg_sl_pct"] == pytest.approx(3.8)
    assert stats["avg_tp_pct"] == pytest.approx(9.0)
    assert stats["avg_notional"] == pytest.approx(100.0)


def test_pullback_session_table_row_includes_exit_counts():
    from routines.macdbb_pullback_hl_backtest import pullback_session_table_row

    row = pullback_session_table_row(
        0,
        tick_count=100,
        trades=[
            _report_trade(exit_reason="stop_loss_close_proxy", pnl_quote=-1.0),
            _report_trade(exit_reason="take_profit_close_proxy", pnl_quote=2.0),
            _report_trade(exit_reason="thesis_decay"),
            _report_trade(exit_reason="session_end"),
        ],
    )
    assert row["Ticks"] == 100
    assert row["Trades"] == 4
    assert row["SL"] == 1
    assert row["TP"] == 1
    assert row["Decay"] == 1
    assert row["Session end"] == 1
    assert row["Flip"] == 0
    assert row["Avg SL/TP"] == "3.80% / 9.00%"
    assert row["Avg notional"] == 100.0


def test_save_pullback_backtest_report_includes_telegram_kpis(
    tmp_path: Path, monkeypatch
):
    captured: dict[str, object] = {"kpis": []}

    class _FakeBuilder:
        def __init__(self, title):
            captured["title"] = title

        def source(self, source_type, source_name):
            return self

        def tags(self, tags):
            return self

        def manual_order(self):
            return self

        def kpi(self, label, value, *args, **kwargs):
            captured["kpis"].append((label, value))
            return self

        def markdown(self, *args, **kwargs):
            return self

        def table(self, *args, **kwargs):
            return self

        async def save(self):
            return "rep1"

    monkeypatch.setattr("condor.reports.ReportBuilder", _FakeBuilder)
    monkeypatch.setattr("condor.reports.get_last_report_id", lambda: "rep1")
    monkeypatch.setattr("condor.reports.reset_last_report_id", lambda: None)

    import asyncio

    from condor.strategy_runners.macdbb_pullback.dynamic import ANNUALIZATION_DAYS
    from routines.macdbb_pullback_hl_backtest import Config, save_pullback_backtest_report

    _install_named_preset(tmp_path, "pullback_sweep_lead_001")

    text, report_id = asyncio.run(
        save_pullback_backtest_report(
            Config(
                preset="pullback_sweep_lead_001",
                range_start_utc="2026-07-18T00:00:00Z",
                range_end_utc="2026-08-17T00:00:00Z",
            ),
            all_trades=[
                _report_trade(
                    exit_reason="stop_loss_close_proxy",
                    pnl_quote=-3.8,
                    entry_class="immediate",
                ),
                _report_trade(exit_reason="take_profit_close_proxy", pnl_quote=9.0),
                _report_trade(exit_reason="thesis_decay", pnl_quote=0.5),
                _report_trade(exit_reason="session_end", pnl_quote=1.2),
            ],
            session_rows=[],
        )
    )
    kpi_map = dict(captured["kpis"])
    expected_annualized = 6.9 * (ANNUALIZATION_DAYS / 30.0)
    annualized_cell = f"${expected_annualized:+.2f} (30.0d window)"
    assert report_id == "rep1"
    assert kpi_map["Sim Trades"] == "4"
    assert kpi_map["Immediate"] == "1"
    assert kpi_map["Pullback"] == "3"
    assert kpi_map["Win rate"] == "75.0%"
    assert kpi_map["SL hits"] == "1"
    assert kpi_map["TP hits"] == "1"
    assert kpi_map["Decay"] == "1"
    assert kpi_map["Session end"] == "1"
    assert "Flip" not in kpi_map
    assert kpi_map["Capital-norm PnL"] == "$+6.90"
    assert kpi_map["Annualized cap-norm"] == annualized_cell
    assert kpi_map["SL rate"] == "0.250"
    assert kpi_map["Avg notional"] == "$100.00"
    assert kpi_map["Avg SL/TP"] == "3.80% / 9.00%"
    assert "SL hits: 1" in text
    assert "TP hits: 1" in text
    assert "Decay: 1" in text
    assert "Session end: 1" in text
    assert "Avg SL/TP: 3.80% / 9.00%" in text
    assert f"Annualized cap-norm: {annualized_cell}" in text
    assert "Range: 18 Jul → 17 Aug 2026" in text


def test_send_promote_telegram_sync_posts_message(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    job = PromoteJob(
        result=_result("imp1_pb1.25_sl3.8_tp9_cl80_cs30", 37.54, trades=96),
        preset_name=f"{PRESET_NAME_PREFIX}005",
        output_tag="lead_005",
    )
    text = promote_telegram_text(job)
    assert "imp1_pb1.25_sl3.8_tp9_cl80_cs30" in text
    assert "$+37.54" in text
    assert f"{PRESET_NAME_PREFIX}005" in text
    assert "Annualized cap-norm" not in text

    posted: dict[str, object] = {}

    class _Response:
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, timeout=None):
            posted["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return _Response()

    monkeypatch.setattr(
        "routines.macdbb_pullback_hl_replay.sweep_automation.httpx.Client",
        _Client,
    )
    send_promote_telegram_sync("1089320799", job)
    assert posted["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert posted["json"]["chat_id"] == "1089320799"
    assert posted["json"]["text"] == text
