"""Strategies API — Condor-native deterministic runners (not Agents, not Bots)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from condor.strategy_runners.catalog import get_strategy, list_strategies
from condor.strategy_runners.promote import load_manifest, promote
from condor.strategy_runners.runner import start_deterministic_strategy
from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategySummary(BaseModel):
    slug: str
    name: str
    description: str
    connector: str
    require_promoted: bool
    status: str = "idle"
    agent_id: str | None = None
    session_num: int | None = None
    promoted_preset: str | None = None
    promoted_preset_hash: str | None = None
    default_config: dict[str, Any] = Field(default_factory=dict)
    strategy_presets: list[dict[str, str]] = Field(default_factory=list)
    last_tick_at: float | None = None
    tick_count: int | None = None
    last_error: str | None = None
    last_tick_summary: str | None = None


class StartStrategyBody(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    chat_id: int = 0
    strategy_preset: str = ""
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    session_num: int | None = None


class PromoteBody(BaseModel):
    preset: str
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    venue: str = "hyperliquid_perpetual"
    notes: str = ""


class DefaultsBody(BaseModel):
    default_config: dict[str, Any] = Field(default_factory=dict)


def _running_for(slug: str) -> list[Any]:
    """Live deterministic runners for ``slug`` (duck-typed; survives class reload)."""
    from condor.runtime.loops import get_supervisor

    return get_supervisor().for_deterministic_slug(slug)


def _is_registered(agent_id: str) -> bool:
    from condor.runtime.loops import get_supervisor

    return get_supervisor().get(agent_id) is not None


def _orphaned_sessions(strat) -> list[int]:
    from condor.strategy_runners.macdbb.sessions import find_orphaned_strategy_sessions

    return find_orphaned_strategy_sessions(
        data_slug=strat.data_slug,
        run_key=strat.key,
        is_registered=_is_registered,
    )


def _session_disk_status(strat, session_num: int, *, running_ids: set[str]) -> str:
    """Infer per-session status for list/performance rows."""
    agent_id = f"{strat.key}_{session_num}"
    if agent_id in running_ids:
        for engine in _running_for(strat.slug):
            if getattr(engine, "agent_id", None) == agent_id:
                if getattr(engine, "is_running", False):
                    return "running"
                if getattr(engine, "is_active", False):
                    return "paused"
                return "running"
        return "running"
    from condor.strategy_runners.macdbb.sessions import find_session_dir

    session_dir = find_session_dir(strat.data_slug, session_num)
    if session_dir is None:
        return "closed"
    from condor.agents.session_status import session_appears_orphaned
    from condor.runtime.registry_file import read_status

    if session_appears_orphaned(session_dir):
        return "orphaned"
    status = read_status(session_dir) or {}
    state = str(status.get("state") or "").lower()
    if state in {"interrupted", "paused", "running", "error"}:
        return state if state != "running" else "interrupted"
    return "closed"


def _merged_default_config(strat) -> dict[str, Any]:
    """Catalog defaults overlaid with persisted strategy.yaml."""
    from condor.strategy_runners.macdbb.sessions import load_default_config

    merged = dict(strat.default_config or {})
    persisted = load_default_config(strat.data_slug)
    if persisted:
        merged.update(persisted)
    return merged


def _preset_catalog(data_slug: str) -> list[dict[str, str]]:
    from condor.strategy_runners.macdbb.presets import agent_preset_catalog

    _ = data_slug
    return list(agent_preset_catalog() or [])


def _expand_preset_params(
    data_slug: str, preset: str, *, frequency_sec: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (strategy_params, risk_limits hint) for a named preset."""
    from condor.strategy_runners.macdbb.presets import (
        agent_preset_catalog,
        resolve_config_with_preset,
        strategy_params_from_preset,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.models import (
        DynamicStrategyReplayConfig,
    )

    _ = data_slug
    catalog = agent_preset_catalog() or []
    allowed = {row["id"] for row in catalog}
    if preset not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'")
    if preset == "custom":
        return {}, {}

    params = strategy_params_from_preset(preset, frequency_sec=frequency_sec) or {}
    risk: dict[str, Any] = {}
    try:
        replay_cfg = resolve_config_with_preset(
            DynamicStrategyReplayConfig(preset=preset, frequency_sec=frequency_sec)
        )
        risk = {"max_open_executors": int(replay_cfg.max_open_executors)}
    except Exception:
        log.debug("No risk hint for preset %s", preset, exc_info=True)
    return params, risk

async def _get_client(server_name: str | None):
    try:
        from config_manager import get_config_manager

        cm = get_config_manager()
        name = (server_name or "").strip() or cm.get_default_server()
        if not name:
            return None
        return await cm.get_client(name)
    except Exception:
        log.warning("Strategies client resolve failed for %s", server_name, exc_info=True)
        return None


def _summary_for(strat) -> StrategySummary:
    running = _running_for(strat.slug)
    manifest = load_manifest(strat.slug)
    engine = running[0] if running else None
    status = "idle"
    agent_id = None
    session_num = None
    last_tick_at = None
    tick_count = None
    last_error = None
    last_tick_summary = None

    if engine is not None:
        if getattr(engine, "is_running", False):
            status = "running"
        elif getattr(engine, "is_active", False):
            status = "paused"
        else:
            status = "idle"
        agent_id = getattr(engine, "agent_id", None)
        session_num = getattr(engine, "session_num", None)
        last_tick_at = getattr(engine, "last_tick_at", None) or None
        tick_count = getattr(engine, "tick_count", None)
        last_error = getattr(engine, "last_error", None) or None
        last_tick_summary = getattr(engine, "last_tick_summary", None) or None
    else:
        orphaned = _orphaned_sessions(strat)
        if orphaned:
            status = "orphaned"
            session_num = orphaned[-1]
            agent_id = f"{strat.key}_{session_num}"
        else:
            from condor.agents.sessions_index import infer_latest_session_status
            from condor.strategy_runners.macdbb.sessions import strategy_runs_root

            disk = infer_latest_session_status(
                strategy_runs_root(strat.data_slug), strat.key
            )
            if disk:
                disk_status = str(disk.get("status") or "idle").lower()
                if disk_status in {"interrupted", "error", "paused"}:
                    status = disk_status
                    agent_id = disk.get("agent_id")
                    session_num = disk.get("session_num")
                    tick_count = disk.get("tick_count")

    return StrategySummary(
        slug=strat.slug,
        name=strat.name,
        description=strat.description,
        connector=strat.connector,
        require_promoted=strat.require_promoted,
        status=status,
        agent_id=agent_id,
        session_num=session_num,
        promoted_preset=manifest.preset if manifest else None,
        promoted_preset_hash=manifest.preset_hash if manifest else None,
        default_config=_merged_default_config(strat),
        strategy_presets=_preset_catalog(strat.data_slug),
        last_tick_at=last_tick_at,
        tick_count=tick_count,
        last_error=last_error,
        last_tick_summary=last_tick_summary,
    )


@router.get("")
async def list_deterministic_strategies(
    user: WebUser = Depends(get_current_user),
) -> list[StrategySummary]:
    _ = user
    return [_summary_for(strat) for strat in list_strategies()]


@router.get("/{slug}")
async def get_deterministic_strategy(
    slug: str, user: WebUser = Depends(get_current_user)
) -> StrategySummary:
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    return _summary_for(strat)


@router.get("/{slug}/defaults")
async def get_strategy_defaults(
    slug: str, user: WebUser = Depends(get_current_user)
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    return {
        "default_config": _merged_default_config(strat),
        "strategy_presets": _preset_catalog(strat.data_slug),
    }


@router.put("/{slug}/defaults")
async def update_strategy_defaults(
    slug: str,
    body: DefaultsBody,
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.agents.config import sanitize_config_dict, strip_session_overrides
    from condor.strategy_runners.macdbb.sessions import save_default_config

    existing = _merged_default_config(strat)
    incoming = dict(body.default_config or {})
    allowed_keys = {
        "server_name",
        "total_amount_quote",
        "frequency_sec",
        "risk_limits",
        "strategy_preset",
        "strategy_params",
        "account_name",
        "bot_name",
        "execution_mode",
        "tick_log_enabled",
        "tick_log_retention_days",
    }
    merged = dict(existing)
    for key, value in incoming.items():
        if key in allowed_keys:
            merged[key] = value
    merged = sanitize_config_dict(strip_session_overrides(merged))
    save_default_config(strat.data_slug, merged)
    return {
        "default_config": _merged_default_config(strat),
        "strategy_presets": _preset_catalog(strat.data_slug),
        "saved": True,
    }


@router.get("/{slug}/strategy-preset-params")
async def get_strategy_preset_params(
    slug: str,
    preset: str = Query(...),
    frequency_sec: int = Query(1800),
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    params, risk = _expand_preset_params(
        strat.data_slug, preset, frequency_sec=frequency_sec
    )
    return {"strategy_params": params, "risk_limits": risk}


@router.get("/{slug}/strategy-config-schema")
async def get_strategy_schema(
    slug: str,
    frequency_sec: int = Query(1800),
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.agents.strategy_configs.registry import get_strategy_config_schema

    saved = dict((_merged_default_config(strat).get("strategy_params") or {}))
    schema = get_strategy_config_schema(
        strat.strategy_slug,
        saved_defaults=saved,
        frequency_sec=frequency_sec,
    )
    if schema is None:
        return {"fields": {}, "groups": [], "defaults": {}}
    return schema


@router.get("/{slug}/ticks")
async def list_strategy_ticks(
    slug: str,
    session: int | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user: WebUser = Depends(get_current_user),
):
    """Recent TTL'd fetch/decide/apply tick audit logs."""
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.strategy_runners.macdbb.tick_log import list_recent_ticks

    rows = list_recent_ticks(strat.data_slug, session=session, limit=limit)
    summaries = []
    for row in rows:
        decide = row.get("decide") or {}
        apply_info = row.get("apply") or {}
        summaries.append(
            {
                "id": f"{row.get('session')}:{row.get('tick')}",
                "ts": row.get("ts"),
                "session": row.get("session"),
                "tick": row.get("tick"),
                "tradeable_count": row.get("tradeable_count"),
                "signal_count": row.get("signal_count"),
                "scanner_regime": row.get("scanner_regime"),
                "hold_reason": decide.get("hold_reason"),
                "creates": decide.get("creates"),
                "stops": decide.get("stops"),
                "apply_ok": apply_info.get("ok"),
                "summary": row.get("summary"),
                "raw": row,
            }
        )
    return {"ticks": summaries, "count": len(summaries)}


@router.get("/{slug}/sessions")
async def list_strategy_sessions(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """List journal sessions under data/strategy_runs (+ legacy agents history)."""
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.strategy_runners.macdbb.sessions import list_session_dirs

    running = _running_for(slug)
    running_ids = {e.agent_id for e in running}
    out: list[dict[str, Any]] = []
    for path in list_session_dirs(strat.data_slug):
        try:
            num = int(path.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        journal = path / "journal.md"
        out.append(
            {
                "session_num": num,
                "agent_id": f"{strat.key}_{num}",
                "path": str(path),
                "has_journal": journal.is_file(),
                "mtime": path.stat().st_mtime,
                "status": _session_disk_status(
                    strat, num, running_ids=running_ids
                ),
            }
        )
    return {"sessions": out}


@router.get("/{slug}/sessions/{session_num}/journal")
async def get_session_journal(
    slug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.strategy_runners.macdbb.sessions import find_session_dir

    session_dir = find_session_dir(strat.data_slug, session_num)
    if not session_dir:
        raise HTTPException(status_code=404, detail=f"Session {session_num} not found")
    journal_path = session_dir / "journal.md"
    content = journal_path.read_text() if journal_path.exists() else ""
    return {"content": content, "session_num": session_num}


@router.get("/{slug}/sessions/{session_num}/executors")
async def get_session_executors(
    slug: str,
    session_num: int,
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.agents.performance import fetch_agent_performance

    agent_id = f"{strat.key}_{session_num}"
    defaults = _merged_default_config(strat)
    client = await _get_client(str(defaults.get("server_name") or ""))
    if client is None:
        return {
            "agent_id": agent_id,
            "executors": [],
            "performance": {
                "agent_id": agent_id,
                "session_num": session_num,
                "total_pnl": 0,
                "realized_pnl": 0,
                "unrealized_pnl": 0,
                "volume": 0,
                "open_count": 0,
                "closed_count": 0,
            },
        }
    perf = await fetch_agent_performance(client, agent_id)
    return {
        "agent_id": agent_id,
        "executors": perf.executors,
        "performance": {
            "agent_id": agent_id,
            "session_num": session_num,
            "realized_pnl": perf.realized_pnl,
            "unrealized_pnl": perf.unrealized_pnl,
            "total_pnl": perf.total_pnl,
            "volume": perf.volume,
            "fees": perf.fees,
            "trade_count": perf.trade_count,
            "win_rate": perf.win_rate,
            "open_count": perf.open_count,
            "closed_count": perf.closed_count,
        },
    }


@router.get("/{slug}/performance")
async def get_strategy_performance(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """Roll up session performance for the Strategies detail page."""
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    from condor.agents.performance import fetch_agent_performance
    from condor.strategy_runners.macdbb.sessions import list_session_dirs

    defaults = _merged_default_config(strat)
    client = await _get_client(str(defaults.get("server_name") or ""))
    running = _running_for(slug)
    running_ids = {e.agent_id for e in running}

    sessions: list[dict[str, Any]] = []
    totals = {
        "total_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "volume": 0.0,
        "open_positions": 0,
    }
    if client is not None:
        for path in list_session_dirs(strat.data_slug):
            try:
                num = int(path.name.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            agent_id = f"{strat.key}_{num}"
            try:
                perf = await fetch_agent_performance(client, agent_id)
            except Exception:
                log.warning("perf fetch failed for %s", agent_id, exc_info=True)
                continue
            row = {
                "agent_id": agent_id,
                "session_num": num,
                "status": _session_disk_status(
                    strat, num, running_ids=running_ids
                ),
                "realized_pnl": perf.realized_pnl,
                "unrealized_pnl": perf.unrealized_pnl,
                "total_pnl": perf.total_pnl,
                "volume": perf.volume,
                "fees": getattr(perf, "fees", 0) or 0,
                "trade_count": getattr(perf, "trade_count", 0) or 0,
                "open_count": perf.open_count,
                "closed_count": perf.closed_count,
            }
            sessions.append(row)
            totals["total_pnl"] += float(perf.total_pnl or 0)
            totals["realized_pnl"] += float(perf.realized_pnl or 0)
            totals["unrealized_pnl"] += float(perf.unrealized_pnl or 0)
            totals["volume"] += float(perf.volume or 0)
            totals["open_positions"] += int(perf.open_count or 0)
    sessions.sort(key=lambda r: r["session_num"], reverse=True)
    return {"slug": slug, "sessions": sessions, "totals": totals}


@router.get("/{slug}/live-executors")
async def get_live_executors(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """Open executors for the live or orphaned DeterministicRunner session."""
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    running = _running_for(slug)
    session_num: int | None = None
    agent_id: str | None = None
    is_live = False
    if running:
        engine = running[0]
        session_num = int(engine.session_num)
        agent_id = engine.agent_id
        is_live = True
    else:
        orphaned = _orphaned_sessions(strat)
        if orphaned:
            session_num = int(orphaned[-1])
            agent_id = f"{strat.key}_{session_num}"
    if session_num is None:
        return {
            "running": False,
            "agent_id": None,
            "session_num": None,
            "executors": [],
            "performance": None,
        }
    payload = await get_session_executors(slug, session_num, user)
    return {
        "running": is_live,
        "agent_id": agent_id,
        "session_num": session_num,
        "executors": payload.get("executors") or [],
        "performance": payload.get("performance"),
    }


@router.post("/{slug}/start")
async def start_strategy(
    slug: str,
    body: StartStrategyBody,
    user: WebUser = Depends(get_current_user),
):
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    if _running_for(slug):
        raise HTTPException(status_code=409, detail=f"Strategy '{slug}' already running")

    orphaned = _orphaned_sessions(strat)
    resume_session_num = body.session_num
    if resume_session_num is not None:
        from condor.strategy_runners.macdbb.sessions import find_session_dir

        if find_session_dir(strat.data_slug, int(resume_session_num)) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session {resume_session_num} not found for '{slug}'",
            )
    elif orphaned:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot start a new session: session(s) {orphaned} journal shows "
                f"Running/Paused but no engine is registered (likely orphaned after "
                f"hot-reload). Resume session {orphaned[-1]} or fully restart Condor."
            ),
        )

    config = dict(body.config or {})
    preset = body.strategy_preset or str(config.get("strategy_preset") or "")
    params = dict(body.strategy_params or config.get("strategy_params") or {})
    freq = int(
        config.get("frequency_sec")
        or _merged_default_config(strat).get("frequency_sec")
        or 1800
    )
    if preset and not params:
        try:
            params, risk = _expand_preset_params(
                strat.data_slug, preset, frequency_sec=freq
            )
            if risk:
                merged_risk = dict(config.get("risk_limits") or {})
                merged_risk.update(risk)
                config["risk_limits"] = merged_risk
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not expand preset '{preset}': {exc}"
            ) from exc
    if preset:
        config["strategy_preset"] = preset
    if params:
        config["strategy_params"] = params

    try:
        runner = await start_deterministic_strategy(
            slug,
            config=config,
            user_id=user.id,
            chat_id=body.chat_id or user.id,
            resume_session_num=resume_session_num,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Failed to start strategy %s", slug)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "started": True,
        "slug": slug,
        "agent_id": runner.agent_id,
        "session_num": runner.session_num,
        "resumed": resume_session_num is not None,
    }


@router.post("/{slug}/stop")
async def stop_strategy(
    slug: str, user: WebUser = Depends(get_current_user)
):
    _ = user
    running = _running_for(slug)
    if not running:
        raise HTTPException(status_code=404, detail=f"No running instance of '{slug}'")
    for engine in list(running):
        await engine.stop()
    return {"stopped": True, "slug": slug}


@router.post("/{slug}/pause")
async def pause_strategy(
    slug: str, user: WebUser = Depends(get_current_user)
):
    _ = user
    running = _running_for(slug)
    if not running:
        raise HTTPException(status_code=404, detail=f"No running instance of '{slug}'")
    for engine in running:
        engine.pause()
    return {"paused": True, "slug": slug}


@router.post("/{slug}/resume")
async def resume_strategy_loop(
    slug: str, user: WebUser = Depends(get_current_user)
):
    """In-process resume (unpause) — not the same as Resuming a session_num."""
    _ = user
    running = _running_for(slug)
    if not running:
        raise HTTPException(status_code=404, detail=f"No running instance of '{slug}'")
    for engine in running:
        engine.resume()
    return {"resumed": True, "slug": slug}


@router.get("/{slug}/promote")
async def get_promote_manifest(
    slug: str, user: WebUser = Depends(get_current_user)
):
    _ = user
    if get_strategy(slug) is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    manifest = load_manifest(slug)
    if manifest is None:
        return {"promoted": False}
    return {"promoted": True, "manifest": manifest.to_dict()}


@router.post("/{slug}/promote")
async def promote_strategy(
    slug: str,
    body: PromoteBody,
    user: WebUser = Depends(get_current_user),
):
    _ = user
    strat = get_strategy(slug)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{slug}' not found")
    if not body.preset or body.preset == "custom":
        raise HTTPException(
            status_code=400,
            detail="Select a named preset before promoting (custom is not promotable).",
        )
    params = dict(body.strategy_params or {})
    freq = int(_merged_default_config(strat).get("frequency_sec") or 1800)
    if not params:
        try:
            params, _risk = _expand_preset_params(
                strat.data_slug, body.preset, frequency_sec=freq
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not expand preset '{body.preset}': {exc}",
            ) from exc
    if not params:
        raise HTTPException(
            status_code=400,
            detail="strategy_params required to promote (parity pack must expand preset)",
        )
    try:
        manifest = promote(
            slug,
            preset=body.preset,
            strategy_params=params,
            venue=body.venue or strat.connector,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"promoted": True, "manifest": manifest.to_dict()}
