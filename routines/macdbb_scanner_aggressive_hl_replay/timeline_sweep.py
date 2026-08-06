"""Timeline mega sweep helpers, validation, and winner application."""

from __future__ import annotations

import asyncio
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from routines.macdbb_scanner_aggressive_hl_backtest import run as run_dynamic_replay
from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    SweepResult,
    SweepRunContext,
    _apply_capital_metrics,
    _dynamic_sweep_base,
    _load_sessions,
    _merge,
    _print_table,
    _write_sweep_csv,
    finalize_sweep_config,
    prepare_shared_candle_stores,
    resolve_sweep_config_iterator,
    resolve_sweep_workers,
    run_sweep_config_batch,
    sweep_base_config,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import warm_snapshot_caches
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
)
from routines.macdbb_scanner_aggressive_hl_replay.presets import (
    _DRIVER_TIMELINE,
    _DYNAMIC_PRESET_INFRA,
    _STRATEGY_TIMELINE_MEGA_BEST,
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    _merge_preset_layers,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_range import (
    timeline_range_from_reports,
)
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    build_reports_by_pair,
    load_reports_index,
)
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import load_manifest

DEFAULT_FREQUENCY_SEC = 1800
DEFAULT_TIME_WINDOW_MIN = 15
TIMELINE_PRESET_NAME = "hl_dynamic_timeline_refine_v5_winner_binance_1y"
AGENT_SLUG = "macdbb_scanner_aggressive_hl"
PRESET_STRIP_KEYS = frozenset(
    {"preset", "session_nums", "range_start_utc", "range_end_utc"}
)

# Benchmark: binance_1y @ 1800s (~13.7k ticks) ≈ 153s per config on this machine.
_BENCHMARK_TICK_COUNT = 13_719
_BENCHMARK_SEC_PER_CONFIG = 153.0
DEFAULT_CHECKPOINT_EVERY = 10


def discover_replay_snapshot_dirs(data_dir: Path = Path("data")) -> list[Path]:
    return sorted(
        path
        for path in data_dir.glob("replay_snapshots*")
        if (path / "manifest.json").is_file()
    )


def estimate_timeline_sweep_seconds(
    snapshot_dirs: list[Path],
    *,
    config_count: int,
    benchmark_tick_count: int = _BENCHMARK_TICK_COUNT,
    benchmark_sec_per_config: float = _BENCHMARK_SEC_PER_CONFIG,
) -> float:
    """Rough wall-clock estimate from tick count vs binance_1y benchmark."""
    total = 0.0
    for path in snapshot_dirs:
        manifest = load_manifest(snapshot_dir=path)
        ticks = int((manifest or {}).get("tick_count") or benchmark_tick_count)
        scale = max(ticks / benchmark_tick_count, 0.01)
        total += config_count * benchmark_sec_per_config * scale
    return total


def _write_sweep_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _checkpoint_csv_path(output_dir: Path, stem: str) -> Path:
    return output_dir / f"{stem}.checkpoint.csv"


def _should_write_checkpoint(
    done: int, config_total: int, *, checkpoint_every: int
) -> bool:
    if checkpoint_every <= 0:
        return False
    return done == 1 or done % checkpoint_every == 0 or done == config_total


