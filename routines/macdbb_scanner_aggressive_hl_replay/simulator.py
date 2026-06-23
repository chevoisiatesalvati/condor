from __future__ import annotations

import datetime as dt
from typing import Any

from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    JournalSignal1h,
    OpenPosition,
    ParsedReport,
    SimTrade,
    StrategyReplayConfig,
    TickMeta,
    compute_return_pct,
)
from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import (
    DynamicReplayPolicy,
    EntryPolicyResult,
    resolve_fixed_entry_policy,
)
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import is_report_driven_data_source
from routines.macdbb_scanner_aggressive_hl_replay.reports import ReportMeta, ScannerReportMeta, load_scanner_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import (
    _default_strategy_params,
    preserve_journal_queue_fields,
    refresh_tick_meta_from_reports,
)
from routines.macdbb_scanner_aggressive_hl_replay.session_config import replay_config_from_session
import logging
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import HlCandleCache, scan_barriers_between

logger = logging.getLogger(__name__)
from routines.macdbb_scanner_aggressive_hl_replay.signals import (
    build_tick_snapshots,
    filter_4h_allows,
    session_has_trusted_prices,
)


def _adaptive_4h_allows(
    side: str,
    trend: str | None,
    passed: bool | None,
    config: StrategyReplayConfig,
) -> bool:
    if config.ignore_adaptive_4h_filter:
        return True
    return filter_4h_allows(side, trend, passed)


def _upper_band_formal_short(parsed: ParsedReport, epsilon: float) -> bool:
    upper = parsed.bb_upper * (1.0 - epsilon / 100.0)
    return (
        parsed.price >= upper
        and parsed.trend == "bearish"
        and parsed.momentum == "decreasing"
        and parsed.histogram < 0
    )


def _formal_short_entry_allowed(
    parsed: ParsedReport | None,
    prev_parsed: ParsedReport | None,
    metrics: dict[str, float | bool],
    *,
    epsilon: float,
    session_first_tick: int,
    tick: int,
) -> bool:
    """Cross-based formal shorts require bearish_cross on the prior 1h report."""
    if not bool(metrics["formal_short"]) or parsed is None:
        return False
    if _upper_band_formal_short(parsed, epsilon):
        return True
    cross_path = parsed.bearish_cross and parsed.macd < 0
    if not cross_path:
        return True
    if prev_parsed is None:
        return tick == session_first_tick
    return prev_parsed.bearish_cross


def _queue_rank(pair: str, meta: TickMeta) -> int:
    try:
        return meta.macd_pairs.index(pair)
    except ValueError:
        return 9999


def _candidate_strength(side: str, metrics: dict[str, float | bool]) -> float:
    if side == "long":
        return float(metrics["adaptive_strength_long"])
    return float(metrics["adaptive_strength_short"])


def _position_barriers(position: OpenPosition, config: StrategyReplayConfig) -> tuple[float, float]:
    sl_pct = position.sl_pct if position.sl_pct > 0 else config.sl_pct
    tp_pct = position.tp_pct if position.tp_pct > 0 else config.tp_pct
    return sl_pct, tp_pct


def barrier_exit_price(
    position: OpenPosition,
    exit_reason: str,
    mark_price: float,
    *,
    sl_pct: float,
    tp_pct: float,
) -> float:
    """Return exit price for sim closes; SL/TP proxy exits use configured barrier levels."""
    if exit_reason == "stop_loss_close_proxy":
        threshold = sl_pct / 100.0
        if position.side == "long":
            return position.entry_price * (1.0 - threshold)
        return position.entry_price * (1.0 + threshold)
    if exit_reason == "take_profit_close_proxy":
        threshold = tp_pct / 100.0
        if position.side == "long":
            return position.entry_price * (1.0 + threshold)
        return position.entry_price * (1.0 - threshold)
    return mark_price


def _resolve_entry_policy(
    *,
    pair: str,
    side: str,
    entry_class: str,
    metrics: dict[str, float | bool],
    meta: TickMeta,
    entry_streak: int,
    config: StrategyReplayConfig,
    entry_time: dt.datetime,
    replay_policy: DynamicReplayPolicy | None,
    journal_signal: JournalSignal1h | None = None,
    hl_candle_cache: HlCandleCache | None = None,
    hl_vol_candle_cache: HlCandleCache | None = None,
) -> EntryPolicyResult:
    if replay_policy is None:
        return resolve_fixed_entry_policy(entry_class=entry_class, config=config)
    return replay_policy.resolve_entry(
        pair=pair,
        side=side,
        entry_class=entry_class,
        metrics=metrics,
        meta=meta,
        entry_streak=entry_streak,
        journal_signal=journal_signal,
        hl_candle_cache=hl_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
        entry_time=entry_time,
    )


