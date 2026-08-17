"""Rebuild the live 1h series as-of a tick (completed hours + forming bar).

Live ``fetch_hl_candles(..., max_records=200)`` includes the in-progress 1h bar
with its current close. Cached 1h bars store the completed-hour close, so using
them as-of an intra-hour tick leaks the rest of that hour. Synthesize the
forming bar from 1m candles instead.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

HOUR_MS = 3_600_000
LIVE_MACD_MAX_RECORDS = 200


def _ts_ms(candle: Mapping[str, Any]) -> int:
    return int(candle["timestamp_ms"])


def completed_1h_bars(
    candles_1h: Sequence[Mapping[str, Any]],
    as_of_ms: int,
) -> list[dict[str, float]]:
    """1h bars whose close is at or before as_of."""
    return [
        dict(candle)
        for candle in candles_1h
        if _ts_ms(candle) + HOUR_MS <= as_of_ms
    ]


def forming_1h_from_1m(
    candles_1m: Sequence[Mapping[str, Any]],
    as_of_ms: int,
) -> dict[str, float] | None:
    """OHLC for the open hour, using 1m bars with open_time <= as_of."""
    hour_open_ms = (as_of_ms // HOUR_MS) * HOUR_MS
    minutes = [
        candle
        for candle in candles_1m
        if hour_open_ms <= _ts_ms(candle) <= as_of_ms
    ]
    if not minutes:
        return None
    return {
        "timestamp_ms": float(hour_open_ms),
        "open": float(minutes[0]["open"]),
        "high": max(float(c["high"]) for c in minutes),
        "low": min(float(c["low"]) for c in minutes),
        "close": float(minutes[-1]["close"]),
        "volume": float(sum(float(c["volume"]) for c in minutes)),
    }


def as_of_1h_candles(
    candles_1h: Sequence[Mapping[str, Any]],
    candles_1m: Sequence[Mapping[str, Any]] | None,
    as_of_ms: int,
    *,
    max_records: int = LIVE_MACD_MAX_RECORDS,
) -> list[dict[str, float]]:
    """Completed 1h bars plus a 1m-synthesized forming bar, last max_records."""
    assert as_of_ms > 0
    assert max_records > 0
    completed = completed_1h_bars(candles_1h, as_of_ms)
    forming = forming_1h_from_1m(candles_1m or [], as_of_ms)
    series = completed + ([forming] if forming is not None else [])
    series.sort(key=_ts_ms)
    return series[-max_records:]
