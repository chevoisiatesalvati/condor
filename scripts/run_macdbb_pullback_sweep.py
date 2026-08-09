#!/usr/bin/env python3
"""Compact sweep of impulse/pullback/SL-TP knobs for macdbb_pullback_hl."""

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

SWEEP = [
    {"label": "baseline_v1", "impulse_atr_mult": 1.25, "pullback_timeout_hours": 12.0, "pullback_epsilon_pct": 0.35, "sl_pct": 3.0, "tp_pct": 6.0},
    {"label": "loose_pullback", "impulse_atr_mult": 1.25, "pullback_timeout_hours": 24.0, "pullback_epsilon_pct": 0.75, "sl_pct": 3.0, "tp_pct": 6.0},
    {"label": "soft_impulse", "impulse_atr_mult": 1.75, "pullback_timeout_hours": 12.0, "pullback_epsilon_pct": 0.35, "sl_pct": 3.0, "tp_pct": 6.0},
    {"label": "tight_rr", "impulse_atr_mult": 1.25, "pullback_timeout_hours": 18.0, "pullback_epsilon_pct": 0.5, "sl_pct": 2.5, "tp_pct": 5.0},
    {"label": "wide_timeout", "impulse_atr_mult": 1.5, "pullback_timeout_hours": 24.0, "pullback_epsilon_pct": 0.75, "sl_pct": 3.0, "tp_pct": 6.0},
]


async def _run_one(range_start: str, range_end: str, overrides: dict) -> dict:
    from routines.macdbb_pullback_hl_backtest import Config, run

    label = overrides.pop("label")
    config = Config(
        preset="pullback_timeline_v1_60s",
        range_start_utc=range_start,
        range_end_utc=range_end,
        **overrides,
    )
    result = await run(config, MagicMock())
    summary_path = Path("data/backtests/macdbb_pullback_hl/last_summary.json")
    payload = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    payload["label"] = label
    payload["text"] = (result.text or "").strip()
    return payload


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--range-start", default="2026-08-01T00:00:00+00:00")
    parser.add_argument("--range-end", default="2026-08-07T23:59:59+00:00")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    rows = []
    for spec in SWEEP:
        overrides = dict(spec)
        logging.info("Sweep config: %s", overrides.get("label"))
        rows.append(await _run_one(args.range_start, args.range_end, overrides))
    out = Path("data/backtests/macdbb_pullback_hl/sweep_7d.json")
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
