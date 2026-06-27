"""Tests for Binance universe fetch helpers."""

import pytest

from routines.market_scanner import fetch_top_pairs


@pytest.mark.asyncio
async def test_fetch_top_pairs_zero_returns_all(monkeypatch):
    tickers = [
        {"symbol": "AAAUSDT", "quoteVolume": "5000000", "lastPrice": "1", "priceChangePercent": "0"},
        {"symbol": "BBBUSDT", "quoteVolume": "3000000", "lastPrice": "1", "priceChangePercent": "0"},
        {"symbol": "ETHUSDT", "quoteVolume": "1000000", "lastPrice": "1", "priceChangePercent": "0"},
        {"symbol": "BTCUSDT", "quoteVolume": "100", "lastPrice": "1", "priceChangePercent": "0"},
    ]

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def json(self):
            return tickers

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr("routines.market_scanner.aiohttp.ClientSession", _Session)

    limited = await fetch_top_pairs(1, min_volume=2_000_000)
    assert len(limited) == 1
    assert limited[0]["trading_pair"] == "AAA-USDT"

    all_pairs = await fetch_top_pairs(0, min_volume=2_000_000)
    assert len(all_pairs) == 2
    assert [row["trading_pair"] for row in all_pairs] == ["AAA-USDT", "BBB-USDT"]
