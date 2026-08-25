#!/usr/bin/env python3
"""Smoke-test pullback signal-tape timing for one config.

Builds the param-independent 1h MACD/BB/impulse tape once, replays the live
winner config, optionally compares against the cold parquet-per-tick path, and
prints an extrapolation for a multi-thousand-config sweep.

Default window is one 60s day (fast identity check). Pass the 14d sweep range
with --skip-cold to time a realistic per-config replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Time one pullback config on a signal tape")
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default="2026-08-07T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-07T23:59:59Z")
    parser.add_argument(
        "--snapshot-dir",
        default="data/replay_snapshots_binance_60s",
    )
    parser.add_argument("--candle-source", default="binance_perpetual")
    parser.add_argument("--total-amount-quote", type=float, default=100.0)
    parser.add_argument(
        "--skip-cold",
        action="store_true",
        help="Do not run the parquet-per-tick path (use for long windows)",
    )
    parser.add_argument("--configs-target", type=int, default=2000)
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=30.0,
        help="Scale taped per-config time to this many days (linear in ticks)",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--out-json",
        default="data/backtests/pullback_tape_smoke/timing.json",
    )
    parser.add_argument(
        "--enable-dynamic-sizing",
        action="store_true",
        help="Enable inverse-vol sizing for this smoke config",
    )
    parser.add_argument(
        "--enable-dynamic-barriers",
        action="store_true",
        help="Enable ATR-scaled SL/TP for this smoke config",
    )
    parser.add_argument(
        "--require-dynamic-sizes",
        action="store_true",
        help="Fail if no trades or all notionals equal the budget (dynamics-on gate)",
    )
    return parser.parse_args()


def _trade_fingerprint(trades: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            t.pair,
            t.side,
            t.entry_class,
            int(t.entry_tick),
            int(t.exit_tick),
            round(float(t.pnl_quote), 6),
            str(t.exit_reason),
        )
        for t in trades
    ]


def _stats(trades: list[Any]) -> dict[str, Any]:
    total = len(trades)
    pnl = sum(float(t.pnl_quote) for t in trades)
    notionals = [round(float(t.notional_quote), 6) for t in trades]
    unique_notionals = sorted(set(notionals))
    return {
        "trades": total,
        "net_pnl_quote": pnl,
        "immediate": sum(1 for t in trades if t.entry_class == "immediate"),
        "pullback": sum(1 for t in trades if t.entry_class == "pullback"),
        "avg_notional": (sum(notionals) / total) if total else 0.0,
        "unique_notional_count": len(unique_notionals),
        "unique_notionals_sample": unique_notionals[:12],
    }


async def _main() -> int:
    from routines.macdbb_pullback_hl_backtest import Config
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
    from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session
    from scripts.run_macdbb_pullback_entry_sltp_sweep import _load_shared_context

    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )

    load_started = time.monotonic()
    shared = await _load_shared_context(
        preset=args.preset,
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
    )
    load_seconds = time.monotonic() - load_started
    tapes = shared.get("signal_tapes") or {}
    tape_build_seconds = sum(float(tape.build_seconds) for tape in tapes.values())
    tick_count = int(shared.get("tick_count") or 0)
    pair_count = sum(len(tape.pairs) for tape in tapes.values())
    logging.info(
        "Hydrate+tape %.1fs (tape build %.1fs) ticks=%d pairs=%d",
        load_seconds,
        tape_build_seconds,
        tick_count,
        pair_count,
    )

    kwargs = dict(shared["base_kwargs"])
    if args.enable_dynamic_sizing:
        kwargs["enable_dynamic_sizing"] = True
    if args.enable_dynamic_barriers:
        kwargs["enable_dynamic_barriers"] = True
    config = resolve_pullback_config(Config(**kwargs))
    loader = shared["loader"]

    def _run(*, use_signal_tape: bool) -> tuple[list[Any], float]:
        all_trades: list[Any] = []
        started = time.monotonic()
        for session_num, tick_meta_map in shared["parsed_sessions"].items():
            _pairs, _ticks, trades, summary = simulate_pullback_session(
                session_num=session_num,
                tick_meta_map=tick_meta_map,
                reports_by_pair=shared["reports_by_pair"],
                config=config,
                signal_config=loader,
                hl_price_cache=shared["hl_caches_by_session"].get(session_num),
                hl_candle_cache=shared["hl_candle_cache"],
                hl_barrier_candle_cache=shared["hl_barrier_candle_cache"],
                hl_vol_candle_cache=shared["hl_vol_candle_cache"],
                signal_tape=tapes.get(session_num) if use_signal_tape else None,
                use_signal_tape=use_signal_tape,
                collect_debug_rows=False,
            )
            if summary.get("status") == "skipped_no_price_data":
                continue
            all_trades.extend(trades)
        return all_trades, time.monotonic() - started

    taped_trades, taped_seconds = _run(use_signal_tape=True)
    logging.info(
        "Taped sim %.2fs stats=%s",
        taped_seconds,
        _stats(taped_trades),
    )

    cold_seconds: float | None = None
    match = True
    if not args.skip_cold:
        cold_trades, cold_seconds = _run(use_signal_tape=False)
        match = _trade_fingerprint(taped_trades) == _trade_fingerprint(cold_trades)
        logging.info(
            "Cold sim %.2fs stats=%s match=%s",
            cold_seconds,
            _stats(cold_trades),
            match,
        )
        if not match:
            logging.error(
                "Tape/cold mismatch taped=%s cold=%s",
                _trade_fingerprint(taped_trades),
                _trade_fingerprint(cold_trades),
            )

    ticks_per_day = 86_400 / 60.0
    window_days = (tick_count / ticks_per_day) if tick_count else 0.0
    scale = (args.horizon_days / window_days) if window_days > 0 else 0.0
    per_config_horizon = taped_seconds * scale
    sweep_seconds = args.configs_target * per_config_horizon / max(1, args.workers)
    payload = {
        "range_start": args.range_start,
        "range_end": args.range_end,
        "tick_count": tick_count,
        "pair_count": pair_count,
        "window_days": window_days,
        "load_seconds": load_seconds,
        "tape_build_seconds": tape_build_seconds,
        "taped_sim_seconds": taped_seconds,
        "cold_sim_seconds": cold_seconds,
        "tape_matches_cold": match,
        "taped_stats": _stats(taped_trades),
        "enable_dynamic_sizing": bool(args.enable_dynamic_sizing),
        "enable_dynamic_barriers": bool(args.enable_dynamic_barriers),
        "speedup_vs_cold": (
            (cold_seconds / taped_seconds) if cold_seconds and taped_seconds else None
        ),
        "extrapolation": {
            "horizon_days": args.horizon_days,
            "configs_target": args.configs_target,
            "workers": args.workers,
            "per_config_horizon_seconds": per_config_horizon,
            "tape_build_horizon_seconds": tape_build_seconds * scale,
            "sweep_seconds": sweep_seconds,
            "sweep_hours": sweep_seconds / 3600.0,
        },
    }
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if not match:
        return 1
    if args.require_dynamic_sizes:
        taped_stats = payload["taped_stats"]
        if int(taped_stats["trades"]) <= 0:
            logging.error("Dynamics smoke failed: no trades")
            return 1
        budget = float(args.total_amount_quote)
        unique = taped_stats["unique_notionals_sample"]
        all_budget = all(abs(float(value) - budget) < 1e-6 for value in unique)
        if taped_stats["unique_notional_count"] <= 1 and all_budget:
            logging.error(
                "Dynamics smoke failed: all notionals equal budget %s sample=%s",
                budget,
                unique,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