def _write_checkpoint_csv(path: Path, results: list[SweepResult]) -> None:
    ranked = sorted(results, key=lambda row: row.capital_normalized_pnl, reverse=True)
    _write_sweep_csv(path, ranked)


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def timeline_sweep_overrides(
    *,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> dict[str, Any]:
    start, end = timeline_range_from_reports()
    if range_start_utc:
        start = range_start_utc
    if range_end_utc:
        end = range_end_utc
    return {
        **_DRIVER_TIMELINE,
        "preset": "custom",
        "range_start_utc": start,
        "range_end_utc": end,
    }


def merge_timeline_config(
    overrides: dict[str, Any],
    *,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    sweep_grid: str = "mega_v5",
) -> dict[str, Any]:
    return finalize_sweep_config(
        _merge(
            overrides,
            **timeline_sweep_overrides(
                range_start_utc=range_start_utc,
                range_end_utc=range_end_utc,
                frequency_sec=frequency_sec,
                time_window_min=time_window_min,
            ),
        ),
        sweep_grid=sweep_grid,
    )


async def run_timeline_dynamic_sweep(
    dynamic_mode: str = "both_on",
    output_dir: Path | None = None,
    *,
    min_configs: int = 560,
    seed: int = 42,
    output_stem: str | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
    snapshot_dir: str | None = None,
    top_n: int = 40,
    progress_path: Path | None = None,
    global_index_offset: int = 0,
    global_total: int | None = None,
    write_output: bool = True,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
    parent_overrides: dict[str, Any] | None = None,
    config_items: list[tuple[str, dict[str, Any]]] | None = None,
    sweep_grid: str = "mega_v5",
    sample_mode: str = "random",
    workers: int = 1,
    worker_ram_gb: float = 2.0,
    allow_non_fork: bool = False,
    start_index: int = 0,
    resume_results: list[SweepResult] | None = None,
    auto_promote: bool = False,
    telegram_chat_id: str | None = None,
    run_refine: bool = True,
    refine_workers: int = 2,
    automation_state_path: Path | None = None,
    repo_root: Path | None = None,
    use_shared_candle_store: bool = True,
    candle_prefetch_mode: str = "full",
    snapshot_range_start_utc: str | None = None,
    snapshot_range_end_utc: str | None = None,
    chunk_days: int = 0,
    max_configs: int | None = None,
) -> tuple[list[SweepResult], str, float, str, str]:
    timeline_fields = timeline_sweep_overrides(
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        frequency_sec=frequency_sec,
        time_window_min=time_window_min,
    )
    if snapshot_dir:
        timeline_fields = {**timeline_fields, "snapshot_dir": snapshot_dir}
    load_config = DynamicStrategyReplayConfig(
        **finalize_sweep_config(
            _merge(
                sweep_base_config(
                    sweep_grid,
                    dynamic_mode,
                    parent_overrides=parent_overrides,
                ),
                **timeline_fields,
                candle_prefetch_mode=candle_prefetch_mode,
            ),
            sweep_grid=sweep_grid,
        )
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_data import (
        configure_replay_data_sources,
    )

    configure_replay_data_sources(load_config)
    warm_snapshot_caches(
        snapshot_dir,
        range_start_utc=snapshot_range_start_utc or timeline_fields.get("range_start_utc"),
        range_end_utc=snapshot_range_end_utc or timeline_fields.get("range_end_utc"),
    )
    (
        parsed_sessions,
        hl_caches,
        hl_candle_cache,
        hl_barrier_candle_cache,
        hl_vol_candle_cache,
        _selected,
    ) = await _load_sessions(load_config)
    tick_count = sum(len(ticks) for ticks in parsed_sessions.values())
    reports_by_pair = build_reports_by_pair(load_reports_index())

    hl_candle_store = None
    hl_barrier_candle_store = None
    hl_vol_candle_store = None
    if candle_prefetch_mode == "lazy":
        from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import prepare_lazy_candle_stores

        (
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
            hl_candle_store,
            hl_barrier_candle_store,
            hl_vol_candle_store,
        ) = prepare_lazy_candle_stores(
            range_start_utc=str(timeline_fields["range_start_utc"]),
            range_end_utc=str(timeline_fields["range_end_utc"]),
            config=load_config,
        )
        print("Lazy candle stores enabled (on-demand parquet loads)", flush=True)
    elif use_shared_candle_store and workers > 1:
        (
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
            hl_candle_store,
            hl_barrier_candle_store,
            hl_vol_candle_store,
        ) = prepare_shared_candle_stores(
            hl_candle_cache,
            hl_barrier_candle_cache,
            hl_vol_candle_cache,
            enabled=True,
        )
        print(
            f"Shared candle stores: price={len(hl_candle_store or [])} "
            f"barrier={len(hl_barrier_candle_store or [])} "
            f"vol={len(hl_vol_candle_store or [])}",
            flush=True,
        )

    stem = (
        output_stem
        or (
            f"macdbb_scanner_aggressive_hl_backtest_{dynamic_mode}_entry_sltp_timeline"
            if sweep_grid in ("entry_sltp", "entry_sltp_v6")
            else f"macdbb_scanner_aggressive_hl_backtest_{dynamic_mode}_mega_timeline"
        )
    )
    baseline = f"dyn_{dynamic_mode}_timeline_baseline_winner"
    benchmark_avg_notional = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL

    print(
        f"Timeline sweep: {timeline_fields['range_start_utc']} -> "
        f"{timeline_fields['range_end_utc']} | ticks={tick_count} | "
        f"time_window={time_window_min}m | freq={frequency_sec}s"
    )

    if parent_overrides is not None:
        print(f"Staged sweep parent: derived from prior phase winner")

    if config_items is None:
        config_items = list(
            resolve_sweep_config_iterator(
                sweep_grid,
                dynamic_mode,
                min_configs=min_configs,
                seed=seed,
                sample_mode=sample_mode,
                parent_overrides=parent_overrides,
            )
        )

    resolved_workers = resolve_sweep_workers(
        workers,
        worker_ram_gb=worker_ram_gb,
        allow_non_fork=allow_non_fork,
    )
    if resolved_workers > 1:
        print(f"Parallel sweep workers: {resolved_workers}")

    merged_items: list[tuple[str, dict[str, Any]]] = []
    for name, overrides in config_items:
        merged = merge_timeline_config(
            overrides,
            frequency_sec=frequency_sec,
            time_window_min=time_window_min,
            range_start_utc=timeline_fields["range_start_utc"],
            range_end_utc=timeline_fields["range_end_utc"],
            sweep_grid=sweep_grid,
        )
        if snapshot_dir:
            merged["snapshot_dir"] = snapshot_dir
        merged_items.append((name, merged))

    full_config_total = len(merged_items)
    if start_index < 0 or start_index > full_config_total:
        raise ValueError(
            f"start_index {start_index} out of range for {full_config_total} configs"
        )
    if start_index:
        merged_items = merged_items[start_index:]
        print(
            f"Resuming sweep from config {start_index + 1}/{full_config_total} "
            f"({len(merged_items)} remaining)"
        )
    if max_configs is not None and max_configs > 0:
        merged_items = merged_items[:max_configs]
        print(f"Batch limit: running {len(merged_items)} config(s) this invocation")
    config_total = full_config_total
    remaining_total = len(merged_items)
    sweep_started = time.monotonic()
    snap_label = snapshot_dir or "default"
    checkpoint_path: Path | None = None
    if output_dir is not None and write_output:
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = _checkpoint_csv_path(output_dir, stem)

    results: list[SweepResult] = list(resume_results or [])
    if results:
        print(f"Loaded {len(results)} prior sweep result(s) from checkpoint")

    promote_queue = None
    leader_tracker = None
    if auto_promote:
        from routines.macdbb_scanner_aggressive_hl_replay.sweep_automation import (
            LeaderTracker,
            PromoteAutomationConfig,
            PromoteQueue,
            default_telegram_chat_id,
        )

        resolved_output_dir = output_dir or Path("data/strategy_replay_sweeps")
        state_path = automation_state_path or (resolved_output_dir / f"{stem}.automation.json")
        leader_tracker = LeaderTracker(state_path)
        promote_queue = PromoteQueue(
            leader_tracker,
            PromoteAutomationConfig(
                enabled=True,
                telegram_chat_id=telegram_chat_id or default_telegram_chat_id(),
                run_refine=run_refine,
                refine_workers=refine_workers,
                dynamic_mode=dynamic_mode,
                sweep_grid=sweep_grid,
                frequency_sec=frequency_sec,
                time_window_min=time_window_min,
                range_start_utc=str(timeline_fields.get("range_start_utc") or ""),
                range_end_utc=str(timeline_fields.get("range_end_utc") or ""),
                snapshot_dir=str(snapshot_dir or ""),
                candle_prefetch_mode=candle_prefetch_mode,
                output_dir=resolved_output_dir,
                automation_state_path=state_path,
                repo_root=repo_root or Path(".").resolve(),
            ),
        )
        promote_queue.start()
        print(
            f"Auto-promote enabled | state={state_path} | "
            f"telegram={'yes' if (telegram_chat_id or default_telegram_chat_id()) else 'no'} | "
            f"refine={'yes' if run_refine else 'no'}"
        )

    def _record_progress(done: int, result: SweepResult) -> None:
        result.snapshot_dir = snap_label
        elapsed = time.monotonic() - sweep_started
        local_done = done - start_index
        rate = local_done / elapsed if elapsed > 0 else 0.0
        remaining_local = remaining_total - local_done
        eta_local = remaining_local / rate if rate > 0 else None
        global_done = global_index_offset + done
        global_rem = (global_total - global_done) if global_total else None
        eta_global = (
            global_rem / rate if rate > 0 and global_rem is not None else eta_local
        )

        if checkpoint_path is not None and _should_write_checkpoint(
            done, config_total, checkpoint_every=checkpoint_every
        ):
            _write_checkpoint_csv(checkpoint_path, results)

        if progress_path is not None and results:
            top_row = max(results, key=lambda row: row.capital_normalized_pnl)
            progress_payload: dict[str, Any] = {
                "status": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_dir": snap_label,
                "config_index": done,
                "config_total": config_total,
                "resume_from_index": start_index or None,
                "global_index": global_done,
                "global_total": global_total,
                "last_config": result.name,
                "last_capital_normalized_pnl": round(result.capital_normalized_pnl, 2),
                "last_trades": result.trades,
                "top_config": top_row.name,
                "top_capital_normalized_pnl": round(top_row.capital_normalized_pnl, 2),
                "elapsed_sec": round(elapsed, 1),
                "eta_local_sec": round(eta_local, 1) if eta_local is not None else None,
                "eta_global_sec": (
                    round(eta_global, 1) if eta_global is not None else None
                ),
                "configs_per_hour": round(rate * 3600, 2),
                "workers": resolved_workers,
            }
            if checkpoint_path is not None:
                progress_payload["checkpoint_csv"] = checkpoint_path.as_posix()
                progress_payload["checkpoint_result_count"] = len(results)
            _write_sweep_progress(progress_path, progress_payload)

        if done == 1 or done % 5 == 0 or done == config_total:
            print(
                f"[{global_done}/{global_total or config_total}] "
                f"{snap_label} {done}/{config_total} {result.name} "
                f"cap_norm=${result.capital_normalized_pnl:+.2f} "
                f"elapsed={_format_eta(elapsed)} eta={_format_eta(eta_global)}",
                flush=True,
            )

    sweep_ctx = SweepRunContext(
        dynamic_mode=dynamic_mode,
        parsed_sessions=parsed_sessions,
        hl_caches_by_session=hl_caches,
        hl_candle_cache=hl_candle_cache,
        hl_barrier_candle_cache=hl_barrier_candle_cache,
        hl_vol_candle_cache=hl_vol_candle_cache,
        hl_candle_store=hl_candle_store,
        hl_barrier_candle_store=hl_barrier_candle_store,
        hl_vol_candle_store=hl_vol_candle_store,
        reports_by_pair=reports_by_pair,
        parent_overrides=parent_overrides,
        benchmark_avg_notional=benchmark_avg_notional,
        chunk_days=chunk_days,
    )

    def _on_batch_result(local_done: int, result: SweepResult) -> None:
        results.append(result)
        if promote_queue is not None and leader_tracker is not None:
            job = leader_tracker.consider(result)
            promote_queue.submit(job)
        _record_progress(start_index + local_done, result)

    try:
        await asyncio.to_thread(
            run_sweep_config_batch,
            merged_items,
            sweep_ctx,
            workers=workers,
            worker_ram_gb=worker_ram_gb,
            allow_non_fork=allow_non_fork,
            on_result=_on_batch_result,
        )
    finally:
        sweep_ctx.close_shared_stores()

    if promote_queue is not None:
        await promote_queue.shutdown()

    results.sort(key=lambda row: row.capital_normalized_pnl, reverse=True)

    if output_dir is not None and write_output:
        _write_sweep_csv(output_dir / f"{stem}.csv", results)

    if progress_path is not None and results:
        top_row = results[0]
        completed_payload: dict[str, Any] = {
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_dir": snap_label,
            "config_index": config_total,
            "config_total": config_total,
            "global_index": global_index_offset + config_total,
            "global_total": global_total,
            "elapsed_sec": round(time.monotonic() - sweep_started, 1),
            "result_count": len(results),
            "top_config": top_row.name,
            "top_capital_normalized_pnl": round(top_row.capital_normalized_pnl, 2),
        }
        if checkpoint_path is not None:
            completed_payload["checkpoint_csv"] = checkpoint_path.as_posix()
        _write_sweep_progress(progress_path, completed_payload)

    baseline_row = next(
        (row for row in results if row.name.endswith("baseline_winner")), results[-1]
    )
    _print_table(
        results,
        baseline_row.pnl,
        top_n=top_n,
        dynamic=True,
        benchmark_avg_notional=benchmark_avg_notional,
        baseline_capital_normalized_pnl=baseline_row.capital_normalized_pnl,
        rank_by_normalized=True,
    )

    return (
        results,
        baseline,
        benchmark_avg_notional,
        timeline_fields["range_start_utc"],
        timeline_fields["range_end_utc"],
    )


async def run_multi_snapshot_timeline_sweep(
    dynamic_mode: str = "both_on",
    output_dir: Path | None = None,
    *,
    min_configs: int = 560,
    seed: int = 42,
    output_stem: str | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    snapshot_dirs: list[Path] | None = None,
    top_n: int = 40,
    progress_path: Path | None = None,
    sweep_grid: str = "mega_v5",
) -> tuple[list[SweepResult], str, str]:
    """Run mega sweep for each snapshot dir; rank combined results by cap-norm PnL."""
    dirs = snapshot_dirs or discover_replay_snapshot_dirs()
    if not dirs:
        raise ValueError("No replay snapshot directories with manifest.json found")

    config_items = list(
        resolve_sweep_config_iterator(
            sweep_grid,
            dynamic_mode,
            min_configs=min_configs,
            seed=seed,
        )
    )
    config_count = len(config_items)
    global_total = config_count * len(dirs)
    stem = (
        output_stem
        or f"macdbb_scanner_aggressive_hl_backtest_{dynamic_mode}_mega_timeline_all_snapshots"
    )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    progress_file = progress_path or (
        (output_dir or Path("data/strategy_replay_sweeps")) / f"{stem}.progress.json"
    )

    combined: list[SweepResult] = []
    run_started = time.monotonic()
    global_offset = 0

    for snap_index, snap_path in enumerate(dirs, start=1):
        snap = snap_path.as_posix()
        manifest = load_manifest(snapshot_dir=snap_path)
        if (
            not manifest
            or not manifest.get("range_start_utc")
            or not manifest.get("range_end_utc")
        ):
            print(f"Skipping {snap}: manifest missing range", flush=True)
            global_offset += config_count
            continue

        print(
            f"\n=== Snapshot {snap_index}/{len(dirs)}: {snap} "
            f"({manifest.get('tick_count', '?')} ticks) ===",
            flush=True,
        )
        results, _baseline, _benchmark, range_start, range_end = (
            await run_timeline_dynamic_sweep(
                dynamic_mode=dynamic_mode,
                output_dir=output_dir,
                min_configs=min_configs,
                seed=seed,
                output_stem=f"{stem}__{snap_path.name}",
                frequency_sec=frequency_sec,
                time_window_min=time_window_min,
                range_start_utc=str(manifest["range_start_utc"]),
                range_end_utc=str(manifest["range_end_utc"]),
                snapshot_dir=snap,
                top_n=min(top_n, 10),
                progress_path=progress_file,
                global_index_offset=global_offset,
                global_total=global_total,
                write_output=False,
                sweep_grid=sweep_grid,
            )
        )
        combined.extend(results)
        global_offset += config_count

        if output_dir is not None:
            _write_checkpoint_csv(_checkpoint_csv_path(output_dir, stem), combined)

    combined.sort(key=lambda row: row.capital_normalized_pnl, reverse=True)
    if output_dir is not None:
        _write_sweep_csv(output_dir / f"{stem}.csv", combined)

    elapsed = time.monotonic() - run_started
    _write_sweep_progress(
        progress_file,
        {
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "global_index": global_total,
            "global_total": global_total,
            "elapsed_sec": round(elapsed, 1),
            "snapshot_dirs": [path.as_posix() for path in dirs],
            "result_count": len(combined),
            "top_capital_normalized_pnl": (
                round(combined[0].capital_normalized_pnl, 2) if combined else None
            ),
            "top_snapshot_dir": combined[0].snapshot_dir if combined else None,
            "top_config": combined[0].name if combined else None,
        },
    )

    baseline_row = combined[-1] if combined else None
    if baseline_row:
        _print_table(
            combined,
            baseline_row.pnl,
            top_n=top_n,
            dynamic=True,
            benchmark_avg_notional=FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
            baseline_capital_normalized_pnl=baseline_row.capital_normalized_pnl,
            rank_by_normalized=True,
        )

    range_start = combined[0].overrides.get("range_start_utc", "") if combined else ""
    range_end = combined[0].overrides.get("range_end_utc", "") if combined else ""
    return combined, range_start, range_end


def full_replay_overrides(
    sweep_delta: dict[str, Any],
    *,
    dynamic_mode: str = "both_on",
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
) -> dict[str, Any]:
    base = finalize_sweep_config(_dynamic_sweep_base(dynamic_mode), sweep_grid="mega_v5")
    return merge_timeline_config(
        _merge(base, **sweep_delta),
        frequency_sec=frequency_sec,
        time_window_min=time_window_min,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
    )


def replay_config_to_agent_strategy_params(
    config: DynamicStrategyReplayConfig,
    *,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
) -> dict[str, Any]:
    hours_per_tick = frequency_sec / 3600.0

    def ticks_to_hours(ticks: int) -> float:
        return round(ticks * hours_per_tick, 4)

    return {
        "adaptive_activation_hours": ticks_to_hours(config.activation_ticks),
        "min_tradeable_for_adaptive": config.min_tradeable_count,
        "adaptive_skip_4h_filter": config.ignore_adaptive_4h_filter,
        "adaptive_requires_flat": config.adaptive_requires_flat,
        "sl_symbol_cooldown_hours": ticks_to_hours(config.sl_cooldown_ticks),
        "adaptive_long_bb_pos_max": config.adaptive_long_bb_pos_max,
        "adaptive_short_bb_pos_min": config.adaptive_short_bb_pos_min,
        "adaptive_strong_long_bb_pos_max": config.adaptive_strong_long_bb_pos_max,
        "adaptive_strong_short_bb_pos_min": config.adaptive_strong_short_bb_pos_min,
        "adaptive_min_macd_gap_ratio": config.adaptive_min_macd_gap_ratio,
        "adaptive_min_hist_ratio": config.adaptive_min_hist_ratio,
        "adaptive_score_open_min": config.adaptive_score_open_min,
        "adaptive_score_open_min_extreme": config.adaptive_score_open_min_extreme,
        "adaptive_hist_sign_bonus": config.adaptive_hist_sign_bonus,
        "adaptive_hist_sign_penalty": config.adaptive_hist_sign_penalty,
        "adaptive_momentum_bonus": config.adaptive_momentum_bonus,
        "adaptive_momentum_penalty": config.adaptive_momentum_penalty,
        "bb_proximity_epsilon_pct": config.bb_proximity_epsilon_pct,
        "sl_pct": config.sl_pct,
        "tp_pct": config.tp_pct,
        "thesis_decay_exit_hours": ticks_to_hours(config.thesis_decay_exit_ticks),
        "thesis_bb_drift_pts": config.thesis_bb_drift_pts,
        "flip_cooldown_hours": ticks_to_hours(config.flip_cooldown_ticks),
        "enable_dynamic_sizing": config.enable_dynamic_sizing,
        "enable_dynamic_barriers": config.enable_dynamic_barriers,
        "min_notional_quote": config.min_notional_quote,
        "max_notional_quote": config.max_notional_quote,
        "min_conviction_mult": config.min_conviction_mult,
        "max_conviction_mult": config.max_conviction_mult,
        "strength_mult_per_unit": config.strength_mult_per_unit,
        "extreme_displacement_mult": config.extreme_displacement_mult,
        "activation_streak_mult_per_tick": config.activation_streak_mult_per_tick,
        "thin_universe_mult": config.thin_universe_mult,
        "mature_tape_low_vol_mult": config.mature_tape_low_vol_mult,
        "vol_inverse_sizing": config.vol_inverse_sizing,
        "min_vol_mult": config.min_vol_mult,
        "max_vol_mult": config.max_vol_mult,
        "ref_volatility_pct": config.ref_volatility_pct,
        "sl_vol_exponent": config.sl_vol_exponent,
        "tp_vol_exponent": config.tp_vol_exponent,
        "sl_min_pct": config.sl_min_pct,
        "sl_max_pct": config.sl_max_pct,
        "tp_min_pct": config.tp_min_pct,
        "tp_max_pct": config.tp_max_pct,
        "volatility_source": config.volatility_source,
    }


def build_timeline_preset_overrides(
    sweep_delta: dict[str, Any],
    *,
    dynamic_mode: str = "both_on",
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> dict[str, Any]:
    del dynamic_mode, frequency_sec, time_window_min
    return _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _DRIVER_TIMELINE,
        _STRATEGY_TIMELINE_MEGA_BEST,
        sweep_delta,
    )


def load_top_sweep_rows(csv_path: Path, top_n: int = 5) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))[:top_n]


