"""Live market signal fetch for macdbb_pullback_hl (1h thesis + impulse)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from condor.strategy_runners.macdbb.market_data import (
    fetch_candidate_pairs,
    signal_from_closes,
)
from condor.strategy_runners.macdbb_pullback.metrics import compute_thesis_metrics
from condor.strategy_runners.macdbb_pullback.replay_bridge import attach_impulse_to_signal
from condor.strategy_runners.macdbb_pullback.types import SignalSnapshot

log = logging.getLogger(__name__)


def _to_pullback_signal(macdbb_signal: Any) -> SignalSnapshot:
    return SignalSnapshot(
        pair=macdbb_signal.pair,
        price=float(macdbb_signal.price),
        bb_pos_pct=float(macdbb_signal.bb_pos_pct),
        bb_mid=float(macdbb_signal.bb_mid),
        bb_upper=float(macdbb_signal.bb_upper),
        macd=float(macdbb_signal.macd),
        signal_line=float(macdbb_signal.signal_line),
        histogram=float(macdbb_signal.histogram),
        trend=str(macdbb_signal.trend),
        momentum=str(macdbb_signal.momentum),
        bullish_cross=bool(macdbb_signal.bullish_cross),
        bearish_cross=bool(macdbb_signal.bearish_cross),
    )


async def load_pullback_signals(
    params: dict[str, Any],
    *,
    extra_pairs: list[str] | None = None,
) -> tuple[list[SignalSnapshot], str | None, int]:
    pairs, regime, tradeable = await fetch_candidate_pairs(params)
    seen: set[str] = set()
    ordered: list[str] = []
    for pair in list(pairs) + list(extra_pairs or []):
        key = str(pair or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    if not ordered:
        return [], regime, tradeable

    from routines.lib.hl_candles import fetch_hl_candles

    signals: list[SignalSnapshot] = []
    interval = str(params.get("macd_interval") or "1h")
    max_records = int(params.get("macd_max_records") or 200)
    for pair in ordered:
        try:
            candles_1h = await fetch_hl_candles(pair, interval, max_records)
            closes = np.array([float(c["close"]) for c in candles_1h], dtype=float)
            base = signal_from_closes(pair, closes)
            if base is None:
                continue
            signal = _to_pullback_signal(base)
            signal = attach_impulse_to_signal(signal, candles_1h, params)
            signal.metrics = compute_thesis_metrics(signal, params)
            signals.append(signal)
        except Exception:
            log.warning("Pullback signal load failed for %s", pair, exc_info=True)
    return signals, regime, tradeable or len(signals)
