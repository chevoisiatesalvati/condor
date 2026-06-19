#!/usr/bin/env python3
"""Run timeline backtests on Binance vs HL candle caches and compare results."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.presets import resolve_config_with_preset
from routines.strategy_replay_backtest_dynamic_amount import run as run_dynamic_replay


@dataclass(frozen=True)
class ExchangeRunSpec:
    label: str
    candle_source: str
    price_source: str
    cache_dir: Path
    snapshot_dir: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare timeline backtests using Binance vs HL candle caches.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Lookback days when range start/end omitted (default: 2)",
    )
    parser.add_argument(
        "--range-start",
        default="",
        help="UTC start ISO (default: --days ago)",
    )
    parser.add_argument(
        "--range-end",
        default="",
        help="UTC end ISO (default: now)",
    )
    parser.add_argument(
        "--frequency-sec",
        type=int,
        default=1800,
        help="Synthetic tick interval (default: 1800)",
    )
    parser.add_argument(
        "--preset",
        default="hl_dynamic_timeline_mega_best",
        help="Strategy preset name",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/exchange_backtest_compare"),
        help="Directory for comparison JSON output",
    )
    return parser.parse_args()


def _default_range(*, days: int = 2) -> tuple[str, str]:
    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=days)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _extract_metrics(result: Any) -> dict[str, Any]:
    text = result.text if hasattr(result, "text") else str(result)
    metrics: dict[str, Any] = {"summary_text": text}
    for line in text.splitlines():
        if line.startswith("Sim trades:"):
            trade_match = re.search(r"Sim trades:\s*(\d+)", line)
            if trade_match:
                metrics["total_trades"] = int(trade_match.group(1))
            for part in line.split("|"):
                part = part.strip()
                if part.startswith("Win rate:"):
                    metrics["win_rate"] = part.split(":", 1)[1].strip()
                if part.startswith("Sim PnL:"):
                    metrics["net_pnl"] = part.split(":", 1)[1].strip()
        if line.startswith("Capital-norm PnL:"):
            metrics["capital_norm_pnl"] = line.split(":", 1)[1].split("(", 1)[0].strip()
        if line.startswith("Ticks replayed:"):
            metrics["ticks_replayed"] = int(line.split("|")[0].split()[-1])
    return metrics


def _resolve_range(args: argparse.Namespace) -> tuple[str, str]:
    default_start, default_end = _default_range(days=args.days)
    return args.range_start or default_start, args.range_end or default_end


async def _run_one(spec: ExchangeRunSpec, args: argparse.Namespace) -> dict[str, Any]:
    range_start, range_end = _resolve_range(args)
    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(
            preset=args.preset,
            replay_mode="timeline_backtest",
            data_source="snapshots",
            range_start_utc=range_start,
            range_end_utc=range_end,
            frequency_sec=args.frequency_sec,
            write_csv=False,
            require_price_data=True,
        )
    )
    config = config.model_copy(
        update={
            "preset": "custom",
            "candle_source": spec.candle_source,
            "price_source": spec.price_source,
            "hl_cache_dir": str(spec.cache_dir),
            "snapshot_dir": str(spec.snapshot_dir),
        }
    )
    context = MagicMock()
    result = await run_dynamic_replay(config, context)
    result_text = result.text if hasattr(result, "text") else str(result)
    if isinstance(result, str):
        return {
            "label": spec.label,
            "status": "error" if "No session" in result or "requires" in result else "ok",
            "metrics": _extract_metrics(result),
            "error": result if "Sim trades:" not in result else None,
        }
    return {
        "label": spec.label,
        "status": "ok",
        "metrics": _extract_metrics(result),
    }


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    specs = [
        ExchangeRunSpec(
            label="binance",
            candle_source="binance_perpetual",
            price_source="binance_candles",
            cache_dir=root / "data" / "binance_candles",
            snapshot_dir=root / "data" / "replay_snapshots_binance_2d",
        ),
        ExchangeRunSpec(
            label="hyperliquid",
            candle_source="hyperliquid",
            price_source="hl_candles",
            cache_dir=root / "data" / "hl_candles",
            snapshot_dir=root / "data" / "replay_snapshots_hl_2d",
        ),
    ]
    range_start, range_end = _resolve_range(args)
    results: dict[str, Any] = {
        "range_start": range_start,
        "range_end": range_end,
        "frequency_sec": args.frequency_sec,
        "runs": {},
    }
    for spec in specs:
        logging.info("Running timeline backtest: %s", spec.label)
        results["runs"][spec.label] = await _run_one(spec, args)
    return results


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    payload = asyncio.run(_main_async(args))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "comparison.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== Exchange backtest comparison ===")
    print(f"Range: {payload['range_start']} -> {payload['range_end']}")
    for label, row in payload["runs"].items():
        print(f"\n--- {label} ---")
        if row.get("error"):
            print(row["error"])
            continue
        metrics = row.get("metrics", {})
        for key in ("ticks_replayed", "total_trades", "win_rate", "net_pnl", "capital_norm_pnl"):
            if key in metrics:
                print(f"{key}: {metrics[key]}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
