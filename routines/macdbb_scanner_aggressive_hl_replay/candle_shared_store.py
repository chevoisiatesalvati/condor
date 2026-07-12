"""Shared-memory candle storage for parallel timeline sweeps."""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_CANDLE_DTYPE = np.dtype(
    [
        ("timestamp_ms", np.int64),
        ("open", np.float64),
        ("high", np.float64),
        ("low", np.float64),
        ("close", np.float64),
    ]
)
_RECORD_BYTES = _CANDLE_DTYPE.itemsize


@dataclass(frozen=True)
class CandleSeriesView:
    """Read-only OHLC series backed by numpy arrays (heap or shared memory)."""

    timestamp_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __len__(self) -> int:
        return int(self.timestamp_ms.shape[0])

    def slice_by_ms(
        self,
        start_ms: int,
        end_ms: int,
        *,
        exclusive_start: bool = True,
    ) -> CandleSeriesView:
        if len(self) == 0:
            return self
        left = int(np.searchsorted(self.timestamp_ms, start_ms, side="right" if exclusive_start else "left"))
        right = int(np.searchsorted(self.timestamp_ms, end_ms, side="right"))
        if left >= right:
            empty = np.empty(0, dtype=np.int64)
            empty_f = np.empty(0, dtype=np.float64)
            return CandleSeriesView(empty, empty_f, empty_f, empty_f, empty_f)
        return CandleSeriesView(
            self.timestamp_ms[left:right],
            self.open[left:right],
            self.high[left:right],
            self.low[left:right],
            self.close[left:right],
        )

    def slice_until_ms(self, end_ms: int) -> CandleSeriesView:
        if len(self) == 0:
            return self
        right = int(np.searchsorted(self.timestamp_ms, end_ms, side="right"))
        if right <= 0:
            empty = np.empty(0, dtype=np.int64)
            empty_f = np.empty(0, dtype=np.float64)
            return CandleSeriesView(empty, empty_f, empty_f, empty_f, empty_f)
        return CandleSeriesView(
            self.timestamp_ms[:right],
            self.open[:right],
            self.high[:right],
            self.low[:right],
            self.close[:right],
        )

    def slice_window_ms(self, start_ms: int, end_ms: int) -> CandleSeriesView:
        return self.slice_by_ms(start_ms, end_ms, exclusive_start=False)

    @classmethod
    def from_candles(cls, candles: list[dict[str, float]]) -> CandleSeriesView:
        if not candles:
            empty = np.empty(0, dtype=np.int64)
            empty_f = np.empty(0, dtype=np.float64)
            return cls(empty, empty_f, empty_f, empty_f, empty_f)
        records = np.empty(len(candles), dtype=_CANDLE_DTYPE)
        for index, candle in enumerate(candles):
            records[index]["timestamp_ms"] = int(candle["timestamp_ms"])
            records[index]["open"] = float(candle["open"])
            records[index]["high"] = float(candle["high"])
            records[index]["low"] = float(candle["low"])
            records[index]["close"] = float(candle["close"])
        order = np.argsort(records["timestamp_ms"], kind="mergesort")
        records = records[order]
        return cls(
            records["timestamp_ms"].copy(),
            records["open"].copy(),
            records["high"].copy(),
            records["low"].copy(),
            records["close"].copy(),
        )


def is_candle_series_view(value: Any) -> bool:
    return isinstance(value, CandleSeriesView)


