"""UI metadata for macdbb_pullback_hl_backtest config."""

from condor.strategy_runners.macdbb_pullback.params import MacdbbPullbackHlParams
from condor.strategy_runners.macdbb_pullback.presets import (
    known_preset_names,
    preset_labels,
)
from routines.macdbb_pullback_hl_replay.models import PullbackReplayConfig


def test_pullback_replay_field_groups():
    groups = PullbackReplayConfig.get_routine_groups()
    assert groups[:2] == ["Preset & data", "Timeline"]
    assert "Thesis" in groups
    assert "Candle prefetch" in groups
    assert PullbackReplayConfig.get_routine_expanded_groups() == [
        "Preset & data",
        "Timeline",
    ]


def test_pullback_replay_field_metadata_has_groups_and_dropdowns():
    fields = PullbackReplayConfig.get_routine_fields()
    expected_options = sorted(
        known_preset_names(), key=lambda opt: (opt != "custom", opt)
    )
    labels = preset_labels()

    assert fields["preset"]["widget"] == "select"
    assert fields["preset"]["options"] == expected_options
    assert fields["preset"]["group"] == "Preset & data"
    assert "custom" in fields["preset"]["options"]
    assert fields["preset"]["option_labels"]["custom"] == labels.get("custom", "Custom")
    assert fields["preset"]["option_labels"]["pullback_decay_2h_60s"] == labels[
        "pullback_decay_2h_60s"
    ]

    assert fields["snapshot_dir"]["widget"] == "select"
    assert fields["snapshot_dir"]["options_from"] == "replay_snapshot_dirs"
    assert fields["snapshot_dir"]["group"] == "Preset & data"

    assert fields["candle_source"]["widget"] == "select"
    assert fields["candle_source"]["options"] == ["hyperliquid", "binance_perpetual"]
    assert fields["candle_source"]["option_labels"]["hyperliquid"] == "Hyperliquid"

    assert fields["frequency_sec"]["widget"] == "select"
    assert fields["frequency_sec"]["options"] == ["60", "300", "1800"]
    assert fields["frequency_sec"]["option_labels"]["60"] == "1 minute"
    assert fields["frequency_sec"]["group"] == "Timeline"

    assert fields["range_start_utc"]["group"] == "Timeline"
    assert fields["range_start_utc"]["widget"] == "date"
    assert fields["hl_price_interval"]["widget"] == "select"
    assert fields["hl_price_interval"]["group"] == "Candle prefetch"
    assert "1m" in fields["hl_price_interval"]["options"]


def test_pullback_replay_hides_internal_fields():
    fields = PullbackReplayConfig.get_routine_fields()
    for name in (
        "strategy_slug",
        "strategy_params",
        "replay_mode",
        "data_source",
        "sessions",
        "compare_journal_flags",
        "use_shared_decide",
        "pullback_timeout_ticks",
        "sl_cooldown_ticks",
        "thesis_decay_exit_ticks",
        "flip_cooldown_ticks",
        "auto_update_snapshots",
        "write_csv",
        "hl_cache_dir",
        "price_source",
    ):
        assert fields[name]["hidden"] is True, name


def test_pullback_replay_reuses_strategy_param_labels():
    fields = PullbackReplayConfig.get_routine_fields()
    strategy_fields = MacdbbPullbackHlParams.get_fields()
    assert fields["bb_proximity_epsilon_pct"]["group"] == "Thesis"
    assert (
        fields["bb_proximity_epsilon_pct"]["description"]
        == strategy_fields["bb_proximity_epsilon_pct"]["description"]
    )
    assert fields["impulse_atr_mult"]["group"] == "Entry quality"
    assert fields["pullback_timeout_hours"]["group"] == "Pullback arm"
    assert fields["sl_pct"]["group"] == "Barriers"
    assert fields["enable_dynamic_barriers"]["group"] == "Barriers"
    assert fields["enable_thesis_decay_exit"]["group"] == "Position monitor"
    assert fields["min_notional_quote"]["group"] == "Sizing"
    assert fields["enable_dynamic_sizing"]["group"] == "Sizing"


