"""Reconcile RUNNING executors vs live exchange positions (orphan detection)."""

from __future__ import annotations

from typing import Any

from condor.open_position_audit import log_open_position_event
from condor.trading_agent.performance import is_running_status


def extract_positions_list(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [p for p in result if isinstance(p, dict)]
    if isinstance(result, dict):
        positions = result.get("positions", result.get("data", result))
        if isinstance(positions, list):
            return [p for p in positions if isinstance(p, dict)]
        if isinstance(positions, dict):
            return [positions]
    return []


def _position_amount(pos: dict[str, Any]) -> float:
    for key in ("net_amount_base", "amount", "position_amount"):
        raw = pos.get(key)
        if raw is None:
            continue
        try:
            val = abs(float(raw))
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    return 0.0


def _pair_from_executor_row(ex: dict[str, Any]) -> str:
    return str(ex.get("pair") or ex.get("trading_pair") or "").strip()


def _ignored_hb_summary_positions(
    hb_positions: list[dict[str, Any]],
    *,
    agent_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split HB positions_summary into stale other-controller vs agent-relevant."""
    stale: list[dict[str, Any]] = []
    relevant: list[dict[str, Any]] = []
    for pos in hb_positions:
        if _position_amount(pos) <= 0:
            continue
        cid = str(pos.get("controller_id") or "").strip()
        if cid and cid != agent_id:
            stale.append(
                {
                    "trading_pair": pos.get("trading_pair"),
                    "controller_id": cid,
                    "amount": _position_amount(pos),
                }
            )
            continue
        relevant.append(pos)
    return stale, relevant


def _ghost_executor_pairs(
    all_executors: list[dict[str, Any]],
    *,
    agent_id: str,
    running_pairs: set[str],
) -> set[str]:
    """Pairs with agent executors but none RUNNING (executor vanished)."""
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for ex in all_executors:
        cid = str(ex.get("controller_id") or "").strip()
        if cid and cid != agent_id:
            continue
        pair = _pair_from_executor_row(ex)
        if not pair:
            continue
        by_pair.setdefault(pair, []).append(ex)

    ghosts: set[str] = set()
    for pair, rows in by_pair.items():
        if pair in running_pairs:
            continue
        if any(is_running_status(str(r.get("status") or "")) for r in rows):
            continue
        if rows:
            ghosts.add(pair)
    return ghosts


def reconcile_executor_positions(
    running_executors: list[dict[str, Any]],
    connector_positions: list[dict[str, Any]],
    *,
    agent_id: str = "",
    hb_summary_positions: list[dict[str, Any]] | None = None,
    all_executors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Find live exchange legs with no matching RUNNING executor."""
    running_pairs = {
        str(e.get("pair") or "").strip()
        for e in running_executors
        if str(e.get("pair") or "").strip()
    }
    open_connector = [p for p in connector_positions if _position_amount(p) > 0]
    connector_pairs = {
        str(p.get("trading_pair") or "").strip()
        for p in open_connector
        if str(p.get("trading_pair") or "").strip()
    }

    stale_hb, _relevant_hb = _ignored_hb_summary_positions(
        hb_summary_positions or [],
        agent_id=agent_id,
    )
    ghost_pairs = _ghost_executor_pairs(
        all_executors or [],
        agent_id=agent_id,
        running_pairs=running_pairs,
    )

    orphans: list[dict[str, Any]] = []
    for pos in open_connector:
        pair = str(pos.get("trading_pair") or "").strip()
        if not pair or pair in running_pairs:
            continue
        orphans.append(
            {
                "trading_pair": pair,
                "connector_name": pos.get("connector_name") or pos.get("connector") or "",
                "position_side": pos.get("position_side") or pos.get("side") or "",
                "amount": _position_amount(pos),
                "entry_price": pos.get("entry_price") or pos.get("buy_breakeven_price"),
                "unrealized_pnl": pos.get("unrealized_pnl") or pos.get("unrealized_pnl_quote"),
                "source": "connector",
                "ghost_executor": pair in ghost_pairs,
            }
        )

    orphan_pairs = {o["trading_pair"] for o in orphans}
    ghost_without_position = sorted(
        pair
        for pair in ghost_pairs
        if pair not in running_pairs and pair not in orphan_pairs and pair not in connector_pairs
    )

    effective_slots = len(running_pairs) + len(orphans)
    return {
        "running_executor_count": len(running_pairs),
        "running_pairs": sorted(running_pairs),
        "connector_open_position_count": len(open_connector),
        "connector_pairs": sorted(connector_pairs),
        "hb_stale_other_controller": stale_hb,
        "ghost_executor_pairs": sorted(ghost_pairs),
        "ghost_without_position": ghost_without_position,
        "orphan_position_count": len(orphans),
        "orphan_positions": orphans,
        "effective_open_slots": effective_slots,
        "has_mismatch": len(orphans) > 0,
    }


def format_reconcile_summary(report: dict[str, Any], *, max_open: int) -> str:
    running_n = int(report.get("running_executor_count") or 0)
    effective = int(report.get("effective_open_slots") or running_n)
    orphans = report.get("orphan_positions") or []
    stale = report.get("hb_stale_other_controller") or []
    ghost_hist = report.get("ghost_without_position") or []
    lines = [
        f"Position reconcile: {running_n} RUNNING executor(s), "
        f"{report.get('connector_open_position_count', 0)} live connector position(s), "
        f"effective slots {effective}/{max_open}",
    ]
    if stale:
        lines.append(
            f"Note: ignored {len(stale)} stale hummingbot positions_summary row(s) "
            f"from other controller_id(s) (not live exchange): "
            + ", ".join(
                f"{s.get('trading_pair')}@{s.get('controller_id')}"
                for s in stale[:5]
            )
        )
    if ghost_hist:
        lines.append(
            "Note: historical terminated executors (no live connector position): "
            + ", ".join(ghost_hist[:8])
            + ("..." if len(ghost_hist) > 8 else "")
        )
    if not orphans:
        lines.append(
            "No orphan legs: every live connector position has a RUNNING executor."
        )
        return "\n".join(lines)

    lines.append(
        f"WARNING: {len(orphans)} live connector leg(s) with NO RUNNING executor:"
    )
    for pos in orphans:
        pair = pos.get("trading_pair") or "?"
        connector = pos.get("connector_name") or "?"
        side = pos.get("position_side") or "?"
        amt = pos.get("amount")
        amt_s = f"amt={amt}" if amt is not None else "amt=?"
        lines.append(f"  {connector} {pair} {side} {amt_s}")
    lines.append(
        "Treat effective slots as RUNNING executors + connector orphan legs only."
    )
    lines.append(
        "CRITICAL: Do NOT market-create position_executor for an orphan — hummingbot "
        "will OPEN AN ADDITIONAL position (doubles size on Hyperliquid). "
        "Orphans lack SL/TP until manually reconciled: reduce duplicate size on the "
        "exchange, or close the orphan leg, then open a fresh managed executor if needed."
    )
    return "\n".join(lines)


async def audit_position_reconcile(
    client: Any,
    *,
    agent_id: str,
    running_executors: list[dict[str, Any]],
    all_executors: list[dict[str, Any]] | None = None,
    tick_num: int,
    max_open_executors: int,
) -> dict[str, Any]:
    """Fetch live connector + HB summary positions, reconcile, log audit line."""
    connector_positions: list[dict[str, Any]] = []
    try:
        result = await client.trading.get_positions(limit=200)
        connector_positions = extract_positions_list(result)
    except Exception as exc:
        log_open_position_event(
            phase="position_reconcile_error",
            message="trading.get_positions failed",
            data={"agent_id": agent_id, "tick_num": tick_num, "error": str(exc)},
        )

    hb_positions: list[dict[str, Any]] = []
    try:
        hb_result = await client.executors.get_positions_summary()
        hb_positions = extract_positions_list(hb_result)
    except Exception as exc:
        log_open_position_event(
            phase="position_reconcile_error",
            message="get_positions_summary failed",
            data={"agent_id": agent_id, "tick_num": tick_num, "error": str(exc)},
        )

    report = reconcile_executor_positions(
        running_executors,
        connector_positions,
        agent_id=agent_id,
        hb_summary_positions=hb_positions,
        all_executors=all_executors,
    )
    log_open_position_event(
        phase="position_reconcile",
        message="executor vs connector position reconcile",
        data={
            "agent_id": agent_id,
            "tick_num": tick_num,
            "max_open_executors": max_open_executors,
            **report,
        },
    )
    report["summary"] = format_reconcile_summary(report, max_open=max_open_executors)
    return report
