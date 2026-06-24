"""Disk cache for report-driven timeline tick hydration."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig, TickMeta
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    MACDBB_FILENAME,
    MANIFEST_FILENAME,
    SCANNER_FILENAME,
    snapshot_dir_or_default,
)

CACHE_VERSION = 1
CACHE_PREFIX = "hydrated_ticks"


def _file_fingerprint(path: Path) -> str:
    if not path.is_file():
        return "missing"
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def hydrated_ticks_cache_key(
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any],
) -> str:
    root = snapshot_dir_or_default(getattr(config, "snapshot_dir", None))
    parts = {
        "version": CACHE_VERSION,
        "manifest": _file_fingerprint(root / MANIFEST_FILENAME),
        "scanner": _file_fingerprint(root / SCANNER_FILENAME),
        "macdbb": _file_fingerprint(root / MACDBB_FILENAME),
        "range_start_utc": config.range_start_utc or "",
        "range_end_utc": config.range_end_utc or "",
        "frequency_sec": config.frequency_sec,
        "time_window_min": config.time_window_min,
        "data_source": config.data_source,
        "strategy_params": strategy_params,
    }
    digest = hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def hydrated_ticks_cache_path(
    config: DynamicStrategyReplayConfig,
    cache_key: str,
) -> Path:
    root = snapshot_dir_or_default(getattr(config, "snapshot_dir", None))
    return root / f"{CACHE_PREFIX}_{cache_key}.pkl"


def load_hydrated_timeline_ticks(
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any],
) -> dict[int, TickMeta] | None:
    cache_key = hydrated_ticks_cache_key(config, strategy_params)
    path = hydrated_ticks_cache_path(config, cache_key)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cache_key") != cache_key or payload.get("version") != CACHE_VERSION:
        return None
    tick_map = payload.get("tick_map")
    if not isinstance(tick_map, dict):
        return None
    return tick_map


def save_hydrated_timeline_ticks(
    config: DynamicStrategyReplayConfig,
    strategy_params: dict[str, Any],
    tick_map: dict[int, TickMeta],
) -> Path:
    cache_key = hydrated_ticks_cache_key(config, strategy_params)
    path = hydrated_ticks_cache_path(config, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "cache_key": cache_key,
        "tick_map": tick_map,
    }
    temp_path = path.with_suffix(".pkl.tmp")
    with temp_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temp_path.replace(path)
    return path