async def validate_top_configs_via_routine(
    csv_path: Path,
    *,
    top_n: int = 5,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
    time_window_min: int = DEFAULT_TIME_WINDOW_MIN,
) -> list[dict[str, Any]]:
    start, end = timeline_range_from_reports()
    rows: list[dict[str, Any]] = []
    for rank, row in enumerate(load_top_sweep_rows(csv_path, top_n=top_n), start=1):
        delta = json.loads(row["overrides_json"])
        overrides = full_replay_overrides(
            delta,
            frequency_sec=frequency_sec,
            time_window_min=time_window_min,
            range_start_utc=start,
            range_end_utc=end,
        )
        config = DynamicStrategyReplayConfig(**overrides)
        result = await run_dynamic_replay(config, None)
        text = (
            result if isinstance(result, str) else getattr(result, "text", str(result))
        )
        rows.append(
            {
                "rank": rank,
                "name": row["name"],
                "sweep_cap_norm": float(row["capital_normalized_pnl"]),
                "sweep_pnl": float(row["pnl"]),
                "sweep_trades": int(row["trades"]),
                "routine_output": text,
            }
        )
    return rows


def format_validation_log(rows: list[dict[str, Any]]) -> str:
    lines = [
        "Timeline top-N routine validation (macdbb_scanner_aggressive_hl_backtest)"
    ]
    for row in rows:
        lines.append("")
        lines.append(f"=== Rank {row['rank']}: {row['name']} ===")
        lines.append(
            f"Sweep: cap-norm=${row['sweep_cap_norm']:+.2f} raw=${row['sweep_pnl']:+.2f} "
            f"trades={row['sweep_trades']}"
        )
        lines.append(str(row["routine_output"]))
    return "\n".join(lines) + "\n"


