"""NDJSON audit trail for position_executor create + open-fill outcomes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_LOG = _REPO_ROOT / ".cursor" / "open-position-audit.ndjson"


def audit_log_path() -> Path:
    raw = os.environ.get("CONDOR_OPEN_POSITION_LOG", "").strip()
    if raw:
        return Path(raw)
    raw = os.environ.get("CONDOR_MCP_AUDIT_LOG", "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_LOG


def log_open_position_event(
    *,
    phase: str,
    message: str = "",
    data: dict[str, Any] | None = None,
    hypothesis_id: str = "",
    run_id: str = "open-audit",
) -> None:
    """Append one NDJSON line. Never raises."""
    payload: dict[str, Any] = {
        "timestamp": int(time.time() * 1000),
        "phase": phase,
        "message": message,
        "data": data or {},
        "runId": run_id,
    }
    if hypothesis_id:
        payload["hypothesisId"] = hypothesis_id
    try:
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except OSError:
        pass


def config_audit_slice(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    tbc = config.get("triple_barrier_config")
    tbc_out: dict[str, Any] = {}
    if isinstance(tbc, dict):
        for key in (
            "open_order_type",
            "stop_loss",
            "take_profit",
            "stop_loss_order_type",
            "take_profit_order_type",
        ):
            if key in tbc:
                tbc_out[key] = tbc[key]
    out: dict[str, Any] = {
        "connector_name": config.get("connector_name"),
        "trading_pair": config.get("trading_pair"),
        "side": config.get("side"),
        "leverage": config.get("leverage"),
        "amount": config.get("amount"),
        "entry_price": config.get("entry_price"),
    }
    if tbc_out:
        out["triple_barrier_config"] = tbc_out
    return out


def summarize_executor_open_state(detail: Any) -> dict[str, Any]:
    """Compact executor snapshot for open-debug (create vs filled vs failed)."""
    if not isinstance(detail, dict):
        return {"raw_type": type(detail).__name__}

    ci = detail.get("custom_info") if isinstance(detail.get("custom_info"), dict) else {}
    cfg = detail.get("config") if isinstance(detail.get("config"), dict) else {}

    entry_price = detail.get("entry_price") or ci.get("current_position_average_price")
    filled = detail.get("filled_amount_quote")
    if filled is None:
        filled = ci.get("realized_buy_size_quote") or ci.get("realized_sell_size_quote")
    position_size = ci.get("position_size_quote")

    error_fields = {
        k: ci[k]
        for k in ci
        if any(token in k.lower() for token in ("error", "fail", "reject", "reason"))
    }

    return {
        "status": detail.get("status"),
        "close_type": detail.get("close_type"),
        "trading_pair": detail.get("trading_pair") or cfg.get("trading_pair"),
        "connector_name": detail.get("connector_name") or cfg.get("connector_name"),
        "entry_price": entry_price,
        "filled_amount_quote": filled,
        "position_size_quote": position_size,
        "has_position": bool(
            (filled and float(filled or 0) > 0)
            or (position_size and float(position_size or 0) > 0)
            or (entry_price and float(entry_price or 0) > 0)
        ),
        "custom_info_keys": sorted(ci.keys()) if ci else [],
        "error_fields": error_fields or None,
    }
