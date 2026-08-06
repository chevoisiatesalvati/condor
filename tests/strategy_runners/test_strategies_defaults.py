"""Defaults round-trip helpers for Strategies API."""

from __future__ import annotations

from condor.strategy_runners.catalog import get_strategy
from condor.web.routes.strategies import _merged_default_config, _preset_catalog


def test_merged_default_config_includes_catalog_keys():
    strat = get_strategy("macdbb_scanner_aggressive_hl")
    assert strat is not None
    cfg = _merged_default_config(strat)
    assert "total_amount_quote" in cfg
    assert "frequency_sec" in cfg
    assert "strategy_preset" in cfg


def test_preset_catalog_nonempty():
    catalog = _preset_catalog("macdbb_scanner_aggressive_hl")
    assert any(row["id"] == "custom" for row in catalog)
    assert len(catalog) >= 2
