"""Hyperliquid per-asset leverage limits (public meta API)."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def hl_symbol_max_leverage(trading_pair: str) -> int | None:
    """Return Hyperliquid maxLeverage for a perpetual symbol, or None if unknown."""
    try:
        base = str(trading_pair).split("-")[0].split(":")[-1].upper()
        body = json.dumps({"type": "meta"}).encode()
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            meta = json.loads(resp.read())
        for asset in meta.get("universe", []):
            if str(asset.get("name", "")).upper() == base:
                lev = int(asset.get("maxLeverage", 0))
                return lev if lev > 0 else None
    except Exception:
        return None
    return None


def apply_hyperliquid_leverage_cap(config: dict[str, Any]) -> str:
    """Clamp leverage to Hyperliquid per-asset maxLeverage. Returns user note if adjusted."""
    cn = str(config.get("connector_name") or "")
    tp = str(config.get("trading_pair") or "")
    if "hyperliquid" not in cn.lower() or "perpetual" not in cn.lower() or not tp:
        return ""
    raw_lev = config.get("leverage")
    if raw_lev is None:
        return ""
    try:
        requested = int(float(raw_lev))
    except (TypeError, ValueError):
        return ""
    if requested <= 0:
        return ""
    hl_max = hl_symbol_max_leverage(tp)
    if hl_max is None or requested <= hl_max:
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
