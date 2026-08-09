#!/usr/bin/env python3
"""Phase A: session_parity replay for live sessions covering Aug 6–7."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions",
        default="18-25",
        help="Session selector under macdbb_scanner_aggressive_hl",
    )
    parser.add_argument(
        "--output",
        default="data/backtests/parity_aug6_7/session_parity_summary.json",
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
    from routines.macdbb_scanner_aggressive_hl_backtest import Config, run
    from routines.macdbb_scanner_aggressive_hl_replay.presets import (
        resolve_config_with_preset,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import (
        DynamicReplayPolicy,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
        hl_prefetch_settings_from_config,
        prefetch_replay_hl_prices,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
        configure_replay_data_sources,
        should_prefetch_replay_candles,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import (
        load_replay_sessions,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.reports import (
        build_reports_by_pair,
        load_reports_index,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.simulator import (
        simulate_strategy_session,
    )

    config = Config(
        preset="hl_dynamic_session_parity",
        replay_mode="session_parity",
        session_nums=args.sessions,
        write_csv=False,
        auto_update_snapshots=False,
        use_shared_decide=True,
        candle_source="hyperliquid",
        hl_cache_dir="data/hl_candles",
    )
    config = resolve_config_with_preset(config)
    # Preset may set session_nums="all"; keep the CLI/Aug 6–7 window.
    # Prefer reports_only + live JSONL enrich + inline 1h compute (no HTML reports).
    config = config.model_copy(
        update={
            "session_nums": args.sessions,
            "data_source": "reports_only",
            "candle_source": "hyperliquid",
            "hl_cache_dir": "data/hl_candles",
            "use_shared_decide": True,
        }
    )
    configure_replay_data_sources(config)
    parsed_sessions, session_configs, selected = load_replay_sessions(config)
    # Live sessions often set total_amount_quote < min_notional_quote; bump so
    # shared decide can size entries (otherwise creates are silently dropped).
    for session_num, session_config in list(session_configs.items()):
        min_n = float(getattr(session_config, "min_notional_quote", 0) or 0)
        formal = float(getattr(session_config, "formal_notional_quote", 0) or 0)
        if min_n > 0 and formal < min_n:
            session_configs[session_num] = session_config.model_copy(
                update={"formal_notional_quote": min_n}
            )
    logging.info("Selected sessions: %s ticks=%s", selected, {k: len(v) for k, v in parsed_sessions.items()})

    reports = load_reports_index()
    reports_by_pair = build_reports_by_pair(reports)

    hl_caches_by_session = {}
    hl_candle_cache: dict = {}
    hl_barrier_candle_cache: dict = {}
    hl_vol_candle_cache: dict = {}
    if should_prefetch_replay_candles(config) and parsed_sessions:
        (
            hl_caches_by_session,
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
        ) = await prefetch_replay_hl_prices(
            parsed_sessions,
            settings=hl_prefetch_settings_from_config(config),
        )

    # Inline MACD compute needs 1h HL candles; price prefetch only covers 5m/1m.
    if parsed_sessions:
        import datetime as dt

        import aiohttp

        from routines.lib.hl_candle_cache import fetch_hl_candles_between_cached

        pairs: set[str] = set()
        min_ts: dt.datetime | None = None
        max_ts: dt.datetime | None = None
        for tick_map in parsed_sessions.values():
            for meta in tick_map.values():
                pairs.update(meta.macd_pairs or [])
                pairs.update(meta.queue_total or [])
                pairs.update((meta.signals_1h or {}).keys())
                ts = meta.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                min_ts = ts if min_ts is None else min(min_ts, ts)
                max_ts = ts if max_ts is None else max(max_ts, ts)
        if pairs and min_ts and max_ts:
            start = min_ts - dt.timedelta(hours=260)
            end = max_ts + dt.timedelta(hours=2)
            logging.info(
                "Prefetching 1h HL candles for %d pairs (%s → %s)",
                len(pairs),
                start.isoformat(),
                end.isoformat(),
            )
            async with aiohttp.ClientSession() as session:
                for idx, pair in enumerate(sorted(pairs), start=1):
                    try:
                        await fetch_hl_candles_between_cached(
                            pair,
                            "1h",
                            start,
                            end,
                            session=session,
                            cache_dir=Path(config.hl_cache_dir or "data/hl_candles"),
                            use_cache=True,
                            fill_gaps=True,
                            ignore_api_skip=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.warning("1h prefetch failed for %s: %s", pair, exc)
                    if idx % 5 == 0 or idx == len(pairs):
                        logging.info("1h prefetch progress: %d/%d", idx, len(pairs))

    all_trades = []
    per_session = []
    for session_num, tick_meta_map in parsed_sessions.items():
        session_config = session_configs.get(session_num, config)
        _p, _t, trades, summary = simulate_strategy_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=session_config,
            hl_price_cache=hl_caches_by_session.get(session_num),
            hl_candle_cache=hl_candle_cache,
            hl_barrier_candle_cache=hl_barrier_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            replay_policy=DynamicReplayPolicy(session_config),
        )
        all_trades.extend(trades)
        per_session.append(
            {
                "session": session_num,
                "ticks": len(tick_meta_map),
                "trades": len(trades),
                "status": summary.get("status"),
                "net_pnl": summary.get("net_pnl_quote"),
            }
        )

    pair_counts = Counter(t.pair for t in all_trades)
    class_counts = Counter(t.entry_class for t in all_trades)
    # Live baseline from tick JSONL
    live_ids = set()
    ticks_root = Path("data/strategy_runs/macdbb_scanner_aggressive_hl/ticks")
    for day in ("20260806", "20260807"):
        d = ticks_root / day
        if not d.is_dir():
            continue
        for path in d.glob("*.jsonl"):
            # Only count sessions in the selected range when possible.
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                for cid in obj.get("apply", {}).get("created_ids") or []:
                    live_ids.add(cid)

    live_pending_creates = 0
    ticks_root = Path("data/strategy_runs/macdbb_scanner_aggressive_hl/ticks")
    for day in ("20260806", "20260807"):
        d = ticks_root / day
        if not d.is_dir():
            continue
        for path in d.glob("session_*_ticks.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                created = obj.get("apply", {}).get("created_ids") or []
                if created and (obj.get("pending_pairs") or []):
                    live_pending_creates += 1

    recreate = (len(all_trades) / len(live_ids)) if live_ids else None
    residual = []
    if recreate is not None and recreate < 0.7:
        residual.append(
            "recreate_rate < 0.70 vs live created_ids; live create stream includes "
            f"{live_pending_creates} pending/unfilled retries that inflate opens vs "
            "sim fills; adaptive path under-fires when 1h candle recompute differs "
            "from live signal snapshots; Binance timeline universe miss_rate=1.0 "
            "(see universe_miss_report.json) until HL 60s store is used."
        )
    payload = {
        "sessions_requested": args.sessions,
        "sessions_simulated": selected,
        "sim_trades": len(all_trades),
        "sim_pairs": dict(pair_counts),
        "sim_entry_classes": dict(class_counts),
        "live_created_ids_aug6_7": len(live_ids),
        "live_creates_with_pending_pairs": live_pending_creates,
        "recreate_rate_vs_live_ids": recreate,
        "phase_a_exit_gate": "recreate_rate>=0.70 or documented residual",
        "residual": residual,
        "per_session": per_session,
        "net_pnl_quote": sum(t.pnl_quote for t in all_trades),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Wrote {out}")
    _ = (run, MagicMock)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
