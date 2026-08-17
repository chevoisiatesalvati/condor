"""Timeline simulator for macdbb_pullback_hl (shared decide + persistent armed state)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from condor.strategy_runners.macdbb_pullback.replay_bridge import decide_from_sim_tick
from condor.strategy_runners.macdbb_pullback.types import MacdbbPullbackState
from routines.macdbb_pullback_hl_replay.impulse_candles import candles_1h_for_tick
from routines.macdbb_pullback_hl_replay.models import (
    PullbackReplayConfig,
    strategy_params_from_config,
)
from routines.macdbb_pullback_hl_replay.signal_tape import (
    PullbackSignalTape,
    build_pullback_signal_tape,
)
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
    HlCandleCache,
    HlPriceCache,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    OpenPosition,
    ReportMeta,
    SignalSnapshot,
    SimTrade,
    TickMeta,
    compute_return_pct,
)
from routines.macdbb_scanner_aggressive_hl_replay.signals import (
    _resolve_price,
    build_tick_snapshots,
    session_has_trusted_prices,
)
from routines.macdbb_scanner_aggressive_hl_replay.simulator import (
    _apply_intrabar_barriers,
    _close_trade,
    _entry_bb_pos_pct,
    _position_barriers,
    _scanner_allows_entries,
    _skipped_summary,
    barrier_exit_price,
)

logger = logging.getLogger(__name__)


def _tick_pairs(meta: TickMeta, extra_pairs: list[str] | None) -> list[str]:
    pairs = list(meta.macd_pairs)
    if meta.queue_total:
        for pair in meta.queue_total:
            if pair not in pairs:
                pairs.append(pair)
    if meta.signals_1h:
        for pair in meta.signals_1h:
            if pair not in pairs:
                pairs.append(pair)
    if meta.create_plans:
        for pair in meta.create_plans:
            if pair not in pairs:
                pairs.append(pair)
    if extra_pairs:
        for pair in extra_pairs:
            if pair not in pairs:
                pairs.append(pair)
    return pairs


def _price_snapshots_from_cache(
    meta: TickMeta,
    extra_pairs: list[str],
    last_price_by_pair: dict[str, float],
    hl_price_cache: HlPriceCache | None,
    snap_config: Any,
) -> dict[str, SignalSnapshot]:
    """Mark prices only — skip report/MACD parse when a signal tape is in use."""
    snapshots: dict[str, SignalSnapshot] = {}
    for pair in _tick_pairs(meta, extra_pairs):
        price, trusted, _tag = _resolve_price(
            pair,
            meta,
            None,
            snap_config,
            last_price_by_pair,
            hl_price_cache,
        )
        snapshots[pair] = SignalSnapshot(
            pair=pair,
            price=price,
            signal="",
            parsed=None,
            metrics={},
            filter_4h_pass=None,
            filter_4h_trend=None,
            source="tape_price",
            price_trusted=trusted,
        )
    return snapshots


def _summary_from_trades(trades: list[SimTrade]) -> dict[str, Any]:
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_quote > 0)
    sl = sum(1 for t in trades if "stop_loss" in t.exit_reason)
    tp = sum(1 for t in trades if "take_profit" in t.exit_reason)
    flip = sum(1 for t in trades if t.exit_reason == "flip_confirm")
    decay = sum(1 for t in trades if t.exit_reason == "thesis_decay")
    immediate = sum(1 for t in trades if t.entry_class == "immediate")
    pullback = sum(1 for t in trades if t.entry_class == "pullback")
    return {
        "status": "ok",
        "total_trades": total,
        "formal_trades": immediate,
        "adaptive_trades": pullback,
        "immediate_trades": immediate,
        "pullback_trades": pullback,
        "win_rate_pct": (wins / total * 100.0) if total else 0.0,
        "net_pnl_quote": sum(t.pnl_quote for t in trades),
        "stop_loss_trades": sl,
        "take_profit_trades": tp,
        "flip_confirm_trades": flip,
        "thesis_decay_trades": decay,
        "sl_before_tp_rate": (sl / total) if total else 0.0,
        "avg_sl_pct": (sum(t.sl_pct_used for t in trades) / total) if total else 0.0,
        "avg_tp_pct": (sum(t.tp_pct_used for t in trades) / total) if total else 0.0,
        "avg_notional": (sum(t.notional_quote for t in trades) / total) if total else 0.0,
    }


def simulate_pullback_session(
    *,
    session_num: int,
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: PullbackReplayConfig,
    signal_config: Any | None = None,
    hl_price_cache: HlPriceCache | None = None,
    hl_candle_cache: HlCandleCache | None = None,
    hl_barrier_candle_cache: HlCandleCache | None = None,
    hl_vol_candle_cache: HlCandleCache | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    candle_cache_dir: Path | None = None,
    signal_tape: PullbackSignalTape | None = None,
    use_signal_tape: bool = True,
    collect_debug_rows: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SimTrade], dict[str, Any]]:
    # signal_config: MACDBB ReplayConfigBase used for snapshot/price resolution.
    snap_config = signal_config if signal_config is not None else config
    if config.require_price_data and not session_has_trusted_prices(
        tick_meta_map,
        reports_by_pair,
        snap_config,
        hl_price_cache=hl_price_cache,
    ):
        return [], [], [], _skipped_summary("skipped_no_price_data")

    barrier_candles = (
        hl_barrier_candle_cache if hl_barrier_candle_cache is not None else hl_candle_cache
    )
    cache_dir = Path(candle_cache_dir or config.hl_cache_dir or "data/hl_candles")
    strategy_params = strategy_params_from_config(config)
    cooldown_ticks = int(
        strategy_params.get("sl_symbol_cooldown_ticks")
        or config.sl_cooldown_ticks
        or 0
    )
    if cooldown_ticks <= 0:
        freq = max(1, int(config.frequency_sec or 60))
        cooldown_ticks = max(
            1,
            int(round(float(config.sl_symbol_cooldown_hours) * 3600 / freq)),
        )
    # Barrier helpers read config.sl_cooldown_ticks / sl_pct / tp_pct.
    config_for_barriers = config.model_copy(
        update={
            "sl_cooldown_ticks": cooldown_ticks,
            "sl_pct": float(strategy_params.get("sl_pct") or config.sl_pct),
            "tp_pct": float(strategy_params.get("tp_pct") or config.tp_pct),
        }
    )

    per_pair_rows: list[dict[str, Any]] = []
    per_tick_rows: list[dict[str, Any]] = []
    simulated_trades: list[SimTrade] = []
    open_positions: dict[str, OpenPosition] = {}
    sl_cooldown_until: dict[str, int] = {}
    engine_state = MacdbbPullbackState()
    last_price_by_pair: dict[str, float] = {}
    filter_4h_cache: dict[tuple[str, int], tuple[bool | None, str | None]] = {}
    candle_memo: dict[tuple[str, int], list[dict[str, float]]] = {}
    active_tape = signal_tape
    if use_signal_tape and active_tape is None:
        active_tape = build_pullback_signal_tape(
            tick_meta_map,
            cache_dir=cache_dir,
            candle_source=config.candle_source,
            impulse_lookback_bars=int(
                strategy_params.get("impulse_lookback_bars") or 2
            ),
            atr_period=int(strategy_params.get("atr_period") or 14),
        )

    sorted_ticks = sorted(tick_meta_map)
    total_ticks = len(sorted_ticks)
    progress_step = max(1, total_ticks // 25) if total_ticks >= 100 else 0
    last_progress_emit_at = 0.0

    for tick_index, tick in enumerate(sorted_ticks):
        now = time.monotonic()
        log_emit = (
            tick_index == 0
            or tick_index + 1 == total_ticks
            or bool(progress_step and (tick_index + 1) % progress_step == 0)
        )
        if log_emit:
            logger.info(
                "Pullback sim session %s: tick %d/%d open=%d armed=%d",
                session_num,
                tick_index + 1,
                total_ticks,
                len(open_positions),
                len(engine_state.armed_by_pair),
            )
        if on_progress is not None and (
            log_emit or (now - last_progress_emit_at) >= 2.0
        ):
            on_progress(tick_index + 1, total_ticks)
            last_progress_emit_at = now

        meta = tick_meta_map[tick]
        extra_pairs = list(open_positions.keys())
        if active_tape is not None:
            snapshots = _price_snapshots_from_cache(
                meta,
                extra_pairs,
                last_price_by_pair,
                hl_price_cache,
                snap_config,
            )
        else:
            snapshots = build_tick_snapshots(
                meta,
                reports_by_pair,
                snap_config,
                last_price_by_pair,
                extra_pairs=extra_pairs,
                hl_price_cache=hl_price_cache,
                filter_4h_cache=filter_4h_cache,
            )

        closes_this_tick: list[str] = []
        opens_this_tick: list[str] = []
        barrier_close_events: list[dict[str, Any]] = []

        if tick_index > 0:
            prev_tick = sorted_ticks[tick_index - 1]
            prev_meta = tick_meta_map[prev_tick]
            _apply_intrabar_barriers(
                session_num,
                tick,
                prev_meta.timestamp,
                meta.timestamp,
                open_positions,
                simulated_trades,
                closes_this_tick,
                sl_cooldown_until,
                config_for_barriers,  # type: ignore[arg-type]
                barrier_candles,
            )
            # Collect barrier closes for engine cooldown bookkeeping.
            for token in closes_this_tick:
                pair, reason = token.split(":", 1)
                close_type = (
                    "STOP_LOSS" if "stop_loss" in reason else "TAKE_PROFIT"
                )
                barrier_close_events.append(
                    {"pair": pair, "close_type": close_type, "pnl_quote": None}
                )

        # Mark-price barriers at tick close.
        for pair in list(open_positions.keys()):
            position = open_positions[pair]
            snapshot = snapshots.get(pair)
            if snapshot is None or not snapshot.price_trusted:
                continue
            mark_price = float(snapshot.price)
            current_return = compute_return_pct(
                position.side, position.entry_price, mark_price
            )
            sl_pct, tp_pct = _position_barriers(position, config_for_barriers)  # type: ignore[arg-type]
            exit_reason = ""
            if current_return <= -(sl_pct / 100.0):
                exit_reason = "stop_loss_close_proxy"
            elif current_return >= (tp_pct / 100.0):
                exit_reason = "take_profit_close_proxy"
            if not exit_reason:
                continue
            exit_price = barrier_exit_price(
                position,
                exit_reason,
                mark_price,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
            )
            simulated_trades.append(
                _close_trade(
                    session_num,
                    position,
                    tick,
                    exit_price,
                    exit_reason,
                    exit_time=meta.timestamp,
                )
            )
            closes_this_tick.append(f"{pair}:{exit_reason}")
            del open_positions[pair]
            close_type = "STOP_LOSS" if "stop_loss" in exit_reason else "TAKE_PROFIT"
            barrier_close_events.append(
                {"pair": pair, "close_type": close_type, "pnl_quote": None}
            )
            if exit_reason == "stop_loss_close_proxy":
                sl_cooldown_until[pair] = tick + cooldown_ticks

        entries_allowed = _scanner_allows_entries(meta, snap_config)
        if entries_allowed or open_positions or engine_state.armed_by_pair:
            eligible = {
                pair: snap
                for pair, snap in snapshots.items()
                if snap.price_trusted
                and pair not in open_positions
                and tick > sl_cooldown_until.get(pair, -1)
            }
            # Always include open legs for monitoring.
            for pair, snap in snapshots.items():
                if pair in open_positions and snap.price_trusted:
                    eligible[pair] = snap
            # Also keep armed pairs visible even if not in scanner this tick.
            for pair in list(engine_state.armed_by_pair):
                if pair in snapshots and snapshots[pair].price_trusted:
                    eligible[pair] = snapshots[pair]

            candles_1h_by_pair: dict[str, list[dict[str, float]]] = {}
            precomputed_signals = None
            if active_tape is not None:
                precomputed_signals = active_tape.materialize_signals(
                    tick,
                    eligible.keys(),
                    strategy_params,
                )
            else:
                for pair in eligible:
                    memo_key = (pair, int(meta.timestamp.timestamp()))
                    if memo_key not in candle_memo:
                        candle_memo[memo_key] = candles_1h_for_tick(
                            pair,
                            meta.timestamp,
                            cache_dir=cache_dir,
                            candle_source=config.candle_source,
                        )
                    candles_1h_by_pair[pair] = candle_memo[memo_key]

            decision = decide_from_sim_tick(
                tick_number=tick,
                snapshots=eligible,
                open_positions=open_positions,
                strategy_params=strategy_params,
                formal_notional_quote=float(config.formal_notional_quote),
                max_open_executors=int(config.max_open_executors),
                tradeable_count=int(meta.tradeable_count or 0),
                candles_1h_by_pair=candles_1h_by_pair,
                barrier_closes=barrier_close_events,
                fee_bps=float(config.fee_bps or 0),
                slippage_bps=float(config.slippage_bps or 0),
                amount_step=float(config.amount_step or 0),
                frequency_sec=int(config.frequency_sec or 60),
                state=engine_state,
                precomputed_signals=precomputed_signals,
            )
            engine_state = decision.state

            for stop in decision.stops:
                position = open_positions.get(stop.pair)
                if position is None:
                    continue
                snap = snapshots.get(stop.pair)
                mark = float(snap.price) if snap is not None else position.entry_price
                simulated_trades.append(
                    _close_trade(
                        session_num,
                        position,
                        tick,
                        mark,
                        stop.reason,
                        exit_time=meta.timestamp,
                    )
                )
                closes_this_tick.append(f"{stop.pair}:{stop.reason}")
                del open_positions[stop.pair]

            if (
                entries_allowed
                and decision.creates
                and len(open_positions) < config.max_open_executors
            ):
                create = decision.creates[0]
                snap = eligible.get(create.pair) or snapshots.get(create.pair)
                if snap is not None and create.pair not in open_positions:
                    metrics = getattr(snap, "metrics", {}) or {}
                    open_positions[create.pair] = OpenPosition(
                        entry_tick=tick,
                        entry_time=meta.timestamp,
                        pair=create.pair,
                        side=create.side,
                        entry_price=snap.price,
                        entry_class=create.entry_class,
                        entry_trigger=f"{create.entry_class}_{create.side}",
                        notional_quote=create.notional_quote,
                        entry_score_long=float(metrics.get("strength_long", 0) or 0),
                        entry_score_short=float(metrics.get("strength_short", 0) or 0),
                        entry_adaptive_activation_streak=0,
                        entry_bb_pos_pct=_entry_bb_pos_pct(snap),
                        entry_price_trusted=True,
                        sl_pct=create.sl_pct,
                        tp_pct=create.tp_pct,
                    )
                    opens_this_tick.append(f"{create.entry_class}_{create.side}")

            if collect_debug_rows:
                for pair, snap in snapshots.items():
                    metrics = getattr(snap, "metrics", {}) or {}
                    per_pair_rows.append(
                        {
                            "session": session_num,
                            "tick": tick,
                            "tick_time_utc": meta.timestamp.isoformat(),
                            "pair": pair,
                            "price": snap.price,
                            "signal": getattr(snap, "signal", ""),
                            "bb_pos_pct": getattr(getattr(snap, "parsed", None), "bb_pos_pct", 0),
                            "thesis_long": int(bool(metrics.get("thesis_long"))),
                            "thesis_short": int(bool(metrics.get("thesis_short"))),
                            "filter_4h_pass": snap.filter_4h_pass,
                            "filter_4h_trend": snap.filter_4h_trend,
                            "armed": int(pair in engine_state.armed_by_pair),
                            "price_trusted": int(bool(snap.price_trusted)),
                        }
                    )

        if collect_debug_rows:
            per_tick_rows.append(
                {
                    "session": session_num,
                    "tick": tick,
                    "tick_time_utc": meta.timestamp.isoformat(),
                    "open_positions": len(open_positions),
                    "armed": len(engine_state.armed_by_pair),
                    "opens": ",".join(opens_this_tick),
                    "closes": ",".join(closes_this_tick),
                }
            )

    # Force-close leftovers at last mark (not entry) so open MTM is visible.
    if sorted_ticks and open_positions:
        last_tick = sorted_ticks[-1]
        last_meta = tick_meta_map[last_tick]
        for pair, position in list(open_positions.items()):
            mark = float(last_price_by_pair.get(pair) or position.entry_price)
            simulated_trades.append(
                _close_trade(
                    session_num,
                    position,
                    last_tick,
                    mark,
                    "session_end",
                    exit_time=last_meta.timestamp,
                )
            )
            del open_positions[pair]

    return per_pair_rows, per_tick_rows, simulated_trades, _summary_from_trades(simulated_trades)


__all__ = ["simulate_pullback_session"]