def _open_position(
    *,
    entry_tick: int,
    entry_time: dt.datetime,
    pair: str,
    side: str,
    entry_price: float,
    entry_class: str,
    entry_trigger: str,
    metrics: dict[str, float | bool],
    meta: TickMeta,
    entry_streak: int,
    config: StrategyReplayConfig,
    replay_policy: DynamicReplayPolicy | None,
    entry_bb_pos_pct: float,
    journal_signal: JournalSignal1h | None = None,
    hl_candle_cache: HlCandleCache | None = None,
    hl_vol_candle_cache: HlCandleCache | None = None,
) -> OpenPosition:
    policy_result = _resolve_entry_policy(
        pair=pair,
        side=side,
        entry_class=entry_class,
        metrics=metrics,
        meta=meta,
        entry_streak=entry_streak,
        config=config,
        entry_time=entry_time,
        replay_policy=replay_policy,
        journal_signal=journal_signal,
        hl_candle_cache=hl_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
    )
    return OpenPosition(
        entry_tick=entry_tick,
        entry_time=entry_time,
        pair=pair,
        side=side,
        entry_price=entry_price,
        entry_class=entry_class,
        entry_trigger=entry_trigger,
        notional_quote=policy_result.notional_quote,
        entry_score_long=float(metrics["adaptive_strength_long"]),
        entry_score_short=float(metrics["adaptive_strength_short"]),
        entry_adaptive_activation_streak=entry_streak,
        entry_bb_pos_pct=entry_bb_pos_pct,
        entry_price_trusted=True,
        sl_pct=policy_result.sl_pct,
        tp_pct=policy_result.tp_pct,
        volatility_proxy_pct=policy_result.volatility_proxy_pct,
        sizing_multiplier=policy_result.sizing_multiplier,
    )


def _thesis_decay_reasons(
    position: OpenPosition,
    *,
    trend: str | None,
    bb_pos_pct: float | None,
    config: StrategyReplayConfig,
) -> tuple[bool, bool]:
    trend_decay = False
    bb_decay = False

    if position.side == "long" and trend == "bearish":
        trend_decay = True
    elif position.side == "short" and trend == "bullish":
        trend_decay = True

    if bb_pos_pct is None:
        return trend_decay, bb_decay

    if position.entry_class == "regime_adaptive_half_size":
        if position.side == "long" and bb_pos_pct > config.adaptive_long_bb_pos_max:
            bb_decay = True
        elif position.side == "short" and bb_pos_pct < config.adaptive_short_bb_pos_min:
            bb_decay = True
    elif position.entry_class == "formal":
        drift = config.thesis_bb_drift_pts
        if position.side == "long" and bb_pos_pct >= position.entry_bb_pos_pct + drift:
            bb_decay = True
        elif position.side == "short" and bb_pos_pct <= position.entry_bb_pos_pct - drift:
            bb_decay = True

    return trend_decay, bb_decay


def _entry_bb_pos_pct(snapshot: Any) -> float:
    if snapshot.parsed is not None:
        return float(snapshot.parsed.bb_pos_pct)
    return 0.0


def _update_thesis_decay_streak(
    position: OpenPosition,
    *,
    snapshot_signal: str,
    metrics: dict[str, float | bool],
    trend: str | None,
    bb_pos_pct: float | None,
    config: StrategyReplayConfig,
) -> None:
    same_direction_formal = (
        position.side == "long" and bool(metrics["formal_long"])
    ) or (position.side == "short" and bool(metrics["formal_short"]))

    if same_direction_formal:
        position.thesis_decay_streak = 0
        position.monitor_state = "thesis_intact"
        position.thesis_decay_extra_pending = False
        return

    if snapshot_signal != "NEUTRAL":
        return

    trend_decay, bb_decay = _thesis_decay_reasons(
        position,
        trend=trend,
        bb_pos_pct=bb_pos_pct,
        config=config,
    )
    if trend_decay or bb_decay:
        position.thesis_decay_streak += 1
        position.monitor_state = "thesis_decay"
    else:
        position.thesis_decay_streak = 0
        position.monitor_state = "thesis_intact"
        position.thesis_decay_extra_pending = False


def _close_trade(
    session_num: int,
    position: OpenPosition,
    exit_tick: int,
    exit_price: float,
    exit_reason: str,
) -> SimTrade:
    return_pct = compute_return_pct(position.side, position.entry_price, exit_price)
    hold_ticks = exit_tick - position.entry_tick
    return SimTrade(
        session_num=session_num,
        entry_tick=position.entry_tick,
        exit_tick=exit_tick,
        pair=position.pair,
        side=position.side,
        entry_price=position.entry_price,
        exit_price=exit_price,
        hold_ticks=hold_ticks,
        exit_reason=exit_reason,
        pnl_quote=position.notional_quote * return_pct,
        return_pct=return_pct * 100.0,
        entry_class=position.entry_class,
        entry_trigger=position.entry_trigger,
        notional_quote=position.notional_quote,
        entry_score_long=position.entry_score_long,
        entry_score_short=position.entry_score_short,
        entry_adaptive_activation_streak=position.entry_adaptive_activation_streak,
        sl_pct_used=position.sl_pct,
        tp_pct_used=position.tp_pct,
        volatility_proxy_pct=position.volatility_proxy_pct,
        sizing_multiplier=position.sizing_multiplier,
    )


