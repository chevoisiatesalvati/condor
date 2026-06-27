#!/usr/bin/env python3
"""Stop a running trading agent session and start a fresh one from agent.md defaults."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from utils.config import ADMIN_USER_ID, WEB_PORT
from condor.web.auth import create_jwt


def _api(method: str, path: str, body: dict | None = None) -> dict:
    token = create_jwt(ADMIN_USER_ID, role="admin")
    url = f"http://127.0.0.1:{WEB_PORT}/api/v1{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart a trading agent session")
    parser.add_argument("--slug", default="macdbb_scanner_aggressive_hl")
    args = parser.parse_args()
    slug = args.slug

    agents = _api("GET", "/agents")
    target = next((row for row in agents if row.get("slug") == slug), None)
    if target is None:
        print(f"Agent slug not found: {slug}", file=sys.stderr)
        return 1

    running = [
        inst
        for inst in (target.get("instances") or [])
        if inst.get("status") in ("running", "paused")
    ]
    if not running:
        print(f"No running/paused instance for {slug}; starting fresh session")
        preserved: dict = {}
    else:
        inst = running[0]
        print(
            f"Stopping {inst['agent_id']} "
            f"(session {inst['session_num']}, status={inst['status']}, ticks={inst['tick_count']})"
        )
        _api("POST", f"/agents/{slug}/stop?agent_id={inst['agent_id']}")
        preserved = {
            "server_name": inst.get("server_name"),
            "total_amount_quote": inst.get("total_amount_quote"),
            "frequency_sec": inst.get("frequency_sec"),
            "execution_mode": inst.get("execution_mode"),
            "digest_interval_ticks": inst.get("digest_interval_ticks"),
            "risk_limits": inst.get("risk_limits") or {},
        }
        if inst.get("agent_key"):
            preserved["agent_key"] = inst["agent_key"]

    start_body = {
        "config": {k: v for k, v in preserved.items() if v not in (None, "", {})},
        "trading_context": running[0].get("trading_context", "") if running else "",
        "user_id": ADMIN_USER_ID,
        "chat_id": ADMIN_USER_ID,
    }
    result = _api("POST", f"/agents/{slug}/start", start_body)
    print(
        f"Started {slug}: agent_id={result.get('agent_id')} "
        f"session_num={result.get('session_num')}"
    )
    print("Config loaded from agent.md defaults (includes latest strategy_params).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
