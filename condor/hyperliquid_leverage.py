"""Hyperliquid per-asset leverage limits (public meta API)."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Used only when the meta API is unreachable / symbol unknown.
_FALLBACK_LEVERAGE = 10
_META_TTL_SEC = 3600.0
_meta_by_base: dict[str, int] | None = None
_meta_fetched_at: float = 0.0

_MAX_LEVERAGE_SENTINELS = frozenset({"", "max", "auto", "none", "null"})


def _base_symbol(trading_pair: str) -> str:
    return str(trading_pair).split("-")[0].split(":")[-1].upper()


def _load_meta_leverage_map(*, force: bool = False) -> dict[str, int]:
    """Return ``{BASE: maxLeverage}`` from Hyperliquid meta, cached briefly."""
    global _meta_by_base, _meta_fetched_at
    now = time.time()
    if (
        not force
        and _meta_by_base is not None
        and (now - _meta_fetched_at) < _META_TTL_SEC
    ):
        return _meta_by_base
    try:
        body = json.dumps({"type": "meta"}).encode()
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            meta = json.loads(resp.read())
        mapping: dict[str, int] = {}
        for asset in meta.get("universe", []):
            name = str(asset.get("name", "")).upper()
            if not name:
                continue
            lev = int(asset.get("maxLeverage", 0) or 0)
            if lev > 0:
                mapping[name] = lev
        if mapping:
            _meta_by_base = mapping
            _meta_fetched_at = now
            return mapping
    except Exception:
        logger.debug("Hyperliquid meta leverage fetch failed", exc_info=True)
    return _meta_by_base or {}


def hl_symbol_max_leverage(trading_pair: str) -> int | None:
    """Return Hyperliquid maxLeverage for a perpetual symbol, or None if unknown."""
    base = _base_symbol(trading_pair)
    if not base:
        return None
    return _load_meta_leverage_map().get(base)


def _parse_requested_leverage(raw_lev: Any) -> int | None:
    """Return positive leverage, None for unset/max sentinel, or raise ValueError."""
    if raw_lev is None:
        return None
    if isinstance(raw_lev, str) and raw_lev.strip().lower() in _MAX_LEVERAGE_SENTINELS:
        return None
    requested = int(float(raw_lev))
    if requested <= 0:
        return None
    return requested


def apply_hyperliquid_leverage_cap(config: dict[str, Any]) -> str:
    """Resolve leverage for Hyperliquid perps.

    - Missing / ``max`` / ``auto`` → use per-asset ``maxLeverage`` from meta.
    - Explicit value → clamp down to that asset's max when needed.
    - Meta unavailable → keep explicit value, else fall back to
      ``_FALLBACK_LEVERAGE``.

    Returns a short user note when leverage was filled or clamped.
    """
    cn = str(config.get("connector_name") or "")
    tp = str(config.get("trading_pair") or "")
    if "hyperliquid" not in cn.lower() or "perpetual" not in cn.lower() or not tp:
        return ""

    try:
        requested = _parse_requested_leverage(config.get("leverage"))
    except (TypeError, ValueError):
        requested = None

    hl_max = hl_symbol_max_leverage(tp)

    if requested is None:
        if hl_max is not None:
            config["leverage"] = hl_max
            logger.info(
                "position_executor: using Hyperliquid max leverage %sx for %s on %s",
                hl_max,
                tp,
                cn,
            )
            return f"Using Hyperliquid max leverage {hl_max}x for `{tp}`.\n"
        config["leverage"] = _FALLBACK_LEVERAGE
        logger.warning(
            "position_executor: meta max leverage unavailable for %s; "
            "using fallback %sx",
            tp,
            _FALLBACK_LEVERAGE,
        )
        return (
            f"Hyperliquid max leverage unavailable for `{tp}`; "
            f"using fallback {_FALLBACK_LEVERAGE}x.\n"
        )

    if hl_max is None or requested <= hl_max:
        config["leverage"] = requested
        return ""

    config["leverage"] = hl_max
    logger.info(
        "position_executor: clamped leverage %s -> %s for %s on %s",
        requested,
        hl_max,
        tp,
        cn,
    )
    return (
        f"Leverage clamped from {requested}x to {hl_max}x for `{tp}` "
        f"(Hyperliquid maxLeverage for this asset).\n"
    )