def _snapshot_row(
    session_num: int,
    tick: int,
    meta: TickMeta,
    pair: str,
    snapshot: Any,
    blockers: list[str],
    config: StrategyReplayConfig,
) -> dict[str, Any]:
    metrics = snapshot.metrics
    row: dict[str, Any] = {
        "session": session_num,
        "tick": tick,
        "tick_time_utc": meta.timestamp.isoformat(),
        "pair": pair,
        "report_id": snapshot.report_id,
        "signal_source": snapshot.source,
        "price_trusted": int(snapshot.price_trusted),
        "entry_class_journal": meta.entry_class or "",
        "adaptive_activation_streak": meta.adaptive_activation_streak
        if meta.adaptive_activation_streak is not None
        else "",
        "signal": snapshot.signal,
        "bb_pos_pct": round(snapshot.parsed.bb_pos_pct, 2) if snapshot.parsed else "",
        "price": round(snapshot.price, 8),
        "trend": snapshot.parsed.trend if snapshot.parsed else "",
        "momentum": snapshot.parsed.momentum if snapshot.parsed else "",
        "macd_gap_ratio": round(float(metrics["macd_gap_ratio"]), 4),
        "hist_ratio": round(float(metrics["hist_ratio"]), 4),
        "formal_long": int(bool(metrics["formal_long"])),
        "formal_short": int(bool(metrics["formal_short"])),
        "adaptive_long_eligible": int(bool(metrics["adaptive_long_eligible"])),
        "adaptive_short_eligible": int(bool(metrics["adaptive_short_eligible"])),
        "adaptive_strength_long": round(float(metrics["adaptive_strength_long"]), 4),
        "adaptive_strength_short": round(float(metrics["adaptive_strength_short"]), 4),
        "adaptive_long_open": int(bool(metrics["adaptive_long_open"])),
        "adaptive_short_open": int(bool(metrics["adaptive_short_open"])),
        "filter_4h_pass": ""
        if snapshot.filter_4h_pass is None
        else int(snapshot.filter_4h_pass),
        "filter_4h_trend": snapshot.filter_4h_trend or "",
        "blockers": ",".join(blockers),
        "match_ok": 1,
        "note": "",
    }
    if config.compare_journal_flags:
        row["journal_fL"] = snapshot.journal_fl if snapshot.journal_fl is not None else ""
        row["journal_fS"] = snapshot.journal_fs if snapshot.journal_fs is not None else ""
        row["journal_aL"] = snapshot.journal_al if snapshot.journal_al is not None else ""
        row["journal_aS"] = snapshot.journal_as if snapshot.journal_as is not None else ""
        row["mismatch_fL"] = (
            int(bool(metrics["formal_long"]) != bool(snapshot.journal_fl))
            if snapshot.journal_fl is not None
            else ""
        )
        row["mismatch_fS"] = (
            int(bool(metrics["formal_short"]) != bool(snapshot.journal_fs))
            if snapshot.journal_fs is not None
            else ""
        )
        row["mismatch_aL"] = (
            int(bool(metrics["adaptive_long_open"]) != bool(snapshot.journal_al))
            if snapshot.journal_al is not None
            else ""
        )
        row["mismatch_aS"] = (
            int(bool(metrics["adaptive_short_open"]) != bool(snapshot.journal_as))
            if snapshot.journal_as is not None
            else ""
        )
    return row


def _advance_simulated_streak(
    snapshots: dict[str, Any],
    current_streak: int,
    open_position_count: int,
    opened_this_tick: bool,
) -> int:
    if opened_this_tick:
        return 0
    if open_position_count > 0:
        return current_streak
    if not snapshots:
        return current_streak
    if all(item.signal == "NEUTRAL" for item in snapshots.values()):
        return current_streak + 1
    return 0


def _effective_adaptive_streak(meta: TickMeta, simulated_streak: int) -> int:
    """Pre-entry streak for adaptive gating.

    Journal ``adaptive_activation_streak`` is logged after the tick decision
    (often reset to 0 on the same tick as an open). Simulated streak matches
    live pre-entry state for parity replay.
    """
    return simulated_streak


def _adaptive_entry_allowed(
    *,
    pair: str,
    meta: TickMeta,
    simulated_streak: int,
    open_position_count: int,
    adaptive_slot_fill_budget: int,
    activation_ticks: int,
) -> bool:
    if pair in meta.create_plans:
        return True
    if activation_ticks == 0:
        return True
    streak = _effective_adaptive_streak(meta, simulated_streak)
    if open_position_count == 0:
        return streak >= activation_ticks
    if adaptive_slot_fill_budget > 0:
        return True
    return False


def _canonical_trading_pair(pair: str) -> str:
    if "-" in pair:
        return pair
    return f"{pair}-USD"


def _exit_price_from_pnl(position: OpenPosition, pnl_quote: float) -> float:
    return_pct = pnl_quote / position.notional_quote
    if position.side == "long":
        return position.entry_price * (1.0 + return_pct)
    return position.entry_price / (1.0 + return_pct)


def _position_pnl_for_pair(meta: TickMeta, pair: str) -> float | None:
    if pair in meta.position_pnl_by_pair:
        return meta.position_pnl_by_pair[pair]
    if meta.monitored_pair == pair:
        return meta.position_pnl_snapshot
    return None


def _monitor_mark_price(
    position: OpenPosition,
    meta: TickMeta,
    snapshot_price: float,
) -> float:
    position_pnl = _position_pnl_for_pair(meta, position.pair)
    if position_pnl is not None and position.notional_quote > 0:
        return _exit_price_from_pnl(position, position_pnl)
    return snapshot_price


