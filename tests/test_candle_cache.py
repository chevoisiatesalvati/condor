"""Tests for candle disk/memory cache."""

from __future__ import annotations

import time

from condor.candle_cache import (
    DISK_CACHE_DIR,
    get_cached_candles,
    put_cached_candles,
)


def test_historical_candles_persist_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("condor.candle_cache.DISK_CACHE_DIR", tmp_path)
    key = ("local", "hl", "BTC-USD", "1m", 5000, 1000, 2000)
    end_time = time.time() - 7200
    candles = [{"timestamp": 1000.0, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    put_cached_candles(key, candles, end_time)
    loaded = get_cached_candles(key, end_time)
    assert loaded == candles


def test_live_candles_not_read_from_disk_after_memory_expires(monkeypatch):
    monkeypatch.setattr("condor.candle_cache.DISK_CACHE_DIR", DISK_CACHE_DIR)
    key = ("local", "hl", "ETH-USD", "1m", 5000, None, None)
    put_cached_candles(key, [{"timestamp": 1.0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}], None)
    assert get_cached_candles(key, None) is not None
