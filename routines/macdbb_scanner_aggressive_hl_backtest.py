"""Backtest simulator for trading_agents/macdbb_scanner_aggressive_hl (dynamic sizing + barriers)."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

RUN_IN_SUBPROCESS = True

import json
import logging
from typing import Any

from telegram.ext import ContextTypes

from routines.base import RoutineResult
from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    write_csv,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay import presets
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    PRESET_LABELS,
    capital_normalized_pnl,
    resolve_config_with_preset,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
    configure_replay_data_sources,
    is_report_driven_data_source,
    should_prefetch_replay_candles,
)
from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

logger = logging.getLogger(__name__)

Config = DynamicStrategyReplayConfig

# Re-exported for routine discovery — UI reads this to sync form fields on preset change.
PRESET_OVERRIDES = {
    **presets.PRESET_OVERRIDES,
    **presets.DYNAMIC_PRESET_OVERRIDES,
}


PER_PAIR_COLUMNS = [
    "session",
    "tick",
    "tick_time_utc",
    "pair",
    "report_id",
    "signal_source",
    "price_trusted",
    "entry_class_journal",
    "adaptive_activation_streak",
    "signal",
    "bb_pos_pct",
    "price",
    "trend",
    "momentum",
    "macd_gap_ratio",
    "hist_ratio",
    "formal_long",
    "formal_short",
    "adaptive_long_eligible",
    "adaptive_short_eligible",
    "adaptive_strength_long",
    "adaptive_strength_short",
    "adaptive_long_open",
    "adaptive_short_open",
    "filter_4h_pass",
    "filter_4h_trend",
    "blockers",
    "match_ok",
    "note",
]

PER_TICK_COLUMNS = [
    "session",
    "tick",
    "tick_time_utc",
    "entry_class_journal",
    "adaptive_activation_streak",
    "sim_streak",
    "open_positions",
    "macd_pairs_count",
    "tradeable_count",
    "sim_actions",
]

TRADE_COLUMNS = [
    "session",
    "pair",
    "side",
    "entry_class",
    "entry_trigger",
    "notional_quote",
    "sizing_multiplier",
    "sl_pct_used",
    "tp_pct_used",
    "volatility_proxy_pct",
    "entry_tick",
    "exit_tick",
    "hold_ticks",
    "exit_reason",
    "entry_price",
    "exit_price",
    "return_pct",
    "pnl_quote",
    "entry_score_long",
    "entry_score_short",
    "entry_adaptive_activation_streak",
]


def _trade_rows(trades: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "session": trade.session_num,
            "pair": trade.pair,
            "side": trade.side.upper(),
            "entry_class": trade.entry_class,
            "entry_trigger": trade.entry_trigger,
            "notional_quote": round(trade.notional_quote, 2),
            "sizing_multiplier": round(trade.sizing_multiplier, 4),
            "sl_pct_used": round(trade.sl_pct_used, 3),
            "tp_pct_used": round(trade.tp_pct_used, 3),
            "volatility_proxy_pct": round(trade.volatility_proxy_pct, 4),
            "entry_tick": trade.entry_tick,
            "exit_tick": trade.exit_tick,
            "hold_ticks": trade.hold_ticks,
            "exit_reason": trade.exit_reason,
            "entry_price": round(trade.entry_price, 8),
            "exit_price": round(trade.exit_price, 8),
            "return_pct": round(trade.return_pct, 3),
            "pnl_quote": round(trade.pnl_quote, 2),
            "entry_score_long": round(trade.entry_score_long, 4),
            "entry_score_short": round(trade.entry_score_short, 4),
            "entry_adaptive_activation_streak": trade.entry_adaptive_activation_streak,
        }
        for trade in trades
    ]


def _dynamic_summary_stats(trades: list[Any]) -> dict[str, float]:
    if not trades:
        return {
            "avg_notional_quote": 0.0,
            "total_entry_exposure": 0.0,
            "avg_sl_pct": 0.0,
            "avg_tp_pct": 0.0,
            "avg_sizing_multiplier": 0.0,
            "pnl_per_exposure": 0.0,
            "capital_normalized_pnl": 0.0,
        }
    total_exposure = sum(trade.notional_quote for trade in trades)
    total_pnl = sum(trade.pnl_quote for trade in trades)
    avg_notional = total_exposure / len(trades)
    return {
        "avg_notional_quote": round(avg_notional, 2),
        "total_entry_exposure": round(total_exposure, 2),
        "avg_sl_pct": round(
            sum(trade.sl_pct_used for trade in trades) / len(trades),
            3,
        ),
        "avg_tp_pct": round(
            sum(trade.tp_pct_used for trade in trades) / len(trades),
            3,
        ),
        "avg_sizing_multiplier": round(
            sum(trade.sizing_multiplier for trade in trades) / len(trades),
            4,
        ),
        "pnl_per_exposure": round(
            total_pnl / total_exposure if total_exposure > 0 else 0.0,
            6,
        ),
        "capital_normalized_pnl": round(
            capital_normalized_pnl(
                total_pnl,
                avg_notional,
                FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
            ),
            2,
        ),
    }


_REPLAY_MODE_LABELS = {
    "session_parity": "Session parity",
    "timeline_backtest": "Timeline backtest",
}


def _backtest_report_title(config: Config) -> str:
    if config.report_label:
        return config.report_label
    preset_label = PRESET_LABELS.get(config.preset, config.preset)
    if config.preset != "custom":
        return f"MACDBB Backtest — {preset_label}"
    return "MACDBB Backtest"


async def run(
    config: Config, context: ContextTypes.DEFAULT_TYPE
) -> str | RoutineResult:
    config = resolve_config_with_preset(config)
    configure_replay_data_sources(config)
    strategy_dir = TRADING_AGENTS_DIR / config.strategy_slug
    sessions_dir = strategy_dir / "sessions"

    logger.info(
        "Dynamic replay: mode=%s data_source=%s preset=%s",
        config.replay_mode,
        config.data_source,
        config.preset,
    )

    if config.replay_mode == "timeline_backtest":
        if not config.range_start_utc or not config.range_end_utc:
            return (
                "timeline_backtest requires range_start_utc and range_end_utc "
                "(ISO UTC datetimes). Leave empty to use full scanner report span."
            )
    elif not sessions_dir.is_dir():
        return f"Sessions directory not found: {sessions_dir}"

    parsed_sessions, session_configs, selected_sessions = load_replay_sessions(config)
    if not selected_sessions:
        if config.replay_mode == "timeline_backtest":
            return (
                "Timeline backtest produced no ticks for the selected range. "
                "Check range_start_utc / range_end_utc and frequency_sec."
            )
        return "No sessions matched the requested selector."

    tick_count = sum(len(ticks) for ticks in parsed_sessions.values())
    logger.info(
        "Loaded %d session(s), %d total ticks (range %s → %s)",
        len(selected_sessions),
        tick_count,
        config.range_start_utc or "?",
        config.range_end_utc or "?",
    )

    reports = load_reports_index()
    if not reports and is_report_driven_data_source(config.data_source):
        return "No macd_bb_analysis reports or snapshots found for replay."
    reports_by_pair = build_reports_by_pair(reports)

    all_pair_rows: list[dict[str, Any]] = []
    all_tick_rows: list[dict[str, Any]] = []
    all_trades: list[Any] = []
    session_rollup_rows: list[dict[str, Any]] = []
    skipped_sessions: list[int] = []
    compare_columns = [
        "journal_fL",
        "journal_fS",
        "journal_aL",
        "journal_aS",
        "mismatch_fL",
        "mismatch_fS",
        "mismatch_aL",
        "mismatch_aS",
    ]
    per_pair_columns = list(PER_PAIR_COLUMNS)
    if config.compare_journal_flags:
        per_pair_columns.extend(compare_columns)

    hl_caches_by_session: dict[int, dict[tuple[str, int], float]] = {}
    hl_candle_cache: dict[str, list[dict[str, float]]] = {}
    hl_barrier_candle_cache: dict[str, list[dict[str, float]]] = {}
    hl_vol_candle_cache: dict[str, list[dict[str, float]]] = {}
    if should_prefetch_replay_candles(config) and parsed_sessions:
        logger.info("Prefetching candle prices (this can take several minutes)...")
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )
        logger.info(
            "Prefetch complete: %d price series, %d barrier series",
            len(hl_candle_cache),
            len(hl_barrier_candle_cache),
        )

    for session_num, tick_meta_map in parsed_sessions.items():
        logger.info(
            "Simulating session %s (%d ticks)...",
            session_num,
            len(tick_meta_map),
        )
        hl_price_cache = hl_caches_by_session.get(session_num)
        session_config = session_configs.get(session_num, config)
        replay_policy = DynamicReplayPolicy(session_config)

        per_pair_rows, per_tick_rows, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=session_config,
            hl_price_cache=hl_price_cache,
            hl_candle_cache=hl_candle_cache,
            hl_barrier_candle_cache=hl_barrier_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            replay_policy=replay_policy,
        )
        status = summary.get("status", "ok")
        if status == "skipped_no_price_data":
            skipped_sessions.append(session_num)
            session_rollup_rows.append(
                {
                    "Session": session_num,
                    "Status": "skipped (no price data)",
                    "Ticks Parsed": len(tick_meta_map),
                    "Pair Rows": 0,
                    "Sim Trades": 0,
                    "Formal Trades": 0,
                    "Adaptive Trades": 0,
                    "Win Rate %": "",
                    "Sim PnL $": "",
                }
            )
            continue

        dynamic_stats = _dynamic_summary_stats(trades)
        summary.update(dynamic_stats)

        all_pair_rows.extend(per_pair_rows)
        all_tick_rows.extend(per_tick_rows)
        all_trades.extend(trades)

        session_rollup_rows.append(
            {
                "Session": session_num,
                "Status": "ok",
                "Ticks Parsed": len(per_tick_rows),
                "Pair Rows": sum(
                    1 for row in per_pair_rows if row.get("match_ok") == 1
                ),
                "Sim Trades": summary["total_trades"],
                "Formal Trades": summary["formal_trades"],
                "Adaptive Trades": summary["adaptive_trades"],
                "Win Rate %": summary["win_rate_pct"],
                "Sim PnL $": summary["net_pnl_quote"],
            }
        )

        if config.write_csv and config.replay_mode != "timeline_backtest":
            output_dir = sessions_dir / f"session_{session_num}"
            write_csv(
                output_dir / "macdbb_scanner_aggressive_hl_backtest_per_pair.csv",
                per_pair_rows,
                per_pair_columns,
            )
            write_csv(
                output_dir / "macdbb_scanner_aggressive_hl_backtest_per_tick.csv",
                per_tick_rows,
                PER_TICK_COLUMNS,
            )
            write_csv(
                output_dir / "macdbb_scanner_aggressive_hl_backtest_trades.csv",
                _trade_rows(trades),
                TRADE_COLUMNS,
            )
            (output_dir / "macdbb_scanner_aggressive_hl_backtest_summary.json").write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )

    if not session_rollup_rows:
        return "No session data could be replayed."

    simulated_sessions = [
        row["Session"] for row in session_rollup_rows if row.get("Status") == "ok"
    ]
    if not simulated_sessions and skipped_sessions:
        return (
            f"No sessions had trusted prices (price_source={config.price_source}). "
            f"Skipped: {', '.join(str(value) for value in skipped_sessions)}. "
            "Set require_price_data=false to replay signals without PnL."
        )

    total_trades = len(all_trades)
    total_wins = sum(1 for trade in all_trades if trade.pnl_quote > 0)
    total_pnl = sum(trade.pnl_quote for trade in all_trades)
    total_win_rate = (total_wins / total_trades) if total_trades else 0.0
    formal_trades = sum(1 for trade in all_trades if trade.entry_class == "formal")
    adaptive_trades = sum(
        1 for trade in all_trades if trade.entry_class == "regime_adaptive_half_size"
    )
    dynamic_stats = _dynamic_summary_stats(all_trades)

    sizing_mode = []
    if config.enable_dynamic_sizing:
        sizing_mode.append("dynamic sizing")
    if config.enable_dynamic_barriers:
        sizing_mode.append("dynamic barriers")
    mode_label = ", ".join(sizing_mode) if sizing_mode else "fixed (both disabled)"

    summary_lines = [
        f"macdbb_scanner_aggressive_hl backtest — {config.strategy_slug}",
        f"Mode: {mode_label}",
        f"Preset: {config.preset} | Entry modes: {config.entry_modes}",
        f"Sessions requested: {', '.join(str(value) for value in selected_sessions)}",
        f"Sessions simulated: {', '.join(str(value) for value in simulated_sessions) or 'none'}",
        (
            f"Ticks replayed: {len(all_tick_rows)} | "
            f"Pair snapshots: {sum(1 for row in all_pair_rows if row.get('match_ok') == 1)}"
        ),
        (
            f"Sim trades: {total_trades} (formal={formal_trades}, adaptive={adaptive_trades}) | "
            f"Win rate: {total_win_rate:.1%} | Sim PnL: ${total_pnl:+.2f}"
        ),
        (
            f"Capital-norm PnL: ${dynamic_stats['capital_normalized_pnl']:+.2f} "
            f"(benchmark avg ${FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL:.0f}) | "
            f"PnL/exposure: {dynamic_stats['pnl_per_exposure']:.4f}"
        ),
        (
            f"Avg notional: ${dynamic_stats['avg_notional_quote']:.2f} | "
            f"Avg SL/TP: {dynamic_stats['avg_sl_pct']:.2f}% / {dynamic_stats['avg_tp_pct']:.2f}% | "
            f"Avg size mult: {dynamic_stats['avg_sizing_multiplier']:.3f}"
        ),
    ]
    if skipped_sessions:
        summary_lines.append(
            "Skipped (no price data): "
            + ", ".join(str(value) for value in skipped_sessions)
        )

    table_columns = [
        "Session",
        "Status",
        "Ticks Parsed",
        "Pair Rows",
        "Sim Trades",
        "Formal Trades",
        "Adaptive Trades",
        "Win Rate %",
        "Sim PnL $",
    ]
    trade_table_rows = [
        {
            "Session": row["session"],
            "Pair": row["pair"],
            "Side": row["side"],
            "Entry Class": row["entry_class"],
            "Trigger": row["entry_trigger"],
            "Notional $": row["notional_quote"],
            "Size Mult": row["sizing_multiplier"],
            "SL %": row["sl_pct_used"],
            "TP %": row["tp_pct_used"],
            "Entry Tick": row["entry_tick"],
            "Exit Tick": row["exit_tick"],
            "Exit Reason": row["exit_reason"],
            "Return %": row["return_pct"],
            "PnL $": row["pnl_quote"],
        }
        for row in _trade_rows(all_trades)
    ]

    pnl_trend = (
        "positive" if total_pnl > 0 else "negative" if total_pnl < 0 else "neutral"
    )

    sections = [
        {"type": "kpi", "label": "Sessions", "value": str(len(session_rollup_rows))},
        {"type": "kpi", "label": "Sim Trades", "value": str(total_trades)},
        {"type": "kpi", "label": "Win Rate", "value": f"{total_win_rate:.1%}"},
        {
            "type": "kpi",
            "label": "Sim PnL",
            "value": f"${total_pnl:+.2f}",
            "trend": pnl_trend,
        },
        {
            "type": "kpi",
            "label": "Capital-norm PnL",
            "value": f"${dynamic_stats['capital_normalized_pnl']:+.2f}",
            "trend": (
                "positive"
                if dynamic_stats["capital_normalized_pnl"] > 0
                else "negative"
                if dynamic_stats["capital_normalized_pnl"] < 0
                else "neutral"
            ),
        },
        {
            "type": "kpi",
            "label": "Avg Notional",
            "value": f"${dynamic_stats['avg_notional_quote']:.2f}",
        },
        {
            "type": "kpi",
            "label": "Avg SL / TP",
            "value": (
                f"{dynamic_stats['avg_sl_pct']:.2f}% / "
                f"{dynamic_stats['avg_tp_pct']:.2f}%"
            ),
        },
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(_backtest_report_title(config))
        builder.source("routine", "macdbb_scanner_aggressive_hl_backtest")
        preset_label = PRESET_LABELS.get(config.preset, config.preset)
        builder.meta("Preset", preset_label)
        builder.meta(
            "Mode",
            _REPLAY_MODE_LABELS.get(config.replay_mode, config.replay_mode),
        )
        builder.manual_order()
        builder.kpi("Sim Trades", str(total_trades))
        builder.kpi("Formal", str(formal_trades))
        builder.kpi("Adaptive", str(adaptive_trades))
        builder.kpi("Sim PnL", f"${total_pnl:+.2f}", trend=pnl_trend)
        builder.kpi(
            "Capital-norm PnL",
            f"${dynamic_stats['capital_normalized_pnl']:+.2f}",
            trend=(
                "positive"
                if dynamic_stats["capital_normalized_pnl"] > 0
                else "negative"
                if dynamic_stats["capital_normalized_pnl"] < 0
                else "neutral"
            ),
        )
        builder.kpi(
            "PnL / exposure",
            f"{dynamic_stats['pnl_per_exposure']:.4f}",
        )
        builder.kpi("Avg Notional", f"${dynamic_stats['avg_notional_quote']:.2f}")
        builder.kpi(
            "Avg SL/TP",
            f"{dynamic_stats['avg_sl_pct']:.2f}% / {dynamic_stats['avg_tp_pct']:.2f}%",
        )
        builder.params(config.model_dump())
        if session_rollup_rows:
            builder.table(session_rollup_rows, columns=table_columns)
        if trade_table_rows:
            builder.table(
                trade_table_rows,
                columns=[
                    "Session",
                    "Pair",
                    "Side",
                    "Entry Class",
                    "Trigger",
                    "Notional $",
                    "Size Mult",
                    "SL %",
                    "TP %",
                    "Entry Tick",
                    "Exit Tick",
                    "Exit Reason",
                    "Return %",
                    "PnL $",
                ],
            )
        await builder.save()
    except Exception as error:
        logger.warning("Report generation failed: %s", error)

    return RoutineResult(
        text="\n".join(summary_lines),
        table_data=session_rollup_rows,
        table_columns=table_columns,
        sections=sections,
    )
