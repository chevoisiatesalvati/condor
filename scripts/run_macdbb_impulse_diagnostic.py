#!/usr/bin/env python3
"""Phase-0 diagnostic: MACDBB entries × prior 1h impulse vs SL/TP outcomes."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MACDBB impulse×exit diagnostic")
    parser.add_argument("--preset", default="hl_dynamic_timeline_refine_lead_013_60s")
    parser.add_argument("--range-start", default="2026-07-31T00:00:00+00:00")
    parser.add_argument("--range-end", default="2026-08-07T23:59:59+00:00")
    parser.add_argument("--impulse-atr-mult", type=float, default=1.25)
    parser.add_argument("--lookback-bars", type=int, default=2)
    parser.add_argument(
        "--output-dir",
        default="data/backtests/macdbb_impulse_diagnostic",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    from routines.macdbb_scanner_aggressive_hl_backtest import Config, run
    from routines.macdbb_pullback_hl_replay.impulse_candles import (
        annotate_trade_impulse,
        summarize_impulse_exit_rates,
    )

    config = Config(
        preset=args.preset,
        range_start_utc=args.range_start,
        range_end_utc=args.range_end,
        write_csv=False,
        auto_update_snapshots=False,
    )
    # Monkey-patch: capture trades from simulate by re-running via internal path.
    # The routine doesn't return trade objects; replicate the simulation collect.
    from condor.routine_progress import write_progress
    from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
    from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
        hl_prefetch_settings_from_config,
        prefetch_replay_hl_prices,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.presets import resolve_config_with_preset
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
    from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

    config = resolve_config_with_preset(config)
    configure_replay_data_sources(config)
    parsed_sessions, session_configs, _selected = load_replay_sessions(config)
    if uses_snapshot_store(config):
        refresh_snapshot_caches(config)
    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    hl_caches_by_session = {}
    hl_candle_cache: dict = {}
    hl_barrier_candle_cache: dict = {}
    hl_vol_candle_cache: dict = {}
    if should_prefetch_replay_candles(config) and parsed_sessions:
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    all_trades = []
    for session_num, tick_meta_map in parsed_sessions.items():
        session_config = session_configs.get(session_num, config)
        _pairs, _ticks, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=session_config,
            hl_price_cache=hl_caches_by_session.get(session_num),
            hl_candle_cache=hl_candle_cache,
            hl_barrier_candle_cache=hl_barrier_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            replay_policy=DynamicReplayPolicy(session_config),
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        all_trades.extend(trades)

    cache_dir = Path("data/binance_candles")
    rows = [
        annotate_trade_impulse(
            trade,
            cache_dir=cache_dir,
            lookback_bars=args.lookback_bars,
            impulse_atr_mult=args.impulse_atr_mult,
        )
        for trade in all_trades
    ]
    summary = summarize_impulse_exit_rates(rows)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "impulse_trade_rows.json").write_text(
        json.dumps(rows, indent=2),
        encoding="utf-8",
    )
    (out_dir / "impulse_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"Wrote {len(rows)} annotated trades → {out_dir}")
    # Keep routine import warm for discovery side-effects; unused run is intentional no-op.
    _ = (run, MagicMock, write_progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
