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
    try:
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
    filled_f = float(filled or 0)
    position_f = float(position_size or 0)
    is_filled = filled_f > 0 or position_f > 0

    error_fields = {
        k: ci[k]
        for k in ci
        if any(token in k.lower() for token in ("error", "fail", "reject", "reason"))
    }
    order_ids = ci.get("order_ids")
    order_id_count = len(order_ids) if isinstance(order_ids, list) else None

    return {
        "status": detail.get("status"),
        "close_type": detail.get("close_type"),
        "trading_pair": detail.get("trading_pair") or cfg.get("trading_pair"),
        "connector_name": detail.get("connector_name") or cfg.get("connector_name"),
        "entry_price": entry_price,
        "filled_amount_quote": filled,
        "position_size_quote": position_size,
        "is_filled": is_filled,
        "has_position": is_filled,
        "current_retries": ci.get("current_retries"),
        "max_retries": ci.get("max_retries"),
        "order_id_count": order_id_count,
        "custom_info_keys": sorted(ci.keys()) if ci else [],
        "error_fields": error_fields or None,
    }
