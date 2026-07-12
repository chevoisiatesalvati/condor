#!/usr/bin/env python3
"""Probe raw Hummingbot executor payloads for side field presence.

Usage (from repo root):
  PYTHONPATH=. .venv/bin/python scripts/probe_executor_sides.py [server_name]

Prints where `side` appears (config / custom_info / top-level) for a sample
of executors returned by hummingbot-api search_executors — independent of Condor
web UI version.
"""

from __future__ import annotations

import asyncio
import json
import sys

from config_manager import get_config_manager
from condor.fetchers.executors import extract_executors_list, resolve_executor_side


def _side_sources(ex: dict) -> dict:
    config = ex.get("config") if isinstance(ex.get("config"), dict) else {}
    custom_info = ex.get("custom_info") if isinstance(ex.get("custom_info"), dict) else {}
    held = custom_info.get("held_position_orders")
    held_side = None
    if isinstance(held, list) and held and isinstance(held[-1], dict):
        held_side = held[-1].get("trade_type") or held[-1].get("side")
    return {
        "top": ex.get("side"),
        "config": config.get("side"),
        "custom_info": custom_info.get("side"),
        "position_side": ex.get("position_side") or custom_info.get("position_side"),
        "trade_type": ex.get("trade_type") or custom_info.get("trade_type"),
        "held_order": held_side,
        "built": resolve_executor_side(ex) if isinstance(ex, dict) else None,
        "status": ex.get("status"),
        "type": config.get("type") or ex.get("type"),
    }


async def main() -> None:
    cm = get_config_manager()
    servers = cm.list_servers()
    if not servers:
        print("No servers configured in config.yml")
        sys.exit(1)

    server = sys.argv[1] if len(sys.argv) > 1 else next(iter(servers))
    if server not in servers:
        print(f"Unknown server {server!r}. Available: {', '.join(servers)}")
        sys.exit(1)

    client = await cm.get_client(server)
    result = await client.executors.search_executors(limit=100)
    raw_list = extract_executors_list(result)
    print(f"Server: {server}")
    print(f"Fetched: {len(raw_list)} executors (limit=100)")

    with_any = 0
    with_built = 0
    for ex in raw_list:
        src = _side_sources(ex)
        if any(v not in (None, "", 0) for k, v in src.items() if k not in ("built", "status", "type")):
            with_any += 1
        if src["built"]:
            with_built += 1

    print(f"Raw side in any field: {with_any}/{len(raw_list)}")
    print(f"_build_executor_info side non-empty: {with_built}/{len(raw_list)}")
    print("\nSample (first 8):")
    for ex in raw_list[:8]:
        eid = str(ex.get("id") or ex.get("executor_id") or "")[:12]
        print(f"  {eid}: {json.dumps(_side_sources(ex), default=str)}")


if __name__ == "__main__":
    asyncio.run(main())
