"""macdbb_pullback_hl decision engine — thesis + staged pullback entries."""

from __future__ import annotations

from typing import Any

from condor.strategy_runners.macdbb_pullback.dynamic import (
    resolve_pullback_entry_policy,
)
from condor.strategy_runners.macdbb_pullback.entry_quality import (
    DEFAULT_CHASE_LONG_BB_POS_MAX,
    DEFAULT_CHASE_SHORT_BB_POS_MIN,
    DEFAULT_IMPULSE_ATR_MULT,
    DEFAULT_PULLBACK_EPSILON_PCT,
    ImpulseMetrics,
    allow_immediate_entry,
    is_chase_extended,
    pullback_reached,
)
from condor.strategy_runners.macdbb_pullback.metrics import (
    compute_thesis_metrics,
    infer_signal_label,
)
from condor.strategy_runners.macdbb_pullback.types import (
    ArmedThesis,
    CreateAction,
    EntryClass,
    EntryMeta,
    MacdbbPullbackState,
    NotifyAction,
    OpenPosition,
    PullbackDecision,
    PullbackTickInput,
    Side,
    SignalSnapshot,
    StopAction,
)
from condor.strategy_runners.macdbb_pullback.params import minutes_to_ticks
from condor.strategy_runners.quantize import apply_fee_slippage, quote_to_base_amount


def _copy_state(state: MacdbbPullbackState, **overrides: Any) -> MacdbbPullbackState:
    payload = {
        "armed_by_pair": dict(state.armed_by_pair),
        "sl_cooldown_until_tick": dict(state.sl_cooldown_until_tick),
        "entry_meta_by_pair": dict(state.entry_meta_by_pair),
        "thesis_decay_by_pair": dict(state.thesis_decay_by_pair),
        "flip_streak_by_pair": dict(state.flip_streak_by_pair),
        "thesis_decay_extra_pending_by_pair": dict(
            state.thesis_decay_extra_pending_by_pair
        ),
        "thesis_decay_grace_until_tick": dict(state.thesis_decay_grace_until_tick),
        "flip_cooldown_until_tick": dict(state.flip_cooldown_until_tick),
        "monitor_state_by_pair": dict(state.monitor_state_by_pair),
    }
    payload.update(overrides)
    return MacdbbPullbackState(**payload)


def _ensure_metrics(signal: SignalSnapshot, params: dict[str, Any]) -> dict[str, Any]:
    if signal.metrics and "thesis_long" in signal.metrics:
        return signal.metrics
    return compute_thesis_metrics(signal, params)


def _hours_to_ticks(hours: float, frequency_sec: int) -> int:
    freq = max(1, int(frequency_sec or 60))
    return max(1, int(round(float(hours) * 3600.0 / freq)))


def _decay_grace_ticks(params: dict[str, Any], frequency_sec: int) -> int:
    if "thesis_decay_negative_grace_ticks" in params:
        return max(0, int(params.get("thesis_decay_negative_grace_ticks") or 0))
    return minutes_to_ticks(
        float(params.get("thesis_decay_negative_grace_minutes") or 30.0),
        frequency_sec,
    )


def _impulse_for_side(signal: SignalSnapshot, side: Side) -> bool:
    if side == "long":
        return bool(signal.impulse_long)
    return bool(signal.impulse_short)


def _notional_quote(tick: PullbackTickInput) -> float:
    return float(tick.total_amount_quote or tick.formal_notional_quote or 500.0)


def _build_create(
    *,
    signal: SignalSnapshot,
    side: Side,
    entry_class: EntryClass,
    score: float,
    tick: PullbackTickInput,
    params: dict[str, Any],
) -> CreateAction | None:
    policy = resolve_pullback_entry_policy(
        budget_quote=_notional_quote(tick),
        atr_pct=signal.atr_pct,
        params=params,
    )
    notional = apply_fee_slippage(
        policy.notional_quote,
        fee_bps=tick.fee_bps,
        slippage_bps=tick.slippage_bps,
    )
    min_notional = float(params.get("min_notional_quote") or 0)
    max_notional = (
        float(params["max_notional_quote"])
        if params.get("max_notional_quote") is not None
        else None
    )
    q = quote_to_base_amount(
        notional_quote=notional,
        price=signal.price,
        min_notional_quote=min_notional,
        max_notional_quote=max_notional,
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
        score=score,
    )


