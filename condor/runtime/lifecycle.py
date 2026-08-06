"""Process-level lifecycle timestamps (not reloaded on web hot-reload)."""

from __future__ import annotations

import time

_PROCESS_STARTED_AT = time.time()


def process_started_at() -> float:
    """When this Condor process started (Unix timestamp)."""
    return _PROCESS_STARTED_AT
