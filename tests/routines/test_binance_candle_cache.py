"""Tests for Binance candle parquet cache."""

from __future__ import annotations

from routines.lib.binance_candle_cache import (
    cache_path,
    load_candles,
    load_candles_in_range,
    meta_covers_range,
    save_candles,
)
from routines.lib.binance_candle_manifest import build_manifest, write_manifest, load_manifest


def test_cache_path_uses_usdt_pair(tmp_path):
    path = cache_path("BTC-USD", "5m", cache_dir=tmp_path)
    assert path == tmp_path / "5m" / "BTC-USDT.parquet"


def test_save_and_load_round_trip(tmp_path):
    candles = [
        {
            "timestamp_ms": 1_700_000_000_000.0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 12.0,
        }
    ]
    save_candles("BTC-USDT", "5m", candles, cache_dir=tmp_path)
    loaded = load_candles("BTC-USD", "5m", cache_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["close"] == 100.5


def test_load_candles_in_range_returns_slice(tmp_path):
    candles = [
        {
            "timestamp_ms": float(ts),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": float(ts),
            "volume": 1.0,
        }
        for ts in (1_000, 2_000, 3_000, 4_000)
    ]
    save_candles("BTC-USDT", "5m", candles, cache_dir=tmp_path)

    loaded = load_candles_in_range("BTC-USDT", "5m", 1_500, 3_500, cache_dir=tmp_path)
    assert [int(c["timestamp_ms"]) for c in loaded] == [2_000, 3_000]


def test_meta_covers_range_uses_sidecar_bounds():
    meta = {"min_ts_ms": 1_000, "max_ts_ms": 10_000}
    assert meta_covers_range(meta, 2_000, 9_000, interval_ms=300_000)
    assert not meta_covers_range(meta, 0, 9_000, interval_ms=300_000)


def test_build_manifest_discovers_usdt_pairs(tmp_path):
    save_candles(
        "ETH-USDT",
        "1h",
        [
            {
                "timestamp_ms": 1_700_000_000_000.0,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            }
        ],
        cache_dir=tmp_path,
    )
    manifest = build_manifest(cache_dir=tmp_path, pairs=["ETH-USDT"], intervals=["1h"])
    assert manifest["exchange"] == "binance_perpetual"
    assert manifest["coverage"]
    write_manifest(manifest, cache_dir=tmp_path)
    loaded = load_manifest(cache_dir=tmp_path)
    assert loaded is not None
    assert loaded["pairs"] == ["ETH-USDT"]
