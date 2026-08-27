#!/usr/bin/env python3
"""CLI entry for macdbb_pullback_hl timeline backtests."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run macdbb_pullback_hl_backtest")
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default="", help="UTC ISO start")
    parser.add_argument("--range-end", default="", help="UTC ISO end")
    parser.add_argument("--snapshot-dir", default="", help="Parquet snapshot directory")
    parser.add_argument("--sessions", default="", help="Live session numbers (journal ticks)")
    parser.add_argument("--total-amount-quote", type=float, default=0.0, help="Per-entry notional")
    parser.add_argument("--frequency-sec", type=int, default=0, help="0 = preset default")
    parser.add_argument("--impulse-atr-mult", type=float, default=0.0)
    parser.add_argument("--pullback-timeout-hours", type=float, default=0.0)
    parser.add_argument("--sl-pct", type=float, default=0.0)
    parser.add_argument("--tp-pct", type=float, default=0.0)
    parser.add_argument(
        "--enable-flip-exit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable opposing-thesis flip early exits (default: preset/off)",
    )
    parser.add_argument(
        "--enable-thesis-decay-exit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable thesis-decay early exits (default: preset/off)",
    )
    parser.add_argument("--flip-confirm-ticks", type=int, default=0)
    parser.add_argument("--thesis-decay-exit-hours", type=float, default=0.0)
    parser.add_argument("--thesis-bb-drift-pts", type=float, default=0.0)
    parser.add_argument(
        "--thesis-decay-negative-grace-minutes",
        type=float,
        default=None,
        help="Minutes of red-PnL grace after decay limit (default: preset/30)",
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
    from routines.macdbb_pullback_hl_backtest import Config, run

    kwargs: dict = {"preset": args.preset}
    if args.range_start:
        kwargs["range_start_utc"] = args.range_start
    if args.range_end:
        kwargs["range_end_utc"] = args.range_end
    if args.snapshot_dir:
        kwargs["snapshot_dir"] = args.snapshot_dir
    if args.sessions:
        kwargs["sessions"] = args.sessions
    if args.total_amount_quote > 0:
        kwargs["total_amount_quote"] = args.total_amount_quote
    if args.frequency_sec > 0:
        kwargs["frequency_sec"] = args.frequency_sec
    if args.impulse_atr_mult > 0:
        kwargs["impulse_atr_mult"] = args.impulse_atr_mult
    if args.pullback_timeout_hours > 0:
        kwargs["pullback_timeout_hours"] = args.pullback_timeout_hours
    if args.sl_pct > 0:
        kwargs["sl_pct"] = args.sl_pct
    if args.tp_pct > 0:
        kwargs["tp_pct"] = args.tp_pct
    if args.enable_flip_exit is not None:
        kwargs["enable_flip_exit"] = args.enable_flip_exit
    if args.enable_thesis_decay_exit is not None:
        kwargs["enable_thesis_decay_exit"] = args.enable_thesis_decay_exit
    if args.flip_confirm_ticks > 0:
        kwargs["flip_confirm_ticks"] = args.flip_confirm_ticks
    if args.thesis_decay_exit_hours > 0:
        kwargs["thesis_decay_exit_hours"] = args.thesis_decay_exit_hours
    if args.thesis_bb_drift_pts > 0:
        kwargs["thesis_bb_drift_pts"] = args.thesis_bb_drift_pts
    if args.thesis_decay_negative_grace_minutes is not None:
        kwargs["thesis_decay_negative_grace_minutes"] = (
            args.thesis_decay_negative_grace_minutes
        )
    config = Config(**kwargs)
    result = await run(config, MagicMock())
    text = (result.text or "").strip()
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
