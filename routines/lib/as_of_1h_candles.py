"""Rebuild the live 1h series as-of a tick (completed hours + forming bar).

Live ``fetch_hl_candles(..., max_records=200)`` includes the in-progress 1h bar
with its current close. Cached 1h bars store the completed-hour close, so using
them as-of an intra-hour tick leaks the rest of that hour. Synthesize the
forming bar from 1m candles instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

HOUR_MS = 3_600_000
LIVE_MACD_MAX_RECORDS = 200

_EMPTY_INT64 = np.array([], dtype=np.int64)
_EMPTY_FLOAT64 = np.array([], dtype=np.float64)


@dataclass(frozen=True)
class OhlcvArrays:
    """Sorted OHLC columns for as-of slicing without per-tick parquet reads."""

    timestamp_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __post_init__(self) -> None:
        count = int(self.timestamp_ms.shape[0])
        assert self.open.shape[0] == count
        assert self.high.shape[0] == count
        assert self.low.shape[0] == count
        assert self.close.shape[0] == count
        assert self.volume.shape[0] == count

    def __len__(self) -> int:
        return int(self.timestamp_ms.shape[0])

    @classmethod
    def empty(cls) -> OhlcvArrays:
        return cls(
            timestamp_ms=_EMPTY_INT64,
            open=_EMPTY_FLOAT64,
            high=_EMPTY_FLOAT64,
            low=_EMPTY_FLOAT64,
            close=_EMPTY_FLOAT64,
            volume=_EMPTY_FLOAT64,
        )

    @classmethod
    def from_candles(cls, candles: Sequence[Mapping[str, Any]]) -> OhlcvArrays:
        if not candles:
            return cls.empty()
        count = len(candles)
        timestamp_ms = np.fromiter(
            (int(candle["timestamp_ms"]) for candle in candles),
            dtype=np.int64,
            count=count,
        )
        open_px = np.fromiter(
            (float(candle["open"]) for candle in candles),
            dtype=np.float64,
            count=count,
        )
        high_px = np.fromiter(
            (float(candle["high"]) for candle in candles),
            dtype=np.float64,
            count=count,
        )
        low_px = np.fromiter(
            (float(candle["low"]) for candle in candles),
            dtype=np.float64,
            count=count,
        )
        close_px = np.fromiter(
            (float(candle["close"]) for candle in candles),
            dtype=np.float64,
            count=count,
        )
        volume = np.fromiter(
            (float(candle["volume"]) for candle in candles),
            dtype=np.float64,
            count=count,
        )
        order = np.argsort(timestamp_ms, kind="mergesort")
        return cls(
            timestamp_ms=timestamp_ms[order],
            open=open_px[order],
            high=high_px[order],
            low=low_px[order],
            close=close_px[order],
            volume=volume[order],
        )

    def slice(self, start: int, end: int) -> OhlcvArrays:
        return OhlcvArrays(
            timestamp_ms=self.timestamp_ms[start:end],
            open=self.open[start:end],
            high=self.high[start:end],
            low=self.low[start:end],
            close=self.close[start:end],
            volume=self.volume[start:end],
        )

    def to_candles(self) -> list[dict[str, float]]:
        return [
            {
                "timestamp_ms": float(self.timestamp_ms[index]),
                "open": float(self.open[index]),
                "high": float(self.high[index]),
                "low": float(self.low[index]),
                "close": float(self.close[index]),
                "volume": float(self.volume[index]),
            }
            for index in range(len(self))
        ]


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


def forming_1h_from_1m_arrays(
    candles_1m: OhlcvArrays,
    as_of_ms: int,
) -> dict[str, float] | None:
    """Same forming bar as ``forming_1h_from_1m``, via searchsorted."""
    view = forming_1h_from_1m_arrays_view(candles_1m, as_of_ms)
    if view is None or len(view) == 0:
        return None
    return view.to_candles()[0]


def forming_1h_from_1m_arrays_view(
    candles_1m: OhlcvArrays,
    as_of_ms: int,
) -> OhlcvArrays | None:
    """Forming 1h bar as a 1-row ``OhlcvArrays`` (no Python candle dict)."""
    if len(candles_1m) == 0:
        return None
    hour_open_ms = (as_of_ms // HOUR_MS) * HOUR_MS
    left = int(np.searchsorted(candles_1m.timestamp_ms, hour_open_ms, side="left"))
    right = int(np.searchsorted(candles_1m.timestamp_ms, as_of_ms, side="right"))
    if left >= right:
        return None
    return OhlcvArrays(
        timestamp_ms=np.array([hour_open_ms], dtype=np.int64),
        open=np.array([float(candles_1m.open[left])], dtype=np.float64),
        high=np.array([float(np.max(candles_1m.high[left:right]))], dtype=np.float64),
        low=np.array([float(np.min(candles_1m.low[left:right]))], dtype=np.float64),
        close=np.array([float(candles_1m.close[right - 1])], dtype=np.float64),
        volume=np.array([float(np.sum(candles_1m.volume[left:right]))], dtype=np.float64),
    )


def _concat_ohlcv(left: OhlcvArrays, right: OhlcvArrays) -> OhlcvArrays:
    if len(left) == 0:
        return right
    if len(right) == 0:
        return left
    return OhlcvArrays(
        timestamp_ms=np.concatenate([left.timestamp_ms, right.timestamp_ms]),
        open=np.concatenate([left.open, right.open]),
        high=np.concatenate([left.high, right.high]),
        low=np.concatenate([left.low, right.low]),
        close=np.concatenate([left.close, right.close]),
        volume=np.concatenate([left.volume, right.volume]),
    )


def completed_1h_end_index(candles_1h: OhlcvArrays, as_of_ms: int) -> int:
    """Exclusive end index of 1h bars whose close is at or before as_of."""
    if len(candles_1h) == 0:
        return 0
    return int(
        np.searchsorted(
            candles_1h.timestamp_ms,
            as_of_ms - HOUR_MS,
            side="right",
        )
    )


def as_of_1h_from_arrays_view(
    candles_1h: OhlcvArrays,
    candles_1m: OhlcvArrays | None,
    as_of_ms: int,
    *,
    max_records: int = LIVE_MACD_MAX_RECORDS,
) -> OhlcvArrays:
    """Same bars as ``as_of_1h_from_arrays`` without per-bar Python dicts."""
    assert as_of_ms > 0
    assert max_records > 0
    completed_end = completed_1h_end_index(candles_1h, as_of_ms)
    forming = forming_1h_from_1m_arrays_view(
        candles_1m or OhlcvArrays.empty(),
        as_of_ms,
    )
    if forming is None:
        start = max(0, completed_end - max_records)
        return candles_1h.slice(start, completed_end)
    completed_start = max(0, completed_end - (max_records - 1))
    completed = candles_1h.slice(completed_start, completed_end)
    series = _concat_ohlcv(completed, forming)
    if len(series) <= max_records:
        return series
    return series.slice(len(series) - max_records, len(series))


def as_of_1h_from_arrays(
    candles_1h: OhlcvArrays,
    candles_1m: OhlcvArrays | None,
    as_of_ms: int,
    *,
    max_records: int = LIVE_MACD_MAX_RECORDS,
) -> list[dict[str, float]]:
    """In-memory equivalent of ``as_of_1h_candles`` (must match bar-for-bar)."""
    return as_of_1h_from_arrays_view(
        candles_1h,
        candles_1m,
        as_of_ms,
        max_records=max_records,
    ).to_candles()