def _split_agent_front_matter(agent_path: Path) -> tuple[dict[str, Any], str]:
    text = agent_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*\Z)", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Could not parse YAML front matter in {agent_path}")
    front = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    return front, body


def apply_winner_to_agent(
    config: DynamicStrategyReplayConfig,
    *,
    agent_path: Path | None = None,
    frequency_sec: int = DEFAULT_FREQUENCY_SEC,
) -> dict[str, Any]:
    """Persist winner defaults to private strategies/{slug}/strategy.yaml."""
    from condor.strategy_runners.macdbb.sessions import (
        load_default_config,
        save_default_config,
    )

    _ = agent_path  # legacy kwarg ignored; defaults live in strategy.yaml
    default_config = dict(load_default_config(AGENT_SLUG) or {})
    default_config.setdefault("frequency_sec", frequency_sec)
    risk = dict(default_config.get("risk_limits") or {})
    risk["max_open_executors"] = config.max_open_executors
    default_config["risk_limits"] = risk
    strategy_params = replay_config_to_agent_strategy_params(
        config, frequency_sec=frequency_sec
    )
    existing = dict(default_config.get("strategy_params") or {})
    existing.update(strategy_params)
    default_config["strategy_params"] = existing
    save_default_config(AGENT_SLUG, default_config)
    return strategy_params


