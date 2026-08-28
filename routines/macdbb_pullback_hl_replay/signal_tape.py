"""Precompute per-tick MACD/BB/impulse raw values for pullback replay.

Param-independent work (1h as-of series, MACD/BB, ATR% windows, signed bodies)
is done once per tape. Sweep knobs (impulse_atr_mult, lookback, ATR period,
bb epsilon, pullback epsilon, SL/TP) are applied later in
``materialize_signals`` / ``decide()``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from condor.strategy_runners.macdbb.market_data import signal_from_closes
from condor.strategy_runners.macdbb_pullback.entry_quality import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_IMPULSE_LOOKBACK_BARS,
    true_range,
)
from condor.strategy_runners.macdbb_pullback.metrics import compute_thesis_metrics
from condor.strategy_runners.macdbb_pullback.types import SignalSnapshot
from routines.lib.as_of_1h_candles import (
    HOUR_MS,
    LIVE_MACD_MAX_RECORDS,
    OhlcvArrays,
    as_of_1h_from_arrays_view,
)
from routines.macdbb_pullback_hl_replay.impulse_candles import (
    CandleSource,
    _load_candles_in_range,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta

logger = logging.getLogger(__name__)

TAPE_ATR_PERIODS: tuple[int, ...] = (7, 14, 21)
TAPE_LOOKBACK_BARS: tuple[int, ...] = (1, 2, 4)


@dataclass(frozen=True, slots=True)
class RawTickSignal:
    """MACD/BB plus impulse numerics; impulse flag is applied per config."""

    price: float
    bb_pos_pct: float
    bb_mid: float
    bb_upper: float
    macd: float
    signal_line: float
    histogram: float
    trend: str
    momentum: str
    bullish_cross: bool
    bearish_cross: bool
    atr_pct: float
    signed_body_sum_long: float
    signed_body_sum_short: float
    atr_pct_by_period: dict[int, float] | None = None
    signed_body_sum_long_by_lookback: dict[int, float] | None = None
    signed_body_sum_short_by_lookback: dict[int, float] | None = None


@dataclass
class PullbackSignalTape:
    by_tick: dict[int, dict[str, RawTickSignal]]
    pairs: tuple[str, ...]
    tick_count: int
    build_seconds: float = 0.0

    def materialize_signals(
        self,
        tick: int,
        pairs: Iterable[str],
        strategy_params: Mapping[str, Any] | None = None,
    ) -> dict[str, SignalSnapshot]:
        params = dict(strategy_params or {})
        impulse_atr_mult = float(params.get("impulse_atr_mult") or 1.25)
        lookback = int(params.get("impulse_lookback_bars") or DEFAULT_IMPULSE_LOOKBACK_BARS)
        atr_period = int(params.get("atr_period") or DEFAULT_ATR_PERIOD)
        raw_by_pair = self.by_tick.get(int(tick)) or {}
        out: dict[str, SignalSnapshot] = {}
        for pair in pairs:
            raw = raw_by_pair.get(pair)
            if raw is None:
                continue
            atr_pct = _select_atr_pct(raw, atr_period)
            signed_long = _select_signed_body(raw, "long", lookback)
            signed_short = _select_signed_body(raw, "short", lookback)
            signal = SignalSnapshot(
                pair=pair,
                price=raw.price,
                bb_pos_pct=raw.bb_pos_pct,
                bb_mid=raw.bb_mid,
                bb_upper=raw.bb_upper,
                macd=raw.macd,
                signal_line=raw.signal_line,
                histogram=raw.histogram,
                trend=raw.trend,
                momentum=raw.momentum,
                bullish_cross=raw.bullish_cross,
                bearish_cross=raw.bearish_cross,
                atr_pct=atr_pct,
                impulse_signed_body_sum_pct=max(signed_long, signed_short),
                impulse_long=_impulse_flag(
                    atr_pct,
                    signed_long,
                    impulse_atr_mult,
                ),
                impulse_short=_impulse_flag(
                    atr_pct,
                    signed_short,
                    impulse_atr_mult,
                ),
            )
            signal.metrics = compute_thesis_metrics(signal, params)
            out[pair] = signal
        return out


def _impulse_flag(atr_pct: float, signed_body_sum_pct: float, impulse_atr_mult: float) -> bool:
    if atr_pct <= 0:
        return False
    return signed_body_sum_pct >= (float(impulse_atr_mult) * atr_pct)


def _select_atr_pct(raw: RawTickSignal, atr_period: int) -> float:
    by_period = raw.atr_pct_by_period
    if by_period and int(atr_period) in by_period:
        return float(by_period[int(atr_period)])
    return float(raw.atr_pct)


def _select_signed_body(
    raw: RawTickSignal,
    side: str,
    lookback_bars: int,
) -> float:
    mapping = (
        raw.signed_body_sum_long_by_lookback
        if side == "long"
        else raw.signed_body_sum_short_by_lookback
    )
    if mapping and int(lookback_bars) in mapping:
        return float(mapping[int(lookback_bars)])
    if side == "long":
        return float(raw.signed_body_sum_long)
    return float(raw.signed_body_sum_short)


def universe_pairs_from_ticks(
    tick_meta_map: Mapping[int, TickMeta],
) -> list[str]:
    seen: dict[str, None] = {}
    for meta in tick_meta_map.values():
        for pair in meta.macd_pairs:
            if pair:
                seen.setdefault(str(pair), None)
        for pair in meta.queue_total:
            if pair:
                seen.setdefault(str(pair), None)
        for pair in meta.signals_1h:
            if pair:
                seen.setdefault(str(pair), None)
        for pair in meta.create_plans:
            if pair:
                seen.setdefault(str(pair), None)
    return list(seen)


def _as_of_ms(timestamp: dt.datetime) -> int:
    if timestamp.tzinfo is None:
        aware = timestamp.replace(tzinfo=dt.timezone.utc)
    else:
        aware = timestamp.astimezone(dt.timezone.utc)
    return int(aware.timestamp() * 1000)


def _load_pair_arrays(
    pair: str,
    *,
    start_1h_ms: int,
    start_1m_ms: int,
    end_ms: int,
    cache_dir: Path,
    candle_source: CandleSource,
) -> tuple[OhlcvArrays, OhlcvArrays]:
    candles_1h = _load_candles_in_range(
        pair,
        "1h",
        start_1h_ms,
        end_ms,
        cache_dir=cache_dir,
        candle_source=candle_source,
    )
    candles_1m = _load_candles_in_range(
        pair,
        "1m",
        start_1m_ms,
        end_ms,
        cache_dir=cache_dir,
        candle_source=candle_source,
    )
    return OhlcvArrays.from_candles(candles_1h), OhlcvArrays.from_candles(candles_1m)


def _atr_pct_from_ohlcv(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
) -> float:
    count = int(close.shape[0])
    if count < period + 1:
        return 0.0
    start = count - (period + 1)
    trs: list[float] = []
    for index in range(start + 1, count):
        trs.append(
            true_range(
                float(high[index]),
                float(low[index]),
                float(close[index - 1]),
            )
        )
    if not trs:
        return 0.0
    atr = sum(trs[-period:]) / float(period)
    last_close = float(close[-1])
    if last_close <= 0:
        return 0.0
    return (atr / last_close) * 100.0


def _signed_body_sum_from_ohlcv(
    open_px: np.ndarray,
    close: np.ndarray,
    lookback: int,
    side: str,
) -> float:
    bars = max(1, int(lookback))
    count = int(close.shape[0])
    if count <= 0:
        return 0.0
    recent_open = open_px[-bars:]
    recent_close = close[-bars:]
    total = 0.0
    for index in range(int(recent_close.shape[0])):
        candle_open = float(recent_open[index])
        if candle_open <= 0:
            continue
        body = ((float(recent_close[index]) - candle_open) / candle_open) * 100.0
        if side == "long":
            if body > 0:
                total += body
        elif body < 0:
            total += -body
    return total


def _signed_body_sums_from_arrays(
    arrays: OhlcvArrays,
) -> tuple[dict[int, float], dict[int, float]]:
    long_by_lookback: dict[int, float] = {}
    short_by_lookback: dict[int, float] = {}
    for lookback in TAPE_LOOKBACK_BARS:
        long_by_lookback[lookback] = _signed_body_sum_from_ohlcv(
            arrays.open,
            arrays.close,
            lookback,
            "long",
        )
        short_by_lookback[lookback] = _signed_body_sum_from_ohlcv(
            arrays.open,
            arrays.close,
            lookback,
            "short",
        )
    return long_by_lookback, short_by_lookback


def _raw_from_as_of_arrays(
    pair: str,
    candles_1h: OhlcvArrays,
    *,
    impulse_lookback_bars: int,
    atr_period: int,
    include_probe_windows: bool = False,
) -> RawTickSignal | None:
    if len(candles_1h) == 0:
        return None
    base = signal_from_closes(pair, np.asarray(candles_1h.close, dtype=float))
    if base is None:
        return None
    default_period = int(atr_period)
    default_lookback = max(1, int(impulse_lookback_bars))
    atr_pct_by_period: dict[int, float] | None = None
    long_by_lookback: dict[int, float] | None = None
    short_by_lookback: dict[int, float] | None = None
    if include_probe_windows:
        atr_pct_by_period = {
            period: _atr_pct_from_ohlcv(
                candles_1h.high,
                candles_1h.low,
                candles_1h.close,
                period,
            )
            for period in TAPE_ATR_PERIODS
        }
        if default_period not in atr_pct_by_period:
            atr_pct_by_period[default_period] = _atr_pct_from_ohlcv(
                candles_1h.high,
                candles_1h.low,
                candles_1h.close,
                default_period,
            )
        long_by_lookback, short_by_lookback = _signed_body_sums_from_arrays(candles_1h)
        if default_lookback not in long_by_lookback:
            long_by_lookback[default_lookback] = _signed_body_sum_from_ohlcv(
                candles_1h.open,
                candles_1h.close,
                default_lookback,
                "long",
            )
            short_by_lookback[default_lookback] = _signed_body_sum_from_ohlcv(
                candles_1h.open,
                candles_1h.close,
                default_lookback,
                "short",
            )
        atr_pct = float(
            atr_pct_by_period.get(
                default_period,
                _atr_pct_from_ohlcv(
                    candles_1h.high,
                    candles_1h.low,
                    candles_1h.close,
                    default_period,
                ),
            )
        )
        signed_long = float(long_by_lookback.get(default_lookback, 0.0))
        signed_short = float(short_by_lookback.get(default_lookback, 0.0))
    else:
        atr_pct = _atr_pct_from_ohlcv(
            candles_1h.high,
            candles_1h.low,
            candles_1h.close,
            default_period,
        )
        signed_long = _signed_body_sum_from_ohlcv(
            candles_1h.open,
            candles_1h.close,
            default_lookback,
            "long",
        )
        signed_short = _signed_body_sum_from_ohlcv(
            candles_1h.open,
            candles_1h.close,
            default_lookback,
            "short",
        )
    return RawTickSignal(
        price=float(base.price),
        bb_pos_pct=float(base.bb_pos_pct),
        bb_mid=float(base.bb_mid),
        bb_upper=float(base.bb_upper),
        macd=float(base.macd),
        signal_line=float(base.signal_line),
        histogram=float(base.histogram),
        trend=str(base.trend),
        momentum=str(base.momentum),
        bullish_cross=bool(base.bullish_cross),
        bearish_cross=bool(base.bearish_cross),
        atr_pct=atr_pct,
        signed_body_sum_long=signed_long,
        signed_body_sum_short=signed_short,
        atr_pct_by_period=atr_pct_by_period,
        signed_body_sum_long_by_lookback=long_by_lookback,
        signed_body_sum_short_by_lookback=short_by_lookback,
    )


def _raw_from_as_of_candles(
    pair: str,
    candles_1h: list[dict[str, float]],
    *,
    impulse_lookback_bars: int,
    atr_period: int,
    include_probe_windows: bool = False,
) -> RawTickSignal | None:
    if not candles_1h:
        return None
    return _raw_from_as_of_arrays(
        pair,
        OhlcvArrays.from_candles(candles_1h),
        impulse_lookback_bars=impulse_lookback_bars,
        atr_period=atr_period,
        include_probe_windows=include_probe_windows,
    )


def build_pullback_signal_tape(
    tick_meta_map: Mapping[int, TickMeta],
    *,
    cache_dir: Path | str,
    candle_source: CandleSource = "hyperliquid",
    pairs: Sequence[str] | None = None,
    lookback_hours: int = LIVE_MACD_MAX_RECORDS,
    impulse_lookback_bars: int = DEFAULT_IMPULSE_LOOKBACK_BARS,
    atr_period: int = DEFAULT_ATR_PERIOD,
    include_probe_windows: bool = False,
) -> PullbackSignalTape:
    """Load each pair's 1h/1m series once, then emit per-tick raw signals."""
    started = time.monotonic()
    cache_path = Path(cache_dir)
    lookback = max(24, int(lookback_hours))
    universe = list(pairs) if pairs is not None else universe_pairs_from_ticks(tick_meta_map)
    sorted_ticks = sorted(tick_meta_map)
    if not universe or not sorted_ticks:
        return PullbackSignalTape(by_tick={}, pairs=tuple(universe), tick_count=0)

    first_meta = tick_meta_map[sorted_ticks[0]]
    last_meta = tick_meta_map[sorted_ticks[-1]]
    first_ms = _as_of_ms(first_meta.timestamp)
    last_ms = _as_of_ms(last_meta.timestamp)
    start_1h_ms = first_ms - lookback * HOUR_MS
    start_1m_ms = (first_ms // HOUR_MS) * HOUR_MS

    logger.info(
        "Building pullback signal tape: %d pairs × %d ticks (source=%s probe_windows=%s)",
        len(universe),
        len(sorted_ticks),
        candle_source,
        include_probe_windows,
    )
    arrays_by_pair: dict[str, tuple[OhlcvArrays, OhlcvArrays]] = {}
    for pair in universe:
        arrays_by_pair[pair] = _load_pair_arrays(
            pair,
            start_1h_ms=start_1h_ms,
            start_1m_ms=start_1m_ms,
            end_ms=last_ms,
            cache_dir=cache_path,
            candle_source=candle_source,
        )

    by_tick: dict[int, dict[str, RawTickSignal]] = {}
    for tick in sorted_ticks:
        meta = tick_meta_map[tick]
        as_of_ms = _as_of_ms(meta.timestamp)
        tick_signals: dict[str, RawTickSignal] = {}
        for pair in universe:
            candles_1h_arr, candles_1m_arr = arrays_by_pair[pair]
            as_of = as_of_1h_from_arrays_view(
                candles_1h_arr,
                candles_1m_arr,
                as_of_ms,
                max_records=lookback,
            )
            raw = _raw_from_as_of_arrays(
                pair,
                as_of,
                impulse_lookback_bars=impulse_lookback_bars,
                atr_period=atr_period,
                include_probe_windows=include_probe_windows,
            )
            if raw is not None:
                tick_signals[pair] = raw
        by_tick[int(tick)] = tick_signals

    elapsed = time.monotonic() - started
    logger.info(
        "Pullback signal tape ready in %.1fs (%d pairs, %d ticks)",
        elapsed,
        len(universe),
        len(sorted_ticks),
    )
    return PullbackSignalTape(
        by_tick=by_tick,
        pairs=tuple(universe),
        tick_count=len(sorted_ticks),
        build_seconds=elapsed,
    )


def build_pullback_signal_tapes(
    parsed_sessions: Mapping[int, Mapping[int, TickMeta]],
    *,
    cache_dir: Path | str,
    candle_source: CandleSource = "hyperliquid",
    lookback_hours: int = LIVE_MACD_MAX_RECORDS,
    impulse_lookback_bars: int = DEFAULT_IMPULSE_LOOKBACK_BARS,
    atr_period: int = DEFAULT_ATR_PERIOD,
    include_probe_windows: bool = False,
) -> dict[int, PullbackSignalTape]:
    tapes: dict[int, PullbackSignalTape] = {}
    for session_num, tick_meta_map in parsed_sessions.items():
        tapes[int(session_num)] = build_pullback_signal_tape(
            tick_meta_map,
            cache_dir=cache_dir,
            candle_source=candle_source,
            lookback_hours=lookback_hours,
            impulse_lookback_bars=impulse_lookback_bars,
            atr_period=atr_period,
            include_probe_windows=include_probe_windows,
        )
    return tapes


__all__ = [
    "PullbackSignalTape",
    "RawTickSignal",
    "TAPE_ATR_PERIODS",
    "TAPE_LOOKBACK_BARS",
    "build_pullback_signal_tape",
    "build_pullback_signal_tapes",
    "universe_pairs_from_ticks",
]