def _apply_journal_barrier_closes(
    session_num: int,
    tick: int,
    meta: TickMeta,
    open_positions: dict[str, OpenPosition],
    simulated_trades: list[SimTrade],
    closes_this_tick: list[str],
    sl_cooldown_until: dict[str, int],
    config: StrategyReplayConfig,
    replay_policy: DynamicReplayPolicy | None = None,
) -> None:
    if replay_policy is not None and replay_policy.skip_journal_barriers():
        return
    for event in meta.barrier_closes:
        position = open_positions.get(event.pair)
        if position is None:
            continue
        if event.close_type in {"stop_loss", "gone_leg"}:
            exit_reason = "stop_loss_close_proxy"
        elif event.close_type == "take_profit":
            exit_reason = "take_profit_close_proxy"
        else:
            continue
        if event.pnl_quote is not None:
            exit_price = _exit_price_from_pnl(position, event.pnl_quote)
        elif exit_reason == "stop_loss_close_proxy":
            sl_pct, _ = _position_barriers(position, config)
            sl = sl_pct / 100.0
            exit_price = (
                position.entry_price * (1.0 - sl)
                if position.side == "long"
                else position.entry_price * (1.0 + sl)
            )
        else:
            _, tp_pct = _position_barriers(position, config)
            tp = tp_pct / 100.0
            exit_price = (
                position.entry_price * (1.0 + tp)
                if position.side == "long"
                else position.entry_price * (1.0 - tp)
            )
        simulated_trades.append(
            _close_trade(
                session_num,
                position,
                tick,
                exit_price,
                exit_reason,
            )
        )
        closes_this_tick.append(f"{event.pair}:{exit_reason}")
        del open_positions[event.pair]
        if exit_reason == "stop_loss_close_proxy":
            sl_cooldown_until[event.pair] = tick + config.sl_cooldown_ticks


def _apply_intrabar_barriers(
    session_num: int,
    tick: int,
    window_start: dt.datetime,
    window_end: dt.datetime,
    open_positions: dict[str, OpenPosition],
    simulated_trades: list[SimTrade],
    closes_this_tick: list[str],
    sl_cooldown_until: dict[str, int],
    config: StrategyReplayConfig,
    hl_barrier_candle_cache: HlCandleCache | None,
) -> None:
    if not hl_barrier_candle_cache:
        return
    for pair in list(open_positions.keys()):
        position = open_positions[pair]
        candles = hl_barrier_candle_cache.get(_canonical_trading_pair(pair))
        if not candles:
            continue
        scan_start = max(window_start, position.entry_time)
        if scan_start >= window_end:
            continue
        sl_pct, tp_pct = _position_barriers(position, config)
        hit = scan_barriers_between(
            candles,
            scan_start,
            window_end,
            position.side,
            position.entry_price,
            sl_pct,
            tp_pct,
        )
        if hit is None:
            continue
        exit_reason, exit_price = hit
        simulated_trades.append(
            _close_trade(
                session_num,
                position,
                tick,
                exit_price,
                exit_reason,
            )
        )
        closes_this_tick.append(f"{pair}:{exit_reason}")
        del open_positions[pair]
        if exit_reason == "stop_loss_close_proxy":
            sl_cooldown_until[pair] = tick + config.sl_cooldown_ticks


def _scanner_allows_entries(meta: TickMeta, config: StrategyReplayConfig) -> bool:
    if meta.tradeable_count is not None and meta.tradeable_count < config.min_tradeable_count:
        return False
    if meta.scanner_analyzed is not None and meta.scanner_analyzed < config.min_tradeable_count:
        return False
    return True


def _session_parity_mode(config: StrategyReplayConfig) -> bool:
    return (
        isinstance(config, DynamicStrategyReplayConfig)
        and config.replay_mode == "session_parity"
    )


def _session_parity_journal_allows_entries(
    meta: TickMeta,
    config: StrategyReplayConfig,
) -> bool:
    if not _session_parity_mode(config):
        return True
    if meta.create_plans:
        return True
    entry_class = meta.entry_class or "hold"
    return entry_class != "hold"


def _session_parity_pair_allowed(
    pair: str,
    meta: TickMeta,
    config: StrategyReplayConfig,
) -> bool:
    if not _session_parity_mode(config) or not meta.create_plans:
        return True
    return pair in meta.create_plans


def _skipped_summary(reason: str) -> dict[str, Any]:
    return {
        "status": reason,
        "total_trades": 0,
        "wins": 0,
        "win_rate_pct": 0.0,
        "net_pnl_quote": 0.0,
        "formal_trades": 0,
        "adaptive_trades": 0,
        "formal_pnl": 0.0,
        "adaptive_pnl": 0.0,
        "by_trigger": {},
    }


