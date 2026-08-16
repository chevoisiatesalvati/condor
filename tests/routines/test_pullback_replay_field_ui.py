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
    assert fields["enable_thesis_decay_exit"]["group"] == "Position monitor"
    assert fields["min_notional_quote"]["group"] == "Sizing"
