"""Backtest simulator for macdbb_pullback_hl (thesis + staged pullback entries)."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

RUN_IN_SUBPROCESS = True

import json
import logging
from pathlib import Path
from typing import Any

from telegram.ext import ContextTypes

from condor.routine_progress import write_progress
from routines.base import RoutineResult
from routines.macdbb_pullback_hl_replay.models import PullbackReplayConfig
from routines.macdbb_pullback_hl_replay.presets import (
    PRESET_LABELS,
    resolve_pullback_config,
)
from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from condor.strategy_runners.macdbb_pullback.dynamic import (
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    capital_normalized_pnl,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
    configure_replay_data_sources,
    refresh_snapshot_caches,
    should_prefetch_replay_candles,
    uses_snapshot_store,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    build_reports_by_pair,
    load_reports_index,
)

logger = logging.getLogger(__name__)

Config = PullbackReplayConfig


def get_preset_overrides() -> dict[str, dict[str, Any]]:
    from condor.strategy_runners.macdbb_pullback.presets import (
        PRESET_OVERRIDES as HARDCODED_PRESET_OVERRIDES,
        _preset_override_dict,
        get_dynamic_preset_overrides,
    )

    overrides = {
        **HARDCODED_PRESET_OVERRIDES,
        **{
            name: _preset_override_dict(name)
            for name in get_dynamic_preset_overrides()
            if name not in HARDCODED_PRESET_OVERRIDES
        },
    }
    # Sweep / live Strategies queue. Named presets must send this or the UI
    # checkbox submits false and hydrate re-queues with a 5-pair NATR floor.
    for name, payload in list(overrides.items()):
        merged = dict(payload)
        merged.setdefault("live_equivalent_queue", True)
        overrides[name] = merged
    # #region agent log
    try:
        import time as _time

        with open(
            "/home/saul/projects/Hummingbot/.cursor/debug-f59e1a.log",
            "a",
            encoding="utf-8",
        ) as _dbg:
            _dbg.write(
                json.dumps(
                    {
                        "sessionId": "f59e1a",
                        "hypothesisId": "H1",
                        "location": "macdbb_pullback_hl_backtest.py:get_preset_overrides",
                        "message": "UI preset_overrides keys",
                        "data": {
                            "key_count": len(overrides),
                            "has_lead_008": "pullback_sweep_lead_008" in overrides,
                            "lead_008_live_eq": (
                                overrides.get("pullback_sweep_lead_008") or {}
                            ).get("live_equivalent_queue"),
                            "keys": sorted(overrides),
                        },
                        "timestamp": int(_time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    return overrides


PRESET_OVERRIDES = get_preset_overrides()


def _loader_config(config: PullbackReplayConfig) -> DynamicStrategyReplayConfig:
    """Thin snapshot/price config for timeline hydration (no refine_lead strategy surface)."""
    loader = DynamicStrategyReplayConfig(
        preset="custom",
        replay_mode="timeline_backtest",
        data_source="snapshots",
        candle_source=config.candle_source,
        price_source=config.price_source,
        snapshot_dir=config.snapshot_dir,
        frequency_sec=config.frequency_sec,
        range_start_utc=config.range_start_utc,
        range_end_utc=config.range_end_utc,
        time_window_min=config.time_window_min,
        require_price_data=config.require_price_data,
        hl_price_interval=config.hl_price_interval,
        hl_barrier_interval=config.hl_barrier_interval,
        hl_cache_dir=config.hl_cache_dir,
        hl_use_cache=config.hl_use_cache,
        auto_update_snapshots=False,
        write_csv=False,
        use_shared_decide=True,
        live_equivalent_queue=bool(getattr(config, "live_equivalent_queue", False)),
        min_tradeable_count=int(config.min_tradeable_count or 1),
        strategy_slug=config.strategy_slug,
        session_nums=config.sessions or "all",
    )
    if loader.candle_source == "binance_perpetual":
        updates: dict[str, Any] = {}
        if not loader.hl_cache_dir or loader.hl_cache_dir == "data/hl_candles":
            updates["hl_cache_dir"] = "data/binance_candles"
        if loader.price_source == "hl_candles":
            updates["price_source"] = "binance_candles"
        if updates:
            loader = loader.model_copy(update=updates)
    return loader


def _snapshot_coverage_hint(snapshot_dir: str) -> str:
    manifest_path = Path(snapshot_dir) / "manifest.json"
    if not manifest_path.is_file():
        return f"No manifest.json under {snapshot_dir}."
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"Unreadable manifest.json under {snapshot_dir}."
    return (
        f"Snapshot {snapshot_dir} covers "
        f"{payload.get('range_start_utc') or '?'} → {payload.get('range_end_utc') or '?'} "
        f"({payload.get('tick_count') or 0} ticks, "
        f"source={payload.get('candle_source') or '?'})."
    )


def _tick_universe_stats(
    parsed_sessions: dict[int, dict[int, Any]],
) -> tuple[int, list[str]]:
    from routines.macdbb_pullback_hl_replay.signal_tape import universe_pairs_from_ticks

    ticks_with_pairs = 0
    pairs: dict[str, None] = {}
    for tick_meta_map in parsed_sessions.values():
        for meta in tick_meta_map.values():
            if meta.macd_pairs or meta.queue_total or meta.signals_1h:
                ticks_with_pairs += 1
        for pair in universe_pairs_from_ticks(tick_meta_map):
            pairs.setdefault(pair, None)
    return ticks_with_pairs, list(pairs)


def journal_tick_maps(config: PullbackReplayConfig) -> dict[int, dict[int, Any]]:
    """Journal tick clocks for ``sessions``, so pause gaps match live."""
    sessions_raw = (config.sessions or "").strip()
    if not sessions_raw:
        return {}
    from routines.macdbb_scanner_aggressive_hl_replay.models import parse_session_selector
    from routines.macdbb_scanner_aggressive_hl_replay.paths import strategy_sessions_dir
    from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import (
        parse_iso_utc,
        parse_tick_schedule_file,
    )

    range_start = (
        parse_iso_utc(config.range_start_utc) if config.range_start_utc else None
    )
    range_end = parse_iso_utc(config.range_end_utc) if config.range_end_utc else None
    sessions_dir = strategy_sessions_dir(config.strategy_slug)
    tick_maps: dict[int, dict[int, Any]] = {}
    for session_num in parse_session_selector(sessions_raw, sessions_dir):
        journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
        if not journal_path.is_file():
            continue
        schedule = parse_tick_schedule_file(journal_path)
        if range_start is not None:
            schedule = {
                tick: meta
                for tick, meta in schedule.items()
                if meta.timestamp >= range_start
            }
        if range_end is not None:
            schedule = {
                tick: meta
                for tick, meta in schedule.items()
                if meta.timestamp <= range_end
            }
        if schedule:
            tick_maps[session_num] = schedule
    return tick_maps


def _trade_exit_reason(trade: Any) -> str:
    return str(getattr(trade, "exit_reason", "") or "")


def summarize_pullback_trades(all_trades: list[Any]) -> dict[str, Any]:
    total_trades = len(all_trades)
    total_pnl = sum(float(t.pnl_quote) for t in all_trades)
    sl_n = sum(1 for t in all_trades if "stop_loss" in _trade_exit_reason(t))
    tp_n = sum(1 for t in all_trades if "take_profit" in _trade_exit_reason(t))
    decay_n = sum(1 for t in all_trades if _trade_exit_reason(t) == "thesis_decay")
    session_end_n = sum(
        1 for t in all_trades if _trade_exit_reason(t) == "session_end"
    )
    flip_n = sum(1 for t in all_trades if _trade_exit_reason(t) == "flip_confirm")
    avg_notional = (
        sum(float(t.notional_quote) for t in all_trades) / total_trades
        if total_trades
        else 0.0
    )
    wins = sum(1 for t in all_trades if float(t.pnl_quote) > 0)
    immediate_n = sum(1 for t in all_trades if t.entry_class == "immediate")
    pullback_n = sum(1 for t in all_trades if t.entry_class == "pullback")
    cap_norm = capital_normalized_pnl(
        total_pnl, avg_notional, FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL
    )
    return {
        "total_trades": total_trades,
        "immediate_n": immediate_n,
        "pullback_n": pullback_n,
        "wins": wins,
        "sl_n": sl_n,
        "tp_n": tp_n,
        "decay_n": decay_n,
        "session_end_n": session_end_n,
        "flip_n": flip_n,
        "total_pnl": total_pnl,
        "avg_notional": avg_notional,
        "cap_norm": cap_norm,
        "win_rate_pct": (wins / total_trades * 100.0) if total_trades else 0.0,
        "sl_rate": (sl_n / total_trades) if total_trades else 0.0,
        "avg_sl_pct": (
            sum(float(t.sl_pct_used) for t in all_trades) / total_trades
            if total_trades
            else 0.0
        ),
        "avg_tp_pct": (
            sum(float(t.tp_pct_used) for t in all_trades) / total_trades
            if total_trades
            else 0.0
        ),
    }


def pullback_session_table_row(
    session_num: int,
    *,
    tick_count: int,
    trades: list[Any],
) -> dict[str, Any]:
    """One Sessions-table row from the trades closed in that session."""
    stats = summarize_pullback_trades(trades)
    return {
        "Session": session_num,
        "Status": "ok",
        "Ticks": tick_count,
        "Trades": stats["total_trades"],
        "Immediate": stats["immediate_n"],
        "Pullback": stats["pullback_n"],
        "Win Rate %": stats["win_rate_pct"],
        "SL": stats["sl_n"],
        "TP": stats["tp_n"],
        "Decay": stats["decay_n"],
        "Session end": stats["session_end_n"],
        "Flip": stats["flip_n"],
        "SL rate": stats["sl_rate"],
        "Avg notional": round(stats["avg_notional"], 2),
        "Avg SL/TP": (
            f"{stats['avg_sl_pct']:.2f}% / {stats['avg_tp_pct']:.2f}%"
        ),
        "Sim PnL $": stats["total_pnl"],
    }


async def save_pullback_backtest_report(
    config: PullbackReplayConfig,
    *,
    all_trades: list[Any],
    session_rows: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """Write the Condor UI report for a completed pullback backtest.

    Returns ``(summary_text, report_id)``.
    """
    stats = summarize_pullback_trades(all_trades)
    summary_lines = [
        f"macdbb_pullback_hl backtest — {config.strategy_slug}",
        f"Preset: {config.preset} ({PRESET_LABELS.get(config.preset, config.preset)})",
        f"Range: {config.range_start_utc} → {config.range_end_utc}",
        (
            f"Trades: {stats['total_trades']} "
            f"(immediate={stats['immediate_n']}, pullback={stats['pullback_n']})"
        ),
        f"Win rate: {stats['win_rate_pct']:.1f}%",
        (
            f"SL hits: {stats['sl_n']} | TP hits: {stats['tp_n']} | "
            f"Decay: {stats['decay_n']} | Session end: {stats['session_end_n']}"
            + (
                f" | Flip: {stats['flip_n']}"
                if stats["flip_n"]
                else ""
            )
            + f" | SL rate: {stats['sl_rate']:.3f}"
        ),
        f"Sim PnL: ${stats['total_pnl']:.2f} | Capital-norm PnL: ${stats['cap_norm']:.2f}",
        (
            f"Avg notional: ${stats['avg_notional']:.2f} | Avg SL/TP: "
            f"{stats['avg_sl_pct']:.2f}% / {stats['avg_tp_pct']:.2f}%"
        ),
    ]
    text = "\n".join(summary_lines)
    logger.info(text)

    report_id: str | None = None
    try:
        from condor.reports import ReportBuilder, get_last_report_id, reset_last_report_id

        reset_last_report_id()
        builder = ReportBuilder(
            f"MACDBB Pullback Backtest — {PRESET_LABELS.get(config.preset, config.preset)}"
        )
        builder.source("routine", "macdbb_pullback_hl_backtest")
        builder.tags(["backtest", "macdbb_pullback", config.preset])
        builder.manual_order()
        builder.kpi("Sim Trades", str(stats["total_trades"]))
        builder.kpi("Immediate", str(stats["immediate_n"]))
        builder.kpi("Pullback", str(stats["pullback_n"]))
        builder.kpi("Win rate", f"{stats['win_rate_pct']:.1f}%")
        builder.kpi("SL hits", str(stats["sl_n"]))
        builder.kpi("TP hits", str(stats["tp_n"]))
        builder.kpi("Decay", str(stats["decay_n"]))
        builder.kpi("Session end", str(stats["session_end_n"]))
        if stats["flip_n"]:
            builder.kpi("Flip", str(stats["flip_n"]))
        builder.kpi("Sim PnL", f"${stats['total_pnl']:+.2f}")
        builder.kpi("Capital-norm PnL", f"${stats['cap_norm']:+.2f}")
        builder.kpi("SL rate", f"{stats['sl_rate']:.3f}")
        builder.kpi("Avg notional", f"${stats['avg_notional']:.2f}")
        builder.kpi(
            "Avg SL/TP",
            f"{stats['avg_sl_pct']:.2f}% / {stats['avg_tp_pct']:.2f}%",
        )
        builder.markdown(
            "## Config\n"
            f"- **Preset:** {config.preset}\n"
            f"- **Frequency:** {config.frequency_sec}s\n"
            f"- **Range:** {config.range_start_utc} → {config.range_end_utc}\n"
            f"- **Snapshot dir:** `{config.snapshot_dir}`\n"
            f"- **Budget:** ${float(config.total_amount_quote):.0f}\n"
            f"- **SL/TP:** {config.sl_pct}% / {config.tp_pct}%\n"
            f"- **Impulse ATR mult:** {config.impulse_atr_mult}\n"
            f"- **Pullback epsilon:** {config.pullback_epsilon_pct}%\n"
            f"- **Decay:** {config.enable_thesis_decay_exit} "
            f"({config.thesis_decay_exit_hours}h)\n"
            f"- **Flip exit:** {config.enable_flip_exit}\n"
            f"- **Dynamic barriers:** {config.enable_dynamic_barriers}\n"
            f"- **Dynamic sizing:** {config.enable_dynamic_sizing}\n"
            f"- **Live-equivalent queue:** {config.live_equivalent_queue}\n"
            f"- **Candle source:** {config.candle_source}\n"
        )
        if session_rows:
            builder.markdown("## Sessions")
            builder.table(session_rows)
        if all_trades:
            trade_rows = [
                {
                    "Pair": t.pair,
                    "Side": t.side,
                    "Class": t.entry_class,
                    "Notional": round(float(t.notional_quote), 2),
                    "SL%": round(float(t.sl_pct_used), 3),
                    "TP%": round(float(t.tp_pct_used), 3),
                    "Entry": t.entry_time_utc.isoformat() if t.entry_time_utc else "",
                    "Exit": t.exit_time_utc.isoformat() if t.exit_time_utc else "",
                    "Reason": _trade_exit_reason(t),
                    "Return%": round(float(t.return_pct), 3),
                    "PnL": round(float(t.pnl_quote), 2),
                }
                for t in all_trades
            ]
            builder.markdown("## Trades")
            builder.table(trade_rows)
        report_id = await builder.save()
        if report_id is None:
            report_id = get_last_report_id()
    except Exception:
        logger.warning("Failed to save pullback backtest report", exc_info=True)
    return text, report_id


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    write_progress(phase="resolve", message="Resolving pullback preset")
    config = resolve_pullback_config(config)
    loader = _loader_config(config)
    configure_replay_data_sources(loader)

    write_progress(phase="hydrate", message="Loading timeline ticks")
    journal_maps = journal_tick_maps(config)
    if journal_maps:
        from routines.macdbb_scanner_aggressive_hl_replay.replay_range import iso_utc
        from routines.macdbb_scanner_aggressive_hl_replay.session_builder import (
            hydrate_timeline_ticks,
        )

        from routines.macdbb_scanner_aggressive_hl_replay.live_tick_jsonl import (
            enrich_ticks_from_live_jsonl,
        )

        parsed_sessions = {}
        for session_num, schedule in journal_maps.items():
            hydrated = hydrate_timeline_ticks(loader, schedule=schedule)
            enriched = enrich_ticks_from_live_jsonl(
                hydrated,
                session_num,
                strategy_slug=config.strategy_slug,
            )
            parsed_sessions[session_num] = {
                tick: meta
                for tick, meta in enriched.items()
                if tick in schedule
            }
        selected = sorted(parsed_sessions.keys())
        all_timestamps = [
            meta.timestamp
            for ticks in parsed_sessions.values()
            for meta in ticks.values()
        ]
        if all_timestamps:
            config = config.model_copy(
                update={
                    "range_start_utc": iso_utc(min(all_timestamps)),
                    "range_end_utc": iso_utc(max(all_timestamps)),
                }
            )
            loader = loader.model_copy(
                update={
                    "range_start_utc": config.range_start_utc,
                    "range_end_utc": config.range_end_utc,
                }
            )
    else:
        parsed_sessions, _session_configs, selected = load_replay_sessions(loader)
    if not selected or not parsed_sessions:
        msg = (
            "Timeline backtest produced no ticks. "
            "Check range_start_utc / range_end_utc, frequency_sec, and snapshot_dir."
        )
        write_progress(phase="error", message=msg)
        return RoutineResult(text=msg)

    if uses_snapshot_store(loader):
        refresh_snapshot_caches(loader)

    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    tick_count = sum(len(ticks) for ticks in parsed_sessions.values())
    ticks_with_pairs, universe_pairs = _tick_universe_stats(parsed_sessions)
    # #region agent log
    try:
        import time as _time

        with open(
            "/home/saul/projects/Hummingbot/.cursor/debug-f59e1a.log",
            "a",
            encoding="utf-8",
        ) as _dbg:
            _dbg.write(
                json.dumps(
                    {
                        "sessionId": "f59e1a",
                        "hypothesisId": "H5",
                        "location": "macdbb_pullback_hl_backtest.py:run:hydrate",
                        "message": "resolved config and tick hydrate",
                        "data": {
                            "preset": config.preset,
                            "range_start_utc": config.range_start_utc,
                            "range_end_utc": config.range_end_utc,
                            "impulse_atr_mult": config.impulse_atr_mult,
                            "pullback_epsilon_pct": config.pullback_epsilon_pct,
                            "sl_pct": config.sl_pct,
                            "tp_pct": config.tp_pct,
                            "enable_dynamic_barriers": config.enable_dynamic_barriers,
                            "live_equivalent_queue": config.live_equivalent_queue,
                            "price_source": config.price_source,
                            "candle_source": config.candle_source,
                            "tick_count": tick_count,
                            "ticks_with_pairs": ticks_with_pairs,
                            "universe_pairs": len(universe_pairs),
                        },
                        "timestamp": int(_time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    logger.info(
        "Pullback backtest: preset=%s ticks=%d ticks_with_pairs=%d pairs=%d "
        "snapshot=%s candle_source=%s range %s → %s",
        config.preset,
        tick_count,
        ticks_with_pairs,
        len(universe_pairs),
        config.snapshot_dir,
        config.candle_source,
        config.range_start_utc,
        config.range_end_utc,
    )
    if not universe_pairs:
        coverage = _snapshot_coverage_hint(str(config.snapshot_dir or ""))
        msg = (
            "Timeline ticks have no scanner pairs, so the backtest cannot arm or trade. "
            f"Requested {config.range_start_utc} → {config.range_end_utc} "
            f"({tick_count} clock ticks). {coverage} "
            "Use a snapshot that covers this range (for lead_008: "
            "data/replay_snapshots_binance_60s + binance_perpetual) and keep the "
            "end date inside that snapshot."
        )
        logger.error(msg)
        write_progress(phase="error", message=msg)
        return RoutineResult(text=msg)

    hl_caches_by_session: dict = {}
    hl_candle_cache: dict = {}
    hl_barrier_candle_cache: dict = {}
    hl_vol_candle_cache: dict = {}
    packed_stores: list[Any] = []
    if should_prefetch_replay_candles(loader) and parsed_sessions:
        write_progress(phase="prefetch_candles", message="Prefetching candles")
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(loader),
        )
        if hl_candle_cache or hl_barrier_candle_cache or hl_vol_candle_cache:
            from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
                prepare_shared_candle_stores,
            )

            logger.info(
                "Packing %d price / %d barrier / %d vol candle series into shared memory",
                len(hl_candle_cache),
                len(hl_barrier_candle_cache),
                len(hl_vol_candle_cache),
            )
            (
                _price_dicts,
                _barrier_dicts,
                _vol_dicts,
                price_store,
                barrier_store,
                vol_store,
            ) = prepare_shared_candle_stores(
                hl_candle_cache,
                hl_barrier_candle_cache,
                hl_vol_candle_cache,
            )
            hl_candle_cache = price_store or {}
            hl_barrier_candle_cache = barrier_store or {}
            hl_vol_candle_cache = vol_store or {}
            packed_stores = [
                store
                for store in (price_store, barrier_store, vol_store)
                if store is not None
            ]

    try:
        all_trades: list[Any] = []
        session_rows: list[dict[str, Any]] = []
        for session_num, tick_meta_map in parsed_sessions.items():
            write_progress(
                phase="simulate",
                message=f"Simulating session {session_num} ({len(tick_meta_map)} ticks)",
                current=0,
                total=len(tick_meta_map),
            )

            def _progress(cur: int, total: int) -> None:
                write_progress(
                    phase="simulate",
                    message=f"Simulating session {session_num}: tick {cur}/{total}",
                    current=cur,
                    total=total,
                )

            _pairs, _ticks, trades, summary = simulate_pullback_session(
                session_num=session_num,
                tick_meta_map=tick_meta_map,
                reports_by_pair=reports_by_pair,
                config=config,
                signal_config=loader,
                hl_price_cache=hl_caches_by_session.get(session_num),
                hl_candle_cache=hl_candle_cache,
                hl_barrier_candle_cache=hl_barrier_candle_cache,
                hl_vol_candle_cache=hl_vol_candle_cache,
                on_progress=_progress,
                collect_debug_rows=False,
            )
            if summary.get("status") == "skipped_no_price_data":
                continue
            all_trades.extend(trades)
            session_rows.append(
                pullback_session_table_row(
                    session_num,
                    tick_count=len(tick_meta_map),
                    trades=trades,
                )
            )

        text, _report_id = await save_pullback_backtest_report(
            config,
            all_trades=all_trades,
            session_rows=session_rows,
        )
        stats = summarize_pullback_trades(all_trades)
        # #region agent log
        try:
            import time as _time

            with open(
                "/home/saul/projects/Hummingbot/.cursor/debug-f59e1a.log",
                "a",
                encoding="utf-8",
            ) as _dbg:
                _dbg.write(
                    json.dumps(
                        {
                            "sessionId": "f59e1a",
                            "hypothesisId": "H3",
                            "location": "macdbb_pullback_hl_backtest.py:run:summary",
                            "message": "simulated trade summary",
                            "data": {
                                "preset": config.preset,
                                "total_trades": stats["total_trades"],
                                "immediate_trades": stats["immediate_n"],
                                "pullback_trades": stats["pullback_n"],
                                "sl_hits": stats["sl_n"],
                                "tp_hits": stats["tp_n"],
                                "net_pnl_quote": stats["total_pnl"],
                                "impulse_atr_mult": config.impulse_atr_mult,
                                "enable_dynamic_barriers": (
                                    config.enable_dynamic_barriers
                                ),
                                "live_equivalent_queue": (
                                    config.live_equivalent_queue
                                ),
                                "decay_hits": stats.get("decay_n"),
                            },
                            "timestamp": int(_time.time() * 1000),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion

        out_dir = __import__("pathlib").Path("data/backtests/macdbb_pullback_hl")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "last_summary.json").write_text(
            json.dumps(
                {
                    "preset": config.preset,
                    "total_trades": stats["total_trades"],
                    "immediate_trades": stats["immediate_n"],
                    "pullback_trades": stats["pullback_n"],
                    "sl_hits": stats["sl_n"],
                    "tp_hits": stats["tp_n"],
                    "sl_rate": stats["sl_rate"],
                    "net_pnl_quote": stats["total_pnl"],
                    "capital_norm_pnl": stats["cap_norm"],
                    "avg_notional": stats["avg_notional"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        write_progress(phase="done", message="Pullback backtest complete", percent=100.0)
        return RoutineResult(text=text)
    finally:
        for store in packed_stores:
            store.close_unlink()
