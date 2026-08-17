#!/usr/bin/env python3
"""2k-config random sweep for macdbb_pullback_hl on a shared 60s tape.

Hydrates ticks/candles/signal tape once. Checkpoints every N cases. Promotes
strict PnL improvements after a positive anchor into pullback_sweep_lead_NNN
without changing the live winner default.
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

CHECKPOINT_EVERY = 10


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pullback mega-sweep (random 2k)")
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default="2026-07-18T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-17T23:59:59Z")
    parser.add_argument(
        "--snapshot-dir",
        default="data/replay_snapshots_binance_60s",
    )
    parser.add_argument("--candle-source", default="binance_perpetual")
    parser.add_argument("--total-amount-quote", type=float, default=100.0)
    parser.add_argument(
        "--out-dir",
        default="data/backtests/pullback_mega_sweep",
    )
    parser.add_argument("--min-configs", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=CHECKPOINT_EVERY)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run cases that already have a JSON result",
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Do not write sweep-lead presets or send Telegram",
    )
    parser.add_argument(
        "--presets-path",
        default="",
        help="Override presets.yaml path (tests / dry-run)",
    )
    parser.add_argument(
        "--automation-state",
        default="",
        help="Leader tracker JSON path (default: <out-dir>/automation.json)",
    )
    return parser.parse_args()


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
    for row in ranked[:50]:
        stats = row["stats"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    f"{stats['net_pnl_quote']:+.2f}",
                    str(stats["trades"]),
                    str(stats["immediate"]),
                    str(stats["pullback"]),
                    f"{stats['win_rate_pct']:.1f}",
                    str(stats["sl_hits"]),
                    str(stats["tp_hits"]),
                    str(stats["thesis_decay"]),
                    f"{stats['avg_return_pct']:+.3f}",
                ]
            )
            + " |"
        )
    if len(ranked) > 50:
        lines.append(f"\n_{len(ranked) - 50} more cases in comparison.json._")
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
        "case_count": len(results),
        "cases": [
            {"name": row["name"], "config": row.get("config"), "stats": row.get("stats")}
            for row in results
        ],
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    ranked = sorted(
        [row for row in results if "stats" in row],
        key=lambda row: float((row.get("stats") or {}).get("net_pnl_quote") or 0),
        reverse=True,
    )
    leader = ranked[0] if ranked else None
    md = (
        f"# Pullback mega-sweep\n\n"
        f"Range: `{args.range_start}` → `{args.range_end}`  \n"
        f"Preset: `{args.preset}`  \n"
        f"Snapshot: `{args.snapshot_dir}`  \n"
        f"Notional: `{args.total_amount_quote}`  \n"
        f"Cases: `{len(results)}`\n\n"
    )
    if leader:
        md += (
            f"Leader: `{leader['name']}`  "
            f"pnl=`{leader['stats']['net_pnl_quote']:+.2f}`  "
            f"trades=`{leader['stats']['trades']}`\n\n"
        )
    md += _markdown_table(results) + "\n"
    (out_dir / "comparison.md").write_text(md, encoding="utf-8")


def _result_to_sweep(row: dict[str, Any]):
    from routines.macdbb_pullback_hl_replay.sweep_automation import PullbackSweepResult

    stats = row.get("stats") or {}
    return PullbackSweepResult(
        name=str(row["name"]),
        pnl=float(stats.get("net_pnl_quote") or 0),
        trades=int(stats.get("trades") or 0),
        overrides=dict(row.get("config") or {}),
        stats=dict(stats),
    )


async def _main() -> int:
    from scripts.run_macdbb_pullback_entry_sltp_sweep import _load_shared_context
    from routines.macdbb_pullback_hl_replay.mega_sweep import (
        MEGA_SWEEP_PRESET,
        pullback_mega_cases,
    )
    from routines.macdbb_pullback_hl_replay.mega_sweep_runner import run_case_batch
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        LeaderTracker,
        consider_and_promote,
        default_telegram_chat_id,
        send_promote_telegram,
    )

    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    if args.preset == "pullback_decay_2h_60s":
        args.preset = MEGA_SWEEP_PRESET
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    presets_path = Path(args.presets_path) if args.presets_path else None
    state_path = Path(args.automation_state) if args.automation_state else (
        out_dir / "automation.json"
    )
    tracker = LeaderTracker(state_path)
    chat_id = None if args.no_promote else default_telegram_chat_id()

    cases = pullback_mega_cases(min_configs=args.min_configs, seed=args.seed)
    logging.info("Mega-sweep grid: %d cases (seed=%d)", len(cases), args.seed)

    shared = await _load_shared_context(
        preset=args.preset,
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
    )

    results: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for case in cases:
        result_path = out_dir / f"{case['name']}.json"
        if result_path.is_file() and not args.force:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            results.append(loaded)
            continue
        pending.append(case)

    logging.info("Resume: %d done, %d pending", len(results), len(pending))

    loop = asyncio.get_running_loop()
    telegram_tasks: list[asyncio.Task[None]] = []

    def _on_result(done_in_batch: int, result: dict[str, Any]) -> None:
        result_path = out_dir / f"{result['name']}.json"
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append(result)
        stats = result.get("stats") or {}
        logging.info(
            "[%d/%d] %s trades=%s pnl=%s",
            len(results),
            len(cases),
            result["name"],
            stats.get("trades"),
            stats.get("net_pnl_quote"),
        )
        if not args.no_promote:
            job = consider_and_promote(
                tracker,
                _result_to_sweep(result),
                presets_path=presets_path,
            )
            if job is not None and chat_id:
                telegram_tasks.append(
                    loop.create_task(send_promote_telegram(chat_id, job))
                )
        if (
            done_in_batch % max(1, args.checkpoint_every) == 0
            or len(results) == len(cases)
        ):
            _write_comparison(
                out_dir,
                args=args,
                tick_count=int(shared.get("tick_count") or 0),
                results=results,
            )

    if pending:
        run_case_batch(
            pending,
            shared,
            workers=args.workers,
            on_result=_on_result,
        )
        if telegram_tasks:
            await asyncio.gather(*telegram_tasks, return_exceptions=True)

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
