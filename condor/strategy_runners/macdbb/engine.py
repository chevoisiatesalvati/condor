"""MACDBB decision engine — shared by DeterministicRunner and research.

Extracts the live playbook's open/hold/monitor core into pure functions so
timeline backtests and live ticks cannot diverge on sizing or signal gates.
"""

from __future__ import annotations

from typing import Any

from condor.strategy_runners.macdbb.dynamic import (
    LivePolicyMeta,
    resolve_live_entry_policy,
)
from condor.strategy_runners.macdbb.metrics import (
    LiveSignalInput,
    compute_live_signal_metrics,
)
from condor.strategy_runners.macdbb.types import (
    CreateAction,
    EntryClass,
    MacdbbDecision,
    MacdbbState,
    MacdbbTickInput,
    NotifyAction,
    OpenPosition,
    Side,
    SignalSnapshot,
    StopAction,
)
from condor.strategy_runners.quantize import apply_fee_slippage, quote_to_base_amount


def _ensure_metrics(signal: SignalSnapshot, params: dict[str, Any]) -> dict[str, Any]:
    if signal.metrics:
        return signal.metrics
    live = LiveSignalInput(
        pair=signal.pair,
        price=signal.price,
        bb_pos_pct=signal.bb_pos_pct,
        bb_mid=signal.bb_mid,
        bb_upper=signal.bb_upper,
        macd=signal.macd,
        signal_line=signal.signal_line,
        histogram=signal.histogram,
        trend=signal.trend,
        momentum=signal.momentum,
        bullish_cross=signal.bullish_cross,
        bearish_cross=signal.bearish_cross,
    )
    return compute_live_signal_metrics(live, params)


def _score_candidate(metrics: dict[str, Any], side: Side, entry_class: EntryClass) -> float:
    if entry_class == "formal":
        return 100.0 + float(
            metrics["adaptive_strength_long"]
            if side == "long"
            else metrics["adaptive_strength_short"]
        )
    return float(
        metrics["adaptive_strength_long"]
        if side == "long"
        else metrics["adaptive_strength_short"]
    )


def _candidate_opens(
    signals: list[SignalSnapshot],
    *,
    adaptive_ready: bool,
    cooldown_pairs: set[str],
    open_pairs: set[str],
    params: dict[str, Any],
) -> list[tuple[SignalSnapshot, Side, EntryClass, float, dict[str, Any]]]:
    out: list[tuple[SignalSnapshot, Side, EntryClass, float, dict[str, Any]]] = []
    for signal in signals:
        if signal.pair in open_pairs or signal.pair in cooldown_pairs:
            continue
        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        if metrics.get("formal_long"):
            out.append(
                (
                    signal,
                    "long",
                    "formal",
                    _score_candidate(metrics, "long", "formal"),
                    metrics,
                )
            )
        if metrics.get("formal_short"):
            out.append(
                (
                    signal,
                    "short",
                    "formal",
                    _score_candidate(metrics, "short", "formal"),
                    metrics,
                )
            )
        if adaptive_ready:
            if metrics.get("adaptive_long_open"):
                out.append(
                    (
                        signal,
                        "long",
                        "regime_adaptive_half_size",
                        _score_candidate(metrics, "long", "regime_adaptive_half_size"),
                        metrics,
                    )
                )
            if metrics.get("adaptive_short_open"):
                out.append(
                    (
                        signal,
                        "short",
                        "regime_adaptive_half_size",
                        _score_candidate(metrics, "short", "regime_adaptive_half_size"),
                        metrics,
                    )
                )
    out.sort(key=lambda row: row[3], reverse=True)
    return out


def _update_adaptive_streak(
    state: MacdbbState,
    signals: list[SignalSnapshot],
    params: dict[str, Any],
    *,
    has_capacity: bool,
) -> MacdbbState:
    if not has_capacity:
        return state
    any_formal = False
    for signal in signals:
        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        if metrics.get("has_formal"):
            any_formal = True
            break
    streak = 0 if any_formal else state.adaptive_activation_streak + 1
    return MacdbbState(
        adaptive_activation_streak=streak,
        thesis_decay_by_pair=dict(state.thesis_decay_by_pair),
        flip_streak_by_pair=dict(state.flip_streak_by_pair),
        sl_cooldown_until_tick=dict(state.sl_cooldown_until_tick),
    )