def test_pullback_preset_is_strategy_only_hl_run_defaults():
    from condor.strategy_runners.macdbb_pullback.presets import (
        DEFAULT_HL_60S_SNAPSHOT_DIR,
        DEFAULT_HL_CANDLE_CACHE_DIR,
        DEFAULT_TIMELINE_PRESET,
        DEFAULT_WINNER_PRESET,
        PRESET_CAPITAL_KEYS,
        PRESET_OVERRIDES,
        strategy_params_from_preset,
    )
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config

    winner = PRESET_OVERRIDES[DEFAULT_WINNER_PRESET]
    assert "candle_source" not in winner
    assert "snapshot_dir" not in winner
    assert "hl_cache_dir" not in winner
    assert "live_equivalent_queue" not in winner
    assert "total_amount_quote" not in winner
    params = strategy_params_from_preset(DEFAULT_WINNER_PRESET)
    assert not (PRESET_CAPITAL_KEYS & set(params))

    timeline = PRESET_OVERRIDES[DEFAULT_TIMELINE_PRESET]
    assert "candle_source" not in timeline
    assert "snapshot_dir" not in timeline

    resolved = resolve_pullback_config(PullbackReplayConfig())
    assert resolved.candle_source == "hyperliquid"
    assert resolved.snapshot_dir == DEFAULT_HL_60S_SNAPSHOT_DIR
    assert resolved.hl_cache_dir == DEFAULT_HL_CANDLE_CACHE_DIR
    assert resolved.live_equivalent_queue is True
    assert resolved.total_amount_quote == 100.0
    assert resolved.min_notional_quote == 10.0
    assert resolved.max_notional_quote == 1000.0
    assert "total_amount_quote" not in resolved.strategy_params


def test_resolve_pullback_config_keeps_winner_thesis_decay():
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config

    resolved = resolve_pullback_config(
        PullbackReplayConfig(preset="pullback_decay_2h_60s")
    )
    assert resolved.enable_thesis_decay_exit is True
    assert resolved.thesis_decay_exit_hours == 2.0
    assert resolved.thesis_decay_exit_ticks == 120
    assert resolved.strategy_params["enable_thesis_decay_exit"] is True
    assert resolved.strategy_params["thesis_decay_exit_hours"] == 2.0
    assert resolved.strategy_params["thesis_decay_exit_ticks"] == 120

    overridden = resolve_pullback_config(
        PullbackReplayConfig(
            preset="pullback_decay_2h_60s",
            enable_thesis_decay_exit=False,
            live_equivalent_queue=True,
            total_amount_quote=50.0,
        )
    )
    assert overridden.enable_thesis_decay_exit is False
    assert overridden.live_equivalent_queue is True
    assert overridden.total_amount_quote == 50.0


def test_resolve_keeps_sessions_and_snapshot_dir():
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config

    resolved = resolve_pullback_config(
        PullbackReplayConfig(
            preset="pullback_decay_2h_60s",
            sessions="2",
            snapshot_dir="data/replay_snapshots_hl_60s_session2",
            total_amount_quote=50.0,
        )
    )
    assert resolved.sessions == "2"
    assert resolved.snapshot_dir == "data/replay_snapshots_hl_60s_session2"
    assert resolved.total_amount_quote == 50.0


def test_journal_tick_maps_reads_session_journal(tmp_path, monkeypatch):
    from routines.macdbb_pullback_hl_backtest import journal_tick_maps

    session_dir = tmp_path / "session_2"
    session_dir.mkdir()
    (session_dir / "journal.md").write_text(
        "## Ticks\n"
        "- tick#1 | 2026-08-08 22:00 | actions=0 | no_entry_candidate\n"
        "- tick#548 | 2026-08-09 08:21 | actions=0 | creates=1 stops=0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.paths.strategy_sessions_dir",
        lambda _slug, agent_slug=None: tmp_path,
    )
    maps = journal_tick_maps(
        PullbackReplayConfig(sessions="2", strategy_slug="macdbb_pullback_hl")
    )
    assert set(maps) == {2}
    assert maps[2][1].timestamp.isoformat() == "2026-08-08T22:00:00+00:00"
    assert maps[2][548].timestamp.isoformat() == "2026-08-09T08:21:00+00:00"
    assert journal_tick_maps(PullbackReplayConfig()) == {}

    filtered = journal_tick_maps(
        PullbackReplayConfig(
            sessions="2",
            strategy_slug="macdbb_pullback_hl",
            range_start_utc="2026-08-09T08:00:00Z",
        )
    )
    assert set(filtered[2]) == {548}


def test_loader_config_binance_uses_binance_candle_cache():
    from routines.macdbb_pullback_hl_backtest import Config, _loader_config

    loader = _loader_config(Config(candle_source="binance_perpetual"))
    assert loader.hl_cache_dir == "data/binance_candles"
    assert loader.price_source == "binance_candles"

    hl_loader = _loader_config(Config())
    assert hl_loader.candle_source == "hyperliquid"
    assert hl_loader.hl_cache_dir == "data/hl_candles"


def test_snapshot_coverage_hint_reads_manifest():
    from routines.macdbb_pullback_hl_backtest import _snapshot_coverage_hint

    hint = _snapshot_coverage_hint("data/replay_snapshots_hl_60s")
    assert "2026-08-05T00:00:00Z" in hint
    assert "hyperliquid" in hint


def test_tick_universe_stats_empty_when_no_pairs():
    import datetime as dt

    from routines.macdbb_pullback_hl_backtest import _tick_universe_stats
    from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta

    ticks = {
        1: TickMeta(
            tick=1,
            timestamp=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
            macd_pairs=[],
        )
    }
    with_pairs, universe = _tick_universe_stats({0: ticks})
    assert with_pairs == 0
    assert universe == []
