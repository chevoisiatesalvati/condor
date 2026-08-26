#!/usr/bin/env python3
"""24-config entry-gate + SL/TP sweep for macdbb_pullback_hl.

Hydrates ticks and candles once, then replays each case on the shared tape.
Default tape is Binance 60s (same venue as the pullback decay-2h winner), last
two weeks of the snapshot. Pass --snapshot-dir / --candle-source for HL.
"""

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pullback entry-gate + SL/TP config sweep (24 cases)"
    )
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default="2026-07-24T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-07T23:59:59Z")
    parser.add_argument(
        "--snapshot-dir",
        default="data/replay_snapshots_binance_60s",
        help="Parquet snapshot directory",
    )
    parser.add_argument(
        "--candle-source",
        default="binance_perpetual",
        help="hyperliquid or binance_perpetual",
    )
    parser.add_argument("--total-amount-quote", type=float, default=50.0)
    parser.add_argument(
        "--out-dir",
        default="data/backtests/pullback_entry_sltp_sweep",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run cases that already have a JSON result",
    )
    return parser.parse_args()


def _trade_stats(trades: list[Any]) -> dict[str, Any]:
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl_quote > 0)
    sl = sum(1 for t in trades if "stop_loss" in t.exit_reason)
    tp = sum(1 for t in trades if "take_profit" in t.exit_reason)
    decay = sum(1 for t in trades if t.exit_reason == "thesis_decay")
    session_end = sum(1 for t in trades if t.exit_reason == "session_end")
    pnl = sum(t.pnl_quote for t in trades)
    immediate = sum(1 for t in trades if t.entry_class == "immediate")
    pullback = sum(1 for t in trades if t.entry_class == "pullback")
    avg_hold = (sum(t.hold_ticks for t in trades) / total) if total else 0.0
    return {
        "trades": total,
        "immediate": immediate,
        "pullback": pullback,
        "wins": wins,
        "win_rate_pct": (wins / total * 100.0) if total else 0.0,
        "net_pnl_quote": pnl,
        "sl_hits": sl,
        "tp_hits": tp,
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
    total_amount_quote: float,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "preset": preset,
        "range_start_utc": range_start,
        "range_end_utc": range_end,
        "total_amount_quote": float(total_amount_quote),
        "live_equivalent_queue": True,
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
        if candle_source.startswith("binance"):
            kwargs["hl_cache_dir"] = "data/binance_candles"
    return kwargs


async def _load_shared_context(
    *,
    preset: str,
    range_start: str,
    range_end: str,
    snapshot_dir: str,
    candle_source: str,
    total_amount_quote: float,
    pack_candles: bool = True,
) -> dict[str, Any]:
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
        total_amount_quote=total_amount_quote,
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
        from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
            prepare_shared_candle_stores,
        )

        if pack_candles and (
            hl_candle_cache or hl_barrier_candle_cache or hl_vol_candle_cache
        ):
            logging.info(
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
    return {
        "name": case["name"],
        "config": {
            "impulse_atr_mult": float(config.impulse_atr_mult),
            "pullback_epsilon_pct": float(config.pullback_epsilon_pct),
            "sl_pct": float(config.sl_pct),
            "tp_pct": float(config.tp_pct),
            "bb_proximity_epsilon_pct": float(config.bb_proximity_epsilon_pct),
            "enable_thesis_decay_exit": bool(config.enable_thesis_decay_exit),
            "thesis_decay_exit_hours": float(config.thesis_decay_exit_hours),
        },
        "stats": stats,
    }


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(
        rows,
        key=lambda row: float((row.get("stats") or {}).get("net_pnl_quote") or 0),
        reverse=True,
    )
    headers = [
        "case",
        "pnl",
        "trades",
        "imm",
        "pb",
        "win%",
        "SL",
        "TP",
        "decay",
        "avg_ret%",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in ranked:
        s = row["stats"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    f"{s['net_pnl_quote']:+.2f}",
                    str(s["trades"]),
                    str(s["immediate"]),
                    str(s["pullback"]),
                    f"{s['win_rate_pct']:.1f}",
                    str(s["sl_hits"]),
                    str(s["tp_hits"]),
                    str(s["thesis_decay"]),
                    f"{s['avg_return_pct']:+.3f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _write_comparison(
    out_dir: Path,
    *,
    args: argparse.Namespace,
    tick_count: int,
    results: list[dict[str, Any]],
) -> None:
    comparison = {
        "range_start": args.range_start,
        "range_end": args.range_end,
        "preset": args.preset,
        "snapshot_dir": args.snapshot_dir or None,
        "candle_source": args.candle_source or None,
        "total_amount_quote": args.total_amount_quote,
        "tick_count": tick_count,
        "cases": [
            {"name": r["name"], "config": r.get("config"), "stats": r.get("stats")}
            for r in results
        ],
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    md = (
        f"# Pullback entry + SL/TP sweep\n\n"
        f"Range: `{args.range_start}` → `{args.range_end}`  \n"
        f"Preset: `{args.preset}`  \n"
        f"Snapshot: `{args.snapshot_dir}`  \n"
        f"Notional: `{args.total_amount_quote}`\n\n"
        f"{_markdown_table([r for r in results if 'stats' in r])}\n"
    )
    (out_dir / "comparison.md").write_text(md, encoding="utf-8")


async def _main() -> int:
    from routines.macdbb_pullback_hl_replay.entry_sltp_sweep import (
        pullback_entry_sltp_cases,
    )

    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = pullback_entry_sltp_cases()
    logging.info("Sweep grid: %d cases", len(cases))

    shared = await _load_shared_context(
        preset=args.preset,
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
    )

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        result_path = out_dir / f"{case['name']}.json"
        if result_path.is_file() and not args.force:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            results.append(loaded)
            logging.info(
                "[%d/%d] skip existing %s pnl=%s",
                index,
                len(cases),
                case["name"],
                (loaded.get("stats") or {}).get("net_pnl_quote"),
            )
            continue
        logging.info("[%d/%d] running %s", index, len(cases), case["name"])
        result = _run_case(case, shared)
        results.append(result)
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        stats = result.get("stats") or {}
        logging.info(
            "Case %s: trades=%s imm=%s pb=%s pnl=%s sl=%s tp=%s",
            case["name"],
            stats.get("trades"),
            stats.get("immediate"),
            stats.get("pullback"),
            stats.get("net_pnl_quote"),
            stats.get("sl_hits"),
            stats.get("tp_hits"),
        )
        _write_comparison(
            out_dir,
            args=args,
            tick_count=int(shared.get("tick_count") or 0),
            results=results,
        )

    _write_comparison(
        out_dir,
        args=args,
        tick_count=int(shared.get("tick_count") or 0),
        results=results,
    )
    print((out_dir / "comparison.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