def _monitor_stops(
    positions: list[OpenPosition],
    signals_by_pair: dict[str, SignalSnapshot],
    state: MacdbbState,
    params: dict[str, Any],
    tick_number: int,
) -> tuple[list[StopAction], MacdbbState]:
    """Thesis-decay / opposing formal flip stops (deterministic subset of playbook)."""
    decay_limit = int(params.get("thesis_decay_exit_ticks") or 3)
    flip_limit = int(params.get("flip_confirm_ticks") or 2)
    thesis = dict(state.thesis_decay_by_pair)
    flips = dict(state.flip_streak_by_pair)
    stops: list[StopAction] = []

    for pos in positions:
        signal = signals_by_pair.get(pos.pair)
        if signal is None:
            continue
        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        opposing_formal = (
            pos.side == "long" and bool(metrics.get("formal_short"))
        ) or (pos.side == "short" and bool(metrics.get("formal_long")))
        if opposing_formal:
            flips[pos.pair] = flips.get(pos.pair, 0) + 1
        else:
            flips[pos.pair] = 0

        trend = (signal.trend or "").lower()
        decaying = (pos.side == "long" and trend == "bearish") or (
            pos.side == "short" and trend == "bullish"
        )
        if decaying:
            thesis[pos.pair] = thesis.get(pos.pair, 0) + 1
        else:
            thesis[pos.pair] = 0

        if flips.get(pos.pair, 0) >= flip_limit:
            stops.append(
                StopAction(
                    executor_id=pos.executor_id,
                    pair=pos.pair,
                    reason="flip_confirm",
                    close_type="EARLY_STOP",
                )
            )
        elif thesis.get(pos.pair, 0) >= decay_limit:
            stops.append(
                StopAction(
                    executor_id=pos.executor_id,
                    pair=pos.pair,
                    reason="thesis_decay",
                    close_type="EARLY_STOP",
                )
            )

    new_state = MacdbbState(
        adaptive_activation_streak=state.adaptive_activation_streak,
        thesis_decay_by_pair=thesis,
        flip_streak_by_pair=flips,
        sl_cooldown_until_tick=dict(state.sl_cooldown_until_tick),
    )
    # Drop cooldown keys that have expired.
    new_state.sl_cooldown_until_tick = {
        pair: until
        for pair, until in new_state.sl_cooldown_until_tick.items()
        if until > tick_number
    }
    return stops, new_state


