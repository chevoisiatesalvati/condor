#!/usr/bin/env python3
"""A/B sweep of optional early-exit settings for macdbb_pullback_hl."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


SWEEP_CASES: list[dict[str, Any]] = [
    {
        "name": "baseline_barriers_only",
        "enable_flip_exit": False,
        "enable_thesis_decay_exit": False,
    },
    {
        "name": "flip_only",
        "enable_flip_exit": True,
        "enable_thesis_decay_exit": False,
        "flip_confirm_ticks": 2,
        "flip_cooldown_hours": 1.5,
    },
    {
        "name": "decay_28h",
        "enable_flip_exit": False,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_hours": 28.0,
        "thesis_bb_drift_pts": 20.0,
    },
    {
        "name": "decay_4h",
        "enable_flip_exit": False,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_hours": 4.0,
        "thesis_bb_drift_pts": 20.0,
    },
    {
        "name": "decay_2h",
        "enable_flip_exit": False,
        "enable_thesis_decay_exit": True,
        "thesis_decay_exit_hours": 2.0,
        "thesis_bb_drift_pts": 20.0,
    },
    {
        "name": "flip_plus_decay_4h",
        "enable_flip_exit": True,
        "enable_thesis_decay_exit": True,
        "flip_confirm_ticks": 2,
        "flip_cooldown_hours": 1.5,
        "thesis_decay_exit_hours": 4.0,
        "thesis_bb_drift_pts": 20.0,
    },
    {
        "name": "flip_plus_decay_28h",
        "enable_flip_exit": True,
        "enable_thesis_decay_exit": True,
        "flip_confirm_ticks": 2,
        "flip_cooldown_hours": 1.5,
        "thesis_decay_exit_hours": 28.0,
        "thesis_bb_drift_pts": 20.0,
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pullback early-exit config sweep")
    parser.add_argument("--preset", default="pullback_timeline_v1_60s")
    parser.add_argument("--range-start", default="2026-08-06T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-08T00:00:00Z")
    parser.add_argument(
        "--snapshot-dir",
        default="",
        help="Override snapshot store (default: preset snapshot_dir)",
    )
    parser.add_argument(
        "--candle-source",
        default="",
        help="Override candle/price source (e.g. binance_perpetual, hyperliquid)",
    )
    parser.add_argument(
        "--out-dir",
        default="data/backtests/pullback_early_exit_sweep_aug6_7",
    )
    return parser.parse_args()


def _trade_stats(trades: list[Any]) -> dict[str, Any]:
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_quote > 0)
    sl = sum(1 for t in trades if "stop_loss" in t.exit_reason)
    tp = sum(1 for t in trades if "take_profit" in t.exit_reason)
    flip = sum(1 for t in trades if t.exit_reason == "flip_confirm")
    decay = sum(1 for t in trades if t.exit_reason == "thesis_decay")
    session_end = sum(1 for t in trades if t.exit_reason == "session_end")
    pnl = sum(t.pnl_quote for t in trades)
    immediate = sum(1 for t in trades if t.entry_class == "immediate")
    pullback = sum(1 for t in trades if t.entry_class == "pullback")
    avg_hold = (
        sum(t.hold_ticks for t in trades) / total if total else 0.0
    )
    return {
        "trades": total,
        "immediate": immediate,
        "pullback": pullback,
        "wins": wins,
        "win_rate_pct": (wins / total * 100.0) if total else 0.0,
        "net_pnl_quote": pnl,
        "sl_hits": sl,
        "tp_hits": tp,
        "flip_confirm": flip,
        "thesis_decay": decay,
        "session_end": session_end,
        "avg_hold_ticks": avg_hold,
        "avg_return_pct": (
            sum(t.return_pct for t in trades) / total if total else 0.0
        ),
    }


def _base_kwargs(
    *,
    preset: str,
    range_start: str,
    range_end: str,
    snapshot_dir: str,
    candle_source: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "preset": preset,
        "range_start_utc": range_start,
        "range_end_utc": range_end,
    }
    if snapshot_dir:
        kwargs["snapshot_dir"] = snapshot_dir
    if candle_source:
        kwargs["candle_source"] = candle_source
        kwargs["price_source"] = (
            "binance_candles"
            if candle_source.startswith("binance")
            else "hl_candles"
            if candle_source.startswith("hyperliquid")
            else "auto"
        )
    return kwargs


async def _load_shared_context(
    *,
    preset: str,
    range_start: str,
    range_end: str,
    snapshot_dir: str,
    candle_source: str,
) -> dict[str, Any]:
    """Hydrate ticks + prefetch candles once for the whole sweep."""
    from routines.macdbb_pullback_hl_backtest import Config, _loader_config
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
    from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
        hl_prefetch_settings_from_config,
        prefetch_replay_hl_prices,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
        configure_replay_data_sources,
        refresh_snapshot_caches,
        should_prefetch_replay_candles,
        uses_snapshot_store,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import (
        load_replay_sessions,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.reports import (
        build_reports_by_pair,
        load_reports_index,
    )

    kwargs = _base_kwargs(
        preset=preset,
        range_start=range_start,
        range_end=range_end,
        snapshot_dir=snapshot_dir,
        candle_source=candle_source,
    )
    config = resolve_pullback_config(Config(**kwargs))
    loader = _loader_config(config)
    configure_replay_data_sources(loader)
    logging.info("Loading / hydrating timeline ticks...")
    parsed_sessions, _session_configs, selected = load_replay_sessions(loader)
    if not selected or not parsed_sessions:
        raise RuntimeError("No ticks loaded for sweep range")
    tick_count = sum(len(v) for v in parsed_sessions.values())
    logging.info("Loaded %d ticks across sessions %s", tick_count, selected)
    if uses_snapshot_store(loader):
        refresh_snapshot_caches(loader)

    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    hl_caches_by_session: dict = {}
    hl_candle_cache: dict = {}
    hl_barrier_candle_cache: dict = {}
    hl_vol_candle_cache: dict = {}
    if should_prefetch_replay_candles(loader) and parsed_sessions:
        logging.info("Prefetching barrier/entry candles...")
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(loader),
        )

    from routines.macdbb_pullback_hl_replay.signal_tape import build_pullback_signal_tapes

    cache_dir = Path(loader.hl_cache_dir or "data/hl_candles")
    logging.info("Building pullback signal tape (once, reused for every case)...")
    signal_tapes = build_pullback_signal_tapes(
        parsed_sessions,
        cache_dir=cache_dir,
        candle_source=loader.candle_source,
    )

    return {
        "base_kwargs": kwargs,
        "loader": loader,
        "parsed_sessions": parsed_sessions,
        "reports_by_pair": reports_by_pair,
        "hl_caches_by_session": hl_caches_by_session,
        "hl_candle_cache": hl_candle_cache,
        "hl_barrier_candle_cache": hl_barrier_candle_cache,
        "hl_vol_candle_cache": hl_vol_candle_cache,
        "signal_tapes": signal_tapes,
        "tick_count": tick_count,
    }


def _run_case(case: dict[str, Any], shared: dict[str, Any]) -> dict[str, Any]:
    from routines.macdbb_pullback_hl_backtest import Config
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
    from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session

    kwargs = {
        **shared["base_kwargs"],
        **{k: v for k, v in case.items() if k != "name"},
    }
    config = resolve_pullback_config(Config(**kwargs))
    loader = shared["loader"]

    all_trades: list[Any] = []
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
            signal_tape=(shared.get("signal_tapes") or {}).get(session_num),
            collect_debug_rows=False,
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        all_trades.extend(trades)

    stats = _trade_stats(all_trades)
    trade_rows = [
        {
            "pair": t.pair,
            "side": t.side,
            "entry_class": t.entry_class,
            "exit_reason": t.exit_reason,
            "hold_ticks": t.hold_ticks,
            "return_pct": round(t.return_pct, 4),
            "pnl_quote": round(t.pnl_quote, 4),
            "entry_time": t.entry_time_utc.isoformat() if t.entry_time_utc else "",
            "exit_time": t.exit_time_utc.isoformat() if t.exit_time_utc else "",
        }
        for t in all_trades
    ]
    return {
        "name": case["name"],
        "config": {
            "enable_flip_exit": bool(config.enable_flip_exit),
            "enable_thesis_decay_exit": bool(config.enable_thesis_decay_exit),
            "flip_confirm_ticks": int(config.flip_confirm_ticks),
            "flip_cooldown_hours": float(config.flip_cooldown_hours),
            "thesis_decay_exit_hours": float(config.thesis_decay_exit_hours),
            "thesis_bb_drift_pts": float(config.thesis_bb_drift_pts),
            "sl_pct": float(config.sl_pct),
            "tp_pct": float(config.tp_pct),
        },
        "stats": stats,
        "trades": trade_rows,
    }


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "case",
        "trades",
        "pnl",
        "win%",
        "SL",
        "TP",
        "flip",
        "decay",
        "session_end",
        "avg_hold_ticks",
        "avg_ret%",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        s = row["stats"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    str(s["trades"]),
                    f"{s['net_pnl_quote']:+.2f}",
                    f"{s['win_rate_pct']:.1f}",
                    str(s["sl_hits"]),
                    str(s["tp_hits"]),
                    str(s["flip_confirm"]),
                    str(s["thesis_decay"]),
                    str(s["session_end"]),
                    f"{s['avg_hold_ticks']:.0f}",
                    f"{s['avg_return_pct']:+.3f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    shared = await _load_shared_context(
        preset=args.preset,
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
    )

    results: list[dict[str, Any]] = []
    for case in SWEEP_CASES:
        logging.info("=== Running case %s ===", case["name"])
        result = _run_case(case, shared)
        results.append(result)
        (out_dir / f"{case['name']}.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
        stats = result.get("stats") or {}
        logging.info(
            "Case %s: trades=%s pnl=%s flip=%s decay=%s session_end=%s",
            case["name"],
            stats.get("trades"),
            stats.get("net_pnl_quote"),
            stats.get("flip_confirm"),
            stats.get("thesis_decay"),
            stats.get("session_end"),
        )

    comparison = {
        "range_start": args.range_start,
        "range_end": args.range_end,
        "preset": args.preset,
        "snapshot_dir": args.snapshot_dir or None,
        "candle_source": args.candle_source or None,
        "tick_count": shared.get("tick_count"),
        "cases": [
            {
                "name": r["name"],
                "config": r.get("config"),
                "stats": r.get("stats"),
                "error": r.get("error"),
            }
            for r in results
        ],
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    md = (
        f"# Pullback early-exit sweep\n\n"
        f"Range: `{args.range_start}` → `{args.range_end}`  \n"
        f"Preset: `{args.preset}`\n\n"
        f"{_markdown_table([r for r in results if 'stats' in r])}\n"
    )
    (out_dir / "comparison.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
