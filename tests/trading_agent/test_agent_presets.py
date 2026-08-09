"""Tests for strategy preset helpers (Strategies path + empty Agents loaders)."""

from condor.agents.agent_presets import (
    AGENT_PRESET_LOADERS,
    apply_agent_strategy_preset,
    get_agent_strategy_preset_catalog,
    strategy_params_for_preset,
)
from condor.strategy_runners.macdbb.presets import (
    agent_preset_catalog,
    private_presets_available,
    strategy_params_from_preset,
)


def test_agents_preset_loaders_exclude_macdbb():
    assert "macdbb_scanner_aggressive_hl" not in AGENT_PRESET_LOADERS
    assert get_agent_strategy_preset_catalog("macdbb_scanner_aggressive_hl") is None


def test_macdbb_strategy_runners_preset_catalog():
    catalog = agent_preset_catalog()
    assert catalog is not None
    ids = [row["id"] for row in catalog]
    assert ids[0] == "custom"
    if private_presets_available():
        assert len(ids) > 1
    else:
        assert "hl_dynamic_session_parity" in ids


def test_session_parity_preset_merges_strategy_params():
    params = strategy_params_from_preset(
        "hl_dynamic_session_parity", frequency_sec=1800
    )
    assert params is not None
    assert params["sl_pct"] == 3.8
    assert params["tp_pct"] == 5.0
    assert "min_notional_quote" not in params
    assert "max_notional_quote" not in params
    assert "formal_notional_quote" not in params
    assert "total_amount_quote" not in params


def test_apply_agent_strategy_preset_noop_for_macdbb():
    config = apply_agent_strategy_preset(
        "macdbb_scanner_aggressive_hl",
        {"frequency_sec": 1800, "strategy_params": {"sl_pct": 1.0}},
        preset="hl_dynamic_session_parity",
    )
    # Agents loader removed — no overlay via apply_agent_strategy_preset.
    assert config["strategy_params"]["sl_pct"] == 1.0


def test_strategy_params_for_preset_custom_is_empty():
    assert (
        strategy_params_for_preset(
            "macdbb_scanner_aggressive_hl", "custom", frequency_sec=1800
        )
        is None
    )
