"""Subprocess CLI entrypoint for isolated routine execution.

Invoked by RoutineWorkerPool via:
  PYTHONPATH=. python -m condor.routine_worker --routine ... --config-json ... ...
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_routine_by_name(routine_name: str, extra_routines_dir: str | None = None):
    """Resolve a routine by name, supporting agent_slug/routine_name format."""
    from routines.base import discover_routines, discover_routines_from_path, get_routine

    if extra_routines_dir:
        extra_path = Path(extra_routines_dir)
        if extra_path.is_dir():
            extra_routines = discover_routines_from_path(extra_path)
            if routine_name in extra_routines:
                return extra_routines[routine_name]
            if "/" in routine_name:
                slug, rname = routine_name.split("/", 1)
                agent_extra = extra_path / slug if (extra_path / slug).is_dir() else None
                if agent_extra and (agent_extra / "routines").is_dir():
                    found = discover_routines_from_path(
                        agent_extra / "routines", agent_slug=slug
                    ).get(rname)
                    if found:
                        return found

    routine = get_routine(routine_name)
    if routine:
        return routine

    if "/" in routine_name:
        slug, rname = routine_name.split("/", 1)
        agents_dir = (
            Path(__file__).resolve().parent.parent
            / "agents"
            / slug
            / "routines"
        )
        agent_routines = discover_routines_from_path(agents_dir, agent_slug=slug)
        found = agent_routines.get(rname)
        if found:
            return found

    # Force reload global cache miss
    discover_routines(force_reload=True)
    return get_routine(routine_name)


def routine_result_to_dict(result) -> dict[str, Any]:
    from routines.base import RoutineResult, normalize_result

    nr = normalize_result(result)
    payload: dict[str, Any] = {
        "text": nr.text,
        "table_data": nr.table_data,
        "table_columns": nr.table_columns,
        "sections": nr.sections,
    }
    if nr.chart_image is not None:
        payload["chart_image_b64"] = base64.b64encode(nr.chart_image).decode("ascii")
    return payload


def routine_result_from_dict(data: dict[str, Any]):
    from routines.base import RoutineResult

    chart_image = None
    if data.get("chart_image_b64"):
        chart_image = base64.b64decode(data["chart_image_b64"])
    return RoutineResult(
        text=data.get("text") or "",
        table_data=data.get("table_data"),
        table_columns=data.get("table_columns"),
        chart_image=chart_image,
        sections=data.get("sections"),
    )


async def _run_routine(
    routine_name: str,
    config: dict[str, Any],
    server_name: str,
    user_id: int,
    extra_routines_dir: str | None = None,
) -> dict[str, Any]:
    import condor.reports as reports
    from condor.routine_store import WebRoutineContext

    routine = resolve_routine_by_name(routine_name, extra_routines_dir=extra_routines_dir)
    if not routine:
        raise ValueError(f"Routine '{routine_name}' not found")

    reports._last_report_id = None
    start = time.time()
    ctx = WebRoutineContext(server_name, chat_id=user_id)
    cfg = routine.config_class(**config)
    raw = await routine.run_fn(cfg, ctx)
    duration = time.time() - start

    return {
        "ok": True,
        "duration_sec": duration,
        "report_id": reports._last_report_id,
        "result": routine_result_to_dict(raw),
        "error": None,
        "traceback": None,
    }


def write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")


def read_envelope(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def main_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config_json)
    result_path = Path(args.result_file)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    try:
        envelope = await _run_routine(
            args.routine,
            config,
            args.server,
            args.user_id,
            extra_routines_dir=args.extra_routines_dir,
        )
    except Exception as exc:
        envelope = {
            "ok": False,
            "duration_sec": 0.0,
            "report_id": None,
            "result": {"text": f"Error: {type(exc).__name__}: {exc}"},
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    write_envelope(result_path, envelope)
    return 0 if envelope.get("ok") else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Condor routine in a worker subprocess")
    parser.add_argument("--routine", required=True, help="Routine name (supports slug/name)")
    parser.add_argument("--config-json", required=True, help="Path to JSON config file")
    parser.add_argument("--server", default="local", help="Active server name for context")
    parser.add_argument("--user-id", type=int, default=0, help="User/chat id for context")
    parser.add_argument("--result-file", required=True, help="Path to write result envelope JSON")
    parser.add_argument(
        "--extra-routines-dir",
        default=None,
        help="Optional directory for additional routine modules (tests/extras)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