def decide(tick: MacdbbTickInput, state: MacdbbState | None = None) -> MacdbbDecision:
    """Pure tick decision: same code path for live runner and parity tests."""
    state = state or MacdbbState()
    params = dict(tick.strategy_params or {})
    activation_ticks = int(params.get("adaptive_activation_ticks") or 3)

    open_pairs = {p.pair for p in tick.open_positions}
    capacity = max(0, int(tick.max_open_executors) - len(tick.open_positions))
    state = _update_adaptive_streak(
        state, tick.signals, params, has_capacity=capacity > 0
    )
    adaptive_ready = state.adaptive_activation_streak >= activation_ticks

    cooldown_pairs = {
        pair
        for pair, until in state.sl_cooldown_until_tick.items()
        if until > tick.tick_number
    }

    signals_by_pair = {s.pair: s for s in tick.signals}
    stops, state = _monitor_stops(
        tick.open_positions, signals_by_pair, state, params, tick.tick_number
    )

    # Register SL barrier cooldowns from prior tick closes.
    cooldown_ticks = int(params.get("sl_symbol_cooldown_ticks") or 0)
    for close in tick.barrier_closes:
        close_type = str(close.get("close_type") or "").upper().replace(" ", "_")
        pair = str(close.get("pair") or "").strip()
        if cooldown_ticks > 0 and pair and close_type == "STOP_LOSS":
            state.sl_cooldown_until_tick[pair] = max(
                state.sl_cooldown_until_tick.get(pair, 0),
                tick.tick_number + cooldown_ticks,
            )
            cooldown_pairs.add(pair)

    notifications = [
        NotifyAction(
            text=(
                f"⚡ CLOSED {close.get('side', '')} {close.get('pair', '?')} | "
                f"{close.get('close_type', '')} | PnL ${float(close.get('pnl') or 0):+.2f} | "
                f"id: {close.get('id', '')}"
            )
        )
        for close in tick.barrier_closes
        if str(close.get("close_type") or "").upper().replace(" ", "_")
        in {"STOP_LOSS", "TAKE_PROFIT"}
    ]

    creates: list[CreateAction] = []
    hold_reason = ""
    if capacity <= 0:
        hold_reason = "at_max_open_executors"
    else:
        candidates = _candidate_opens(
            tick.signals,
            adaptive_ready=adaptive_ready,
            cooldown_pairs=cooldown_pairs,
            open_pairs=open_pairs,
            params=params,
        )
        if not candidates:
            hold_reason = (
                "no_formal_or_adaptive_trigger"
                if not adaptive_ready
                else "no_open_candidate"
            )
        else:
            signal, side, entry_class, score, metrics = candidates[0]
            policy = resolve_live_entry_policy(
                pair=signal.pair,
                side=side,
                entry_class=entry_class,
                metrics=metrics,
                meta=LivePolicyMeta(
                    tradeable_count=tick.tradeable_count,
                    scanner_regime=tick.scanner_regime,
                ),
                entry_streak=state.adaptive_activation_streak,
                strategy_params=params,
                formal_notional_quote=tick.formal_notional_quote,
                natr_mean_pct=signal.natr_mean_pct,
                bb_mid=signal.bb_mid,
                bb_upper=signal.bb_upper,
            )
            notional = apply_fee_slippage(
                policy.notional_quote,
                fee_bps=tick.fee_bps,
                slippage_bps=tick.slippage_bps,
            )
            q = quote_to_base_amount(
                notional_quote=notional,
                price=signal.price,
                min_notional_quote=float(params.get("min_notional_quote") or 0),
                max_notional_quote=(
                    float(params["max_notional_quote"])
                    if params.get("max_notional_quote") is not None
                    else None
                ),
                amount_step=tick.amount_step,
            )
            creates.append(
                CreateAction(
                    pair=signal.pair,
                    side=side,
                    entry_class=entry_class,
                    notional_quote=q.notional_quote,
                    base_amount=q.base_amount,
                    sl_pct=policy.sl_pct,
                    tp_pct=policy.tp_pct,
                    volatility_proxy_pct=policy.volatility_proxy_pct,
                    sizing_multiplier=policy.sizing_multiplier,
                    score=score,
                )
            )
            notifications.append(
                NotifyAction(
                    text=(
                        f"⚡ OPEN {side.upper()} {signal.pair} | {entry_class} | "
                        f"notional ${q.notional_quote:.2f} | SL {policy.sl_pct:.2f}% "
                        f"TP {policy.tp_pct:.2f}%"
                    )
                )
            )

    hold = not creates and not stops
    best = creates[0] if creates else None
    journal = {
        "entry_class": best.entry_class if best else "hold",
        "hold_reason": hold_reason if hold else "",
        "adaptive_activation_streak": state.adaptive_activation_streak,
        "scanner_regime": tick.scanner_regime or "",
        "tradeable_count": tick.tradeable_count,
        "queue_total": ",".join(s.pair for s in tick.signals),
        "best_candidate": best.pair if best else "",
        "best_score": best.score if best else 0.0,
        "stops": [s.reason for s in stops],
    }

    return MacdbbDecision(
        hold=hold,
        hold_reason=hold_reason,
        creates=creates,
        stops=stops,
        notifications=notifications,
        state=state,
        journal_fields=journal,
    )
