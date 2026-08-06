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
    infer_signal_label,
)
from condor.strategy_runners.macdbb.types import (
    CreateAction,
    EntryClass,
    EntryMeta,
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


def _copy_state(state: MacdbbState, **overrides: Any) -> MacdbbState:
    payload = {
        "adaptive_activation_streak": state.adaptive_activation_streak,
        "thesis_decay_by_pair": dict(state.thesis_decay_by_pair),
        "flip_streak_by_pair": dict(state.flip_streak_by_pair),
        "sl_cooldown_until_tick": dict(state.sl_cooldown_until_tick),
        "flip_cooldown_until_tick": dict(state.flip_cooldown_until_tick),
        "thesis_decay_extra_pending_by_pair": dict(
            state.thesis_decay_extra_pending_by_pair
        ),
        "entry_meta_by_pair": dict(state.entry_meta_by_pair),
        "monitor_state_by_pair": dict(state.monitor_state_by_pair),
    }
    payload.update(overrides)
    return MacdbbState(**payload)


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


def _filter_4h_allows(side: Side, trend: str | None, passed: bool | None) -> bool:
    """Match replay ``filter_4h_allows`` — reverse requires an explicit pass."""
    if passed is not True:
        return False
    if trend is None:
        return True
    trend_l = str(trend).lower()
    if side == "long":
        return trend_l == "bullish"
    return trend_l == "bearish"


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
    return _copy_state(state, adaptive_activation_streak=streak)


def _hydrate_position(pos: OpenPosition, state: MacdbbState) -> OpenPosition:
    """Fill entry_class / entry_bb_pos_pct from persisted state when available."""
    meta = state.entry_meta_by_pair.get(pos.pair)
    if meta is None:
        return pos
    return OpenPosition(
        executor_id=pos.executor_id,
        pair=pos.pair,
        side=pos.side,
        entry_class=meta.entry_class,
        pnl=pos.pnl,
        thesis_decay_streak=state.thesis_decay_by_pair.get(pos.pair, pos.thesis_decay_streak),
        flip_streak=state.flip_streak_by_pair.get(pos.pair, pos.flip_streak),
        entry_bb_pos_pct=meta.entry_bb_pos_pct,
        filled=pos.filled,
    )


def _thesis_decay_reasons(
    *,
    side: Side,
    entry_class: EntryClass,
    entry_bb_pos_pct: float,
    trend: str | None,
    bb_pos_pct: float | None,
    params: dict[str, Any],
) -> tuple[bool, bool]:
    trend_decay = False
    bb_decay = False
    trend_l = (trend or "").lower()
    if side == "long" and trend_l == "bearish":
        trend_decay = True
    elif side == "short" and trend_l == "bullish":
        trend_decay = True

    if bb_pos_pct is None:
        return trend_decay, bb_decay

    if entry_class == "regime_adaptive_half_size":
        long_max = float(params.get("adaptive_long_bb_pos_max") or 45.0)
        short_min = float(params.get("adaptive_short_bb_pos_min") or 55.0)
        if side == "long" and bb_pos_pct > long_max:
            bb_decay = True
        elif side == "short" and bb_pos_pct < short_min:
            bb_decay = True
    elif entry_class == "formal":
        drift = float(params.get("thesis_bb_drift_pts") or 20.0)
        if side == "long" and bb_pos_pct >= entry_bb_pos_pct + drift:
            bb_decay = True
        elif side == "short" and bb_pos_pct <= entry_bb_pos_pct - drift:
            bb_decay = True

    return trend_decay, bb_decay


def _build_create_action(
    *,
    signal: SignalSnapshot,
    side: Side,
    entry_class: EntryClass,
    score: float,
    metrics: dict[str, Any],
    tick: MacdbbTickInput,
    params: dict[str, Any],
    entry_streak: int,
) -> CreateAction | None:
    # total_amount_quote below 2*min_notional makes half-size entries always
    # clamp to the floor (seen live: formal=100, min=112 → every open $112).
    formal_notional = float(tick.formal_notional_quote)
    min_notional = float(params.get("min_notional_quote") or 0)
    if min_notional > 0 and formal_notional < (2.0 * min_notional):
        formal_notional = 2.0 * min_notional
    policy = resolve_live_entry_policy(
        pair=signal.pair,
        side=side,
        entry_class=entry_class,
        metrics=metrics,
        meta=LivePolicyMeta(
            tradeable_count=tick.tradeable_count,
            scanner_regime=tick.scanner_regime,
        ),
        entry_streak=entry_streak,
        strategy_params=params,
        formal_notional_quote=formal_notional,
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
    if q.base_amount <= 0 or q.notional_quote <= 0:
        return None
    return CreateAction(
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


def _monitor_stops(
    positions: list[OpenPosition],
    signals_by_pair: dict[str, SignalSnapshot],
    state: MacdbbState,
    params: dict[str, Any],
    tick: MacdbbTickInput,
) -> tuple[list[StopAction], list[CreateAction], MacdbbState, dict[str, str]]:
    """Full Step 5: flip confirm, thesis decay (NEUTRAL + BB), optional flip reverse."""
    decay_limit = int(params.get("thesis_decay_exit_ticks") or 3)
    flip_limit = int(params.get("flip_confirm_ticks") or 2)
    flip_cooldown_ticks = int(params.get("flip_cooldown_ticks") or 0)

    thesis = dict(state.thesis_decay_by_pair)
    flips = dict(state.flip_streak_by_pair)
    extra_pending = dict(state.thesis_decay_extra_pending_by_pair)
    monitor = dict(state.monitor_state_by_pair)
    flip_cooldown = {
        pair: until
        for pair, until in state.flip_cooldown_until_tick.items()
        if until > tick.tick_number
    }
    entry_meta = dict(state.entry_meta_by_pair)

    stops: list[StopAction] = []
    reverse_creates: list[CreateAction] = []
    stopped_pairs: set[str] = set()

    for raw_pos in positions:
        pos = _hydrate_position(raw_pos, state)
        # Pending / never-filled legs occupy capacity for dedup but are not live risk.
        if not pos.filled:
            monitor[pos.pair] = "pending_unfilled"
            continue
        signal = signals_by_pair.get(pos.pair)
        if signal is None:
            monitor[pos.pair] = monitor.get(pos.pair) or "hold"
            continue

        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        snapshot_signal = infer_signal_label(metrics)

        opposing_formal = (
            pos.side == "long" and bool(metrics.get("formal_short"))
        ) or (pos.side == "short" and bool(metrics.get("formal_long")))

        exit_reason = ""
        if opposing_formal and tick.tick_number > flip_cooldown.get(pos.pair, -1):
            flips[pos.pair] = flips.get(pos.pair, 0) + 1
            if flips[pos.pair] >= flip_limit:
                exit_reason = "flip_confirm"
            else:
                monitor[pos.pair] = "flip_pending"
        else:
            flips[pos.pair] = 0
            if monitor.get(pos.pair) == "flip_pending":
                monitor[pos.pair] = "thesis_intact"

        if not exit_reason:
            same_direction_formal = (
                pos.side == "long" and bool(metrics.get("formal_long"))
            ) or (pos.side == "short" and bool(metrics.get("formal_short")))
            if same_direction_formal:
                thesis[pos.pair] = 0
                extra_pending[pos.pair] = False
                monitor[pos.pair] = "thesis_intact"
            elif snapshot_signal == "NEUTRAL":
                trend_decay, bb_decay = _thesis_decay_reasons(
                    side=pos.side,
                    entry_class=pos.entry_class,
                    entry_bb_pos_pct=pos.entry_bb_pos_pct,
                    trend=signal.trend,
                    bb_pos_pct=signal.bb_pos_pct,
                    params=params,
                )
                if trend_decay or bb_decay:
                    thesis[pos.pair] = thesis.get(pos.pair, 0) + 1
                    monitor[pos.pair] = "thesis_decay"
                else:
                    thesis[pos.pair] = 0
                    extra_pending[pos.pair] = False
                    monitor[pos.pair] = "thesis_intact"

                if thesis.get(pos.pair, 0) >= decay_limit:
                    if pos.pnl < 0 and not extra_pending.get(pos.pair, False):
                        extra_pending[pos.pair] = True
                    else:
                        exit_reason = "thesis_decay"

        if exit_reason:
            stops.append(
                StopAction(
                    executor_id=pos.executor_id,
                    pair=pos.pair,
                    reason=exit_reason,
                    close_type="EARLY_STOP",
                )
            )
            stopped_pairs.add(pos.pair)
            monitor[pos.pair] = exit_reason
            thesis.pop(pos.pair, None)
            flips.pop(pos.pair, None)
            extra_pending.pop(pos.pair, None)

            if exit_reason == "flip_confirm":
                if flip_cooldown_ticks > 0:
                    flip_cooldown[pos.pair] = tick.tick_number + flip_cooldown_ticks
                reverse_side: Side = "short" if pos.side == "long" else "long"
                # Capacity after this stop frees one slot for same-pair reverse.
                open_after = len(positions) - len(stopped_pairs) + len(reverse_creates)
                if (
                    open_after < int(tick.max_open_executors)
                    and bool(metrics.get(f"formal_{reverse_side}"))
                    and _filter_4h_allows(
                        reverse_side,
                        signal.filter_4h_trend,
                        signal.filter_4h_pass,
                    )
                ):
                    create = _build_create_action(
                        signal=signal,
                        side=reverse_side,
                        entry_class="formal",
                        score=_score_candidate(metrics, reverse_side, "formal"),
                        metrics=metrics,
                        tick=tick,
                        params=params,
                        entry_streak=state.adaptive_activation_streak,
                    )
                    if create is not None:
                        reverse_creates.append(create)
                        entry_meta[pos.pair] = EntryMeta(
                            entry_class="formal",
                            entry_bb_pos_pct=float(signal.bb_pos_pct),
                            side=reverse_side,
                        )
            else:
                entry_meta.pop(pos.pair, None)

    new_state = _copy_state(
        state,
        thesis_decay_by_pair=thesis,
        flip_streak_by_pair=flips,
        thesis_decay_extra_pending_by_pair=extra_pending,
        flip_cooldown_until_tick=flip_cooldown,
        entry_meta_by_pair=entry_meta,
        monitor_state_by_pair=monitor,
        sl_cooldown_until_tick={
            pair: until
            for pair, until in state.sl_cooldown_until_tick.items()
            if until > tick.tick_number
        },
    )
    return stops, reverse_creates, new_state, monitor


def decide(tick: MacdbbTickInput, state: MacdbbState | None = None) -> MacdbbDecision:
    """Pure tick decision: same code path for live runner and parity tests."""
    state = state or MacdbbState()
    params = dict(tick.strategy_params or {})
    activation_ticks = int(params.get("adaptive_activation_ticks") or 3)

    open_pairs = {p.pair for p in tick.open_positions if p.pair}
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
    cooldown_pairs |= {
        pair
        for pair, until in state.flip_cooldown_until_tick.items()
        if until > tick.tick_number
    }

    signals_by_pair = {s.pair: s for s in tick.signals}
    stops, reverse_creates, state, monitor = _monitor_stops(
        tick.open_positions, signals_by_pair, state, params, tick
    )
    stopped_pairs = {s.pair for s in stops}

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
        # Drop entry meta for barrier-closed pairs.
        if pair and close_type in {"STOP_LOSS", "TAKE_PROFIT"}:
            state.entry_meta_by_pair.pop(pair, None)
            state.thesis_decay_by_pair.pop(pair, None)
            state.flip_streak_by_pair.pop(pair, None)
            state.thesis_decay_extra_pending_by_pair.pop(pair, None)
            state.monitor_state_by_pair.pop(pair, None)

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

    creates: list[CreateAction] = list(reverse_creates)
    hold_reason = ""

    # Effective open set after stops (reverse creates occupy the freed slot).
    effective_open = (open_pairs - stopped_pairs) | {c.pair for c in creates}
    remaining_capacity = max(
        0, int(tick.max_open_executors) - len(tick.open_positions) + len(stops) - len(creates)
    )

    if not tick.inventory_available:
        hold_reason = "inventory_unavailable"
        creates = []
    elif remaining_capacity <= 0 and not creates:
        hold_reason = "at_max_open_executors"
    elif remaining_capacity > 0:
        candidates = _candidate_opens(
            tick.signals,
            adaptive_ready=adaptive_ready,
            cooldown_pairs=cooldown_pairs,
            open_pairs=effective_open | cooldown_pairs,
            params=params,
        )
        # Never open a pair we are stopping this tick (except flip reverse already added).
        candidates = [c for c in candidates if c[0].pair not in stopped_pairs]
        if not candidates and not creates:
            hold_reason = (
                "no_formal_or_adaptive_trigger"
                if not adaptive_ready
                else "no_open_candidate"
            )
        elif candidates and remaining_capacity > 0:
            signal, side, entry_class, score, metrics = candidates[0]
            create = _build_create_action(
                signal=signal,
                side=side,
                entry_class=entry_class,
                score=score,
                metrics=metrics,
                tick=tick,
                params=params,
                entry_streak=state.adaptive_activation_streak,
            )
            if create is not None:
                creates.append(create)
                state.entry_meta_by_pair[signal.pair] = EntryMeta(
                    entry_class=entry_class,
                    entry_bb_pos_pct=float(signal.bb_pos_pct),
                    side=side,
                )
                notifications.append(
                    NotifyAction(
                        text=(
                            f"⚡ OPEN {side.upper()} {signal.pair} | {entry_class} | "
                            f"notional ${create.notional_quote:.2f} | "
                            f"SL {create.sl_pct:.2f}% TP {create.tp_pct:.2f}%"
                        )
                    )
                )
            elif not creates:
                hold_reason = "sizing_rejected"

    reverse_pairs = {c.pair for c in reverse_creates}
    for create in creates:
        if create.pair not in reverse_pairs:
            continue
        notifications.append(
            NotifyAction(
                text=(
                    f"⚡ OPEN {create.side.upper()} {create.pair} | {create.entry_class} | "
                    f"flip_reverse | notional ${create.notional_quote:.2f} | "
                    f"SL {create.sl_pct:.2f}% TP {create.tp_pct:.2f}%"
                )
            )
        )

    hold = not creates and not stops
    best = creates[0] if creates else None
    position_actions = [f"{s.pair}:{s.reason}" for s in stops]
    if tick.barrier_closes:
        position_actions.extend(
            f"{c.get('pair')}:{c.get('close_type')}" for c in tick.barrier_closes
        )
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
        "open_count": len(tick.open_positions),
        "open_pairs": ",".join(sorted(open_pairs)),
        "inventory_available": tick.inventory_available,
        "position_action": ",".join(position_actions),
    }
    for pair, mon in sorted(monitor.items()):
        if pair in open_pairs or pair in stopped_pairs:
            journal[f"monitor_{pair}"] = mon

    return MacdbbDecision(
        hold=hold,
        hold_reason=hold_reason,
        creates=creates,
        stops=stops,
        notifications=notifications,
        state=state,
        journal_fields=journal,
    )
