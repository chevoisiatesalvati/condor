"""Configure replay data sources (HTML reports vs parquet snapshots)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    configure_snapshot_dir,
    reload_snapshot_caches,
    warm_snapshot_caches,
)

if TYPE_CHECKING:
    from routines.macdbb_scanner_aggressive_hl_replay.models import ReplayConfigBase


def is_report_driven_data_source(data_source: str) -> bool:
    return data_source in ("reports_only", "snapshots")


def uses_snapshot_store(config: ReplayConfigBase) -> bool:
    return config.data_source == "snapshots" or bool(getattr(config, "snapshot_dir", None))


def should_prefetch_replay_candles(config: ReplayConfigBase) -> bool:
    """Prefetch 1m candles for intrabar SL/TP when barriers or candle prices are used."""
    if config.price_source in ("auto", "hl_candles", "binance_candles"):
        return True
    enable_barriers = getattr(config, "enable_dynamic_barriers", False)
    return bool(enable_barriers)


def configure_replay_data_sources(config: ReplayConfigBase) -> None:
    """Apply snapshot store configuration from replay config."""
    snapshot_dir = getattr(config, "snapshot_dir", None)
    if uses_snapshot_store(config):
        if snapshot_dir:
            configure_snapshot_dir(Path(snapshot_dir))
        else:
            from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import DEFAULT_SNAPSHOT_DIR

            configure_snapshot_dir(DEFAULT_SNAPSHOT_DIR)
        if config.data_source == "snapshots":
            range_start = str(getattr(config, "range_start_utc", "") or "").strip() or None
            range_end = str(getattr(config, "range_end_utc", "") or "").strip() or None
            if range_start and range_end:
                warm_snapshot_caches(
                    snapshot_dir,
                    range_start_utc=range_start,
                    range_end_utc=range_end,
                )
            else:
                warm_snapshot_caches(snapshot_dir)
    else:
        configure_snapshot_dir(None)


def refresh_snapshot_caches(config: ReplayConfigBase) -> None:
    """Rebind snapshot dir and reload parquet indexes (after incremental builds)."""
    if not uses_snapshot_store(config):
        configure_snapshot_dir(None)
        return
    snapshot_dir = getattr(config, "snapshot_dir", None)
    if snapshot_dir:
        configure_snapshot_dir(Path(snapshot_dir))
    else:
        from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import DEFAULT_SNAPSHOT_DIR

        configure_snapshot_dir(DEFAULT_SNAPSHOT_DIR)
    range_start = str(getattr(config, "range_start_utc", "") or "").strip() or None
    range_end = str(getattr(config, "range_end_utc", "") or "").strip() or None
    reload_snapshot_caches(
        snapshot_dir,
        range_start_utc=range_start,
        range_end_utc=range_end,
    )
