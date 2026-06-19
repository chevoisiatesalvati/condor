"""Configure replay data sources (HTML reports vs parquet snapshots)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from routines.macdbb_replay.snapshot_store import configure_snapshot_dir

if TYPE_CHECKING:
    from routines.macdbb_replay.models import ReplayConfigBase


def is_report_driven_data_source(data_source: str) -> bool:
    return data_source in ("reports_only", "snapshots")


def uses_snapshot_store(config: ReplayConfigBase) -> bool:
    return config.data_source == "snapshots" or bool(getattr(config, "snapshot_dir", None))


def configure_replay_data_sources(config: ReplayConfigBase) -> None:
    """Apply snapshot store configuration from replay config."""
    snapshot_dir = getattr(config, "snapshot_dir", None)
    if uses_snapshot_store(config):
        if snapshot_dir:
            configure_snapshot_dir(Path(snapshot_dir))
        else:
            from routines.macdbb_replay.snapshot_store import DEFAULT_SNAPSHOT_DIR

            configure_snapshot_dir(DEFAULT_SNAPSHOT_DIR)
    else:
        configure_snapshot_dir(None)
