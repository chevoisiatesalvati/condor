"""Compare report-driven replay PnL vs live journal reference (parity mode)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

_SUMMARY_PNL_RE = re.compile(r"PnL:\s*\$([+-]?[0-9.]+)")


def parse_journal_live_pnl(journal_path: Path) -> float | None:
    if not journal_path.is_file():
        return None
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Status:") and "PnL:" not in line:
            continue
        match = _SUMMARY_PNL_RE.search(line)
        if match:
            return float(match.group(1))
    return None


def validate_sessions(
    *,
    strategy_slug: str = "macdbb_scanner_aggressive_hl",
    session_nums: str = "58,59,60",
    output_csv: Path | None = None,
) -> list[dict[str, str | float | int]]:
    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_session_parity",
        strategy_slug=strategy_slug,
        session_nums=session_nums,
        replay_mode="session_parity",
        data_source="reports_only",
        config_source="session",
        write_csv=False,
    )
    tick_maps, session_configs, selected = load_replay_sessions(config)
    reports_by_pair = build_reports_by_pair(load_reports_index())
    sessions_dir = TRADING_AGENTS_DIR / strategy_slug / "sessions"

    rows: list[dict[str, str | float | int]] = []
    for session_num in selected:
        tick_map = tick_maps.get(session_num, {})
        session_config = session_configs.get(session_num, config)
        policy = DynamicReplayPolicy(session_config)
        _, _, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_map,
            reports_by_pair=reports_by_pair,
            config=session_config,
            replay_policy=policy,
        )
        live_pnl = parse_journal_live_pnl(
            sessions_dir / f"session_{session_num}" / "journal.md"
        )
        sim_pnl = float(summary.get("net_pnl_quote") or sum(t.pnl_quote for t in trades))
        delta = sim_pnl - live_pnl if live_pnl is not None else 0.0
        rows.append(
            {
                "session": session_num,
                "live_pnl": live_pnl if live_pnl is not None else "",
                "sim_pnl": round(sim_pnl, 2),
                "delta": round(delta, 2) if live_pnl is not None else "",
                "sim_trades": len(trades),
                "ticks": len(tick_map),
                "status": summary.get("status", "ok"),
            }
        )

    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["session", "live_pnl", "sim_pnl", "delta", "sim_trades", "ticks", "status"]
        with output_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate session parity replay vs journal PnL")
    parser.add_argument("--strategy-slug", default="macdbb_scanner_aggressive_hl")
    parser.add_argument("--session-nums", default="58,59,60")
    parser.add_argument("--output-csv", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = validate_sessions(
        strategy_slug=args.strategy_slug,
        session_nums=args.session_nums,
        output_csv=args.output_csv,
    )
    for row in rows:
        print(
            f"session={row['session']} live={row['live_pnl']} sim={row['sim_pnl']} "
            f"delta={row['delta']} trades={row['sim_trades']} status={row['status']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
