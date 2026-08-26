#!/usr/bin/env python3
"""30d identity + timing gate for pullback sim speed changes.

Hydrates the sweep window once (unpacked), then:

1. Winner preset: old tape path (scanner snapshots, no pair filter) vs slim+filter
2. Pack SharedCandleStore; winner preset packed vs unpacked (same fingerprint)
3. Re-run saved YAML leads and require stats to match prior sweep JSON

Does not start a sweep. Does not change current_winner_preset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RANGE_START = "2026-07-18T00:00:00Z"
RANGE_END = "2026-08-17T10:20:00Z"
SNAPSHOT_DIR = "data/replay_snapshots_binance_60s"

STAT_INT_KEYS = (
    "trades",
    "immediate",
    "pullback",
    "wins",
    "sl_hits",
    "tp_hits",
    "thesis_decay",
    "session_end",
)
STAT_FLOAT_KEYS = (
    "net_pnl_quote",
    "win_rate_pct",
    "avg_hold_ticks",
    "avg_return_pct",
)

SAVED_LEADS = (
    {
        "preset": "pullback_sweep_lead_001",
        # YAML 001 is tp 6 / chase 70-20, not the later mega leader (tp 9 / 80-30).
        "json_path": "data/backtests/pullback_mega_sweep/imp1_pb1.25_sl3.8_tp6_cl70_cs20.json",
    },
    {
        "preset": "pullback_sweep_lead_007",
        "json_path": (
            "data/backtests/pullback_dynamics_sweep/"
            "imp1_d2_f1_b0.5_1_2.5-6_6-12_s0_r0.75_dr20.json"
        ),
    },
    {
        "preset": "pullback_sweep_lead_008",
        "json_path": (
            "data/backtests/pullback_dynamics_sweep/"
            "imp0.75_d2_f0_b0.5_1_2-6_4-12_s0_r0.75_dr20.json"
        ),
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify pullback sim speed changes vs saved 30d results"
    )
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default=RANGE_START)
    parser.add_argument("--range-end", default=RANGE_END)
    parser.add_argument("--snapshot-dir", default=SNAPSHOT_DIR)
    parser.add_argument("--candle-source", default="binance_perpetual")
    parser.add_argument("--total-amount-quote", type=float, default=100.0)
    parser.add_argument(
        "--out-json",
        default="data/backtests/pullback_tape_smoke/verify_sim_speed.json",
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


def _stats_mismatch(got: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in STAT_INT_KEYS:
        if key not in expected:
            continue
        if int(got[key]) != int(expected[key]):
            errors.append(f"{key}: got {got[key]} expected {expected[key]}")
    for key in STAT_FLOAT_KEYS:
        if key not in expected:
            continue
        if not math.isclose(
            float(got[key]), float(expected[key]), rel_tol=0.0, abs_tol=1e-9
        ):
            errors.append(f"{key}: got {got[key]} expected {expected[key]}")
    return errors


async def _main() -> int:
    from routines.macdbb_pullback_hl_backtest import Config
    from routines.macdbb_pullback_hl_replay.mega_sweep_runner import trade_stats
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
        pack_candles=False,
    )
    load_seconds = time.monotonic() - load_started
    logging.info("Hydrate+tape %.1fs ticks=%s", load_seconds, shared.get("tick_count"))

    tapes = shared.get("signal_tapes") or {}
    loader = shared["loader"]

    def _run(
        preset: str,
        *,
        use_scanner_price_snapshots: bool = False,
        filter_inactive_decide_pairs: bool = True,
    ) -> tuple[list[Any], dict[str, Any], float]:
        kwargs = dict(shared["base_kwargs"])
        kwargs["preset"] = preset
        config = resolve_pullback_config(Config(**kwargs))
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
                signal_tape=tapes.get(session_num),
                collect_debug_rows=False,
                use_scanner_price_snapshots=use_scanner_price_snapshots,
                filter_inactive_decide_pairs=filter_inactive_decide_pairs,
            )
            if summary.get("status") == "skipped_no_price_data":
                continue
            all_trades.extend(trades)
        elapsed = time.monotonic() - started
        stats = trade_stats(all_trades)
        logging.info(
            "%s sim %.2fs trades=%s pnl=%.6f scanner_snaps=%s filter=%s",
            preset,
            elapsed,
            stats["trades"],
            stats["net_pnl_quote"],
            use_scanner_price_snapshots,
            filter_inactive_decide_pairs,
        )
        return all_trades, stats, elapsed

    payload: dict[str, Any] = {
        "range_start": args.range_start,
        "range_end": args.range_end,
        "load_seconds": load_seconds,
        "tick_count": shared.get("tick_count"),
        "ok": True,
        "errors": [],
    }

    old_trades, old_stats, old_seconds = _run(
        args.preset,
        use_scanner_price_snapshots=True,
        filter_inactive_decide_pairs=False,
    )
    slim_trades, slim_stats, slim_seconds = _run(
        args.preset,
        use_scanner_price_snapshots=False,
        filter_inactive_decide_pairs=True,
    )
    old_fp = _trade_fingerprint(old_trades)
    slim_fp = _trade_fingerprint(slim_trades)
    slim_match = old_fp == slim_fp
    payload["winner"] = {
        "preset": args.preset,
        "unpacked_scanner_seconds": old_seconds,
        "unpacked_slim_seconds": slim_seconds,
        "slim_matches_scanner": slim_match,
        "unpacked_stats": slim_stats,
        "trades": slim_stats["trades"],
    }
    if not slim_match:
        payload["ok"] = False
        payload["errors"].append("slim+filter fingerprint != scanner snapshots")
        logging.error("Slim/filter changed trades vs scanner snapshots — stop")
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1
    if not slim_trades:
        payload["ok"] = False
        payload["errors"].append("winner preset produced zero trades")
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1

    from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
        prepare_shared_candle_stores,
    )

    (
        _px,
        _bar,
        _vol,
        price_store,
        barrier_store,
        vol_store,
    ) = prepare_shared_candle_stores(
        shared["hl_candle_cache"] or {},
        shared["hl_barrier_candle_cache"] or {},
        shared["hl_vol_candle_cache"] or {},
    )
    shared["hl_candle_cache"] = price_store or {}
    shared["hl_barrier_candle_cache"] = barrier_store or {}
    shared["hl_vol_candle_cache"] = vol_store or {}
    packed_stores = [
        store
        for store in (price_store, barrier_store, vol_store)
        if store is not None
    ]

    packed_trades, packed_stats, packed_seconds = _run(
        args.preset,
        use_scanner_price_snapshots=False,
        filter_inactive_decide_pairs=True,
    )
    packed_match = old_fp == _trade_fingerprint(packed_trades)
    payload["winner"]["packed_seconds"] = packed_seconds
    payload["winner"]["packed_matches_unpacked"] = packed_match
    payload["winner"]["packed_stats"] = packed_stats
    payload["winner"]["speedup_vs_unpacked_slim"] = (
        (slim_seconds / packed_seconds) if packed_seconds else None
    )
    payload["winner"]["speedup_vs_unpacked_scanner"] = (
        (old_seconds / packed_seconds) if packed_seconds else None
    )
    if not packed_match:
        payload["ok"] = False
        payload["errors"].append("packed fingerprint != unpacked")
        logging.error("Packed candles changed trades — stop")
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        for store in packed_stores:
            store.close_unlink()
        return 1

    lead_rows: list[dict[str, Any]] = []
    for lead in SAVED_LEADS:
        expected = json.loads(
            Path(lead["json_path"]).read_text(encoding="utf-8")
        )
        _trades, stats, elapsed = _run(
            lead["preset"],
            use_scanner_price_snapshots=False,
            filter_inactive_decide_pairs=True,
        )
        mismatches = _stats_mismatch(stats, expected["stats"])
        row = {
            "preset": lead["preset"],
            "saved_json": lead["json_path"],
            "seconds": elapsed,
            "match": not mismatches,
            "mismatches": mismatches,
            "got": {key: stats[key] for key in (*STAT_INT_KEYS, *STAT_FLOAT_KEYS)},
            "expected": {
                key: expected["stats"][key]
                for key in (*STAT_INT_KEYS, *STAT_FLOAT_KEYS)
                if key in expected["stats"]
            },
        }
        lead_rows.append(row)
        if mismatches:
            payload["ok"] = False
            payload["errors"].append(
                f"{lead['preset']} != {lead['json_path']}: {mismatches}"
            )
            logging.error(
                "Saved-result mismatch %s %s", lead["preset"], mismatches
            )
    payload["leads"] = lead_rows

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    for store in packed_stores:
        store.close_unlink()
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
