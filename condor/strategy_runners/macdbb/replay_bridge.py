"""Bridge timeline/simulator ticks into the shared ``decide()`` engine."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from condor.strategy_runners.macdbb.engine import decide
from condor.strategy_runners.macdbb.types import (
    MacdbbDecision,
    MacdbbState,
    MacdbbTickInput,
    OpenPosition,
    Side,
    SignalSnapshot,
)


def signal_from_sim_snapshot(
    pair: str,
    snapshot: Any,
    *,
    metrics: Mapping[str, Any] | None = None,
) -> SignalSnapshot:
    """Convert a replay tick snapshot into a live/engine ``SignalSnapshot``."""
    parsed = getattr(snapshot, "parsed", None)
    price = float(getattr(snapshot, "price", 0) or 0)
    bb_pos = float(getattr(parsed, "bb_pos_pct", 0) or 0) if parsed else 0.0
    snap_metrics = dict(metrics or getattr(snapshot, "metrics", {}) or {})
    return SignalSnapshot(
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
        natr_mean_pct=(
            float(snap_metrics["natr_mean_pct"])
            if snap_metrics.get("natr_mean_pct") is not None
            else None
        ),
        metrics=snap_metrics,
    )


def open_positions_from_sim(
    positions: Mapping[str, Any] | Iterable[Any],
) -> list[OpenPosition]:
    """Map simulator open positions into engine open-position rows."""
    items = positions.values() if isinstance(positions, Mapping) else positions
    out: list[OpenPosition] = []
    for pos in items:
        side_raw = str(getattr(pos, "side", "long")).lower()
        side: Side = "short" if side_raw == "short" else "long"
        entry_class = str(getattr(pos, "entry_class", "formal") or "formal")
        if entry_class not in {"formal", "regime_adaptive_half_size", "hold"}:
            entry_class = "formal"
        out.append(
            OpenPosition(
                executor_id=str(
                    getattr(pos, "executor_id", None)
                    or f"sim:{getattr(pos, 'pair', '')}:{getattr(pos, 'entry_tick', 0)}"
                ),
                pair=str(getattr(pos, "pair", "")),
                side=side,
                entry_class=entry_class,  # type: ignore[arg-type]
                pnl=float(getattr(pos, "unrealized_pnl", 0) or 0),
                thesis_decay_streak=int(getattr(pos, "thesis_decay_streak", 0) or 0),
                flip_streak=int(getattr(pos, "flip_streak", 0) or 0),
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
    scanner_regime: str | None = None,
    adaptive_activation_streak: int = 0,
    sl_cooldown_until_tick: Mapping[str, int] | None = None,
    barrier_closes: list[dict[str, Any]] | None = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    amount_step: float = 0.0,
    state: MacdbbState | None = None,
) -> MacdbbDecision:
    """Build ``MacdbbTickInput`` from simulator state and call shared ``decide()``."""
    signals = [
        signal_from_sim_snapshot(pair, snap)
        for pair, snap in snapshots.items()
        if getattr(snap, "price_trusted", True)
    ]
    regime = None
    if scanner_regime in {"mature", "degen"}:
        regime = scanner_regime  # type: ignore[assignment]
    engine_state = state or MacdbbState(
        adaptive_activation_streak=int(adaptive_activation_streak),
        sl_cooldown_until_tick=dict(sl_cooldown_until_tick or {}),
    )
    if state is None and adaptive_activation_streak:
        engine_state.adaptive_activation_streak = int(adaptive_activation_streak)
    tick = MacdbbTickInput(
        tick_number=int(tick_number),
        scanner_regime=regime,
        tradeable_count=int(tradeable_count),
        signals=signals,
        open_positions=open_positions_from_sim(open_positions),
        barrier_closes=list(barrier_closes or []),
        formal_notional_quote=float(formal_notional_quote),
        strategy_params=dict(strategy_params or {}),
        max_open_executors=int(max_open_executors),
        fee_bps=float(fee_bps),
        slippage_bps=float(slippage_bps),
        amount_step=float(amount_step),
    )
    return decide(tick, engine_state)
