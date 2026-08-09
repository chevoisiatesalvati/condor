"""Report-driven tick builders for session parity and timeline backtest."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from routines.macdbb_scanner_aggressive_hl_replay.hydrated_ticks_cache import (
    load_hydrated_timeline_ticks,
    save_hydrated_timeline_ticks,
)
from routines.macdbb_scanner_aggressive_hl_replay.journal import parse_journal_ticks
from routines.macdbb_scanner_aggressive_hl_replay.metrics import compute_metrics
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig, TickMeta
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    ReportMeta,
    ScannerReportMeta,
    build_reports_by_pair,
    load_parsed_report,
    load_parsed_scanner_report,
    load_reports_index,
    load_scanner_reports_index,
    nearest_report,
    nearest_scanner_report,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import is_report_driven_data_source
from routines.macdbb_scanner_aggressive_hl_replay.scanner_queue import build_scanner_queue
from routines.macdbb_scanner_aggressive_hl_replay.session_config import replay_config_from_session
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import (
    build_range_tick_schedule,
    parse_iso_utc,
    parse_tick_schedule_file,
)


def _default_strategy_params(config: DynamicStrategyReplayConfig) -> dict[str, Any]:
    live_eq = bool(getattr(config, "live_equivalent_queue", False))
    return {
        "live_equivalent_queue": live_eq,
        "natr_floor_mature_pct": 0.0 if live_eq else 0.08,
        "natr_floor_degen_pct": 0.0 if live_eq else 0.1,
        "macd_queue_primary_size": 8,
        "macd_primary_review_count": 8 if live_eq else 5,
        "macd_queue_pass2_min": 8,
        "macd_queue_pass2_max": 12,
        "macd_queue_total_cap": 20,
        "min_tradeable_for_adaptive": config.min_tradeable_count,
        "min_scanner_analyzed": 3,
    }


def load_timeline_strategy_params(config: DynamicStrategyReplayConfig) -> dict[str, Any]:
    """Strategy params for timeline tick hydration (scanner queue sizing)."""
    from condor.trading_agent.strategy_paths import resolve_agent_md_for_read

    params = _default_strategy_params(config)
    agent_path = resolve_agent_md_for_read(config.strategy_slug)
    if not agent_path.is_file():
        return params
    try:
        import yaml

        text = agent_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return params
        end = text.find("---", 3)
        if end == -1:
            return params
        front = yaml.safe_load(text[3:end]) or {}
        agent_params = (front.get("default_config") or {}).get("strategy_params") or {}
        if isinstance(agent_params, dict):
            params = {**params, **agent_params}
    except Exception:
        return params
    params["min_tradeable_for_adaptive"] = config.min_tradeable_count
    return params


def hydrate_timeline_ticks(
    config: DynamicStrategyReplayConfig,
    schedule: dict[int, TickMeta] | None = None,
) -> dict[int, TickMeta]:
    """Populate scanner queue fields on a bare timeline schedule."""
    tick_map = schedule if schedule is not None else build_timeline_ticks(config)
    if not tick_map:
        return {}
    if not is_report_driven_data_source(config.data_source):
        return tick_map
    params = load_timeline_strategy_params(config)
    cached = load_hydrated_timeline_ticks(config, params)
    if cached is not None:
        return cached
    built = build_report_driven_ticks(
        tick_map,
        config,
        params,
    )
    save_hydrated_timeline_ticks(config, params, built)
    return built


def _populate_tick_from_reports(
    tick_meta: TickMeta,
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any],
    macd_reports_by_pair: dict[str, list[ReportMeta]],
    scanner_reports: list[ScannerReportMeta],
    *,
    open_pairs: list[str] | None = None,
) -> TickMeta:
    scanner_meta = nearest_scanner_report(
        scanner_reports,
        tick_meta.timestamp,
        config.time_window_min,
    )
    if scanner_meta is None:
        return tick_meta

    parsed_scanner = load_parsed_scanner_report(scanner_meta)
    if parsed_scanner is None:
        return tick_meta

    queue = build_scanner_queue(
        parsed_scanner,
        strategy_params,
        open_pairs=open_pairs,
    )
    if queue.tradeable_count == 0 or queue.scanner_analyzed == 0:
        return TickMeta(
            tick=tick_meta.tick,
            timestamp=tick_meta.timestamp,
            macd_pairs=[],
            tradeable_count=0,
            scanner_analyzed=queue.scanner_analyzed,
            scanner_regime=queue.regime,
            natr_floor_used=queue.natr_floor_used,
            queue_total=[],
            natr_by_pair={},
        )

    if config.replay_mode == "timeline_backtest":
        return TickMeta(
            tick=tick_meta.tick,
            timestamp=tick_meta.timestamp,
            macd_pairs=list(queue.macd_pairs),
            tradeable_count=queue.tradeable_count,
            scanner_analyzed=queue.scanner_analyzed,
            scanner_regime=queue.regime,
            natr_floor_used=queue.natr_floor_used,
            queue_total=list(queue.queue_total),
            natr_by_pair=dict(queue.natr_by_pair),
            signals_1h=dict(tick_meta.signals_1h),
            filter_4h=dict(tick_meta.filter_4h),
            monitored_pair=tick_meta.monitored_pair,
            position_pnl_snapshot=tick_meta.position_pnl_snapshot,
            position_pnl_by_pair=dict(tick_meta.position_pnl_by_pair),
            barrier_closes=list(tick_meta.barrier_closes),
            create_plans=dict(tick_meta.create_plans),
            adaptive_activation_streak=tick_meta.adaptive_activation_streak,
            thesis_decay_streak=tick_meta.thesis_decay_streak,
            entry_class=tick_meta.entry_class,
        )

    best_score = 0.0
    best_pair: str | None = None

    for pair in queue.macd_pairs:
        report_meta = nearest_report(
            macd_reports_by_pair,
            pair,
            tick_meta.timestamp,
            config.time_window_min,
            interval="1h",
        )
        if report_meta is None:
            continue
        parsed_1h = load_parsed_report(report_meta)
        if parsed_1h is None:
            continue
        metrics = compute_metrics(parsed_1h, config)
        strength_long = float(metrics.get("adaptive_strength_long") or 0.0)
        strength_short = float(metrics.get("adaptive_strength_short") or 0.0)
        pair_best = max(strength_long, strength_short)
        if pair_best > best_score:
            best_score = pair_best
            best_pair = pair

    return TickMeta(
        tick=tick_meta.tick,
        timestamp=tick_meta.timestamp,
        macd_pairs=list(queue.macd_pairs),
        tradeable_count=queue.tradeable_count,
        scanner_analyzed=queue.scanner_analyzed,
        scanner_regime=queue.regime,
        natr_floor_used=queue.natr_floor_used,
        best_score=best_score if best_pair else None,
        queue_total=list(queue.queue_total),
        natr_by_pair=dict(queue.natr_by_pair),
        signals_1h=dict(tick_meta.signals_1h),
        filter_4h=dict(tick_meta.filter_4h),
        monitored_pair=tick_meta.monitored_pair,
        position_pnl_snapshot=tick_meta.position_pnl_snapshot,
        position_pnl_by_pair=dict(tick_meta.position_pnl_by_pair),
        barrier_closes=list(tick_meta.barrier_closes),
        create_plans=dict(tick_meta.create_plans),
        adaptive_activation_streak=tick_meta.adaptive_activation_streak,
        thesis_decay_streak=tick_meta.thesis_decay_streak,
        entry_class=tick_meta.entry_class,
    )


def build_report_driven_ticks(
    schedule: dict[int, TickMeta],
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any] | None = None,
    macd_reports_by_pair: dict[str, list[ReportMeta]] | None = None,
    scanner_reports: list[ScannerReportMeta] | None = None,
    *,
    open_pairs_by_tick: dict[int, list[str]] | None = None,
) -> dict[int, TickMeta]:
    params = {**_default_strategy_params(config), **(strategy_params or {})}
    macd_index = macd_reports_by_pair or build_reports_by_pair(load_reports_index())
    scanner_index = scanner_reports or load_scanner_reports_index()
    open_by_tick = open_pairs_by_tick or {}

    built: dict[int, TickMeta] = {}
    for tick_number in sorted(schedule):
        built[tick_number] = _populate_tick_from_reports(
            schedule[tick_number],
            config,
            params,
            macd_index,
            scanner_index,
            open_pairs=open_by_tick.get(tick_number),
        )
    return built


def refresh_tick_meta_from_reports(
    meta: TickMeta,
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any],
    macd_reports_by_pair: dict[str, list[ReportMeta]],
    scanner_reports: list[ScannerReportMeta],
    *,
    open_pairs: list[str] | None = None,
) -> TickMeta:
    """Rebuild scanner queue fields for one tick (e.g. with live open legs)."""
    return _populate_tick_from_reports(
        meta,
        config,
        strategy_params,
        macd_reports_by_pair,
        scanner_reports,
        open_pairs=open_pairs,
    )


def build_session_parity_ticks(
    session_dir: Path,
    strategy_slug: str,
    config: DynamicStrategyReplayConfig | None = None,
) -> tuple[dict[int, TickMeta], DynamicStrategyReplayConfig, dict[str, Any]]:
    journal_path = session_dir / "journal.md"
    if not journal_path.is_file():
        return {}, DynamicStrategyReplayConfig(), {}

    schedule = parse_tick_schedule_file(journal_path)
    if config is not None and config.config_source == "preset":
        replay_config = config
        params = _default_strategy_params(config)
    else:
        replay_config, params = replay_config_from_session(
            session_dir,
            strategy_slug,
            base=config,
        )
    ticks = build_report_driven_ticks(schedule, replay_config, params)
    journal_ticks = parse_journal_ticks(journal_path.read_text(encoding="utf-8"), session_dir)
    # DeterministicRunner sessions store signal telemetry in tick JSONL, not
    # classic signals_1h journal blobs.
    try:
        session_num = int(session_dir.name.split("_", 1)[1])
    except (IndexError, ValueError):
        session_num = -1
    if session_num >= 0:
        from routines.macdbb_scanner_aggressive_hl_replay.live_tick_jsonl import (
            enrich_ticks_from_live_jsonl,
        )

        journal_ticks = enrich_ticks_from_live_jsonl(
            journal_ticks,
            session_num,
            strategy_slug=strategy_slug,
        )
    merged: dict[int, TickMeta] = {}
    for tick_number, meta in ticks.items():
        journal_meta = journal_ticks.get(tick_number)
        if journal_meta is None:
            merged[tick_number] = meta
            continue
        merged[tick_number] = TickMeta(
            tick=meta.tick,
            timestamp=meta.timestamp,
            macd_pairs=journal_meta.macd_pairs or meta.macd_pairs,
            tradeable_count=(
                journal_meta.tradeable_count
                if journal_meta.tradeable_count is not None
                else meta.tradeable_count
            ),
            scanner_analyzed=(
                journal_meta.scanner_analyzed
                if journal_meta.scanner_analyzed is not None
                else meta.scanner_analyzed
            ),
            scanner_regime=journal_meta.scanner_regime or meta.scanner_regime,
            natr_floor_used=(
                journal_meta.natr_floor_used
                if journal_meta.natr_floor_used is not None
                else meta.natr_floor_used
            ),
            best_score=(
                journal_meta.best_score
                if journal_meta.best_score is not None
                else meta.best_score
            ),
            queue_total=journal_meta.queue_total or meta.queue_total,
            natr_by_pair=meta.natr_by_pair,
            signals_1h=journal_meta.signals_1h or meta.signals_1h,
            filter_4h=journal_meta.filter_4h or meta.filter_4h,
            monitored_pair=journal_meta.monitored_pair or meta.monitored_pair,
            position_pnl_snapshot=journal_meta.position_pnl_snapshot
            if journal_meta.position_pnl_snapshot is not None
            else meta.position_pnl_snapshot,
            position_pnl_by_pair={
                **meta.position_pnl_by_pair,
                **journal_meta.position_pnl_by_pair,
            },
            barrier_closes=list(journal_meta.barrier_closes),
            create_plans=dict(journal_meta.create_plans),
            adaptive_activation_streak=journal_meta.adaptive_activation_streak
            if journal_meta.adaptive_activation_streak is not None
            else meta.adaptive_activation_streak,
            thesis_decay_streak=journal_meta.thesis_decay_streak
            if journal_meta.thesis_decay_streak is not None
            else meta.thesis_decay_streak,
            entry_class=journal_meta.entry_class or meta.entry_class,
        )
    return merged, replay_config, params


def preserve_journal_queue_fields(
    journal_meta: TickMeta,
    report_meta: TickMeta,
) -> TickMeta:
    """Keep journal scanner queue fields after per-tick report refresh."""
    if not journal_meta.signals_1h and not journal_meta.create_plans:
        return report_meta
    return TickMeta(
        tick=report_meta.tick,
        timestamp=report_meta.timestamp,
        macd_pairs=journal_meta.macd_pairs or report_meta.macd_pairs,
        tradeable_count=(
            journal_meta.tradeable_count
            if journal_meta.tradeable_count is not None
            else report_meta.tradeable_count
        ),
        scanner_analyzed=(
            journal_meta.scanner_analyzed
            if journal_meta.scanner_analyzed is not None
            else report_meta.scanner_analyzed
        ),
        scanner_regime=journal_meta.scanner_regime or report_meta.scanner_regime,
        natr_floor_used=(
            journal_meta.natr_floor_used
            if journal_meta.natr_floor_used is not None
            else report_meta.natr_floor_used
        ),
        best_score=(
            journal_meta.best_score
            if journal_meta.best_score is not None
            else report_meta.best_score
        ),
        queue_total=journal_meta.queue_total or report_meta.queue_total,
        natr_by_pair=report_meta.natr_by_pair,
        signals_1h=journal_meta.signals_1h or report_meta.signals_1h,
        filter_4h=journal_meta.filter_4h or report_meta.filter_4h,
        monitored_pair=journal_meta.monitored_pair or report_meta.monitored_pair,
        position_pnl_snapshot=(
            journal_meta.position_pnl_snapshot
            if journal_meta.position_pnl_snapshot is not None
            else report_meta.position_pnl_snapshot
        ),
        position_pnl_by_pair={
            **report_meta.position_pnl_by_pair,
            **journal_meta.position_pnl_by_pair,
        },
        barrier_closes=list(journal_meta.barrier_closes or report_meta.barrier_closes),
        create_plans=dict(journal_meta.create_plans or report_meta.create_plans),
        adaptive_activation_streak=(
            journal_meta.adaptive_activation_streak
            if journal_meta.adaptive_activation_streak is not None
            else report_meta.adaptive_activation_streak
        ),
        thesis_decay_streak=(
            journal_meta.thesis_decay_streak
            if journal_meta.thesis_decay_streak is not None
            else report_meta.thesis_decay_streak
        ),
        entry_class=journal_meta.entry_class or report_meta.entry_class,
    )


def build_timeline_ticks(
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any] | None = None,
) -> dict[int, TickMeta]:
    if not config.range_start_utc or not config.range_end_utc:
        raise ValueError("timeline_backtest requires range_start_utc and range_end_utc")
    start = parse_iso_utc(config.range_start_utc)
    end = parse_iso_utc(config.range_end_utc)
    schedule = build_range_tick_schedule(start, end, config.frequency_sec)
    return {
        tick_number: TickMeta(
            tick=tick_number,
            timestamp=meta.timestamp,
            macd_pairs=[],
        )
        for tick_number, meta in schedule.items()
    }
