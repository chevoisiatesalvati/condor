#!/usr/bin/env python3
"""Run macdbb_pullback_hl_backtest for sweep-lead presets and save Condor reports.

Used after auto-promote (one preset) and for backfilling existing leads
(``--all-leads`` hydrates the tape once).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pullback lead backtest → Condor report")
    parser.add_argument("--preset", action="append", default=[], help="Preset name (repeatable)")
    parser.add_argument(
        "--all-leads",
        action="store_true",
        help="Run every pullback_sweep_lead_* preset in presets.yaml",
    )
    parser.add_argument("--range-start", default="2026-07-18T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-17T10:20:00Z")
    parser.add_argument("--snapshot-dir", default="data/replay_snapshots_binance_60s")
    parser.add_argument("--candle-source", default="binance_perpetual")
    parser.add_argument("--total-amount-quote", type=float, default=100.0)
    parser.add_argument("--telegram-chat-id", default="")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run even when a Condor report already exists for the preset",
    )
    return parser.parse_args()


def _lead_presets_from_yaml() -> list[str]:
    from routines.macdbb_pullback_hl_replay.sweep_automation import lead_preset_names

    return lead_preset_names()


async def _send_report_telegram(chat_id: str, preset_name: str, report_id: str | None) -> None:
    from routines.macdbb_pullback_hl_replay.sweep_automation import send_report_html_telegram

    await send_report_html_telegram(chat_id, preset_name, report_id)


async def _run_one(
    preset_name: str,
    args: argparse.Namespace,
    chat_id: str | None,
) -> None:
    from routines.macdbb_pullback_hl_replay.sweep_automation import run_backtest_for_preset

    logging.info("Running pullback backtest routine for %s", preset_name)
    report_id, text = await run_backtest_for_preset(
        preset_name,
        range_start_utc=args.range_start,
        range_end_utc=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
    )
    logging.info("Report id=%s\n%s", report_id, text)
    if chat_id:
        try:
            await _send_report_telegram(chat_id, preset_name, report_id)
        except Exception:
            logging.exception("Telegram report send failed for %s", preset_name)


async def _run_tape_once(presets: list[str], args: argparse.Namespace, chat_id: str | None) -> None:
    from routines.macdbb_pullback_hl_replay.sweep_automation import save_lead_reports_from_shared
    from scripts.run_macdbb_pullback_entry_sltp_sweep import _load_shared_context

    shared = await _load_shared_context(
        preset="pullback_decay_2h_60s",
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
    )
    saved = await save_lead_reports_from_shared(
        shared,
        presets,
        total_amount_quote=args.total_amount_quote,
    )
    if chat_id:
        for preset_name, report_id in saved:
            try:
                await _send_report_telegram(chat_id, preset_name, report_id)
            except Exception:
                logging.exception("Telegram report send failed for %s", preset_name)


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    presets = list(args.preset)
    if args.all_leads:
        presets = _lead_presets_from_yaml()
    if not presets:
        logging.error("No presets given (pass --preset or --all-leads)")
        return 1
    if not args.force:
        from routines.macdbb_pullback_hl_replay.sweep_automation import (
            preset_has_backtest_report,
        )

        skipped = [name for name in presets if preset_has_backtest_report(name)]
        for name in skipped:
            logging.info("Skipping %s — report already exists", name)
        presets = [name for name in presets if name not in skipped]
    if not presets:
        logging.info("All requested presets already have Condor reports")
        return 0
    chat_id = str(args.telegram_chat_id).strip() or None
    if not chat_id:
        from routines.macdbb_pullback_hl_replay.sweep_automation import (
            default_telegram_chat_id,
        )

        chat_id = default_telegram_chat_id()
    if len(presets) == 1:
        await _run_one(presets[0], args, chat_id)
        return 0
    await _run_tape_once(presets, args, chat_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
