"""Tests for HL candle cache manifest helpers."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from routines.lib.hl_candle_manifest import (
    build_manifest,
    find_earliest_bar,
    load_manifest,
    write_manifest,
)


def test_build_manifest_from_empty_cache(tmp_path: Path):
    manifest = build_manifest(cache_dir=tmp_path, pairs=["BTC-USD"], intervals=["5m"])
    assert manifest["version"] == 1
    assert manifest["pairs"] == ["BTC-USD"]
    assert manifest["intervals"] == ["5m"]
    assert manifest["coverage"] == []


def test_write_and_load_manifest_round_trip(tmp_path: Path):
    manifest = build_manifest(
        cache_dir=tmp_path,
        pairs=["ETH-USD"],
        intervals=["1h"],
        universe_source="metaAndAssetCtxs",
    )
    write_manifest(manifest, cache_dir=tmp_path)
    loaded = load_manifest(cache_dir=tmp_path)
    assert loaded is not None
    assert loaded["pairs"] == ["ETH-USD"]
    assert loaded["universe_source"] == "metaAndAssetCtxs"
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["version"] == 1


@pytest.mark.asyncio
async def test_find_earliest_bar_binary_search(monkeypatch):
    """Simulate HL data starting 100 hours into the search window."""
    hour_ms = 3_600_000
    search_start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    search_end = search_start + dt.timedelta(days=30)
    data_start_ms = int(search_start.timestamp() * 1000) + 100 * hour_ms

    async def _fake_window(
        _pair: str,
        _interval: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        session,
    ):
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        if end_ms <= data_start_ms or start_ms >= int(search_end.timestamp() * 1000):
            return []
        ts = max(start_ms, data_start_ms)
        return [
            {
                "timestamp_ms": float(ts),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            }
        ]

    monkeypatch.setattr(
        "routines.lib.hl_candle_manifest.fetch_hl_candle_window",
        _fake_window,
    )

    class _Session:
        pass

    result = await find_earliest_bar(
        _Session(),
        "BTC-USD",
        "1h",
        search_start,
        search_end,
        probe_bars=24,
    )
    assert result["has_data"] is True
    assert result["earliest_ts_ms"] == data_start_ms
    assert result["api_requests"] >= 1
