"""Persist MacdbbState + runner inventory locks across process restarts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from condor.strategy_runners.macdbb.types import MacdbbState

log = logging.getLogger(__name__)

STATE_FILENAME = "macdbb_state.json"
PENDING_OPEN_TTL_TICKS = 5


def state_path(session_dir: Path | None) -> Path | None:
    if session_dir is None:
        return None
    return Path(session_dir) / STATE_FILENAME


def load_runner_state(session_dir: Path | None) -> dict[str, Any]:
    """Return ``{macdbb_state, pending_opens, last_running_ids, barrier_notified_ids}``."""
    path = state_path(session_dir)
    empty = {
        "macdbb_state": MacdbbState(),
        "pending_opens": {},
        "last_running_ids": [],
        "barrier_notified_ids": [],
    }
    if path is None or not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Failed to read %s", path, exc_info=True)
        return empty
    if not isinstance(raw, dict):
        return empty
    return {
        "macdbb_state": MacdbbState.from_dict(raw.get("macdbb_state")),
        "pending_opens": dict(raw.get("pending_opens") or {}),
        "last_running_ids": [str(x) for x in (raw.get("last_running_ids") or [])],
        "barrier_notified_ids": [
            str(x) for x in (raw.get("barrier_notified_ids") or [])
        ],
    }


def save_runner_state(
    session_dir: Path | None,
    *,
    macdbb_state: MacdbbState,
    pending_opens: dict[str, Any],
    last_running_ids: set[str] | list[str],
    barrier_notified_ids: set[str] | list[str],
) -> None:
    path = state_path(session_dir)
    if path is None:
        return
    payload = {
        "macdbb_state": macdbb_state.to_dict(),
        "pending_opens": pending_opens,
        "last_running_ids": sorted(str(x) for x in last_running_ids),
        "barrier_notified_ids": sorted(str(x) for x in barrier_notified_ids),
    }
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        log.warning("Failed to write %s", path, exc_info=True)
