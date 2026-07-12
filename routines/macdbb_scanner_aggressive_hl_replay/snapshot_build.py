"""Build and incrementally extend parquet replay snapshots."""

from __future__ import annotations

import datetime as dt
import logging
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import aiohttp

from routines.lib.binance_candle_cache import DEFAULT_CACHE_DIR as BINANCE_DEFAULT_CACHE_DIR
from routines.lib.binance_candles import configure_binance_rate_limit
from routines.lib.hl_candle_cache import DEFAULT_CACHE_DIR as HL_DEFAULT_CACHE_DIR
from routines.lib.hl_candles import configure_hl_rate_limit
from routines.lib.shared_universe import (
    load_intersection_manifest,
    universe_rows_for_exchange,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.report_backfill import (
    BackfillSettings,
    CandleCache,
    fetch_binance_universe,
    fetch_hl_universe,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_range import iso_utc
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import _default_strategy_params
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    append_states,
    existing_tick_ids,
    load_manifest,
    load_scanner_index,
    snapshot_dir_or_default,
    write_manifest,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import (
    TickMarketSettings,
    TickMarketState,
    compute_tick_market_state,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import build_range_tick_schedule

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotBuildSettings:
    snapshot_dir: Path
    candle_source: str = "binance_perpetual"
    cache_dir: Path | None = None
    frequency_sec: int = 1800
    volume_source: str = "same"
    intersection_manifest: Path | None = None
    universe_top_n: int = 100
    candidate_pool: int = 45
    batch_size: int = 50
    max_concurrent: int = 30
    file_cache_entries: int = 80
    request_interval_ms: int = 600
    max_retries: int = 8
    force: bool = False
    limit: int = 0
    lookback_hours: int = 6
    sessions: str = ""


@dataclass
class BuildResult:
    ticks: int = 0
    built: int = 0
    skipped: int = 0
    errors: int = 0
    range_start_utc: str | None = None
    range_end_utc: str | None = None


@dataclass(frozen=True)
class SnapshotGap:
    gap_start_utc: str
    gap_end_utc: str
    gap_days: float
    coverage_end_utc: str | None


def settings_from_replay_config(config: DynamicStrategyReplayConfig) -> SnapshotBuildSettings:
    cache_dir_raw = config.hl_cache_dir
    return SnapshotBuildSettings(
        snapshot_dir=snapshot_dir_or_default(config.snapshot_dir),
        candle_source=config.candle_source,
        cache_dir=Path(cache_dir_raw) if cache_dir_raw else None,
        frequency_sec=config.frequency_sec,
        lookback_hours=config.scanner_lookback_hours,
        request_interval_ms=config.hl_request_interval_ms,
        max_retries=config.hl_max_retries,
        max_concurrent=config.hl_max_concurrent,
    )


def merge_manifest(
    existing: dict[str, Any] | None,
    *,
    built: int,
    range_start_utc: str | None,
    range_end_utc: str | None,
    snapshot_dir: Path,
    cache_dir: Path,
    candle_source: str,
    volume_source: str,
    frequency_sec: int,
    intersection_manifest: Path | None,
    sessions: str,
) -> dict[str, Any]:
    """Merge incremental build metadata into an existing manifest."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    prior = dict(existing or {})
    prior_tick_count = int(prior.get("tick_count") or 0)
    merged_start = range_start_utc
    merged_end = range_end_utc
    if prior.get("range_start_utc") and merged_start:
        prior_start = parse_iso_utc(str(prior["range_start_utc"]))
        new_start = parse_iso_utc(merged_start)
        merged_start = iso_utc(min(prior_start, new_start))
    elif prior.get("range_start_utc"):
        merged_start = str(prior["range_start_utc"])
    if prior.get("range_end_utc") and merged_end:
        prior_end = parse_iso_utc(str(prior["range_end_utc"]))
        new_end = parse_iso_utc(merged_end)
        merged_end = iso_utc(max(prior_end, new_end))
    elif prior.get("range_end_utc"):
        merged_end = str(prior["range_end_utc"])

    return {
        "version": 1,
        "updated_at": now,
        "snapshot_dir": str(snapshot_dir),
        "cache_dir": str(cache_dir),
        "candle_source": candle_source,
        "volume_source": volume_source,
        "intersection_manifest": str(intersection_manifest) if intersection_manifest else None,
        "tick_count": prior_tick_count + built,
        "frequency_sec": frequency_sec,
        "range_start_utc": merged_start,
        "range_end_utc": merged_end,
        "sessions": sessions,
        **{
            key: value
            for key, value in prior.items()
            if key
            not in {
                "version",
                "updated_at",
                "snapshot_dir",
                "cache_dir",
                "candle_source",
                "volume_source",
                "intersection_manifest",
                "tick_count",
                "frequency_sec",
                "range_start_utc",
                "range_end_utc",
                "sessions",
            }
        },
    }


def compute_snapshot_gap(
    requested_start: dt.datetime,
    requested_end: dt.datetime,
    *,
    coverage_start: dt.datetime | None,
    coverage_end: dt.datetime | None,
) -> SnapshotGap | None:
    """Return the UTC sub-range that needs building, or None if fully covered."""
    if coverage_end is None and coverage_start is None:
        gap_days = (requested_end - requested_start).total_seconds() / 86400.0
        return SnapshotGap(
            gap_start_utc=iso_utc(requested_start),
            gap_end_utc=iso_utc(requested_end),
            gap_days=gap_days,
            coverage_end_utc=None,
        )

    if coverage_end is not None and requested_end > coverage_end:
        gap_start = max(requested_start, coverage_end)
        gap_end = requested_end
        if gap_start < gap_end:
            gap_days = (gap_end - gap_start).total_seconds() / 86400.0
            return SnapshotGap(
                gap_start_utc=iso_utc(gap_start),
                gap_end_utc=iso_utc(gap_end),
                gap_days=gap_days,
                coverage_end_utc=iso_utc(coverage_end),
            )

    if coverage_start is not None and requested_start < coverage_start:
        gap_start = requested_start
        gap_end = min(requested_end, coverage_start)
        if gap_start < gap_end:
            gap_days = (gap_end - gap_start).total_seconds() / 86400.0
            return SnapshotGap(
                gap_start_utc=iso_utc(gap_start),
                gap_end_utc=iso_utc(gap_end),
                gap_days=gap_days,
                coverage_end_utc=iso_utc(coverage_end) if coverage_end else None,
            )

    return None


def _resolve_cache_dir(settings: SnapshotBuildSettings) -> Path:
    if settings.cache_dir is not None:
        return settings.cache_dir
    return (
        BINANCE_DEFAULT_CACHE_DIR
        if settings.candle_source == "binance_perpetual"
        else HL_DEFAULT_CACHE_DIR
    )


def _tick_times_for_range(
    start: dt.datetime,
    end: dt.datetime,
    frequency_sec: int,
    *,
    snapshot_dir: Path,
    force: bool,
    limit: int,
) -> list[dt.datetime]:
    schedule = build_range_tick_schedule(start, end, frequency_sec)
    tick_times = [meta.timestamp for meta in schedule.values()]
    if limit > 0:
        tick_times = tick_times[:limit]
    if force:
        return tick_times
    known_ticks = existing_tick_ids(snapshot_dir=snapshot_dir)
    return [
        tick_time
        for tick_time in tick_times
        if tick_time.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M%S") not in known_ticks
    ]


async def build_snapshots_for_range(
    range_start: dt.datetime | str,
    range_end: dt.datetime | str,
    settings: SnapshotBuildSettings,
    *,
    tick_times: list[dt.datetime] | None = None,
    on_progress: Callable[[int, int, dict[str, int]], None] | None = None,
    install_signal_handlers: bool = True,
) -> BuildResult:
    """Build scanner + MACD BB parquet snapshots for a UTC date range."""
    start = parse_iso_utc(range_start) if isinstance(range_start, str) else range_start
    end = parse_iso_utc(range_end) if isinstance(range_end, str) else range_end
    cache_dir = _resolve_cache_dir(settings)

    if settings.candle_source == "binance_perpetual":
        configure_binance_rate_limit(
            request_interval_ms=settings.request_interval_ms,
            max_retries=settings.max_retries,
        )
    else:
        configure_hl_rate_limit(
            request_interval_ms=settings.request_interval_ms,
            max_retries=settings.max_retries,
        )

    volume_source = (
        settings.candle_source
        if settings.volume_source == "same"
        else settings.volume_source
    )
    tick_times = tick_times or _tick_times_for_range(
        start,
        end,
        settings.frequency_sec,
        snapshot_dir=settings.snapshot_dir,
        force=settings.force,
        limit=settings.limit,
    )
    if tick_times and settings.limit > 0:
        tick_times = tick_times[: settings.limit]
    if not settings.force and tick_times:
        known_ticks = existing_tick_ids(snapshot_dir=settings.snapshot_dir)
        tick_times = [
            tick_time
            for tick_time in tick_times
            if tick_time.astimezone(dt.timezone.utc).strftime("%Y%m%d_%H%M%S") not in known_ticks
        ]

    backfill_settings = BackfillSettings(
        candidate_pool=settings.candidate_pool,
        cache_dir=cache_dir,
        candle_source=settings.candle_source,
        lookback_hours=settings.lookback_hours,
    )
    market_settings = TickMarketSettings(
        lookback_hours=backfill_settings.lookback_hours,
        top_n=backfill_settings.top_n,
        min_volume_usd=backfill_settings.min_volume_usd,
        mature_count=backfill_settings.mature_count,
        degen_count=backfill_settings.degen_count,
        candidate_pool=backfill_settings.candidate_pool,
        macd_review_count=backfill_settings.macd_review_count,
        macd_pairs_superset=backfill_settings.macd_review_count,
        max_concurrent=settings.max_concurrent,
    )
    strategy_params = _default_strategy_params(DynamicStrategyReplayConfig())

    totals = {"ticks": len(tick_times), "built": 0, "skipped": 0, "errors": 0}
    pending_states: list[TickMarketState] = []
    stop_requested = False

    def _request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        logger.info(
            "Stop requested (signal %s); finishing current tick then flushing batch",
            signum,
        )

    def _flush_pending() -> None:
        if not pending_states:
            return
        append_states(pending_states, snapshot_dir=settings.snapshot_dir)
        pending_states.clear()

    previous_handlers: dict[int, Any] = {}
    if install_signal_handlers:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[sig] = signal.signal(sig, _request_stop)
            except (AttributeError, ValueError, OSError):
                pass

    if not tick_times:
        logger.info("No ticks to build for %s → %s", iso_utc(start), iso_utc(end))
        return BuildResult(
            ticks=0,
            built=0,
            skipped=0,
            errors=0,
            range_start_utc=iso_utc(start),
            range_end_utc=iso_utc(end),
        )

    try:
        async with aiohttp.ClientSession() as session:
            cache = CandleCache(
                session=session,
                cache_dir=cache_dir,
                candle_source=settings.candle_source,
                max_file_cache_entries=settings.file_cache_entries,
            )
            volume_cache: CandleCache | None = None
            if volume_source != settings.candle_source:
                volume_cache_dir = (
                    BINANCE_DEFAULT_CACHE_DIR
                    if volume_source == "binance_perpetual"
                    else HL_DEFAULT_CACHE_DIR
                )
                volume_cache = CandleCache(
                    session=session,
                    cache_dir=volume_cache_dir,
                    candle_source=volume_source,
                    max_file_cache_entries=settings.file_cache_entries,
                )
            if settings.intersection_manifest:
                intersection = load_intersection_manifest(settings.intersection_manifest)
                universe = universe_rows_for_exchange(intersection, settings.candle_source)
            elif settings.candle_source == "binance_perpetual":
                universe = await fetch_binance_universe(
                    session,
                    top_n=settings.universe_top_n,
                    min_volume_usd=backfill_settings.min_volume_usd,
                )
            else:
                universe = await fetch_hl_universe(
                    session,
                    exclude_hip3=backfill_settings.exclude_hip3,
                )
                if settings.universe_top_n > 0:
                    universe = universe[: settings.universe_top_n]

            for index, tick_time in enumerate(tick_times, start=1):
                if stop_requested:
                    logger.info("Stopping build after %d ticks in this run", index - 1)
                    break
                try:
                    state = await compute_tick_market_state(
                        tick_time,
                        universe=universe,
                        loader=cache,
                        settings=market_settings,
                        strategy_params=strategy_params,
                        volume_loader=volume_cache,
                    )
                    if state.parsed_scanner is None:
                        totals["skipped"] += 1
                        continue
                    pending_states.append(state)
                    totals["built"] += 1
                    if len(pending_states) >= settings.batch_size:
                        _flush_pending()
                except Exception:
                    totals["errors"] += 1
                    logger.exception("Snapshot build failed at %s", tick_time)
                    continue
                if on_progress:
                    on_progress(index, len(tick_times), dict(totals))
                elif index % 10 == 0 or index == len(tick_times):
                    logger.info(
                        "Progress %d/%d — built=%d skipped=%d errors=%d pending=%d",
                        index,
                        len(tick_times),
                        totals["built"],
                        totals["skipped"],
                        totals["errors"],
                        len(pending_states),
                    )
    finally:
        _flush_pending()
        if install_signal_handlers:
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (AttributeError, ValueError, OSError):
                    pass

    existing_manifest = load_manifest(snapshot_dir=settings.snapshot_dir)
    write_manifest(
        merge_manifest(
            existing_manifest,
            built=totals["built"],
            range_start_utc=iso_utc(start),
            range_end_utc=iso_utc(end),
            snapshot_dir=settings.snapshot_dir,
            cache_dir=cache_dir,
            candle_source=settings.candle_source,
            volume_source=volume_source,
            frequency_sec=settings.frequency_sec,
            intersection_manifest=settings.intersection_manifest,
            sessions=settings.sessions,
        ),
        snapshot_dir=settings.snapshot_dir,
    )
    return BuildResult(
        ticks=totals["ticks"],
        built=totals["built"],
        skipped=totals["skipped"],
        errors=totals["errors"],
        range_start_utc=iso_utc(start),
        range_end_utc=iso_utc(end),
    )


def coverage_datetimes(
    snapshot_dir: Path | str | None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Return (coverage_start, coverage_end) from manifest or scanner index."""
    root = snapshot_dir_or_default(snapshot_dir)
    manifest = load_manifest(snapshot_dir=root)
    coverage_start: dt.datetime | None = None
    coverage_end: dt.datetime | None = None
    if manifest:
        raw_start = manifest.get("range_start_utc")
        raw_end = manifest.get("range_end_utc")
        if raw_start:
            coverage_start = parse_iso_utc(str(raw_start))
        if raw_end:
            coverage_end = parse_iso_utc(str(raw_end))
    if coverage_start is None or coverage_end is None:
        reports = load_scanner_index(snapshot_dir=root)
        if reports:
            if coverage_start is None:
                coverage_start = min(report.created_at for report in reports)
            if coverage_end is None:
                coverage_end = max(report.created_at for report in reports)
    return coverage_start, coverage_end