def _hydrate_position(pos: OpenPosition, state: MacdbbPullbackState) -> OpenPosition:
    meta = state.entry_meta_by_pair.get(pos.pair)
    if meta is None:
        return OpenPosition(
            executor_id=pos.executor_id,
            pair=pos.pair,
            side=pos.side,
            entry_class=pos.entry_class,
            pnl=pos.pnl,
            entry_bb_pos_pct=pos.entry_bb_pos_pct,
            filled=pos.filled,
            thesis_decay_streak=state.thesis_decay_by_pair.get(
                pos.pair, pos.thesis_decay_streak
            ),
            flip_streak=state.flip_streak_by_pair.get(pos.pair, pos.flip_streak),
        )
    return OpenPosition(
        executor_id=pos.executor_id,
        pair=pos.pair,
        side=pos.side,
        entry_class=meta.entry_class,
        pnl=pos.pnl,
        entry_bb_pos_pct=meta.entry_bb_pos_pct,
        filled=pos.filled,
        thesis_decay_streak=state.thesis_decay_by_pair.get(
            pos.pair, pos.thesis_decay_streak
        ),
        flip_streak=state.flip_streak_by_pair.get(pos.pair, pos.flip_streak),
    )


def _thesis_decay_reasons(
    *,
    side: Side,
    entry_bb_pos_pct: float,
    trend: str | None,
    bb_pos_pct: float | None,
    params: dict[str, Any],
) -> tuple[bool, bool]:
    """Trend against entry and/or BB drift from entry BB%."""
    trend_decay = False
    bb_decay = False
    trend_l = (trend or "").lower()
    if side == "long" and trend_l == "bearish":
        trend_decay = True
    elif side == "short" and trend_l == "bullish":
        trend_decay = True

    if bb_pos_pct is None:
        return trend_decay, bb_decay

    drift = float(params.get("thesis_bb_drift_pts") or 20.0)
    if side == "long" and bb_pos_pct >= entry_bb_pos_pct + drift:
        bb_decay = True
    elif side == "short" and bb_pos_pct <= entry_bb_pos_pct - drift:
        bb_decay = True
    return trend_decay, bb_decay


