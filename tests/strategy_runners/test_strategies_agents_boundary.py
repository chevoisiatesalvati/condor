"""Tests for Strategies catalog filters and Agents exclusion."""

from __future__ import annotations

from condor.agents.agent import AgentStore
from condor.strategy_runners.catalog import is_deterministic_strategy_slug, list_strategies


def test_macdbb_not_in_chat_specialists():
    specialists = {a.slug for a in AgentStore().list_specialists()}
    assert "macdbb_scanner_aggressive_hl" not in specialists
    assert is_deterministic_strategy_slug("macdbb_scanner_aggressive_hl")


def test_macdbb_not_in_consult_index():
    index = AgentStore().list_index()
    assert "macdbb_scanner_aggressive_hl" not in index


def test_macdbb_still_in_strategies_catalog():
    slugs = {s.slug for s in list_strategies()}
    assert "macdbb_scanner_aggressive_hl" in slugs


def test_strategies_preset_catalog_endpoint_helpers():
    from condor.web.routes.strategies import _expand_preset_params, _preset_catalog

    catalog = _preset_catalog("macdbb_scanner_aggressive_hl")
    assert any(row["id"] == "custom" for row in catalog)
    named = next(row["id"] for row in catalog if row["id"] != "custom")
    params, _risk = _expand_preset_params(
        "macdbb_scanner_aggressive_hl", named, frequency_sec=1800
    )
    assert isinstance(params, dict) and params
