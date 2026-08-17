"""Bridge timeline/simulator ticks into the pullback ``decide()`` engine."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from condor.strategy_runners.macdbb_pullback.engine import decide
from condor.strategy_runners.macdbb_pullback.entry_quality import compute_impulse_metrics
from condor.strategy_runners.macdbb_pullback.metrics import compute_thesis_metrics
from condor.strategy_runners.macdbb_pullback.types import (
    MacdbbPullbackState,
    OpenPosition,
    PullbackDecision,
    PullbackTickInput,
    Side,
    SignalSnapshot,
)


def attach_impulse_to_signal(
    signal: SignalSnapshot,
    candles_1h: list[Any] | None,
    strategy_params: dict[str, Any] | None = None,
) -> SignalSnapshot:
    params = dict(strategy_params or {})
    lookback = int(params.get("impulse_lookback_bars") or 2)
    atr_period = int(params.get("atr_period") or 14)
    mult = float(params.get("impulse_atr_mult") or 1.25)
    if not candles_1h:
        return signal
    long_m = compute_impulse_metrics(
        candles_1h,
        "long",
        lookback_bars=lookback,
        atr_period=atr_period,
        impulse_atr_mult=mult,
    )
    short_m = compute_impulse_metrics(
        candles_1h,
        "short",
        lookback_bars=lookback,
        atr_period=atr_period,
        impulse_atr_mult=mult,
    )
    signal.atr_pct = long_m.atr_pct
    signal.impulse_signed_body_sum_pct = max(
        long_m.signed_body_sum_pct, short_m.signed_body_sum_pct
    )
    signal.impulse_long = long_m.is_impulse
    signal.impulse_short = short_m.is_impulse
    return signal


def _signal_from_1h_closes(
    pair: str,
    candles_1h: list[Any],
    strategy_params: dict[str, Any] | None,
) -> SignalSnapshot | None:
    """Same MACD/BB path as live load_pullback_signals (forming bar included)."""
    import numpy as np

    from condor.strategy_runners.macdbb.market_data import signal_from_closes

    closes = np.array([float(c["close"]) for c in candles_1h], dtype=float)
    base = signal_from_closes(pair, closes)
    if base is None:
        return None
    signal = SignalSnapshot(
        pair=base.pair,
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
    )
    signal.metrics = compute_thesis_metrics(signal, strategy_params)
    return attach_impulse_to_signal(signal, candles_1h, strategy_params)


def signal_from_sim_snapshot(
    pair: str,
    snapshot: Any,
    *,
    strategy_params: dict[str, Any] | None = None,
    candles_1h: list[Any] | None = None,
) -> SignalSnapshot:
    if candles_1h:
        computed = _signal_from_1h_closes(pair, candles_1h, strategy_params)
        if computed is not None:
            return computed
    parsed = getattr(snapshot, "parsed", None)
    price = float(getattr(snapshot, "price", 0) or 0)
    bb_pos = float(getattr(parsed, "bb_pos_pct", 0) or 0) if parsed else 0.0
    signal = SignalSnapshot(
        pair=pair,
        price=price,
        bb_pos_pct=bb_pos,
        bb_mid=float(getattr(parsed, "bb_mid", 0) or 0) if parsed else 0.0,
        bb_upper=float(getattr(parsed, "bb_upper", 0) or 0) if parsed else 0.0,
        macd=float(getattr(parsed, "macd", 0) or 0) if parsed else 0.0,
        signal_line=float(getattr(parsed, "signal_line", 0) or 0) if parsed else 0.0,
        histogram=float(getattr(parsed, "histogram", 0) or 0) if parsed else 0.0,
        trend=str(getattr(parsed, "trend", "") or "") if parsed else "",
        momentum=str(getattr(parsed, "momentum", "") or "") if parsed else "",
        bullish_cross=bool(getattr(parsed, "bullish_cross", False)) if parsed else False,
        bearish_cross=bool(getattr(parsed, "bearish_cross", False)) if parsed else False,
    )
    signal.metrics = compute_thesis_metrics(signal, strategy_params)
    return attach_impulse_to_signal(signal, candles_1h, strategy_params)


def _sim_unrealized_pnl(
    pos: Any,
    snapshots: Mapping[str, Any] | None,
) -> float:
    explicit = getattr(pos, "unrealized_pnl", None)
    if explicit is not None:
        return float(explicit)
    if not snapshots:
        return 0.0
    pair = str(getattr(pos, "pair", "") or "")
    snap = snapshots.get(pair) if pair else None
    if snap is None:
        return 0.0
    mark = float(getattr(snap, "price", 0) or 0)
    entry = float(getattr(pos, "entry_price", 0) or 0)
    notional = float(getattr(pos, "notional_quote", 0) or 0)
    if mark <= 0 or entry <= 0 or notional <= 0:
        return 0.0
    side_raw = str(getattr(pos, "side", "long")).lower()
    ret = (mark - entry) / entry
    if side_raw == "short":
        ret = -ret
    return notional * ret


def open_positions_from_sim(
    positions: Mapping[str, Any] | Iterable[Any],
    snapshots: Mapping[str, Any] | None = None,
) -> list[OpenPosition]:
    items = positions.values() if isinstance(positions, Mapping) else positions
    out: list[OpenPosition] = []
    for pos in items:
        side_raw = str(getattr(pos, "side", "long")).lower()
        side: Side = "short" if side_raw == "short" else "long"
        entry_class = str(getattr(pos, "entry_class", "immediate") or "immediate")
        if entry_class not in {"immediate", "pullback"}:
            entry_class = "immediate"
        out.append(
            OpenPosition(
                executor_id=str(
                    getattr(pos, "executor_id", None)
                    or f"sim:{getattr(pos, 'pair', '')}:{getattr(pos, 'entry_tick', 0)}"
                ),
                pair=str(getattr(pos, "pair", "")),
                side=side,
                entry_class=entry_class,  # type: ignore[arg-type]
                pnl=_sim_unrealized_pnl(pos, snapshots),
                entry_bb_pos_pct=float(getattr(pos, "entry_bb_pos_pct", 0) or 0),
            )
        )
    return out


def decide_from_sim_tick(
    *,
    tick_number: int,
    snapshots: Mapping[str, Any],
    open_positions: Mapping[str, Any] | Iterable[Any],
    strategy_params: dict[str, Any],
    formal_notional_quote: float,
    max_open_executors: int,
    tradeable_count: int,
    candles_1h_by_pair: Mapping[str, list[Any]] | None = None,
    barrier_closes: list[dict[str, Any]] | None = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    amount_step: float = 0.0,
    frequency_sec: int = 60,
    state: MacdbbPullbackState | None = None,
    precomputed_signals: Mapping[str, SignalSnapshot] | None = None,
) -> PullbackDecision:
    candle_map = candles_1h_by_pair or {}
    taped = precomputed_signals or {}
    signals: list[SignalSnapshot] = []
    for pair, snap in snapshots.items():
        if not getattr(snap, "price_trusted", True):
            continue
        taped_signal = taped.get(pair)
        if taped_signal is not None:
            signals.append(taped_signal)
            continue
        signals.append(
            signal_from_sim_snapshot(
                pair,
                snap,
                strategy_params=strategy_params,
                candles_1h=candle_map.get(pair),
            )
        )
    tick = PullbackTickInput(
        tick_number=int(tick_number),
        tradeable_count=int(tradeable_count),
        signals=signals,
        open_positions=open_positions_from_sim(open_positions, snapshots),
        barrier_closes=list(barrier_closes or []),
        total_amount_quote=float(formal_notional_quote),
        strategy_params=dict(strategy_params or {}),
        max_open_executors=int(max_open_executors),
        fee_bps=float(fee_bps),
        slippage_bps=float(slippage_bps),
        amount_step=float(amount_step),
        frequency_sec=int(frequency_sec),
    )
    return decide(tick, state)