class SharedCandleStore:
    """Dict-like read-only candle cache backed by SharedMemory segments."""

    def __init__(self) -> None:
        self._segments: list[shared_memory.SharedMemory] = []
        self._series: dict[str, CandleSeriesView] = {}

    def __bool__(self) -> bool:
        return bool(self._series)

    def __len__(self) -> int:
        return len(self._series)

    def get(self, pair: str) -> CandleSeriesView | None:
        return self._series.get(pair)

    def keys(self):
        return self._series.keys()

    @classmethod
    def pack(
        cls,
        caches: dict[str, list[dict[str, float]]],
        *,
        name_prefix: str = "condor_candles",
    ) -> SharedCandleStore:
        store = cls()
        for pair, candles in caches.items():
            if not candles:
                continue
            view = CandleSeriesView.from_candles(candles)
            if len(view) == 0:
                continue
            records = np.empty(len(view), dtype=_CANDLE_DTYPE)
            records["timestamp_ms"] = view.timestamp_ms
            records["open"] = view.open
            records["high"] = view.high
            records["low"] = view.low
            records["close"] = view.close
            shm_name = f"{name_prefix}_{uuid.uuid4().hex}"
            shm = shared_memory.SharedMemory(name=shm_name, create=True, size=records.nbytes)
            shared_arr = np.ndarray(records.shape, dtype=_CANDLE_DTYPE, buffer=shm.buf)
            shared_arr[:] = records
            store._segments.append(shm)
            store._series[pair] = CandleSeriesView(
                shared_arr["timestamp_ms"],
                shared_arr["open"],
                shared_arr["high"],
                shared_arr["low"],
                shared_arr["close"],
            )
        logger.info("SharedCandleStore packed %d pair series", len(store._series))
        return store

    def close_unlink(self) -> None:
        for shm in self._segments:
            try:
                shm.close()
            except BufferError:
                pass
            try:
                shm.unlink()
            except FileNotFoundError:
                pass
        self._segments.clear()
        self._series.clear()


def pack_candle_caches(
    price_cache: dict[str, list[dict[str, float]]],
    barrier_cache: dict[str, list[dict[str, float]]],
    vol_cache: dict[str, list[dict[str, float]]],
    *,
    name_prefix: str = "condor_candles",
) -> tuple[SharedCandleStore, SharedCandleStore, SharedCandleStore]:
    return (
        SharedCandleStore.pack(price_cache, name_prefix=f"{name_prefix}_px"),
        SharedCandleStore.pack(barrier_cache, name_prefix=f"{name_prefix}_bar"),
        SharedCandleStore.pack(vol_cache, name_prefix=f"{name_prefix}_vol"),
    )


def natr_from_candle_view(
    view: CandleSeriesView,
    entry_time: dt.datetime,
    *,
    lookback_periods: int,
) -> float | None:
    if len(view) == 0:
        return None
    end_ms = int(entry_time.timestamp() * 1000)
    window = view.slice_until_ms(end_ms)
    if len(window) < lookback_periods + 1:
        return None
    closes = window.close
    highs = window.high
    lows = window.low
    true_ranges: list[float] = []
    for index in range(1, len(window)):
        high_low = highs[index] - lows[index]
        high_prev = abs(highs[index] - closes[index - 1])
        low_prev = abs(lows[index] - closes[index - 1])
        true_ranges.append(max(high_low, high_prev, low_prev))
    if len(true_ranges) < lookback_periods:
        return None
    recent_tr = true_ranges[-lookback_periods:]
    atr = sum(recent_tr) / len(recent_tr)
    last_close = float(closes[-1])
    if last_close <= 0:
        return None
    return (atr / last_close) * 100.0


