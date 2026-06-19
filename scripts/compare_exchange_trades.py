#!/usr/bin/env python3
"""Compare trade-level output between Binance and HL timeline backtests."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from routines.lib.pair_format import hl_pair_from_any
from routines.macdbb_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_replay.hl_prices import (
    hl_prefetch_settings_from_config,
    prefetch_replay_hl_prices,
)
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.macdbb_replay.replay_data import configure_replay_data_sources
from routines.macdbb_replay.replay_loader import load_replay_sessions
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_replay.simulator import simulate_strategy_session
from routines.strategy_replay_backtest_dynamic_amount import _trade_rows


@dataclass(frozen=True)
class RunSpec:
    label: str
    candle_source: str
    price_source: str
    cache_dir: Path
    snapshot_dir: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff trades between exchange candle runs.")
    parser.add_argument("--range-start", default="2026-06-17T21:00:00Z")
    parser.add_argument("--range-end", default="2026-06-19T21:00:00Z")
    parser.add_argument("--preset", default="hl_dynamic_timeline_mega_best")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/exchange_backtest_compare"),
    )
    parser.add_argument(
        "--ignore-pairs",
        default="PURR-USD,PURR-USDT",
        help="Comma-separated pairs to exclude from comparison",
    )
    parser.add_argument(
        "--shared-universe",
        action="store_true",
        help="Use replay_snapshots_*_shared_2d snapshot dirs",
    )
    parser.add_argument(
        "--binance-volume",
        action="store_true",
        help="Use *_shared_2d_bnv_vol snapshot dirs (shared ranking volume)",
    )
    return parser.parse_args()


def _base_asset(pair: str) -> str:
    text = pair.upper()
    for suffix in ("-USDT", "-USD"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text.split("-", 1)[0]


def _build_config(spec: RunSpec, args: argparse.Namespace) -> DynamicStrategyReplayConfig:
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset=args.preset,
            replay_mode="timeline_backtest",
            data_source="snapshots",
            range_start_utc=args.range_start,
            range_end_utc=args.range_end,
            frequency_sec=1800,
            write_csv=False,
            require_price_data=True,
        )
    )
    return config.model_copy(
        update={
            "preset": "custom",
            "candle_source": spec.candle_source,
            "price_source": spec.price_source,
            "hl_cache_dir": str(spec.cache_dir),
            "snapshot_dir": str(spec.snapshot_dir),
        }
    )


async def _run_simulation(spec: RunSpec, args: argparse.Namespace) -> list[dict[str, Any]]:
    config = _build_config(spec, args)
    configure_replay_data_sources(config)
    parsed_sessions, session_configs, selected = load_replay_sessions(config)
    if not parsed_sessions:
        raise RuntimeError(f"No ticks for {spec.label}")

    reports_by_pair = build_reports_by_pair(load_reports_index())
    (
        hl_caches_by_session,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
    ) = await prefetch_replay_hl_prices(
        parsed_sessions,
        settings=hl_prefetch_settings_from_config(config),
    )

    all_trades: list[dict[str, Any]] = []
    for session_num, tick_meta_map in parsed_sessions.items():
        session_config = session_configs.get(session_num, config)
        _pair_rows, _tick_rows, trades, summary = simulate_strategy_session(
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
            raise RuntimeError(f"{spec.label} skipped: no price data")
        rows = _trade_rows(trades)
        for row in rows:
            row["exchange"] = spec.label
            row["base_asset"] = _base_asset(row["pair"])
            row["hl_pair"] = hl_pair_from_any(row["pair"])
        all_trades.extend(rows)
    return all_trades


def _trade_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["base_asset"],
        row["side"],
        row["entry_tick"],
        row["entry_class"],
        row["entry_trigger"],
    )


def _compare_trades(
    binance_trades: list[dict[str, Any]],
    hl_trades: list[dict[str, Any]],
    *,
    ignore_bases: set[str],
) -> dict[str, Any]:
    def _filter(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if row["base_asset"] not in ignore_bases]

    b_rows = _filter(binance_trades)
    h_rows = _filter(hl_trades)

    b_by_key = {_trade_key(row): row for row in b_rows}
    h_by_key = {_trade_key(row): row for row in h_rows}

    matched_keys = sorted(set(b_by_key) & set(h_by_key))
    binance_only = sorted(set(b_by_key) - set(h_by_key))
    hl_only = sorted(set(h_by_key) - set(b_by_key))

    matched_diffs: list[dict[str, Any]] = []
    for key in matched_keys:
        b_row = b_by_key[key]
        h_row = h_by_key[key]
        pnl_delta = round(b_row["pnl_quote"] - h_row["pnl_quote"], 2)
        if (
            pnl_delta != 0.0
            or b_row["entry_price"] != h_row["entry_price"]
            or b_row["exit_price"] != h_row["exit_price"]
            or b_row["exit_reason"] != h_row["exit_reason"]
            or b_row["notional_quote"] != h_row["notional_quote"]
        ):
            matched_diffs.append(
                {
                    "key": {
                        "base_asset": key[0],
                        "side": key[1],
                        "entry_tick": key[2],
                        "entry_class": key[3],
                        "entry_trigger": key[4],
                    },
                    "binance_pair": b_row["pair"],
                    "hl_pair": h_row["pair"],
                    "binance": {
                        "pnl": b_row["pnl_quote"],
                        "entry_price": b_row["entry_price"],
                        "exit_price": b_row["exit_price"],
                        "exit_reason": b_row["exit_reason"],
                        "notional": b_row["notional_quote"],
                        "exit_tick": b_row["exit_tick"],
                    },
                    "hl": {
                        "pnl": h_row["pnl_quote"],
                        "entry_price": h_row["entry_price"],
                        "exit_price": h_row["exit_price"],
                        "exit_reason": h_row["exit_reason"],
                        "notional": h_row["notional_quote"],
                        "exit_tick": h_row["exit_tick"],
                    },
                    "pnl_delta": pnl_delta,
                }
            )

    def _summarize_keys(keys: list[tuple[Any, ...]], source: dict[tuple[Any, ...], dict]) -> list[dict]:
        out = []
        for key in keys:
            row = source[key]
            out.append(
                {
                    "base_asset": key[0],
                    "side": key[1],
                    "entry_tick": key[2],
                    "entry_class": key[3],
                    "entry_trigger": key[4],
                    "pair": row["pair"],
                    "pnl": row["pnl_quote"],
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "exit_reason": row["exit_reason"],
                }
            )
        return out

    b_pairs = sorted({row["base_asset"] for row in b_rows})
    h_pairs = sorted({row["base_asset"] for row in h_rows})

    return {
        "counts": {
            "binance_trades": len(b_rows),
            "hl_trades": len(h_rows),
            "matched_keys": len(matched_keys),
            "binance_only": len(binance_only),
            "hl_only": len(hl_only),
            "matched_with_differences": len(matched_diffs),
        },
        "pair_sets": {
            "binance_base_assets": b_pairs,
            "hl_base_assets": h_pairs,
            "both": sorted(set(b_pairs) & set(h_pairs)),
            "binance_only_assets": sorted(set(b_pairs) - set(h_pairs)),
            "hl_only_assets": sorted(set(h_pairs) - set(b_pairs)),
        },
        "pnl_totals": {
            "binance": round(sum(row["pnl_quote"] for row in b_rows), 2),
            "hl": round(sum(row["pnl_quote"] for row in h_rows), 2),
            "matched_binance": round(sum(b_by_key[k]["pnl_quote"] for k in matched_keys), 2),
            "matched_hl": round(sum(h_by_key[k]["pnl_quote"] for k in matched_keys), 2),
            "binance_only_pnl": round(sum(b_by_key[k]["pnl_quote"] for k in binance_only), 2),
            "hl_only_pnl": round(sum(h_by_key[k]["pnl_quote"] for k in hl_only), 2),
        },
        "binance_only_trades": _summarize_keys(binance_only, b_by_key),
        "hl_only_trades": _summarize_keys(hl_only, h_by_key),
        "matched_trade_differences": matched_diffs,
    }


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    if args.shared_universe:
        suffix = "_shared_2d_bnv_vol" if args.binance_volume else "_shared_2d"
        binance_snap = root / "data" / f"replay_snapshots_binance{suffix}"
        hl_snap = root / "data" / f"replay_snapshots_hl{suffix}"
    else:
        binance_snap = root / "data" / "replay_snapshots_binance_2d"
        hl_snap = root / "data" / "replay_snapshots_hl_2d"
    specs = [
        RunSpec(
            "binance",
            "binance_perpetual",
            "binance_candles",
            root / "data" / "binance_candles",
            binance_snap,
        ),
        RunSpec(
            "hyperliquid",
            "hyperliquid",
            "hl_candles",
            root / "data" / "hl_candles",
            hl_snap,
        ),
    ]
    ignore_bases = {_base_asset(part.strip()) for part in args.ignore_pairs.split(",") if part.strip()}

    trades_by_exchange: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        trades_by_exchange[spec.label] = await _run_simulation(spec, args)

    diff = _compare_trades(
        trades_by_exchange["binance"],
        trades_by_exchange["hyperliquid"],
        ignore_bases=ignore_bases,
    )

    return {
        "range_start": args.range_start,
        "range_end": args.range_end,
        "ignore_bases": sorted(ignore_bases),
        "trades": trades_by_exchange,
        "diff": diff,
    }


def _print_summary(payload: dict[str, Any]) -> None:
    diff = payload["diff"]
    counts = diff["counts"]
    pairs = diff["pair_sets"]
    pnl = diff["pnl_totals"]

    print("=== Trade pair sets (base assets, PURR excluded) ===")
    print(f"Binance traded: {pairs['binance_base_assets']}")
    print(f"HL traded:      {pairs['hl_base_assets']}")
    print(f"Both:           {pairs['both']}")
    print(f"Binance only:   {pairs['binance_only_assets']}")
    print(f"HL only:        {pairs['hl_only_assets']}")
    print()
    print("=== Trade key counts (side + entry_tick + class + trigger) ===")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print()
    print("=== PnL decomposition ===")
    for key, value in pnl.items():
        print(f"  {key}: ${value:+.2f}")
    print()
    if diff["binance_only_trades"]:
        print("=== Binance-only trades ===")
        for row in diff["binance_only_trades"]:
            print(
                f"  {row['base_asset']} {row['side']} tick={row['entry_tick']} "
                f"{row['entry_class']}/{row['entry_trigger']} pnl=${row['pnl']:+.2f} "
                f"({row['exit_reason']})"
            )
        print()
    if diff["hl_only_trades"]:
        print("=== HL-only trades ===")
        for row in diff["hl_only_trades"]:
            print(
                f"  {row['base_asset']} {row['side']} tick={row['entry_tick']} "
                f"{row['entry_class']}/{row['entry_trigger']} pnl=${row['pnl']:+.2f} "
                f"({row['exit_reason']})"
            )
        print()
    if diff["matched_trade_differences"]:
        print("=== Matched trades with differences ===")
        for row in diff["matched_trade_differences"]:
            key = row["key"]
            print(
                f"  {key['base_asset']} {key['side']} tick={key['entry_tick']} "
                f"{key['entry_class']}/{key['entry_trigger']} "
                f"pnl_delta=${row['pnl_delta']:+.2f}"
            )
            print(f"    binance: entry={row['binance']['entry_price']} exit={row['binance']['exit_price']} "
                  f"reason={row['binance']['exit_reason']} notional={row['binance']['notional']}")
            print(f"    hl:      entry={row['hl']['entry_price']} exit={row['hl']['exit_price']} "
                  f"reason={row['hl']['exit_reason']} notional={row['hl']['notional']}")


def main() -> int:
    args = _parse_args()
    payload = asyncio.run(_main_async(args))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / (
        "trade_diff_shared_bnv_vol.json"
        if args.shared_universe and args.binance_volume
        else "trade_diff_shared.json"
        if args.shared_universe
        else "trade_diff.json"
    )
    # trades list is large but useful; keep full payload
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print_summary(payload)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
