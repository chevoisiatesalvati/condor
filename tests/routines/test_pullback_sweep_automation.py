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


def _result(name: str, pnl: float, trades: int = 5, **overrides) -> PullbackSweepResult:
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
    return PullbackSweepResult(
        name=name,
        pnl=pnl,
        trades=trades,
        overrides=config,
        stats={"net_pnl_quote": pnl, "trades": trades, "immediate": 1, "pullback": 2},
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

    async def _save(config, *, all_trades, session_rows):
        posted["saved_preset"] = config.preset
        posted["n_trades"] = len(all_trades)
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