def scanner_natr_from_candle_view(
    view: CandleSeriesView,
    entry_time: dt.datetime | None,
    *,
    lookback_hours: int,
    natr_period: int,
    min_bars: int,
) -> float | None:
    if len(view) == 0:
        return None
    if entry_time is not None:
        end_ms = int(entry_time.timestamp() * 1000)
        start_ms = end_ms - lookback_hours * 3600 * 1000
        window = view.slice_window_ms(start_ms, end_ms)
    else:
        window = view
    if len(window) < min_bars:
        return None
    closes = window.close
    highs = window.high
    lows = window.low
    if closes[-1] <= 0:
        return None
    true_ranges: list[float] = []
    for index in range(len(window)):
        prev_close = closes[index - 1] if index > 0 else closes[0]
        high_low = highs[index] - lows[index]
        high_prev = abs(highs[index] - prev_close)
        low_prev = abs(lows[index] - prev_close)
        true_ranges.append(max(high_low, high_prev, low_prev))

    if len(true_ranges) >= natr_period * 2:
        natr_values: list[float] = []
        for index in range(natr_period, len(true_ranges)):
            atr = sum(true_ranges[index - natr_period : index]) / natr_period
            close_i = float(closes[index])
            if close_i > 0:
                natr_values.append((atr / close_i) * 100.0)
        if not natr_values:
            return None
        return sum(natr_values) / len(natr_values)

    atr = sum(true_ranges) / len(true_ranges)
    return (atr / float(closes[-1])) * 100.0


CandleStoreLike = "SharedCandleStore | LazyCandleStore"


class LazyCandleStore:
    """On-demand candle loads from parquet with LRU pair cache."""

    def __init__(
        self,
        *,
        interval: str,
        cache_dir: Path | None,
        candle_source: str,
        range_start_ms: int,
        range_end_ms: int,
        max_cached_pairs: int = 32,
    ) -> None:
        self._interval = interval
        self._cache_dir = cache_dir
        self._candle_source = candle_source
        self._range_start_ms = range_start_ms
        self._range_end_ms = range_end_ms
        self._max_cached_pairs = max(1, max_cached_pairs)
        self._lru: OrderedDict[str, CandleSeriesView] = OrderedDict()

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return len(self._lru)

    def get(self, pair: str) -> CandleSeriesView | None:
        if pair in self._lru:
            self._lru.move_to_end(pair)
            return self._lru[pair]
        candles = self._load_pair_range(pair)
        if not candles:
            return None
        view = CandleSeriesView.from_candles(candles)
        self._lru[pair] = view
        if len(self._lru) > self._max_cached_pairs:
            self._lru.popitem(last=False)
        return view

    def close_unlink(self) -> None:
        self._lru.clear()

    def _load_pair_range(self, pair: str) -> list[dict[str, float]]:
        if self._candle_source == "binance_perpetual":
            from routines.lib import binance_candle_cache as cache_mod
            from routines.lib import binance_candles as candle_mod

            symbol = candle_mod.trading_pair_to_symbol(pair)
            return cache_mod.load_candles_in_range(
                symbol,
                self._interval,
                self._range_start_ms,
                self._range_end_ms,
                cache_dir=self._cache_dir,
            )
        from routines.lib import hl_candle_cache as cache_mod
        from routines.lib import hl_candles as candle_mod

        coin = candle_mod.trading_pair_to_hl_coin(pair)
        return cache_mod.load_candles_in_range(
            coin,
            self._interval,
            self._range_start_ms,
            self._range_end_ms,
            cache_dir=self._cache_dir,
        )


def build_lazy_candle_stores(
    *,
    range_start_utc: str,
    range_end_utc: str,
    price_interval: str,
    barrier_interval: str,
    cache_dir: Path | None,
    candle_source: str,
    buffer_hours: int = 1,
) -> tuple[LazyCandleStore, LazyCandleStore, LazyCandleStore]:
    start = dt.datetime.fromisoformat(range_start_utc.replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(range_end_utc.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt.timezone.utc)
    buffer_ms = buffer_hours * 3600 * 1000
    start_ms = int(start.timestamp() * 1000) - buffer_ms
    end_ms = int(end.timestamp() * 1000) + buffer_ms
    common = dict(
        cache_dir=cache_dir,
        candle_source=candle_source,
        range_start_ms=start_ms,
        range_end_ms=end_ms,
    )
    return (
        LazyCandleStore(interval=price_interval, **common),
        LazyCandleStore(interval=barrier_interval, **common),
        LazyCandleStore(interval=barrier_interval, **common),
    )
