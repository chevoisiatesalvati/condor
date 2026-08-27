"""Execute pullback mega-sweep cases on a shared in-memory tape."""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
from pathlib import Path
from typing import Any, Callable

from condor.strategy_runners.macdbb_pullback.dynamic import (
    annualized_cap_norm,
    capital_normalized_pnl,
    window_days,
)
from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import resolve_sweep_workers

logger = logging.getLogger(__name__)

_SHARED: dict[str, Any] | None = None


def load_completed_results(
    cases: list[dict[str, Any]],
    out_dir: Path | str,
    *,
    force: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the grid into already-written JSON results vs cases still to run."""
    directory = Path(out_dir)
    completed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for case in cases:
        result_path = directory / f"{case['name']}.json"
        if force or not result_path.is_file():
            pending.append(case)
            continue
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Unreadable result %s — will re-run", result_path)
            pending.append(case)
            continue
        if not isinstance(loaded, dict) or "stats" not in loaded:
            logger.warning("Incomplete result %s — will re-run", result_path)
            pending.append(case)
            continue
        completed.append(loaded)
    return completed, pending


def trade_stats(trades: list[Any]) -> dict[str, Any]:
    total = len(trades)
    wins = sum(1 for trade in trades if trade.pnl_quote > 0)
    sl = sum(1 for trade in trades if "stop_loss" in trade.exit_reason)
    tp = sum(1 for trade in trades if "take_profit" in trade.exit_reason)
    decay = sum(1 for trade in trades if trade.exit_reason == "thesis_decay")
    session_end = sum(1 for trade in trades if trade.exit_reason == "session_end")
    pnl = sum(trade.pnl_quote for trade in trades)
    immediate = sum(1 for trade in trades if trade.entry_class == "immediate")
    pullback = sum(1 for trade in trades if trade.entry_class == "pullback")
    avg_hold = (sum(trade.hold_ticks for trade in trades) / total) if total else 0.0
    avg_notional = (
        sum(float(trade.notional_quote) for trade in trades) / total if total else 0.0
    )
    return {
        "trades": total,
        "immediate": immediate,
        "pullback": pullback,
        "wins": wins,
        "win_rate_pct": (wins / total * 100.0) if total else 0.0,
        "net_pnl_quote": pnl,
        "sl_hits": sl,
        "tp_hits": tp,
        "thesis_decay": decay,
        "session_end": session_end,
        "avg_hold_ticks": avg_hold,
        "avg_return_pct": (
            sum(trade.return_pct for trade in trades) / total if total else 0.0
        ),
        "avg_notional": avg_notional,
        "avg_sl_pct": (
            sum(float(trade.sl_pct_used) for trade in trades) / total if total else 0.0
        ),
        "avg_tp_pct": (
            sum(float(trade.tp_pct_used) for trade in trades) / total if total else 0.0
        ),
        "capital_normalized_pnl": capital_normalized_pnl(pnl, avg_notional),
    }


def run_one_case(case: dict[str, Any], shared: dict[str, Any]) -> dict[str, Any]:
    from routines.macdbb_pullback_hl_backtest import Config
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
    from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session

    kwargs = {
        **shared["base_kwargs"],
        **{key: value for key, value in case.items() if key != "name"},
    }
    config = resolve_pullback_config(Config(**kwargs))
    loader = shared["loader"]
    tapes = shared.get("signal_tapes") or {}

    all_trades: list[Any] = []
    for session_num, tick_meta_map in shared["parsed_sessions"].items():
        _pairs, _ticks, trades, summary = simulate_pullback_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=shared["reports_by_pair"],
            config=config,
            signal_config=loader,
            hl_price_cache=shared["hl_caches_by_session"].get(session_num),
            hl_candle_cache=shared["hl_candle_cache"],
            hl_barrier_candle_cache=shared["hl_barrier_candle_cache"],
            hl_vol_candle_cache=shared["hl_vol_candle_cache"],
            signal_tape=tapes.get(session_num),
            collect_debug_rows=False,
        )
        if summary.get("status") == "skipped_no_price_data":
            continue
        all_trades.extend(trades)

    stats = trade_stats(all_trades)
    base_kwargs = shared.get("base_kwargs") or {}
    days = window_days(
        str(base_kwargs.get("range_start_utc") or ""),
        str(base_kwargs.get("range_end_utc") or ""),
    )
    cap_norm = float(stats["capital_normalized_pnl"])
    stats["window_days"] = days
    stats["annualized_cap_norm"] = annualized_cap_norm(cap_norm, window_days=days)
    return {
        "name": case["name"],
        "config": {
            "impulse_atr_mult": float(config.impulse_atr_mult),
            "pullback_epsilon_pct": float(config.pullback_epsilon_pct),
            "sl_pct": float(config.sl_pct),
            "tp_pct": float(config.tp_pct),
            "chase_long_bb_pos_max": float(config.chase_long_bb_pos_max),
            "chase_short_bb_pos_min": float(config.chase_short_bb_pos_min),
            "bb_proximity_epsilon_pct": float(config.bb_proximity_epsilon_pct),
            "impulse_lookback_bars": int(config.impulse_lookback_bars),
            "atr_period": int(config.atr_period),
            "pullback_timeout_hours": float(config.pullback_timeout_hours),
            "sl_symbol_cooldown_hours": float(config.sl_symbol_cooldown_hours),
            "enable_flip_exit": bool(config.enable_flip_exit),
            "enable_thesis_decay_exit": bool(config.enable_thesis_decay_exit),
            "thesis_decay_exit_hours": float(config.thesis_decay_exit_hours),
            "thesis_bb_drift_pts": float(config.thesis_bb_drift_pts),
            "flip_confirm_ticks": int(config.flip_confirm_ticks),
            "flip_cooldown_hours": float(config.flip_cooldown_hours),
            "enable_dynamic_barriers": bool(config.enable_dynamic_barriers),
            "ref_volatility_pct": float(config.ref_volatility_pct),
            "sl_vol_exponent": float(config.sl_vol_exponent),
            "tp_vol_exponent": float(config.tp_vol_exponent),
            "sl_min_pct": float(config.sl_min_pct),
            "sl_max_pct": float(config.sl_max_pct),
            "tp_min_pct": float(config.tp_min_pct),
            "tp_max_pct": float(config.tp_max_pct),
            "enable_dynamic_sizing": bool(config.enable_dynamic_sizing),
            "min_vol_mult": float(config.min_vol_mult),
            "max_vol_mult": float(config.max_vol_mult),
            "frequency_sec": int(config.frequency_sec or 60),
        },
        "stats": stats,
    }


def _worker(case: dict[str, Any]) -> dict[str, Any]:
    if _SHARED is None:
        raise RuntimeError("Pullback mega-sweep worker context is not set")
    return run_one_case(case, _SHARED)


def run_case_batch(
    cases: list[dict[str, Any]],
    shared: dict[str, Any],
    *,
    workers: int = 1,
    on_result: Callable[[int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    resolved = resolve_sweep_workers(workers, worker_ram_gb=2.0)
    logger.info("Pullback mega-sweep workers=%d (requested=%d)", resolved, workers)
    if resolved <= 1:
        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            result = run_one_case(case, shared)
            results.append(result)
            if on_result is not None:
                on_result(index, result)
        return results

    global _SHARED
    _SHARED = shared
    results = []
    completed = 0
    try:
        pool_ctx = mp.get_context("fork")
        with pool_ctx.Pool(processes=resolved) as pool:
            for result in pool.imap(_worker, cases, chunksize=1):
                completed += 1
                results.append(result)
                if on_result is not None:
                    on_result(completed, result)
    finally:
        _SHARED = None
    return results


__all__ = ["load_completed_results", "run_case_batch", "run_one_case", "trade_stats"]