def simulate_strategy_session(
    session_num: int,
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: StrategyReplayConfig,
    hl_price_cache: dict[tuple[str, int], float] | None = None,
    hl_candle_cache: HlCandleCache | None = None,
    hl_barrier_candle_cache: HlCandleCache | None = None,
    hl_vol_candle_cache: HlCandleCache | None = None,
    replay_policy: DynamicReplayPolicy | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SimTrade], dict[str, Any]]:
    if config.require_price_data and not session_has_trusted_prices(
        tick_meta_map,
        reports_by_pair,
        config,
        hl_price_cache=hl_price_cache,
    ):
        return [], [], [], _skipped_summary("skipped_no_price_data")

    barrier_candles = hl_barrier_candle_cache if hl_barrier_candle_cache is not None else hl_candle_cache
    vol_candles = (
        hl_vol_candle_cache
        if hl_vol_candle_cache is not None
        else barrier_candles
    )

    per_pair_rows: list[dict[str, Any]] = []
    per_tick_rows: list[dict[str, Any]] = []
    simulated_trades: list[SimTrade] = []

    open_positions: dict[str, OpenPosition] = {}
    sl_cooldown_until: dict[str, int] = {}
    flip_cooldown_until: dict[str, int] = {}
    last_price_by_pair: dict[str, float] = {}
    last_signal_by_pair: dict[str, JournalSignal1h] = {}
    last_parsed_1h_by_pair: dict[str, ParsedReport] = {}
    last_metrics_by_pair: dict[str, dict[str, float | bool]] = {}
    last_seen_by_pair: dict[str, tuple[int, float]] = {}
    simulated_streak = 0
    adaptive_slot_fill_budget = 0

    report_driven_params: dict[str, Any] | None = None
    scanner_reports: list[ScannerReportMeta] | None = None
    if is_report_driven_data_source(config.data_source):
        session_dir = TRADING_AGENTS_DIR / config.strategy_slug / f"sessions/session_{session_num}"
        if session_dir.is_dir():
            _, report_driven_params = replay_config_from_session(
                session_dir,
                config.strategy_slug,
                base=config if isinstance(config, DynamicStrategyReplayConfig) else None,
            )
        else:
            report_driven_params = _default_strategy_params(
                config
                if isinstance(config, DynamicStrategyReplayConfig)
                else DynamicStrategyReplayConfig()
            )
        scanner_reports = load_scanner_reports_index()

    sorted_ticks = sorted(tick_meta_map)
    session_first_tick = sorted_ticks[0] if sorted_ticks else 0
    total_ticks = len(sorted_ticks)
    progress_step = max(1, total_ticks // 25) if total_ticks >= 100 else 0
    if progress_step:
        logger.info(
            "Sim session %s: starting %d ticks (progress every ~%d ticks)",
            session_num,
            total_ticks,
            progress_step,
        )

    for tick_index, tick in enumerate(sorted_ticks):
        if progress_step and (
            tick_index == 0
            or (tick_index + 1) % progress_step == 0
            or tick_index + 1 == total_ticks
        ):
            logger.info(
                "Sim session %s: tick %d/%d (%.0f%%), open_positions=%d",
                session_num,
                tick_index + 1,
                total_ticks,
                100.0 * (tick_index + 1) / total_ticks,
                len(open_positions),
            )
        meta = tick_meta_map[tick]
        if is_report_driven_data_source(config.data_source) and report_driven_params and scanner_reports:
            journal_meta = meta
            open_pair_list = list(open_positions.keys())
            refreshed = refresh_tick_meta_from_reports(
                journal_meta,
                config,
                report_driven_params,
                reports_by_pair,
                scanner_reports,
                open_pairs=open_pair_list,
            )
            if (
                isinstance(config, DynamicStrategyReplayConfig)
                and config.replay_mode == "session_parity"
            ):
                meta = preserve_journal_queue_fields(journal_meta, refreshed)
            else:
                meta = refreshed
        entry_streak = simulated_streak
        extra_pairs = list(open_positions.keys())
        snapshots = build_tick_snapshots(
            meta,
            reports_by_pair,
            config,
            last_price_by_pair,
            extra_pairs=extra_pairs,
            hl_price_cache=hl_price_cache,
            last_signal_by_pair=last_signal_by_pair,
        )
        for pair, signal in meta.signals_1h.items():
            last_signal_by_pair[pair] = signal
        for pair, snapshot in snapshots.items():
            if snapshot.price_trusted:
                last_seen_by_pair[pair] = (tick, snapshot.price)
        for pair, position in open_positions.items():
            position_pnl = _position_pnl_for_pair(meta, pair)
            if position_pnl is not None and position.notional_quote > 0:
                last_seen_by_pair[pair] = (
                    tick,
                    _exit_price_from_pnl(position, position_pnl),
                )

        tick_actions: list[str] = []
        closes_this_tick: list[str] = []
        opens_this_tick: list[str] = []

        if tick_index > 0:
            prev_tick = sorted_ticks[tick_index - 1]
            prev_meta = tick_meta_map[prev_tick]
            _apply_journal_barrier_closes(
                session_num,
                tick,
                meta,
                open_positions,
                simulated_trades,
                closes_this_tick,
                sl_cooldown_until,
                config,
                replay_policy,
            )
            _apply_intrabar_barriers(
                session_num,
                tick,
                prev_meta.timestamp,
                meta.timestamp,
                open_positions,
                simulated_trades,
                closes_this_tick,
                sl_cooldown_until,
                config,
                barrier_candles,
            )
        elif meta.barrier_closes:
            _apply_journal_barrier_closes(
                session_num,
                tick,
                meta,
                open_positions,
                simulated_trades,
                closes_this_tick,
                sl_cooldown_until,
                config,
                replay_policy,
            )

        # Step 5 + barriers on RUNNING legs
        for pair in list(open_positions.keys()):
            position = open_positions[pair]
            snapshot = snapshots.get(pair)
            mark_price: float | None = None
            metrics: dict[str, float | bool] | None = None
            snapshot_signal = "NEUTRAL"
            filter_trend = None
            filter_pass = None
            has_fresh_snapshot = snapshot is not None

            if snapshot is not None:
                metrics = snapshot.metrics
                snapshot_signal = snapshot.signal
                filter_trend = snapshot.filter_4h_trend
                filter_pass = snapshot.filter_4h_pass
                if snapshot.price_trusted:
                    mark_price = _monitor_mark_price(position, meta, snapshot.price)
                elif (
                    _position_pnl_for_pair(meta, pair) is not None
                    and position.notional_quote > 0
                ):
                    mark_price = _monitor_mark_price(
                        position, meta, position.entry_price
                    )
            elif pair in last_metrics_by_pair:
                metrics = last_metrics_by_pair[pair]
                carried_price = last_price_by_pair.get(pair, 0.0)
                if carried_price <= 0:
                    continue
                mark_price = carried_price
            else:
                continue

            if mark_price is None or metrics is None:
                continue

            last_seen_by_pair[pair] = (tick, mark_price)
            if mark_price > 0:
                last_price_by_pair[pair] = mark_price

            current_return_pct = compute_return_pct(
                position.side, position.entry_price, mark_price
            )
            exit_reason = ""

            sl_pct, tp_pct = _position_barriers(position, config)
            sl_threshold = sl_pct / 100.0
            tp_threshold = tp_pct / 100.0
            if current_return_pct <= -sl_threshold:
                exit_reason = "stop_loss_close_proxy"
            elif current_return_pct >= tp_threshold:
                exit_reason = "take_profit_close_proxy"

            if not exit_reason:
                opposite_formal = (
                    position.side == "long" and bool(metrics["formal_short"])
                ) or (position.side == "short" and bool(metrics["formal_long"]))
                if opposite_formal and tick > flip_cooldown_until.get(pair, -1):
                    if position.flip_streak >= 1:
                        exit_reason = "flip_confirmed"
                    else:
                        position.monitor_state = "flip_pending"
                        position.flip_streak = 1
                elif has_fresh_snapshot and position.flip_streak >= 1:
                    position.flip_streak = 0
                    position.monitor_state = "thesis_intact"

            if not exit_reason and has_fresh_snapshot and snapshot is not None:
                trend = snapshot.parsed.trend if snapshot.parsed else None
                bb_pos_pct = snapshot.parsed.bb_pos_pct if snapshot.parsed else None
                _update_thesis_decay_streak(
                    position,
                    snapshot_signal=snapshot_signal,
                    metrics=metrics,
                    trend=trend,
                    bb_pos_pct=bb_pos_pct,
                    config=config,
                )

                if position.thesis_decay_streak >= config.thesis_decay_exit_ticks:
                    if current_return_pct < 0 and not position.thesis_decay_extra_pending:
                        position.thesis_decay_extra_pending = True
                    else:
                        exit_reason = "thesis_decay_exit"

            if exit_reason:
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
                    )
                )
                closes_this_tick.append(f"{pair}:{exit_reason}")
                del open_positions[pair]

                if exit_reason == "stop_loss_close_proxy":
                    sl_cooldown_until[pair] = tick + config.sl_cooldown_ticks
                elif exit_reason == "flip_confirmed":
                    flip_cooldown_until[pair] = tick + config.flip_cooldown_ticks
                    reverse_side = "short" if position.side == "long" else "long"
                    if (
                        len(open_positions) < config.max_open_executors
                        and filter_4h_allows(
                            reverse_side,
                            filter_trend,
                            filter_pass,
                        )
                        and config.entry_modes in {"all", "formal"}
                    ):
                        reverse_trigger = (
                            f"flip_reverse_{reverse_side}"
                        )
                        open_positions[pair] = _open_position(
                            entry_tick=tick,
                            entry_time=meta.timestamp,
                            pair=pair,
                            side=reverse_side,
                            entry_price=mark_price,
                            entry_class="formal",
                            entry_trigger=reverse_trigger,
                            metrics=metrics,
                            meta=meta,
                            entry_streak=entry_streak,
                            config=config,
                            replay_policy=replay_policy,
                            entry_bb_pos_pct=_entry_bb_pos_pct(snapshot),
                            journal_signal=meta.signals_1h.get(pair),
                            hl_candle_cache=hl_candle_cache,
                            hl_vol_candle_cache=vol_candles,
                        )
                        opens_this_tick.append(reverse_trigger)

        # Step 4 entries
        entries_allowed = (
            _scanner_allows_entries(meta, config)
            and _session_parity_journal_allows_entries(meta, config)
        )
        open_before_entry = list(open_positions.keys())
        if closes_this_tick:
            adaptive_slot_fill_budget = 0
        if entries_allowed:
            formal_candidates: list[tuple[str, str, Any]] = []
            adaptive_candidates: list[tuple[str, str, Any]] = []
            barrier_reentry_this_tick = any(
                token.endswith(
                    (
                        ":stop_loss_close_proxy",
                        ":take_profit_close_proxy",
                    )
                )
                for token in closes_this_tick
            )

            for pair, snapshot in snapshots.items():
                if pair in open_positions:
                    continue
                if not _session_parity_pair_allowed(pair, meta, config):
                    continue
                formal_blockers: list[str] = []
                adaptive_blockers: list[str] = []
                if tick <= flip_cooldown_until.get(pair, -1):
                    formal_blockers.append("flip_cooldown")
                    adaptive_blockers.append("flip_cooldown")
                if tick <= sl_cooldown_until.get(pair, -1):
                    if pair not in meta.create_plans:
                        adaptive_blockers.append("sl_cooldown")

                metrics = snapshot.metrics
                if not snapshot.price_trusted:
                    formal_blockers.append("no_price_data")
                    adaptive_blockers.append("no_price_data")
                create_plan = meta.create_plans.get(pair)
                if config.entry_modes in {"all", "formal"}:
                    if (
                        create_plan
                        and create_plan.entry_class == "formal"
                        and create_plan.side in {"long", "short"}
                        and snapshot.price_trusted
                        and len(open_positions) < config.max_open_executors
                        and not formal_blockers
                    ):
                        plan_side = create_plan.side
                        if filter_4h_allows(
                            plan_side,
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        ):
                            formal_candidates.append((pair, plan_side, snapshot))
                    if bool(metrics["formal_long"]):
                        if not filter_4h_allows(
                            "long",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        ):
                            formal_blockers.append("4h_filter_block_long")
                        elif (
                            snapshot.price_trusted
                            and len(open_positions) < config.max_open_executors
                            and not formal_blockers
                        ):
                            formal_candidates.append((pair, "long", snapshot))
                    if bool(metrics["formal_short"]) and _formal_short_entry_allowed(
                        snapshot.parsed,
                        last_parsed_1h_by_pair.get(pair),
                        metrics,
                        epsilon=config.bb_proximity_epsilon_pct,
                        session_first_tick=session_first_tick,
                        tick=tick,
                    ):
                        if not filter_4h_allows(
                            "short",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                        ):
                            formal_blockers.append("4h_filter_block_short")
                        elif (
                            snapshot.price_trusted
                            and len(open_positions) < config.max_open_executors
                            and not formal_blockers
                        ):
                            formal_candidates.append((pair, "short", snapshot))

                adaptive_flat_ok = (
                    len(open_positions) == 0
                    if config.adaptive_requires_flat
                    else len(open_positions) < config.max_open_executors
                )
                tradeable_ok = (
                    meta.tradeable_count is None
                    or meta.tradeable_count >= config.min_tradeable_count
                )
                if (
                    config.entry_modes in {"all", "adaptive"}
                    and adaptive_flat_ok
                    and tradeable_ok
                ):
                    if bool(metrics["adaptive_long_open"]):
                        if not _adaptive_entry_allowed(
                            pair=pair,
                            meta=meta,
                            simulated_streak=entry_streak,
                            open_position_count=len(open_positions),
                            adaptive_slot_fill_budget=adaptive_slot_fill_budget,
                            activation_ticks=config.activation_ticks,
                        ):
                            adaptive_blockers.append("activation_streak")
                        elif not _adaptive_4h_allows(
                            "long",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                            config,
                        ):
                            adaptive_blockers.append("4h_filter_block_long")
                        elif snapshot.price_trusted and not adaptive_blockers:
                            adaptive_candidates.append((pair, "long", snapshot))
                    if bool(metrics["adaptive_short_open"]):
                        if not _adaptive_entry_allowed(
                            pair=pair,
                            meta=meta,
                            simulated_streak=entry_streak,
                            open_position_count=len(open_positions),
                            adaptive_slot_fill_budget=adaptive_slot_fill_budget,
                            activation_ticks=config.activation_ticks,
                        ):
                            adaptive_blockers.append("activation_streak")
                        elif not _adaptive_4h_allows(
                            "short",
                            snapshot.filter_4h_trend,
                            snapshot.filter_4h_pass,
                            config,
                        ):
                            adaptive_blockers.append("4h_filter_block_short")
                        elif snapshot.price_trusted and not adaptive_blockers:
                            adaptive_candidates.append((pair, "short", snapshot))

                blockers = formal_blockers + [
                    b for b in adaptive_blockers if b not in formal_blockers
                ]
                per_pair_rows.append(
                    _snapshot_row(session_num, tick, meta, pair, snapshot, blockers, config)
                )

            if formal_candidates:
                pair, side, snapshot = sorted(
                    formal_candidates,
                    key=lambda item: (
                        -_candidate_strength(item[1], item[2].metrics),
                        _queue_rank(item[0], meta),
                    ),
                )[0]
                if pair not in open_positions and len(open_positions) < config.max_open_executors:
                    metrics = snapshot.metrics
                    trigger = f"formal_{side}"
                    open_positions[pair] = _open_position(
                        entry_tick=tick,
                        entry_time=meta.timestamp,
                        pair=pair,
                        side=side,
                        entry_price=snapshot.price,
                        entry_class="formal",
                        entry_trigger=trigger,
                        metrics=metrics,
                        meta=meta,
                        entry_streak=entry_streak,
                        config=config,
                        replay_policy=replay_policy,
                        entry_bb_pos_pct=_entry_bb_pos_pct(snapshot),
                        journal_signal=meta.signals_1h.get(pair),
                        hl_candle_cache=hl_candle_cache,
                        hl_vol_candle_cache=vol_candles,
                    )
                    opens_this_tick.append(trigger)

            if not opens_this_tick and adaptive_candidates:
                ranked = sorted(
                    adaptive_candidates,
                    key=lambda item: (
                        -_candidate_strength(item[1], item[2].metrics),
                        _queue_rank(item[0], meta),
                    ),
                )
                pair, side, snapshot = ranked[0]
                metrics = snapshot.metrics
                trigger = f"adaptive_{side}"
                opened_from_flat = len(open_before_entry) == 0
                opened_via_create_plan = pair in meta.create_plans
                open_positions[pair] = _open_position(
                    entry_tick=tick,
                    entry_time=meta.timestamp,
                    pair=pair,
                    side=side,
                    entry_price=snapshot.price,
                    entry_class="regime_adaptive_half_size",
                    entry_trigger=trigger,
                    metrics=metrics,
                    meta=meta,
                    entry_streak=entry_streak,
                    config=config,
                    replay_policy=replay_policy,
                    entry_bb_pos_pct=_entry_bb_pos_pct(snapshot),
                    journal_signal=meta.signals_1h.get(pair),
                    hl_candle_cache=hl_candle_cache,
                    hl_vol_candle_cache=vol_candles,
                )
                opens_this_tick.append(trigger)
                if opened_from_flat and entry_streak >= config.activation_ticks:
                    adaptive_slot_fill_budget = max(
                        0, config.max_open_executors - len(open_positions)
                    )
                elif (
                    not opened_via_create_plan
                    and adaptive_slot_fill_budget > 0
                    and len(open_before_entry) > 0
                ):
                    adaptive_slot_fill_budget = max(
                        0, adaptive_slot_fill_budget - 1
                    )

        else:
            for pair, snapshot in snapshots.items():
                per_pair_rows.append(
                    _snapshot_row(
                        session_num,
                        tick,
                        meta,
                        pair,
                        snapshot,
                        ["scanner_gate"],
                        config,
                    )
                )

        for pair in meta.macd_pairs:
            if pair not in snapshots:
                per_pair_rows.append(
                    {
                        "session": session_num,
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "match_ok": 0,
                        "note": "no signal snapshot",
                    }
                )

        if opens_this_tick:
            tick_actions.extend([f"open:{action}" for action in opens_this_tick])
        if closes_this_tick:
            tick_actions.extend([f"close:{action}" for action in closes_this_tick])
        if not tick_actions:
            tick_actions = ["hold"]

        for pair, snapshot in snapshots.items():
            if snapshot.parsed is not None:
                last_parsed_1h_by_pair[pair] = snapshot.parsed
            last_metrics_by_pair[pair] = snapshot.metrics

        simulated_streak = _advance_simulated_streak(
            snapshots,
            simulated_streak,
            len(open_positions),
            bool(opens_this_tick),
        )

        if (
            adaptive_slot_fill_budget > 0
            and len(open_positions) < config.max_open_executors
            and not any(action.startswith("adaptive_") for action in opens_this_tick)
        ):
            adaptive_slot_fill_budget = 0

        per_tick_rows.append(
            {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "entry_class_journal": meta.entry_class or "",
                "adaptive_activation_streak": meta.adaptive_activation_streak
                if meta.adaptive_activation_streak is not None
                else simulated_streak,
                "sim_streak": simulated_streak,
                "open_positions": len(open_positions),
                "macd_pairs_count": len(meta.macd_pairs),
                "tradeable_count": meta.tradeable_count or "",
                "sim_actions": "|".join(tick_actions),
            }
        )

    for pair, position in list(open_positions.items()):
        if not position.entry_price_trusted:
            continue
        last_seen = last_seen_by_pair.get(pair)
        if last_seen is None:
            continue
        exit_tick, exit_price = last_seen
        simulated_trades.append(
            _close_trade(
                session_num,
                position,
                exit_tick,
                exit_price,
                "session_end_proxy",
            )
        )

    summary = _build_summary(simulated_trades)
    summary["status"] = "ok"

    from routines.macdbb_scanner_aggressive_hl_replay import monitor_macdbb

    flushed = monitor_macdbb.flush_monitor_macdbb_buffer(
        snapshot_dir=getattr(config, "snapshot_dir", None),
    )
    if flushed:
        monitor_macdbb.update_monitor_manifest(
            snapshot_dir=getattr(config, "snapshot_dir", None),
            rows_added=flushed,
        )
        logger.info("Flushed %d monitor MACD rows to supplement parquet", flushed)

    if progress_step:
        logger.info(
            "Sim session %s: done — %d trades, net_pnl=$%.2f",
            session_num,
            len(simulated_trades),
            summary.get("net_pnl_quote", 0.0),
        )

    return per_pair_rows, per_tick_rows, simulated_trades, summary


