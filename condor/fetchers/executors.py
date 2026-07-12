"""Fetch and manage executors via Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

from condor.hyperliquid_leverage import apply_hyperliquid_leverage_cap

logger = logging.getLogger(__name__)

# Safety cap to avoid runaway pagination loops
MAX_EXECUTORS_FETCH = 5000
EXECUTORS_PAGE_SIZE = 500


# ============================================
# EXTRACTION / PARSING HELPERS
# ============================================


def extract_executors_list(result) -> list[dict]:
    """Extract executor list from various API response shapes."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def get_executor_type(executor: Dict[str, Any]) -> str:
    """Determine executor type from its data.

    Returns the executor type label (e.g. 'grid', 'position', 'order', 'dca', 'lp').
    """
    config = get_executor_config(executor)
    for source in (config, executor):
        ex_type = source.get("type", "") or source.get("executor_type", "")
        if isinstance(ex_type, str) and ex_type:
            label = ex_type.lower().replace("_executor", "").replace("executor", "").strip("_")
            if label:
                return label
    if "start_price" in config and "end_price" in config:
        return "grid"
    if "stop_loss" in config or "trailing_stop" in config:
        return "position"
    return "unknown"


def get_executor_pnl(executor: Dict[str, Any]) -> float:
    """Extract PnL from an executor response."""
    for key in (
        "net_pnl_quote", "pnl_quote", "unrealized_pnl_quote",
        "realized_pnl_quote", "net_pnl", "pnl", "close_pnl",
    ):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def get_executor_volume(executor: Dict[str, Any]) -> float:
    """Extract filled/traded volume from an executor response."""
    for key in ("filled_amount_quote", "volume_traded", "total_volume"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def get_executor_fees(executor: Dict[str, Any]) -> float:
    """Extract cumulative fees from an executor response."""
    for key in ("cum_fees_quote", "fees_quote", "total_fees"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def _positive_float(value: Any) -> float | None:
    try:
        price = float(value or 0)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _timestamp_to_seconds(raw: Any) -> float | None:
    """Parse HB executor timestamp fields to unix seconds."""
    if raw is None or raw == "" or raw == 0:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        return ts / 1000.0 if ts > 1e12 else ts
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        from datetime import datetime

        dt = datetime.fromisoformat(text)
        return dt.timestamp()
    except ValueError:
        pass
    try:
        ts = float(text)
        return ts / 1000.0 if ts > 1e12 else ts
    except (TypeError, ValueError):
        return None


def get_executor_timestamp(executor: Dict[str, Any]) -> float:
    """Resolve executor open time from API shapes (config, top-level, created_at)."""
    config = get_executor_config(executor)
    for source in (config, executor):
        for key in ("timestamp", "created_at"):
            ts = _timestamp_to_seconds(source.get(key))
            if ts is not None and ts > 0:
                return ts
    return 0.0


def get_executor_close_timestamp(executor: Dict[str, Any]) -> float:
    """Resolve executor close time from API shapes."""
    for key in ("close_timestamp", "closed_at"):
        ts = _timestamp_to_seconds(executor.get(key))
        if ts is not None and ts > 0:
            return ts
    return 0.0


def parse_executor_json_field(val: Any) -> dict[str, Any]:
    """Parse executor ``config`` / ``custom_info`` that may be JSON strings."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        import json

        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def get_executor_config(executor: Dict[str, Any]) -> Dict[str, Any]:
    """Resolved config dict (parsed JSON string or top-level fallback)."""
    raw = executor.get("config")
    if isinstance(raw, dict) and raw:
        return raw
    parsed = parse_executor_json_field(raw)
    if parsed:
        return parsed
    return executor


def get_executor_custom_info(executor: Dict[str, Any]) -> Dict[str, Any]:
    """Resolved custom_info dict (parsed JSON string)."""
    return parse_executor_json_field(executor.get("custom_info"))


_DISPLAY_CONFIG_KEYS = (
    "leverage",
    "total_amount_quote",
    "amount",
    "stop_loss",
    "take_profit",
    "triple_barrier_config",
    "connector_name",
    "trading_pair",
    "side",
    "controller_id",
    "type",
)


def normalize_executor_side(
    *sources: Any,
) -> str:
    """Map HB side encodings to BUY/SELL; empty when unknown."""
    _map = {
        "1": "BUY",
        "2": "SELL",
        "buy": "BUY",
        "sell": "SELL",
        "long": "BUY",
        "short": "SELL",
        "tradetype.buy": "BUY",
        "tradetype.sell": "SELL",
    }
    for raw in sources:
        if raw is None:
            continue
        if isinstance(raw, (int, float)) and raw in (1, 2):
            return "BUY" if int(raw) == 1 else "SELL"
        text = str(raw).strip()
        if not text:
            continue
        mapped = _map.get(text.lower())
        if mapped:
            return mapped
        upper = text.upper()
        if upper in ("BUY", "SELL"):
            return upper
    return ""


def get_executor_side(executor: Dict[str, Any]) -> str:
    """Resolve side from config, custom_info, and held_position_orders fills."""
    config = get_executor_config(executor)
    custom_info = get_executor_custom_info(executor)
    side = normalize_executor_side(
        config.get("side"),
        executor.get("side"),
        custom_info.get("side"),
        custom_info.get("position_side"),
    )
    if side:
        return side

    held_orders = custom_info.get("held_position_orders")
    if isinstance(held_orders, list):
        for order in held_orders:
            if not isinstance(order, dict):
                continue
            side = normalize_executor_side(
                order.get("side"),
                order.get("trade_type"),
                order.get("position_side"),
                order.get("order_type"),
            )
            if side:
                return side
    return ""


def get_executor_display_config(executor: Dict[str, Any]) -> Dict[str, Any]:
    """Chart/tooltip config: nested config dict merged with top-level HB fields."""
    raw = executor.get("config")
    out: Dict[str, Any] = (
        dict(raw)
        if isinstance(raw, dict) and raw
        else parse_executor_json_field(raw)
    )
    resolved = get_executor_config(executor)
    for key in _DISPLAY_CONFIG_KEYS:
        val = resolved.get(key)
        if val is not None and val != "" and key not in out:
            out[key] = val

    tb = out.get("triple_barrier_config")
    if isinstance(tb, str):
        tb = parse_executor_json_field(tb)
    if isinstance(tb, dict):
        for key in ("stop_loss", "take_profit", "time_limit", "trailing_stop"):
            if key in tb and key not in out:
                out[key] = tb[key]
    return out


def get_executor_entry_price(executor: Dict[str, Any]) -> float:
    """Resolve entry/average fill price from executor API shapes."""
    config = get_executor_config(executor)
    custom_info = get_executor_custom_info(executor)

    candidates: list[Any] = [
        config.get("entry_price"),
        executor.get("entry_price"),
        custom_info.get("current_position_average_price"),
        custom_info.get("buy_breakeven_price"),
        custom_info.get("breakeven_price"),
        custom_info.get("break_even_price"),
    ]

    held_orders = custom_info.get("held_position_orders")
    if isinstance(held_orders, list):
        for order in held_orders:
            if isinstance(order, dict):
                candidates.append(order.get("price"))

    for candidate in candidates:
        price = _positive_float(candidate)
        if price is not None:
            return price
    return 0.0


# ============================================
# API FETCHERS
# ============================================


async def fetch_executors(client, **_kw) -> list[dict]:
    """Fetch all executors via cursor-based pagination (used by SDS)."""
    return await fetch_all_executors(client)


async def fetch_all_executors(
    client, max_items: int = MAX_EXECUTORS_FETCH, **filters
) -> list[dict]:
    """Fetch all executors via cursor-based pagination.

    Walks the cursor until exhausted or safety cap reached.
    """
    all_items: list[dict] = []
    cursor: str | None = None
    while True:
        remaining = max_items - len(all_items)
        if remaining <= 0:
            break
        page_size = min(EXECUTORS_PAGE_SIZE, remaining)
        kwargs = {**filters, "limit": page_size}
        if cursor:
            kwargs["cursor"] = cursor
        result = await client.executors.search_executors(**kwargs)
        page = extract_executors_list(result)
        all_items.extend(page)

        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_cursor") or result.get("cursor")
            pagination = result.get("pagination")
            if not next_cursor and isinstance(pagination, dict):
                next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
        if not next_cursor:
            if len(page) < page_size:
                break
            break
        if len(all_items) >= max_items:
            break
        cursor = next_cursor
    return all_items


async def create_executor(
    client, config: Dict[str, Any], account_name: str = "master_account"
) -> Dict[str, Any]:
    """Create a new executor."""
    if config.get("type") == "position_executor" or config.get("executor_type") == "position_executor":
        apply_hyperliquid_leverage_cap(config)
    try:
        return await client.executors.create_executor(
            executor_config=config, account_name=account_name
        )
    except Exception as e:
        logger.error("Error creating executor: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


async def stop_executor(
    client, executor_id: str, keep_position: bool = False
) -> Dict[str, Any]:
    """Stop a running executor."""
    try:
        return await client.executors.stop_executor(
            executor_id=executor_id, keep_position=keep_position
        )
    except Exception as e:
        logger.error("Error stopping executor: %s", e, exc_info=True)
        error_str = str(e)
        if "404" in error_str and "not found" in error_str.lower():
            return {"status": "error", "message": "Executor not found (may have already stopped or expired)"}
        elif "403" in error_str:
            return {"status": "error", "message": "Permission denied - cannot stop this executor"}
        elif "400" in error_str:
            return {"status": "error", "message": "Bad request - executor may be in invalid state"}
        return {"status": "error", "message": error_str}


async def get_executor_detail(client, executor_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific executor."""
    try:
        return await client.executors.get_executor(executor_id=executor_id)
    except Exception as e:
        logger.error("Error getting executor detail: %s", e, exc_info=True)
        return None
