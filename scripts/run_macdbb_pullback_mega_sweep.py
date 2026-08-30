#!/usr/bin/env python3
"""Tape-once pullback sweep: entry-gate 2k, dynamics 5k, or lookback/ATR probe.

Hydrates the 30d ticks/candles/signal tape once. 1y verify hydrates lazily on
the first screen leader and is kept for later verifies. Checkpoints every N
cases. Promotes capital-normalized PnL improvements after a positive 1y
verify into pullback_sweep_lead_NNN without changing the live winner default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

CHECKPOINT_EVERY = 10


def include_probe_windows_for_grid(grid: str) -> bool:
    return str(grid) == "probe"


def log_process_rss(label: str) -> None:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            fields: dict[str, str] = {}
            for line in handle:
                if line.startswith("VmRSS:") or line.startswith("RssAnon:"):
                    key, value = line.split(":", 1)
                    fields[key] = value.strip()
        logging.info(
            "RAM %s | %s",
            label,
            " ".join(f"{key}={value}" for key, value in fields.items()),
        )
    except OSError:
        logging.info("RAM %s | unavailable", label)


def run_coro_blocking(coro_factory: Any) -> Any:
    """Run an async factory from a sync callback while the main loop is blocked."""
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:
            error["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def verify_tape_policy(*, verify_enabled: bool, coverage_ok: bool) -> str:
    if not verify_enabled:
        return "disabled"
    if not coverage_ok:
        return "refuse"
    return "lazy"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pullback mega-sweep (random 2k / dynamics 5k / lookback-ATR probe)"
    )
    parser.add_argument(
        "--grid",
        choices=("entry", "dynamics", "probe"),
        default="entry",
        help=(
            "entry = 2k entry/SL/TP grid; dynamics = decay/ATR/epsilon/TP grid; "
            "probe = 9-cell lookback × ATR on the current lead"
        ),
    )
    parser.add_argument(
        "--gate-dry-run",
        action="store_true",
        help="Run the 3-config dynamics gate (decay-off, decay-2h, dynamic-on)",
    )
    parser.add_argument("--preset", default="pullback_decay_2h_60s")
    parser.add_argument("--range-start", default="2026-07-18T00:00:00Z")
    parser.add_argument("--range-end", default="2026-08-17T10:20:00Z")
    parser.add_argument(
        "--snapshot-dir",
        default="data/replay_snapshots_binance_60s",
    )
    parser.add_argument("--candle-source", default="binance_perpetual")
    parser.add_argument("--total-amount-quote", type=float, default=100.0)
    parser.add_argument(
        "--out-dir",
        default="",
        help=(
            "Default: pullback_mega_sweep (entry), "
            "pullback_dynamics_sweep_lead_NNN (dynamics), or "
            "pullback_lookback_atr_probe_lead_NNN (probe)"
        ),
    )
    parser.add_argument(
        "--min-configs",
        "--configs",
        dest="min_configs",
        type=int,
        default=0,
        help=(
            "Number of configs to draw. 0 uses the grid default "
            "(2000 entry, 5000 dynamics, probe cell count). "
            "Same override pattern as --seed."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=-1,
        help=(
            "RNG seed. Negative uses the grid default "
            "(auto from latest sweep lead for dynamics/probe). "
            "Pass an explicit value to override."
        ),
    )
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
        "--no-verify",
        action="store_true",
        help="Promote from the sweep window only (legacy 30d cap-norm path)",
    )
    parser.add_argument(
        "--verify-range-start",
        default="",
        help="UTC start for 1y consistency verify (default: 1y before --range-end)",
    )
    parser.add_argument(
        "--verify-range-end",
        default="",
        help="UTC end for 1y consistency verify (default: same as --range-end)",
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
    parser.add_argument(
        "--telegram-chat-id",
        default="",
        help="Telegram chat id (default: ADMIN_USER_ID or SWEEP_TELEGRAM_CHAT_ID from .env)",
    )
    return parser.parse_args()


def _resolve_grid_defaults(args: argparse.Namespace) -> argparse.Namespace:
    from routines.macdbb_pullback_hl_replay.dynamics_sweep import (
        DYNAMICS_SWEEP_CONFIG_COUNT,
        dynamics_sweep_out_dir,
        dynamics_sweep_seed,
        lookback_atr_probe_out_dir,
        lookback_atr_probe_space_size,
    )
    from routines.macdbb_pullback_hl_replay.mega_sweep import MEGA_SWEEP_SEED

    presets_path = args.presets_path or None
    grid = "dynamics" if args.gate_dry_run else args.grid
    args.grid = grid
    if not args.out_dir:
        if grid == "dynamics":
            args.out_dir = dynamics_sweep_out_dir(presets_path=presets_path)
        elif grid == "probe":
            args.out_dir = lookback_atr_probe_out_dir(presets_path=presets_path)
        else:
            args.out_dir = "data/backtests/pullback_mega_sweep"
        if args.gate_dry_run:
            args.out_dir = "data/backtests/pullback_dynamics_gate"
    if args.min_configs <= 0:
        if grid == "dynamics":
            args.min_configs = DYNAMICS_SWEEP_CONFIG_COUNT
        elif grid == "probe":
            args.min_configs = lookback_atr_probe_space_size()
        else:
            args.min_configs = 2000
    if args.seed < 0:
        args.seed = (
            dynamics_sweep_seed(presets_path=presets_path)
            if grid in ("dynamics", "probe")
            else MEGA_SWEEP_SEED
        )
    return args


def _rank_metric(row: dict[str, Any]) -> float:
    stats = row.get("stats") or {}
    if "capital_normalized_pnl" in stats:
        return float(stats.get("capital_normalized_pnl") or 0)
    return float(stats.get("net_pnl_quote") or 0)


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(rows, key=_rank_metric, reverse=True)
    headers = [
        "case",
        "cap_norm",
        "ann_cap",
        "pnl",
        "avg_n",
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
        raw_pnl = float(stats.get("net_pnl_quote") or 0)
        cap_norm = float(stats.get("capital_normalized_pnl", raw_pnl) or 0)
        annualized = stats.get("annualized_cap_norm")
        ann_cell = f"{float(annualized):+.2f}" if annualized is not None else "—"
        avg_n = stats.get("avg_notional")
        avg_n_cell = f"{float(avg_n):.1f}" if avg_n is not None else "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    row["name"],
                    f"{cap_norm:+.2f}",
                    ann_cell,
                    f"{raw_pnl:+.2f}",
                    avg_n_cell,
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
        "grid": getattr(args, "grid", "entry"),
        "seed": getattr(args, "seed", None),
        "tick_count": tick_count,
        "case_count": len(results),
        "verify_range_start": getattr(args, "verify_range_start", "") or None,
        "verify_range_end": getattr(args, "verify_range_end", "") or None,
        "verify_enabled": not bool(getattr(args, "no_verify", False)),
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
        key=_rank_metric,
        reverse=True,
    )
    leader = ranked[0] if ranked else None
    title = (
        "Pullback dynamics sweep"
        if getattr(args, "grid", "entry") == "dynamics"
        else "Pullback mega-sweep"
    )
    md = (
        f"# {title}\n\n"
        f"Range: `{args.range_start}` → `{args.range_end}`  \n"
        f"Preset: `{args.preset}`  \n"
        f"Snapshot: `{args.snapshot_dir}`  \n"
        f"Budget: `{args.total_amount_quote}`  \n"
        f"Grid: `{getattr(args, 'grid', 'entry')}`  \n"
        f"Cases: `{len(results)}`  \n"
        f"Verify: `{getattr(args, 'verify_range_start', '') or 'off'}` → "
        f"`{getattr(args, 'verify_range_end', '') or 'off'}`\n\n"
    )
    if leader:
        stats = leader["stats"]
        cap = stats.get("capital_normalized_pnl", stats.get("net_pnl_quote"))
        md += (
            f"Leader: `{leader['name']}`  "
            f"cap_norm=`{float(cap):+.2f}`  "
            f"pnl=`{stats['net_pnl_quote']:+.2f}`  "
            f"avg_n=`{stats.get('avg_notional', '—')}`  "
            f"trades=`{stats['trades']}`\n\n"
        )
    md += _markdown_table(results) + "\n"
    (out_dir / "comparison.md").write_text(md, encoding="utf-8")


def _result_to_sweep(
    row: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
):
    from routines.macdbb_pullback_hl_replay.sweep_automation import PullbackSweepResult

    stats = row.get("stats") or {}
    raw_pnl = float(stats.get("net_pnl_quote") or 0)
    cap_norm = stats.get("capital_normalized_pnl")
    rank_pnl = float(cap_norm) if cap_norm is not None else raw_pnl
    annualized = stats.get("annualized_cap_norm")
    source_overrides = overrides if overrides is not None else row.get("config") or {}
    return PullbackSweepResult(
        name=str(row["name"]),
        pnl=rank_pnl,
        trades=int(stats.get("trades") or 0),
        overrides=dict(source_overrides),
        stats=dict(stats),
        annualized_cap_norm=float(annualized) if annualized is not None else None,
    )


def _case_from_row(row: dict[str, Any]) -> dict[str, Any]:
    case = dict(row.get("config") or {})
    case["name"] = row["name"]
    return case


def _emit_promote_job(job, *, chat_id: str | None) -> None:
    if job is None:
        return
    if chat_id:
        try:
            from routines.macdbb_pullback_hl_replay.sweep_automation import (
                send_promote_telegram_sync,
            )

            send_promote_telegram_sync(chat_id, job)
        except Exception:
            logging.exception("Telegram promote failed for %s", job.preset_name)
    else:
        logging.warning("Promoted %s but Telegram chat id is unset", job.preset_name)


async def _main() -> int:
    from scripts.run_macdbb_pullback_entry_sltp_sweep import _load_shared_context
    from routines.macdbb_pullback_hl_replay.dynamics_sweep import (
        dynamics_sweep_preset,
        gate_dry_run_cases,
        lookback_atr_probe_cases,
        pullback_dynamics_cases,
    )
    from routines.macdbb_pullback_hl_replay.mega_sweep import (
        MEGA_SWEEP_PRESET,
        pullback_mega_cases,
    )
    from routines.macdbb_pullback_hl_replay.mega_sweep_runner import (
        load_completed_results,
        run_case_batch,
        run_one_case,
    )
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        LeaderTracker,
        consider_and_promote,
        default_telegram_chat_id,
        default_verify_range_start,
        REPORT_WINDOW_SWEEP_30D,
        REPORT_WINDOW_TITLES,
        REPORT_WINDOW_VERIFY_1Y,
        lead_presets_missing_reports,
        load_verify_result,
        promote_leader,
        save_lead_reports_from_shared,
        save_verify_result,
        send_report_html_telegram,
        verify_range_coverage_gap,
    )

    args = _resolve_grid_defaults(_parse_args())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    presets_path = Path(args.presets_path) if args.presets_path else None
    preset_default = (
        dynamics_sweep_preset(presets_path=presets_path)
        if args.grid in ("dynamics", "probe")
        else MEGA_SWEEP_PRESET
    )
    if args.preset == "pullback_decay_2h_60s":
        args.preset = preset_default
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.automation_state) if args.automation_state else (
        out_dir / "automation.json"
    )
    tracker = LeaderTracker(state_path, presets_path=presets_path)
    chat_id = None
    if not args.no_promote:
        chat_id = str(args.telegram_chat_id).strip() or default_telegram_chat_id()
    if args.no_promote:
        logging.info("Auto-promote disabled")
    else:
        logging.info("Auto-promote enabled | telegram=%s", "yes" if chat_id else "no")
        if not chat_id:
            logging.warning(
                "ADMIN_USER_ID / SWEEP_TELEGRAM_CHAT_ID not set; "
                "lead presets will still be written but Telegram will be skipped"
            )

    if not args.verify_range_end:
        args.verify_range_end = args.range_end
    if not args.verify_range_start:
        args.verify_range_start = default_verify_range_start(args.verify_range_end)
    verify_enabled = not args.no_verify and not args.no_promote
    if args.no_verify:
        logging.info("1y verify disabled; promote uses sweep-window cap-norm")
    elif args.no_promote:
        logging.info("1y verify skipped because promote is disabled")
    else:
        logging.info(
            "1y verify enabled | %s → %s",
            args.verify_range_start,
            args.verify_range_end,
        )

    if args.gate_dry_run:
        cases = gate_dry_run_cases(presets_path=presets_path)
        logging.info("Dynamics gate dry-run: %d cases", len(cases))
    elif args.grid == "dynamics":
        cases = pullback_dynamics_cases(
            min_configs=args.min_configs,
            seed=args.seed,
            presets_path=presets_path,
        )
        logging.info(
            "Dynamics-sweep grid: %d cases (seed=%d, baseline=%s)",
            len(cases),
            args.seed,
            args.preset,
        )
    elif args.grid == "probe":
        cases = lookback_atr_probe_cases(presets_path=presets_path)
        logging.info(
            "Lookback/ATR probe: %d cases (baseline=%s)",
            len(cases),
            args.preset,
        )
    else:
        cases = pullback_mega_cases(min_configs=args.min_configs, seed=args.seed)
        logging.info("Mega-sweep grid: %d cases (seed=%d)", len(cases), args.seed)

    shared = await _load_shared_context(
        preset=args.preset,
        range_start=args.range_start,
        range_end=args.range_end,
        snapshot_dir=args.snapshot_dir,
        candle_source=args.candle_source,
        total_amount_quote=args.total_amount_quote,
        include_probe_windows=include_probe_windows_for_grid(args.grid),
    )
    log_process_rss("after 30d sweep hydrate")

    verify_shared: dict[str, Any] | None = None
    coverage_ok = True
    if verify_enabled:
        coverage_gap = verify_range_coverage_gap(
            args.snapshot_dir,
            args.verify_range_start,
            args.verify_range_end,
        )
        if coverage_gap is not None:
            logging.error(
                "Refusing 1y verify/promote: snapshot coverage gap "
                "%s → %s (%.1fd). Manifest covers %s → %s. "
                "Finish the 60s year backfill before promoting.",
                coverage_gap.gap_start_utc,
                coverage_gap.gap_end_utc,
                coverage_gap.gap_days,
                coverage_gap.coverage_start_utc,
                coverage_gap.coverage_end_utc,
            )
            verify_enabled = False
            coverage_ok = False
        else:
            logging.info(
                "1y verify deferred until a screen leader "
                "(policy=%s)",
                verify_tape_policy(
                    verify_enabled=True,
                    coverage_ok=coverage_ok,
                ),
            )

    results, pending = load_completed_results(
        cases, out_dir, force=args.force
    )
    logging.info("Resume: %d done, %d pending", len(results), len(pending))

    async def _hydrate_verify_shared(
        case_row: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        nonlocal verify_shared
        if not verify_enabled:
            return None
        if verify_shared is not None:
            return verify_shared
        config = (case_row or {}).get("config") or {}
        logging.info("Hydrating 1y verify tape (lazy, parent only, not worker _SHARED)...")
        verify_shared = await _load_shared_context(
            preset=args.preset,
            range_start=args.verify_range_start,
            range_end=args.verify_range_end,
            snapshot_dir=args.snapshot_dir,
            candle_source=args.candle_source,
            total_amount_quote=args.total_amount_quote,
            include_probe_windows=False,
            impulse_lookback_bars=config.get("impulse_lookback_bars"),
            atr_period=config.get("atr_period"),
        )
        log_process_rss("after 1y verify hydrate")
        return verify_shared

    def _ensure_verify_shared_blocking(
        case_row: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not verify_enabled:
            return None
        if verify_shared is not None:
            return verify_shared
        return run_coro_blocking(lambda: _hydrate_verify_shared(case_row))

    async def _write_window_lead_reports(
        *,
        window_tag: str,
        report_shared: dict[str, Any] | None,
        tape_label: str,
        reason: str,
    ) -> None:
        if report_shared is None:
            logging.info(
                "Deferring %s lead reports until tape is ready (%s)",
                tape_label,
                reason,
            )
            return
        missing = lead_presets_missing_reports(window_tag=window_tag)
        if not missing:
            logging.info("No missing %s lead reports (%s)", tape_label, reason)
            return
        logging.info(
            "Writing Condor reports for %d lead(s) on the %s (%s)",
            len(missing),
            tape_label,
            reason,
        )
        saved = await save_lead_reports_from_shared(
            report_shared,
            missing,
            total_amount_quote=args.total_amount_quote,
            window_tag=window_tag,
        )
        window_label = REPORT_WINDOW_TITLES.get(window_tag, window_tag)
        if chat_id:
            for preset_name, report_id in saved:
                try:
                    await send_report_html_telegram(
                        chat_id,
                        preset_name,
                        report_id,
                        window_label=window_label,
                    )
                except Exception:
                    logging.exception(
                        "Telegram report send failed for %s", preset_name
                    )

    async def _write_missing_lead_reports(reason: str) -> None:
        await _write_window_lead_reports(
            window_tag=REPORT_WINDOW_SWEEP_30D,
            report_shared=shared,
            tape_label="30d sweep tape",
            reason=reason,
        )
        if not verify_enabled:
            return
        if verify_shared is None:
            logging.info(
                "Deferring 1y lead reports until verify tape is hydrated (%s)",
                reason,
            )
            return
        await _write_window_lead_reports(
            window_tag=REPORT_WINDOW_VERIFY_1Y,
            report_shared=verify_shared,
            tape_label="1y verify tape",
            reason=reason,
        )

    def _verify_and_maybe_promote(screen_row: dict[str, Any]) -> None:
        if verify_shared is None:
            return
        name = str(screen_row["name"])
        verify_row = load_verify_result(out_dir, name)
        if verify_row is None:
            logging.info("Running 1y verify for %s", name)
            verify_row = run_one_case(_case_from_row(screen_row), verify_shared)
            save_verify_result(out_dir, verify_row)
        else:
            logging.info("Reusing persisted 1y verify for %s", name)
        verify_stats = verify_row.get("stats") or {}
        logging.info(
            "Verify %s trades=%s cap_norm=%s annualized=%s",
            name,
            verify_stats.get("trades"),
            verify_stats.get("capital_normalized_pnl"),
            verify_stats.get("annualized_cap_norm"),
        )
        try:
            job = tracker.consider_verified(
                _result_to_sweep(
                    verify_row,
                    overrides=screen_row.get("config") or {},
                )
            )
            if job is not None:
                promote_leader(job, presets_path=presets_path)
            _emit_promote_job(job, chat_id=chat_id)
        except Exception:
            logging.exception("Verify promote failed for %s", name)

    def _legacy_promote(result: dict[str, Any]) -> None:
        try:
            job = consider_and_promote(
                tracker,
                _result_to_sweep(result),
                presets_path=presets_path,
            )
        except Exception:
            logging.exception("Promote failed for %s", result.get("name"))
            job = None
        _emit_promote_job(job, chat_id=chat_id)

    if (
        verify_enabled
        and tracker.state.pending_verify_name
    ):
        pending_name = tracker.state.pending_verify_name
        pending_row = next(
            (row for row in results if row.get("name") == pending_name),
            None,
        )
        if pending_row is not None:
            logging.info("Resuming pending 1y verify for %s", pending_name)
            await _hydrate_verify_shared(pending_row)
            _verify_and_maybe_promote(pending_row)

    if not args.no_promote:
        await _write_missing_lead_reports("existing leads before workers")

    def _on_result(done_in_batch: int, result: dict[str, Any]) -> None:
        result_path = out_dir / f"{result['name']}.json"
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append(result)
        stats = result.get("stats") or {}
        logging.info(
            "[%d/%d] %s trades=%s pnl=%s cap_norm=%s avg_n=%s",
            len(results),
            len(cases),
            result["name"],
            stats.get("trades"),
            stats.get("net_pnl_quote"),
            stats.get("capital_normalized_pnl"),
            stats.get("avg_notional"),
        )
        if not args.no_promote:
            if verify_enabled:
                try:
                    if tracker.consider_screen(_result_to_sweep(result)):
                        _ensure_verify_shared_blocking(result)
                        _verify_and_maybe_promote(result)
                except Exception:
                    logging.exception("Screen/verify failed for %s", result.get("name"))
            elif not args.no_verify:
                logging.warning(
                    "Skipping promote for %s: 1y verify tape was not hydrated",
                    result.get("name"),
                )
            else:
                _legacy_promote(result)
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

    log_process_rss("before workers")
    if pending:
        run_case_batch(
            pending,
            shared,
            workers=args.workers,
            on_result=_on_result,
        )

    _write_comparison(
        out_dir,
        args=args,
        tick_count=int(shared.get("tick_count") or 0),
        results=results,
    )
    if not args.no_promote:
        await _write_missing_lead_reports("new leads after workers")
    print((out_dir / "comparison.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
