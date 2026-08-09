"""Backtest simulator for macdbb_pullback_hl (thesis + staged pullback entries)."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

RUN_IN_SUBPROCESS = True

import json
import logging
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
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
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
    from routines.macdbb_pullback_hl_replay.presets import PRESET_OVERRIDES

    return dict(PRESET_OVERRIDES)


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
        live_equivalent_queue=bool(getattr(config, "live_equivalent_queue", True)),
        min_tradeable_count=int(config.min_tradeable_count or 1),
    )
    return loader


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    write_progress(phase="resolve", message="Resolving pullback preset")
    config = resolve_pullback_config(config)
    loader = _loader_config(config)
    configure_replay_data_sources(loader)

    write_progress(phase="hydrate", message="Loading timeline ticks")
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
    logger.info(
        "Pullback backtest: preset=%s ticks=%d range %s → %s",
        config.preset,
        tick_count,
        config.range_start_utc,
        config.range_end_utc,
    )

    hl_caches_by_session: dict = {}
    hl_candle_cache: dict = {}
    hl_barrier_candle_cache: dict = {}
    hl_vol_candle_cache: dict = {}
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
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        all_trades.extend(trades)
        session_rows.append(
            {
                "Session": session_num,
                "Status": "ok",
                "Ticks": len(tick_meta_map),
                "Trades": summary.get("total_trades", 0),
                "Immediate": summary.get("immediate_trades", 0),
                "Pullback": summary.get("pullback_trades", 0),
                "Win Rate %": summary.get("win_rate_pct", 0),
                "SL rate": summary.get("sl_before_tp_rate", 0),
                "Sim PnL $": summary.get("net_pnl_quote", 0),
            }
        )

    total_trades = len(all_trades)
    total_pnl = sum(t.pnl_quote for t in all_trades)
    wins = sum(1 for t in all_trades if t.pnl_quote > 0)
    sl_n = sum(1 for t in all_trades if "stop_loss" in t.exit_reason)
    tp_n = sum(1 for t in all_trades if "take_profit" in t.exit_reason)
    avg_notional = (
        sum(t.notional_quote for t in all_trades) / total_trades if total_trades else 0.0
    )
    cap_norm = capital_normalized_pnl(
        total_pnl, avg_notional, FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL
    )
    immediate_n = sum(1 for t in all_trades if t.entry_class == "immediate")
    pullback_n = sum(1 for t in all_trades if t.entry_class == "pullback")

    summary_lines = [
        f"macdbb_pullback_hl backtest — {config.strategy_slug}",
        f"Preset: {config.preset} ({PRESET_LABELS.get(config.preset, config.preset)})",
        f"Range: {config.range_start_utc} → {config.range_end_utc}",
        f"Trades: {total_trades} (immediate={immediate_n}, pullback={pullback_n})",
        f"Win rate: {(wins / total_trades * 100.0) if total_trades else 0.0:.1f}%",
        f"SL hits: {sl_n} | TP hits: {tp_n} | SL rate: {(sl_n / total_trades) if total_trades else 0.0:.3f}",
        f"Sim PnL: ${total_pnl:.2f} | Capital-norm PnL: ${cap_norm:.2f}",
        f"Avg notional: ${avg_notional:.2f} | Avg SL/TP: "
        f"{(sum(t.sl_pct_used for t in all_trades) / total_trades) if total_trades else 0:.2f}% / "
        f"{(sum(t.tp_pct_used for t in all_trades) / total_trades) if total_trades else 0:.2f}%",
    ]
    text = "\n".join(summary_lines)
    logger.info(text)

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(
            f"MACDBB Pullback Backtest — {PRESET_LABELS.get(config.preset, config.preset)}"
        )
        builder.source("routine", "macdbb_pullback_hl_backtest")
        builder.tags(["backtest", "macdbb_pullback"])
        builder.manual_order()
        builder.kpi("Sim Trades", str(total_trades))
        builder.kpi("Immediate", str(immediate_n))
        builder.kpi("Pullback", str(pullback_n))
        builder.kpi("Sim PnL", f"${total_pnl:+.2f}")
        builder.kpi("Capital-norm PnL", f"${cap_norm:+.2f}")
        builder.kpi("SL rate", f"{(sl_n / total_trades) if total_trades else 0.0:.3f}")
        builder.markdown(
            "## Config\n"
            f"- **Preset:** {config.preset}\n"
            f"- **Frequency:** {config.frequency_sec}s\n"
            f"- **Range:** {config.range_start_utc} → {config.range_end_utc}\n"
            f"- **Snapshot dir:** `{config.snapshot_dir}`\n"
            f"- **SL/TP:** {config.sl_pct}% / {config.tp_pct}%\n"
            f"- **Impulse ATR mult:** {config.impulse_atr_mult}\n"
            f"- **Pullback timeout hours:** {config.pullback_timeout_hours}\n"
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
                    "Notional": round(t.notional_quote, 2),
                    "SL%": round(t.sl_pct_used, 3),
                    "TP%": round(t.tp_pct_used, 3),
                    "Entry": t.entry_time_utc.isoformat() if t.entry_time_utc else "",
                    "Exit": t.exit_time_utc.isoformat() if t.exit_time_utc else "",
                    "Reason": t.exit_reason,
                    "Return%": round(t.return_pct, 3),
                    "PnL": round(t.pnl_quote, 2),
                }
                for t in all_trades
            ]
            builder.markdown("## Trades")
            builder.table(trade_rows)
        await builder.save()
    except Exception:
        logger.warning("Failed to save pullback backtest report", exc_info=True)

    out_dir = __import__("pathlib").Path("data/backtests/macdbb_pullback_hl")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "last_summary.json").write_text(
        json.dumps(
            {
                "preset": config.preset,
                "total_trades": total_trades,
                "immediate_trades": immediate_n,
                "pullback_trades": pullback_n,
                "sl_hits": sl_n,
                "tp_hits": tp_n,
                "sl_rate": (sl_n / total_trades) if total_trades else 0.0,
                "net_pnl_quote": total_pnl,
                "capital_norm_pnl": cap_norm,
                "avg_notional": avg_notional,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_progress(phase="done", message="Pullback backtest complete", percent=100.0)
    return RoutineResult(text=text)
