"""Run reports_only session parity replay with live vs sim position comparison."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import hl_prefetch_settings_from_config, prefetch_replay_hl_prices
from routines.macdbb_scanner_aggressive_hl_replay.live_ledger import (
    compare_legs,
    extract_live_legs,
    format_comparison_report,
    parse_journal_live_pnl,
    sim_trades_to_legs,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_scanner_aggressive_hl_replay.session_config import (
    apply_policy_override,
    replay_config_from_session,
    session_has_dynamic_policy,
)
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session


def _parse_session_nums(raw: str) -> list[int]:
    sessions: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            sessions.extend(range(int(start_text), int(end_text) + 1))
        else:
            sessions.append(int(token))
    return sessions


def _policy_note(config: DynamicStrategyReplayConfig, params: dict) -> str:
    mode = "dynamic" if (
        config.enable_dynamic_sizing or config.enable_dynamic_barriers
    ) else "fixed"
    snapshot = "dynamic" if session_has_dynamic_policy(params) else "fixed"
    parts = [
        f"sim={mode}",
        f"sizing={'on' if config.enable_dynamic_sizing else 'off'}",
        f"barriers={'on' if config.enable_dynamic_barriers else 'off'}",
        f"sl={config.sl_pct}% tp={config.tp_pct}%",
    ]
    if mode != snapshot:
        parts.append(f"(session snapshot={snapshot})")
    return " | ".join(parts)


async def run_session_parity(
    session_num: int,
    *,
    policy_mode: str,
    tick_maps: dict[int, dict],
    session_configs: dict[int, DynamicStrategyReplayConfig],
    reports_by_pair: dict,
    hl_caches: dict,
    hl_candle_cache,
    hl_barrier_candle_cache,
    hl_vol_candle_cache,
    strategy_slug: str,
) -> int:
    if session_num not in tick_maps:
        print(f"session {session_num}: not loaded", file=sys.stderr)
        return 1

    session_dir = TRADING_AGENTS_DIR / strategy_slug / f"sessions/session_{session_num}"
    journal_path = session_dir / "journal.md"

    session_config = apply_policy_override(
        session_configs[session_num],
        policy_mode=policy_mode,
    )
    _, params = replay_config_from_session(
        session_dir,
        strategy_slug,
        base=session_config,
    )

    _, _, trades, summary = simulate_strategy_session(
        session_num=session_num,
        tick_meta_map=tick_maps[session_num],
        reports_by_pair=reports_by_pair,
        config=session_config,
        hl_price_cache=hl_caches.get(session_num),
        hl_candle_cache=hl_candle_cache,
        hl_barrier_candle_cache=hl_barrier_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
        replay_policy=DynamicReplayPolicy(session_config),
    )

    sim_pnl = float(summary.get("net_pnl_quote") or 0.0)
    live_pnl = parse_journal_live_pnl(journal_path)
    live_legs = extract_live_legs(journal_path, session_dir)
    sim_legs = sim_trades_to_legs(trades)
    comparisons = compare_legs(live_legs, sim_legs)

    print(
        format_comparison_report(
            session_num,
            live_pnl,
            sim_pnl,
            comparisons,
            policy_note=_policy_note(session_config, params),
        )
    )
    print(f"status={summary.get('status', 'ok')}")
    print()
    return 0


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare reports_only replay vs live journal (PnL + positions)",
    )
    parser.add_argument(
        "--strategy-slug",
        default="macdbb_scanner_aggressive_hl",
    )
    parser.add_argument(
        "--session-nums",
        default="60",
        help="Comma-separated session numbers or ranges (e.g. 58,59,60 or 58-60)",
    )
    parser.add_argument(
        "--time-window-min",
        type=int,
        default=5,
        help="Report lookback window in minutes (default 5)",
    )
    parser.add_argument(
        "--policy",
        choices=("session", "fixed", "dynamic"),
        default="session",
        help=(
            "Entry policy: session=from config.yml (fixed when enable_dynamic_* absent), "
            "fixed=force sl_pct/tp_pct/notional, dynamic=force dynamic sizing+barriers"
        ),
    )
    parser.add_argument(
        "--no-prefetch-hl",
        action="store_true",
        help="Skip Hyperliquid candle prefetch (faster, less accurate intrabar)",
    )
    args = parser.parse_args(argv)

    session_nums = _parse_session_nums(args.session_nums)
    config = DynamicStrategyReplayConfig(
        strategy_slug=args.strategy_slug,
        session_nums=",".join(str(num) for num in session_nums),
        replay_mode="session_parity",
        data_source="reports_only",
        config_source="session",
        time_window_min=args.time_window_min,
        write_csv=False,
    )
    tick_maps, session_configs, selected = load_replay_sessions(config)
    reports_by_pair = build_reports_by_pair(load_reports_index())

    hl_caches: dict = {}
    hl_candle_cache = None
    hl_barrier_candle_cache = None
    hl_vol_candle_cache = None
    if not args.no_prefetch_hl:
        subset = {num: tick_maps[num] for num in selected if num in tick_maps}
        if subset:
            hl_caches, hl_candle_cache, hl_barrier_candle_cache, hl_vol_candle_cache = (
                await prefetch_replay_hl_prices(
                    subset,
                    settings=hl_prefetch_settings_from_config(config),
                )
            )

    exit_code = 0
    for session_num in selected:
        code = await run_session_parity(
            session_num,
            policy_mode=args.policy,
            tick_maps=tick_maps,
            session_configs=session_configs,
            reports_by_pair=reports_by_pair,
            hl_caches=hl_caches,
            hl_candle_cache=hl_candle_cache,
            hl_barrier_candle_cache=hl_barrier_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            strategy_slug=args.strategy_slug,
        )
        if code != 0:
            exit_code = code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
