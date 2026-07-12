"""In-memory + on-disk cache for historical candle ranges (terminated charts)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
DISK_CACHE_DIR = _REPO_ROOT / "data" / "candle_cache"

# Live/near-live requests: short TTL, small memory cap
_TTL_LIVE_SEC = 30.0
_MEMORY_MAX_LIVE = 50

# Historical ranges (end_time >1h ago): long TTL, persisted to disk
_HISTORICAL_END_GRACE_SEC = 3600
_TTL_HISTORICAL_MEM_SEC = 6 * 3600.0
_TTL_HISTORICAL_DISK_SEC = 30 * 86400.0
_MEMORY_MAX_HISTORICAL = 300

_memory_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}


def _is_historical(end_time: float | None) -> bool:
    if end_time is None:
        return False
    return float(end_time) < time.time() - _HISTORICAL_END_GRACE_SEC


def cache_ttl(end_time: float | None) -> float:
    return _TTL_HISTORICAL_MEM_SEC if _is_historical(end_time) else _TTL_LIVE_SEC


def _memory_max(end_time: float | None) -> int:
    return _MEMORY_MAX_HISTORICAL if _is_historical(end_time) else _MEMORY_MAX_LIVE


def _key_hash(cache_key: tuple) -> str:
    return hashlib.sha256(repr(cache_key).encode()).hexdigest()[:32]


def _disk_path(cache_key: tuple) -> Path:
    return DISK_CACHE_DIR / f"{_key_hash(cache_key)}.json"


def get_cached_candles(cache_key: tuple, end_time: float | None) -> list[dict[str, Any]] | None:
    """Return cached candle dicts if present in memory or on disk."""
    now = time.monotonic()
    ttl = cache_ttl(end_time)
    mem = _memory_cache.get(cache_key)
    if mem and (now - mem[0]) < ttl:
        return mem[1]

    if not _is_historical(end_time):
        return None

    path = _disk_path(cache_key)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > _TTL_HISTORICAL_DISK_SEC:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            return None
        _memory_cache[cache_key] = (now, data)
        return data
    except Exception:
        logger.debug("Failed reading candle disk cache %s", path, exc_info=True)
        return None


def put_cached_candles(
    cache_key: tuple,
    candles: list[dict[str, Any]],
    end_time: float | None,
) -> None:
    """Store candles in memory; persist historical ranges to disk."""
    now = time.monotonic()
    ttl = cache_ttl(end_time)
    expired = [k for k, (ts, _) in _memory_cache.items() if now - ts >= ttl]
    for k in expired:
        _memory_cache.pop(k, None)

    _memory_cache[cache_key] = (now, candles)
    cap = _memory_max(end_time)
    while len(_memory_cache) > cap:
        _memory_cache.pop(next(iter(_memory_cache)))

    if not _is_historical(end_time) or not candles:
        return

    try:
        DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _disk_path(cache_key)
        path.write_text(json.dumps(candles))
    except Exception:
        logger.debug("Failed writing candle disk cache", exc_info=True)