def render_preset_block(preset_name: str, overrides: dict[str, Any]) -> str:
    lines = [f'    "{preset_name}": {{']
    for key, value in overrides.items():
        if key == "preset":
            continue
        lines.append(f"        {json.dumps(key)}: {repr(value)},")
    lines.append("    },")
    return "\n".join(lines)


def apply_winner_to_presets(
    preset_overrides: dict[str, Any],
    *,
    preset_name: str | None = None,
    presets_path: Path | None = None,
    models_path: Path | None = None,
) -> None:
    del models_path  # preset names are validated dynamically; models.py no longer lists them
    from condor.trading_agent.strategy_paths import private_strategy_dir

    preset_name = preset_name or TIMELINE_PRESET_NAME
    yaml_path = presets_path or (private_strategy_dir(AGENT_SLUG) / "presets.yaml")
    bundle: dict[str, Any] = {}
    if yaml_path.is_file():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            bundle = loaded

    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_name in dynamic_overrides:
        raise ValueError(f"Preset {preset_name!r} already exists in {yaml_path}")

    filtered = {
        key: value
        for key, value in preset_overrides.items()
        if key not in PRESET_STRIP_KEYS
    }
    dynamic_overrides[preset_name] = filtered

    labels = bundle.setdefault("labels", {})
    labels.setdefault(preset_name, preset_name)

    names = list(bundle.get("agent_strategy_preset_names") or [])
    if preset_name not in names:
        names.insert(0, preset_name)
    bundle["agent_strategy_preset_names"] = names
    bundle["default_agent_strategy_preset"] = preset_name
    bundle["current_winner_preset"] = preset_name

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(bundle, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