def _build_summary(trades: list[SimTrade]) -> dict[str, Any]:
    by_trigger: dict[str, dict[str, Any]] = {}
    for trade in trades:
        bucket = by_trigger.setdefault(
            trade.entry_trigger,
            {"count": 0, "wins": 0, "pnl": 0.0},
        )
        bucket["count"] += 1
        if trade.pnl_quote > 0:
            bucket["wins"] += 1
        bucket["pnl"] += trade.pnl_quote

    formal_trades = [trade for trade in trades if trade.entry_class == "formal"]
    adaptive_trades = [
        trade for trade in trades if trade.entry_class == "regime_adaptive_half_size"
    ]
    total_pnl = sum(trade.pnl_quote for trade in trades)
    wins = sum(1 for trade in trades if trade.pnl_quote > 0)
    return {
        "status": "ok",
        "total_trades": len(trades),
        "wins": wins,
        "win_rate_pct": round((wins / len(trades) * 100.0) if trades else 0.0, 1),
        "net_pnl_quote": round(total_pnl, 2),
        "formal_trades": len(formal_trades),
        "adaptive_trades": len(adaptive_trades),
        "formal_pnl": round(sum(trade.pnl_quote for trade in formal_trades), 2),
        "adaptive_pnl": round(sum(trade.pnl_quote for trade in adaptive_trades), 2),
        "by_trigger": {
            trigger: {
                "count": values["count"],
                "wins": values["wins"],
                "pnl": round(values["pnl"], 2),
            }
            for trigger, values in sorted(by_trigger.items())
        },
    }
