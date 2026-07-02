"""Debug-mode NDJSON trace — delegates to open_position_audit."""

from __future__ import annotations

from typing import Any

from condor.open_position_audit import log_open_position_event


def debug_trace(
    *,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    log_open_position_event(
        phase=location,
        message=message,
        data=data,
        hypothesis_id=hypothesis_id,
        run_id=run_id,
    )
