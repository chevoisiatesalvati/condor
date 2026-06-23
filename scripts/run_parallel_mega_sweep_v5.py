#!/usr/bin/env python3
"""Launch a single v5 mega sweep alongside other running sweeps (not staged A/B/C).

For sequential phased sweeps (A winner → B → C), use run_staged_mega_sweep_v5.py instead.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    MEGA_GRID_VERSION,
    default_min_configs_for_mode,
    _mega_dynamic_space_size,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Run one timeline mega sweep ({MEGA_GRID_VERSION}) in the background"
    )
    parser.add_argument(
        "--dynamic-mode",
        default="sizing_only",
        choices=["sizing_only", "barriers_only", "both_on", "both_keep_journal"],
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/replay_snapshots_binance_1y"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/strategy_replay_sweeps"),
    )
    parser.add_argument("--min-configs", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument(
        "--output-stem",
        default="",
        help="CSV stem override (default: includes mode + v5 + snapshot slug)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print launch plan without starting a process",
    )
    return parser.parse_args()


def _snapshot_slug(snapshot_dir: Path) -> str:
    name = snapshot_dir.name
    if name.startswith("replay_snapshots_"):
        return name.removeprefix("replay_snapshots_")
    return name.replace("/", "_")


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    runner = repo_root / "scripts" / "run_timeline_mega_sweep.py"
    slug = _snapshot_slug(args.snapshot_dir)
    min_configs = args.min_configs or default_min_configs_for_mode(args.dynamic_mode)
    stem = args.output_stem or (
        f"macdbb_scanner_aggressive_hl_backtest_{args.dynamic_mode}_mega_timeline_{MEGA_GRID_VERSION}_{slug}"
    )
    log_path = args.output_dir / f"mega_sweep_{MEGA_GRID_VERSION}_{args.dynamic_mode}_{slug}.log"
    space = _mega_dynamic_space_size(args.dynamic_mode)

    cmd = [
        str(repo_root / ".venv" / "bin" / "python"),
        "-u",
        str(runner),
        "sweep",
        "--dynamic-mode",
        args.dynamic_mode,
        "--min-configs",
        str(min_configs),
        "--seed",
        str(args.seed),
        "--top",
        str(args.top),
        "--snapshot-dir",
        str(args.snapshot_dir),
        "--output-dir",
        str(args.output_dir),
        "--output-stem",
        stem,
    ]

    print(f"=== Single v5 sweep ({MEGA_GRID_VERSION}) ===")
    print(f"  Mode     : {args.dynamic_mode}")
    print(f"  Configs  : {min_configs}  space~{space:,}")
    print(f"  Snapshot : {args.snapshot_dir}")
    print(f"  Log      : {log_path}")
    print(f"  Cmd      : {' '.join(cmd)}")

    if args.dry_run:
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    log_handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=repo_root,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    print(f"Started pid={proc.pid}")
    print(f"  tail -f {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