def _monitor_stops(
    positions: list[OpenPosition],
    signals_by_pair: dict[str, SignalSnapshot],
    state: MacdbbPullbackState,
    params: dict[str, Any],
    tick: PullbackTickInput,
) -> tuple[list[StopAction], MacdbbPullbackState]:
    """Optional flip confirm + thesis decay early exits (gated by params)."""
    enable_flip = bool(params.get("enable_flip_exit"))
    enable_decay = bool(params.get("enable_thesis_decay_exit"))
    if not enable_flip and not enable_decay:
        return [], state

    decay_limit = int(params.get("thesis_decay_exit_ticks") or 0)
    if decay_limit <= 0 and enable_decay:
        decay_hours = float(params.get("thesis_decay_exit_hours") or 28.0)
        decay_limit = _hours_to_ticks(decay_hours, tick.frequency_sec)
    flip_limit = max(1, int(params.get("flip_confirm_ticks") or 2))
    flip_cooldown_ticks = int(params.get("flip_cooldown_ticks") or 0)
    if flip_cooldown_ticks <= 0 and enable_flip:
        flip_hours = float(params.get("flip_cooldown_hours") or 0.0)
        if flip_hours > 0:
            flip_cooldown_ticks = _hours_to_ticks(flip_hours, tick.frequency_sec)

    thesis = dict(state.thesis_decay_by_pair)
    flips = dict(state.flip_streak_by_pair)
    grace_until = dict(state.thesis_decay_grace_until_tick)
    for pair, pending in state.thesis_decay_extra_pending_by_pair.items():
        if pending and pair not in grace_until:
            grace_until[pair] = tick.tick_number + 1
    monitor = dict(state.monitor_state_by_pair)
    flip_cooldown = {
        pair: until
        for pair, until in state.flip_cooldown_until_tick.items()
        if until > tick.tick_number
    }
    entry_meta = dict(state.entry_meta_by_pair)
    grace_ticks = _decay_grace_ticks(params, tick.frequency_sec)

    stops: list[StopAction] = []

    for raw_pos in positions:
        pos = _hydrate_position(raw_pos, state)
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

        opposing_thesis = (
            pos.side == "long" and bool(metrics.get("thesis_short"))
        ) or (pos.side == "short" and bool(metrics.get("thesis_long")))

        exit_reason = ""
        if enable_flip:
            if opposing_thesis and tick.tick_number > flip_cooldown.get(pos.pair, -1):
                flips[pos.pair] = flips.get(pos.pair, 0) + 1
                if flips[pos.pair] >= flip_limit:
                    exit_reason = "flip_confirm"
                else:
                    monitor[pos.pair] = "flip_pending"
            else:
                flips[pos.pair] = 0
                if monitor.get(pos.pair) == "flip_pending":
                    monitor[pos.pair] = "thesis_intact"

        if not exit_reason and enable_decay:
            same_direction = (
                pos.side == "long" and bool(metrics.get("thesis_long"))
            ) or (pos.side == "short" and bool(metrics.get("thesis_short")))
            if same_direction:
                thesis[pos.pair] = 0
                grace_until.pop(pos.pair, None)
                monitor[pos.pair] = "thesis_intact"
            elif snapshot_signal == "NEUTRAL":
                trend_decay, bb_decay = _thesis_decay_reasons(
                    side=pos.side,
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
                    grace_until.pop(pos.pair, None)
                    monitor[pos.pair] = "thesis_intact"

                if decay_limit > 0 and thesis.get(pos.pair, 0) >= decay_limit:
                    if pos.pnl >= 0 or grace_ticks <= 0:
                        exit_reason = "thesis_decay"
                    else:
                        if pos.pair not in grace_until:
                            grace_until[pos.pair] = tick.tick_number + grace_ticks
                        if tick.tick_number >= grace_until[pos.pair]:
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
            monitor[pos.pair] = exit_reason
            thesis.pop(pos.pair, None)
            flips.pop(pos.pair, None)
            grace_until.pop(pos.pair, None)
            entry_meta.pop(pos.pair, None)
            if exit_reason == "flip_confirm" and flip_cooldown_ticks > 0:
                flip_cooldown[pos.pair] = tick.tick_number + flip_cooldown_ticks

    new_state = _copy_state(
        state,
        thesis_decay_by_pair=thesis,
        flip_streak_by_pair=flips,
        thesis_decay_extra_pending_by_pair={},
        thesis_decay_grace_until_tick=grace_until,
        flip_cooldown_until_tick=flip_cooldown,
        entry_meta_by_pair=entry_meta,
        monitor_state_by_pair=monitor,
    )
    return stops, new_state


def _update_armed(
    state: MacdbbPullbackState,
    signals: list[SignalSnapshot],
    *,
    open_pairs: set[str],
    cooldown_pairs: set[str],
    params: dict[str, Any],
    tick: PullbackTickInput,
) -> tuple[MacdbbPullbackState, list[tuple[SignalSnapshot, Side, EntryClass, float]]]:
    """Arm / expire theses and collect ready entry candidates."""
    timeout_ticks = int(params.get("pullback_timeout_ticks") or 0)
    if timeout_ticks <= 0:
        timeout_hours = float(params.get("pullback_timeout_hours") or 12.0)
        timeout_ticks = _hours_to_ticks(timeout_hours, tick.frequency_sec)
    pullback_eps = float(
        params.get("pullback_epsilon_pct") or DEFAULT_PULLBACK_EPSILON_PCT
    )
    chase_long_max = float(
        params.get("chase_long_bb_pos_max") or DEFAULT_CHASE_LONG_BB_POS_MAX
    )
    chase_short_min = float(
        params.get("chase_short_bb_pos_min") or DEFAULT_CHASE_SHORT_BB_POS_MIN
    )

    armed = dict(state.armed_by_pair)
    signals_by_pair = {s.pair: s for s in signals}
    ready: list[tuple[SignalSnapshot, Side, EntryClass, float]] = []

    for pair, thesis in list(armed.items()):
        if pair in open_pairs or pair in cooldown_pairs:
            armed.pop(pair, None)
            continue
        signal = signals_by_pair.get(pair)
        if signal is None:
            continue
        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        still_valid = (
            thesis.side == "long" and bool(metrics.get("thesis_long"))
        ) or (thesis.side == "short" and bool(metrics.get("thesis_short")))
        if not still_valid:
            opposing = (
                thesis.side == "long" and bool(metrics.get("thesis_short"))
            ) or (thesis.side == "short" and bool(metrics.get("thesis_long")))
            if opposing or not bool(metrics.get("has_thesis")):
                armed.pop(pair, None)
            elif tick.tick_number - thesis.armed_tick >= timeout_ticks:
                armed.pop(pair, None)
            continue
        if tick.tick_number - thesis.armed_tick >= timeout_ticks:
            armed.pop(pair, None)
            continue
        if pullback_reached(
            thesis.side,
            signal.price,
            signal.bb_mid or thesis.bb_mid_at_arm,
            pullback_epsilon_pct=pullback_eps,
        ):
            score = float(
                metrics["strength_long"]
                if thesis.side == "long"
                else metrics["strength_short"]
            )
            ready.append((signal, thesis.side, "pullback", 50.0 + score))
            armed.pop(pair, None)

    for signal in signals:
        if signal.pair in open_pairs or signal.pair in cooldown_pairs:
            continue
        if signal.pair in armed:
            continue
        if any(r[0].pair == signal.pair for r in ready):
            continue
        metrics = _ensure_metrics(signal, params)
        signal.metrics = metrics
        for side in ("long", "short"):
            key = f"thesis_{side}"
            if not bool(metrics.get(key)):
                continue
            side_t: Side = side  # type: ignore[assignment]
            impulse = _impulse_for_side(signal, side_t)
            chase = is_chase_extended(
                side_t,
                signal.bb_pos_pct,
                chase_long_bb_pos_max=chase_long_max,
                chase_short_bb_pos_min=chase_short_min,
            )
            impulse_metrics = ImpulseMetrics(
                atr_pct=float(signal.atr_pct or 0.0),
                signed_body_sum_pct=float(signal.impulse_signed_body_sum_pct or 0.0),
                is_impulse=impulse,
                lookback_bars=int(params.get("impulse_lookback_bars") or 2),
                bars_used=int(params.get("impulse_lookback_bars") or 2),
            )
            score = float(
                metrics["strength_long"] if side_t == "long" else metrics["strength_short"]
            )
            if allow_immediate_entry(impulse=impulse_metrics, chase_extended=chase):
                ready.append((signal, side_t, "immediate", 100.0 + score))
            else:
                armed[signal.pair] = ArmedThesis(
                    pair=signal.pair,
                    side=side_t,
                    armed_tick=tick.tick_number,
                    armed_price=float(signal.price),
                    impulse_flag=impulse or chase,
                    bb_mid_at_arm=float(signal.bb_mid or 0.0),
                )
            break

    ready.sort(key=lambda row: row[3], reverse=True)
    return _copy_state(state, armed_by_pair=armed), ready


def decide(
    tick: PullbackTickInput,
    state: MacdbbPullbackState | None = None,
) -> PullbackDecision:
    state = state or MacdbbPullbackState()
    params = dict(tick.strategy_params or {})
    params.setdefault("impulse_atr_mult", DEFAULT_IMPULSE_ATR_MULT)

    open_pairs = {p.pair for p in tick.open_positions if p.pair}
    signals_by_pair = {s.pair: s for s in tick.signals}

    stops, state = _monitor_stops(
        tick.open_positions, signals_by_pair, state, params, tick
    )
    stopped_pairs = {s.pair for s in stops}

    cooldown_ticks = int(params.get("sl_symbol_cooldown_ticks") or 0)
    if cooldown_ticks <= 0:
        cooldown_hours = float(params.get("sl_symbol_cooldown_hours") or 5.0)
        cooldown_ticks = _hours_to_ticks(cooldown_hours, tick.frequency_sec)
    for close in tick.barrier_closes:
        close_type = str(close.get("close_type") or "").upper().replace(" ", "_")
        pair = str(close.get("pair") or "").strip()
        if cooldown_ticks > 0 and pair and close_type == "STOP_LOSS":
            state.sl_cooldown_until_tick[pair] = max(
                state.sl_cooldown_until_tick.get(pair, 0),
                tick.tick_number + cooldown_ticks,
            )
        if pair and close_type in {"STOP_LOSS", "TAKE_PROFIT"}:
            state.entry_meta_by_pair.pop(pair, None)
            state.armed_by_pair.pop(pair, None)
            state.thesis_decay_by_pair.pop(pair, None)
            state.flip_streak_by_pair.pop(pair, None)
            state.thesis_decay_extra_pending_by_pair.pop(pair, None)
            state.thesis_decay_grace_until_tick.pop(pair, None)
            state.monitor_state_by_pair.pop(pair, None)

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
    state = _copy_state(
        state,
        sl_cooldown_until_tick={
            pair: until
            for pair, until in state.sl_cooldown_until_tick.items()
            if until > tick.tick_number
        },
        flip_cooldown_until_tick={
            pair: until
            for pair, until in state.flip_cooldown_until_tick.items()
            if until > tick.tick_number
        },
    )

    notifications = [
        NotifyAction(
            text=(
                f"[BARRIER] {close.get('pair')} "
                f"{close.get('close_type')} pnl={close.get('pnl_quote')}"
            )
        )
        for close in tick.barrier_closes
        if str(close.get("close_type") or "").upper().replace(" ", "_")
        in {"STOP_LOSS", "TAKE_PROFIT"}
    ]
    for stop in stops:
        notifications.append(
            NotifyAction(
                text=f"[EARLY_STOP] {stop.pair} {stop.reason}",
            )
        )

    creates: list[CreateAction] = []
    hold_reason = ""
    effective_open = open_pairs - stopped_pairs
    remaining = max(
        0,
        int(tick.max_open_executors) - len(tick.open_positions) + len(stops),
    )

    if not tick.inventory_available:
        hold_reason = "inventory_unavailable"
    elif remaining <= 0:
        hold_reason = "at_max_open_executors"
    else:
        state, ready = _update_armed(
            state,
            tick.signals,
            open_pairs=effective_open | cooldown_pairs,
            cooldown_pairs=cooldown_pairs,
            params=params,
            tick=tick,
        )
        ready = [row for row in ready if row[0].pair not in stopped_pairs]
        if not ready:
            hold_reason = "no_entry_candidate"
        else:
            signal, side, entry_class, score = ready[0]
            create = _build_create(
                signal=signal,
                side=side,
                entry_class=entry_class,
                score=score,
                tick=tick,
                params=params,
            )
            if create is None:
                hold_reason = "sizing_rejected"
            else:
                creates.append(create)
                state.armed_by_pair.pop(signal.pair, None)
                state.entry_meta_by_pair[signal.pair] = EntryMeta(
                    entry_class=entry_class,
                    entry_bb_pos_pct=float(signal.bb_pos_pct),
                    side=side,
                )
                notifications.append(
                    NotifyAction(
                        text=(
                            f"[OPEN] {side.upper()} {signal.pair} "
                            f"class={entry_class} notional={create.notional_quote:.2f} "
                            f"SL={create.sl_pct:.2f}% TP={create.tp_pct:.2f}%"
                        )
                    )
                )

    hold = not creates and not stops
    best = creates[0] if creates else None
    journal = {
        "entry_class": best.entry_class if best else "hold",
        "hold_reason": hold_reason if hold else "",
        "armed_pairs": ",".join(sorted(state.armed_by_pair)),
        "best_candidate": best.pair if best else "",
        "best_score": best.score if best else 0.0,
        "stops": [s.reason for s in stops],
        "open_count": len(tick.open_positions),
        "tradeable_count": tick.tradeable_count,
        "enable_flip_exit": bool(params.get("enable_flip_exit")),
        "enable_thesis_decay_exit": bool(params.get("enable_thesis_decay_exit")),
        "monitor": ",".join(
            f"{pair}:{status}"
            for pair, status in sorted(state.monitor_state_by_pair.items())
        ),
    }
    return PullbackDecision(
        hold=hold,
        hold_reason=hold_reason,
        creates=creates,
        stops=stops,
        notifications=notifications,
        state=state,
        journal_fields=journal,
    )
