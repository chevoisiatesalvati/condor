"""Tests for cross-venue volume ranking in tick market state."""

from __future__ import annotations

import datetime as dt

import pytest

from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import (
    TickMarketSettings,
    _quote_volume_for_pair,
    compute_tick_market_state,
)


class _StaticLoader:
    def __init__(self, candles_by_pair: dict[str, list[dict[str, float]]]) -> None:
        self._candles = candles_by_pair

    async def get_interval_window(
        self,
        trading_pair: str,
        interval: str,
        end: dt.datetime,
        hours: int,
    ) -> list[dict[str, float]]:
        return list(self._candles[trading_pair])


def _candle(close: float, volume: float) -> dict[str, float]:
    return {"open": close, "high": close, "low": close, "close": close, "volume": volume}


@pytest.mark.asyncio
async def test_quote_volume_for_pair_uses_volume_loader():
    tick = dt.datetime(2026, 6, 19, 12, 0, tzinfo=dt.timezone.utc)
    primary = [_candle(100.0, 1.0)] * 288
    volume = [_candle(100.0, 10.0)] * 288
    loader = _StaticLoader({"BTC-USD": primary})
    volume_loader = _StaticLoader({"BTC-USD": volume})

    result = await _quote_volume_for_pair(
        pair="BTC-USD",
        scanner_interval="5m",
        tick_time=tick,
        fetch_hours=30,
        loader=loader,
        volume_loader=volume_loader,
        primary_candles=primary,
    )

    assert result == pytest.approx(288_000.0)


@pytest.mark.asyncio
async def test_compute_tick_market_state_ranks_by_volume_loader():
    tick = dt.datetime(2026, 6, 9, 12, 0, tzinfo=dt.timezone.utc)
    bars = [_candle(100.0, 1.0)] * 400
    high_vol = [_candle(100.0, 20.0)] * 400
    low_vol = [_candle(100.0, 1.0)] * 400

    loader = _StaticLoader({"AAA-USD": bars, "BBB-USD": bars})
    volume_loader = _StaticLoader({"AAA-USD": low_vol, "BBB-USD": high_vol})
    universe = [
        {"trading_pair": "AAA-USD", "volume_24h_usd": 1.0},
        {"trading_pair": "BBB-USD", "volume_24h_usd": 1.0},
    ]

    state = await compute_tick_market_state(
        tick,
        universe=universe,
        loader=loader,
        volume_loader=volume_loader,
        settings=TickMarketSettings(
            min_volume_usd=0,
            top_n=2,
            candidate_pool=2,
            mature_count=1,
            degen_count=1,
        ),
    )

    assert state.parsed_scanner is not None
    pairs = [row.pair for row in state.parsed_scanner.mature + state.parsed_scanner.degen]
    assert pairs[0] == "BBB-USD"
