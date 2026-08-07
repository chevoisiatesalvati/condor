#!/usr/bin/env python3
"""CLI entry for macdbb timeline backtests with worker-style logging.

Mirrors the UI routine worker logging setup so ``logger.info`` lines reach stdout
(and any redirected log file), unlike bare one-off scripts that only ``print``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is on sys.path when invoked as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run macdbb_scanner_aggressive_hl_backtest")
    parser.add_argument("--preset", default="hl_dynamic_timeline_refine_lead_013")
    parser.add_argument("--range-start", default="", help="UTC ISO start")
    parser.add_argument("--range-end", default="", help="UTC ISO end")
    parser.add_argument("--frequency-sec", type=int, default=0, help="0 = preset default")
    parser.add_argument(
        "--progress-file",
        default="",
        help="Optional path for CONDOR_ROUTINE_PROGRESS_PATH JSON updates",
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

    if args.progress_file:
        from condor.routine_progress import PROGRESS_ENV
        import os

        os.environ[PROGRESS_ENV] = str(Path(args.progress_file).resolve())

    from routines.macdbb_scanner_aggressive_hl_backtest import Config, run

    kwargs: dict = {"preset": args.preset}
    if args.range_start:
        kwargs["range_start_utc"] = args.range_start
    if args.range_end:
        kwargs["range_end_utc"] = args.range_end
    if args.frequency_sec > 0:
        kwargs["frequency_sec"] = args.frequency_sec
    config = Config(**kwargs)

    result = await run(config, MagicMock())
    text = (result.text or "").strip()
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
