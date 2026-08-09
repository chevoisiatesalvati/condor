#!/usr/bin/env python3
"""Phase A: live creates × nearest Binance snapshot membership × price coverage."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-start", default="20260806")
    parser.add_argument("--day-end", default="20260807")
    parser.add_argument(
        "--snapshot-dir",
        default="data/replay_snapshots_binance_60s",
    )
    parser.add_argument(
        "--ticks-root",
        default="data/strategy_runs/macdbb_scanner_aggressive_hl/ticks",
    )
    parser.add_argument(
        "--output",
        default="data/backtests/parity_aug6_7/universe_miss_report.json",
    )
    parser.add_argument("--window-min", type=int, default=5)
    return parser.parse_args()


def _hl_to_usdt(pair: str) -> str:
    pair = pair.strip()
    if pair.endswith("-USD"):
        return pair[:-4] + "-USDT"
    return pair


def _load_live_creates(ticks_root: Path, day_start: str, day_end: str) -> list[dict]:
    days = []
    start = datetime.strptime(day_start, "%Y%m%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(day_end, "%Y%m%d").replace(tzinfo=timezone.utc)
    cur = start
    while cur <= end:
        days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    rows: list[dict] = []
    for day in days:
        day_dir = ticks_root / day
        if not day_dir.is_dir():
            continue
        for path in sorted(day_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                apply = obj.get("apply") or {}
                ids = apply.get("created_ids") or []
                if not ids:
                    continue
                decide = obj.get("decide") or {}
                scores = decide.get("scores") or {}
                pairs = list(scores.keys()) or list((obj.get("pending_pairs") or []))
                if not pairs and obj.get("best_candidate"):
                    pairs = [str(obj["best_candidate"])]
                # Prefer decide.scores keys; fall back to pending_pairs list field.
                if not pairs:
                    pending = obj.get("pending_pairs")
                    if isinstance(pending, list):
                        pairs = [str(p) for p in pending]
                    elif isinstance(pending, str) and pending:
                        pairs = [p for p in pending.split(",") if p]
                ts = obj.get("ts") or obj.get("tick_time_utc") or obj.get("timestamp")
                # Infer entry class from score magnitude (formal ~100+, adaptive ~1-3).
                entry_class = ""
                if scores:
                    top_score = max(float(v) for v in scores.values())
                    entry_class = (
                        "formal" if top_score >= 50 else "regime_adaptive_half_size"
                    )
                for pair in pairs or list(
                    (obj.get("pending_pairs") or [])[:1]
                ) or ["UNKNOWN"]:
                    rows.append(
                        {
                            "day": day,
                            "tick_file": path.name,
                            "tick_time_utc": ts,
                            "pair_hl": str(pair),
                            "pair_usdt": _hl_to_usdt(str(pair)),
                            "created_ids": list(ids),
                            "entry_class": entry_class,
                            "score": float(scores.get(pair, 0) or 0) if scores else 0.0,
                        }
                    )
    return rows


def _nearest_macdbb_pairs(
    macdbb: pd.DataFrame,
    ts: pd.Timestamp,
    window_min: int,
) -> tuple[set[str], str | None, float | None]:
    if macdbb.empty:
        return set(), None, None
    delta = (macdbb["tick_ts_iso"] - ts).abs()
    idx = int(delta.argmin())
    nearest_ts = macdbb.iloc[idx]["tick_ts_iso"]
    gap_min = abs((nearest_ts - ts).total_seconds()) / 60.0
    if gap_min > float(window_min):
        return set(), str(nearest_ts), gap_min
    subset = macdbb[macdbb["tick_ts_iso"] == nearest_ts]
    return set(subset["pair"].astype(str)), str(nearest_ts), gap_min


def _binance_1m_exists(pair_usdt: str, cache_dir: Path) -> bool:
    from routines.lib.binance_candle_cache import cache_path

    path = cache_path(pair_usdt, "1m", cache_dir=cache_dir)
    return path.is_file() and path.stat().st_size > 0


def main() -> int:
    args = _parse_args()
    creates = _load_live_creates(Path(args.ticks_root), args.day_start, args.day_end)
    snap_dir = Path(args.snapshot_dir)
    macdbb = pq.read_table(snap_dir / "macdbb.parquet").to_pandas()
    macdbb["tick_ts_iso"] = pd.to_datetime(macdbb["tick_ts_iso"], utc=True)
    cache_dir = Path("data/binance_candles")

    annotated = []
    in_snap = 0
    has_price = 0
    for row in creates:
        ts_raw = row["tick_time_utc"]
        try:
            ts = pd.Timestamp(ts_raw)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
        except Exception:
            ts = pd.NaT
        snap_pairs, nearest, gap = (
            _nearest_macdbb_pairs(macdbb, ts, args.window_min)
            if pd.notna(ts)
            else (set(), None, None)
        )
        member = row["pair_hl"] in snap_pairs or row["pair_usdt"] in snap_pairs
        price_ok = _binance_1m_exists(row["pair_usdt"], cache_dir)
        if member:
            in_snap += 1
        if price_ok:
            has_price += 1
        annotated.append(
            {
                **row,
                "in_nearest_macdbb": member,
                "nearest_macdbb_ts": nearest,
                "nearest_gap_min": gap,
                "nearest_macdbb_pairs": sorted(snap_pairs),
                "binance_1m_cache_exists": price_ok,
            }
        )

    n = len(annotated)
    summary = {
        "live_create_rows": n,
        "unique_created_ids": len({cid for r in annotated for cid in r["created_ids"]}),
        "in_nearest_macdbb": in_snap,
        "in_nearest_macdbb_rate": (in_snap / n) if n else 0.0,
        "miss_rate": (1.0 - in_snap / n) if n else 0.0,
        "binance_1m_cache_exists": has_price,
        "binance_1m_cache_rate": (has_price / n) if n else 0.0,
        "both_in_snap_and_price": sum(
            1
            for r in annotated
            if r["in_nearest_macdbb"] and r["binance_1m_cache_exists"]
        ),
        "snapshot_dir": str(snap_dir),
        "window_min": args.window_min,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "rows": annotated}
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
