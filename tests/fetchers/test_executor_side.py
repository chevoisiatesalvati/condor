"""Tests for executor side resolution and list/detail hydration."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from condor.fetchers.executors import (
    apply_executor_enrichment_cache,
    executor_list_payload_needs_detail,
    hydrate_executor_list_details,
    merge_executor_summary_with_detail,
    normalize_executor_side,
    reset_executor_list_enrichment_state,
    resolve_executor_side,
    get_executor_side,
)


@pytest.fixture(autouse=True)
def _reset_executor_enrichment_state():
    reset_executor_list_enrichment_state()
    yield
    reset_executor_list_enrichment_state()
from condor.web.routes.executors import _build_executor_info


def test_normalize_executor_side_numeric_and_strings():
    assert normalize_executor_side(1) == "BUY"
    assert normalize_executor_side(2) == "SELL"
    assert normalize_executor_side("LONG") == "BUY"
    assert normalize_executor_side("SHORT") == "SELL"
    assert normalize_executor_side("") == ""


def test_resolve_executor_side_from_custom_info():
    ex = {
        "id": "abc",
        "config": {"side": 2},
        "custom_info": {},
        "status": "terminated",
    }
    assert resolve_executor_side(ex) == "SELL"


def test_get_executor_side_from_held_position_orders():
    ex = {
        "config": {},
        "custom_info": {
            "held_position_orders": [{"side": "BUY", "price": 100.0}],
        },
    }
    assert get_executor_side(ex) == "BUY"


def test_merge_executor_summary_with_detail_fills_side():
    summary = {"id": "x", "status": "TERMINATED", "config": {}, "custom_info": None}
    detail = {
        "id": "x",
        "config": {"side": 1, "connector_name": "hl", "trading_pair": "BTC-USD"},
        "custom_info": {"side": "BUY"},
    }
    merged = merge_executor_summary_with_detail(summary, detail)
    assert resolve_executor_side(merged) == "BUY"
    assert merged["config"]["connector_name"] == "hl"


@pytest.mark.asyncio
async def test_hydrate_executor_list_details_fetches_missing_side(monkeypatch):
    summary = {"id": "term-1", "status": "TERMINATED", "config": {}}
    detail = {
        "id": "term-1",
        "config": {"side": 1},
        "custom_info": {"side": "BUY"},
    }
    client = AsyncMock()
    monkeypatch.setattr(
        "condor.fetchers.executors.get_executor_detail",
        AsyncMock(return_value=detail),
    )
    hydrated = await hydrate_executor_list_details(client, [summary])

    assert resolve_executor_side(hydrated[0]) == "BUY"
    assert not executor_list_payload_needs_detail(hydrated[0])


@pytest.mark.asyncio
async def test_hydrate_skips_repeat_fetch_but_keeps_enrichment(monkeypatch):
    summary = {"id": "term-2", "status": "TERMINATED", "config": {}}
    detail = {
        "id": "term-2",
        "config": {"side": 1, "stop_loss": 0.02, "take_profit": 0.04},
        "custom_info": {"side": "BUY"},
    }
    client = AsyncMock()
    fetch = AsyncMock(return_value=detail)
    monkeypatch.setattr("condor.fetchers.executors.get_executor_detail", fetch)

    await hydrate_executor_list_details(client, [summary])
    fresh = {"id": "term-2", "status": "TERMINATED", "config": {}, "pnl": 12.5}
    second = await hydrate_executor_list_details(client, [fresh])

    fetch.assert_awaited_once()
    assert resolve_executor_side(second[0]) == "BUY"
    assert second[0]["config"]["stop_loss"] == 0.02
    assert second[0]["config"]["take_profit"] == 0.04
    assert second[0]["pnl"] == 12.5
    assert not executor_list_payload_needs_detail(second[0])


def test_apply_enrichment_cache_restores_side_without_fetch():
    enriched = {
        "id": "term-3",
        "config": {"side": 2, "stop_loss": 0.01},
        "custom_info": {"side": "SELL"},
    }
    from condor.fetchers.executors import remember_executor_enrichment

    remember_executor_enrichment(enriched)
    fresh = {"id": "term-3", "status": "TERMINATED", "config": {}}
    restored = apply_executor_enrichment_cache([fresh])[0]

    assert resolve_executor_side(restored) == "SELL"
    assert restored["config"]["stop_loss"] == 0.01


def test_build_executor_info_uses_resolve_executor_side():
    info = _build_executor_info(
        {
            "id": "x",
            "config": {"side": 1},
            "status": "running",
        }
    )
    assert info is not None
    assert info.side == "BUY"
