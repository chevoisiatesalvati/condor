"""Structured progress updates for long-running routine workers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PROGRESS_ENV = "CONDOR_ROUTINE_PROGRESS_PATH"
PROGRESS_FILENAME_SUFFIX = ".progress.json"


def progress_path_from_env() -> Path | None:
    raw = (os.environ.get(PROGRESS_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw)


def progress_path_for_instance(instance_id: str, runs_dir: Path) -> Path:
    return runs_dir / f"{instance_id}{PROGRESS_FILENAME_SUFFIX}"


def write_progress(
    *,
    phase: str,
    message: str = "",
    current: int | float | None = None,
    total: int | float | None = None,
    percent: float | None = None,
    path: Path | str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Atomically write a progress JSON file for the current routine worker.

    Returns the path written, or None when no progress path is configured.
    """
    target = Path(path) if path is not None else progress_path_from_env()
    if target is None:
        return None

    resolved_percent = percent
    if (
        resolved_percent is None
        and current is not None
        and total is not None
        and float(total) > 0
    ):
        resolved_percent = round(100.0 * float(current) / float(total), 2)

    payload: dict[str, Any] = {
        "phase": str(phase),
        "message": str(message or ""),
        "current": current,
        "total": total,
        "percent": resolved_percent,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        payload.update(extra)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def read_progress(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    progress_path = Path(path)
    if not progress_path.is_file():
        return None
    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_log_tail(
    path: Path | str,
    *,
    offset: int = 0,
    tail: int = 200,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Return log lines from ``offset`` (byte offset into file) with optional tail.

    When ``offset`` is 0, return the last ``tail`` lines and set ``next_offset``
    to the current file size so subsequent polls only append new content.
    """
    log_path = Path(path)
    if not log_path.is_file():
        return {
            "lines": [],
            "next_offset": 0,
            "truncated": False,
            "complete": False,
            "size": 0,
        }

    size = log_path.stat().st_size
    start = max(0, int(offset))
    if start > size:
        start = size
    tail = max(1, int(tail))

    with log_path.open("rb") as handle:
        if start == 0:
            # Initial poll: last N lines.
            read_from = max(0, size - max_bytes)
            handle.seek(read_from)
            chunk = handle.read()
            text = chunk.decode("utf-8", errors="replace")
            lines = text.splitlines()
            truncated = read_from > 0 or len(lines) > tail
            if read_from > 0 and lines:
                lines = lines[1:]  # drop partial first line after mid-file seek
            if len(lines) > tail:
                lines = lines[-tail:]
            next_offset = size
        else:
            handle.seek(start)
            chunk = handle.read(max_bytes)
            next_offset = start + len(chunk)
            if next_offset < size and chunk and not chunk.endswith(b"\n"):
                last_nl = chunk.rfind(b"\n")
                if last_nl >= 0:
                    chunk = chunk[: last_nl + 1]
                    next_offset = start + len(chunk)
                else:
                    chunk = b""
                    next_offset = start
            lines = chunk.decode("utf-8", errors="replace").splitlines()
            truncated = False

    return {
        "lines": lines,
        "next_offset": next_offset,
        "truncated": truncated,
        "complete": False,
        "size": size,
    }


def resolve_safe_log_path(
    log_path: str | Path | None,
    *,
    runs_dir: Path,
) -> Path | None:
    """Resolve a log path only if it stays inside ``runs_dir``."""
    if not log_path:
        return None
    runs_root = runs_dir.resolve()
    candidate = Path(log_path)
    if not candidate.is_absolute():
        candidate = runs_root / candidate.name
    try:
        resolved = candidate.resolve()
        resolved.relative_to(runs_root)
    except (OSError, ValueError):
        return None
    return resolved
