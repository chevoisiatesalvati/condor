"""Tests for live agent strategy preset application."""

from condor.trading_agent.agent_presets import (
    apply_agent_strategy_preset,
    get_agent_strategy_preset_catalog,
    strategy_params_for_preset,
)


def test_macdbb_agent_preset_catalog():
    from trading_agents.macdbb_scanner_aggressive_hl.presets import private_presets_available

    catalog = get_agent_strategy_preset_catalog("macdbb_scanner_aggressive_hl")
    assert catalog is not None
    ids = [row["id"] for row in catalog]
    assert ids[0] == "custom"
    if private_presets_available():
        assert len(ids) > 1
    else:
        assert "hl_dynamic_session_parity" in ids


def test_apply_session_parity_preset_merges_strategy_params():
    config = apply_agent_strategy_preset(
        "macdbb_scanner_aggressive_hl",
        {"frequency_sec": 1800, "strategy_params": {"sl_pct": 1.0}},
        preset="hl_dynamic_session_parity",
    )
    params = config["strategy_params"]
    assert params["sl_pct"] == 3.8
    assert params["tp_pct"] == 5.0
    assert config["risk_limits"]["max_open_executors"] == 10


def test_strategy_params_for_preset_custom_is_empty():
    assert strategy_params_for_preset("macdbb_scanner_aggressive_hl", "custom", frequency_sec=1800) is None
