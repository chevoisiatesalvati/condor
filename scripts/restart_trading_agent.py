#!/usr/bin/env python3
"""Stop a running trading agent session and start a fresh one from strategy defaults."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from utils.config import ADMIN_USER_ID, WEB_PORT
from condor.agents.session_status import latest_orphaned_session_num
from condor.agents.strategy_paths import ensure_strategy_data_dir
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
    parser.add_argument(
        "--sslug",
        default=None,
        help="Strategy slug (defaults to --slug when agent has one primary strategy)",
    )
    args = parser.parse_args()
    slug = args.slug
    sslug = args.sslug or slug
    run_key = f"{slug}.{sslug}"
    strategy_dir = ensure_strategy_data_dir(slug, sslug)

    strategy = _api("GET", f"/agents/{slug}/strategies/{sslug}")
    running = [
        inst
        for inst in (strategy.get("instances") or [])
        if inst.get("status") in ("running", "paused")
    ]
    if not running:
        resume_num = latest_orphaned_session_num(run_key, strategy_dir)
        if resume_num is not None:
            print(
                f"No API instance for {run_key}, but session {resume_num} journal shows "
                f"active — resuming instead of starting fresh"
            )
            start_body = {
                "session_num": resume_num,
                "user_id": ADMIN_USER_ID,
                "chat_id": ADMIN_USER_ID,
            }
            result = _api(
                "POST", f"/agents/{slug}/strategies/{sslug}/start", start_body
            )
            print(
                f"Resumed {run_key}: agent_id={result.get('agent_id')} "
                f"session_num={result.get('session_num')}"
            )
            return 0
        print(f"No running/paused instance for {run_key}; starting fresh session")
        preserved: dict = {}
        trading_context = ""
    else:
        inst = running[0]
        print(
            f"Stopping {inst['agent_id']} "
            f"(session {inst['session_num']}, status={inst['status']}, ticks={inst['tick_count']})"
        )
        _api(
            "POST",
            f"/agents/{slug}/strategies/{sslug}/stop?agent_id={inst['agent_id']}",
        )
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
        trading_context = inst.get("trading_context", "")

    start_body = {
        "config": {k: v for k, v in preserved.items() if v not in (None, "", {})},
        "trading_context": trading_context,
        "user_id": ADMIN_USER_ID,
        "chat_id": ADMIN_USER_ID,
    }
    result = _api("POST", f"/agents/{slug}/strategies/{sslug}/start", start_body)
    print(
        f"Started {run_key}: agent_id={result.get('agent_id')} "
        f"session_num={result.get('session_num')}"
    )
    print("Config loaded from strategy defaults (includes latest strategy_params).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
