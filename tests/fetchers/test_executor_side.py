"""Tests for executor side resolution and list/detail hydration."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from condor.fetchers.executors import (
    executor_list_payload_needs_detail,
    hydrate_executor_list_details,
    merge_executor_summary_with_detail,
    normalize_executor_side,
    resolve_executor_side,
    get_executor_side,
)
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
