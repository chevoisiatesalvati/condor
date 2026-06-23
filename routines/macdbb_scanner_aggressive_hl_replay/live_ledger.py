"""Reconstruct live position legs from session journals and compare to sim trades."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.journal import (
    _DECISION_RE,
    _parse_decision_line,
    parse_journal_ticks,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import SimTrade

_TRIGGER_RE = re.compile(r"trigger=([a-z_]+)")
_ENTRY_CLASS_FIELD_RE = re.compile(r"entry_class=([a-zA-Z0-9_]+)")
_FLIP_EXIT_RE = re.compile(r"\b([A-Z][A-Z0-9]*):flip_exit")
_OPENED_SIDE_RE = re.compile(
    r"OPENED\s+(LONG|SHORT)\s+([A-Z][A-Z0-9]*-USD)",
    re.IGNORECASE,
)
_OPENED_ADAPTIVE_SIDE_RE = re.compile(
    r"OPENED\s+adaptive\s+(LONG|SHORT)\s+([A-Z][A-Z0-9]*-USD)",
    re.IGNORECASE,
)
_OPENED_BOLD_PAIR_RE = re.compile(
    r"Opened\s+\*\*([A-Z][A-Z0-9]*-USD)\s+(?:adaptive|formal)\s+(LONG|SHORT)",
    re.IGNORECASE,
)
_OPENED_EMBEDDED_SIDE_RE = re.compile(
    r"\*\*Opened\*\*[^*]*\*\*(LONG|SHORT)\s+([A-Z][A-Z0-9]*-USD)\*\*",
    re.IGNORECASE,
)
_CLOSED_NARRATIVE_RE = re.compile(
    r"Closed\s+([A-Z][A-Z0-9]*)(?:-USD)?\s+(LONG|SHORT)",
    re.IGNORECASE,
)
_THESIS_DECAY_PNL_RE = re.compile(r"at\s+\+\$?([0-9.]+)|at\s+-\$?([0-9.]+)")
_ABSENT_LEG_RE = re.compile(
    r"([A-Z0-9]+)(?:-USD)? from tick #\d+ no longer in CORE DATA",
    re.IGNORECASE,
)
_SUMMARY_PNL_RE = re.compile(r"PnL:\s*\$([+-]?[0-9.]+)")


@dataclass(frozen=True)
class LegRecord:
    leg_num: int
    entry_tick: int
    exit_tick: int | None
    pair: str
    side: str
    entry_trigger: str
    entry_class: str
    exit_reason: str | None
    pnl_quote: float | None


@dataclass(frozen=True)
class LegComparison:
    index: int
    live: LegRecord | None
    sim: LegRecord | None
    entry_tick_match: bool
    exit_tick_match: bool
    side_match: bool
    trigger_match: bool
    exit_reason_match: bool
    pnl_delta: float | None


def parse_journal_live_pnl(journal_path: Path) -> float | None:
    if not journal_path.is_file():
        return None
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Status:") and "PnL:" not in line:
            continue
        match = _SUMMARY_PNL_RE.search(line)
        if match:
            return float(match.group(1))
    return None


def _normalize_trigger(trigger: str) -> str:
    token = trigger.rstrip("_")
    if token == "adaptive_long_open":
        return "adaptive_long"
    if token == "adaptive_short_open":
        return "adaptive_short"
    return token


def _infer_trigger(entry_class: str | None, side: str, trigger: str | None) -> str:
    if trigger and trigger not in {"none", ""}:
        return _normalize_trigger(trigger)
    if entry_class == "formal":
        return f"formal_{side}"
    if entry_class and "adaptive" in entry_class:
        return f"adaptive_{side}"
    return f"unknown_{side}"


def _narrative_entry_class(header: str, entry_class: str | None) -> str:
    if entry_class and entry_class not in {"hold", "none"}:
        return entry_class
    if re.search(r"OPENED\s+adaptive", header, re.IGNORECASE):
        return "regime_adaptive_half_size"
    if re.search(r"OPENED\s+(?:LONG|SHORT)", header, re.IGNORECASE):
        return "formal"
    return entry_class or ""


def _narrative_closes(header: str) -> list[tuple[str, str, float | None]]:
    closes: list[tuple[str, str, float | None]] = []
    if "thesis decay exit" not in header.lower():
        return closes
    for match in _CLOSED_NARRATIVE_RE.finditer(header):
        pair = match.group(1)
        if not pair.endswith("-USD"):
            pair = f"{pair.upper()}-USD"
        side = match.group(2).lower()
        pnl: float | None = None
        pnl_match = _THESIS_DECAY_PNL_RE.search(header[match.end() : match.end() + 40])
        if pnl_match:
            if pnl_match.group(1):
                pnl = float(pnl_match.group(1))
            elif pnl_match.group(2):
                pnl = -float(pnl_match.group(2))
        closes.append((pair, side, pnl))
    return closes


def _narrative_opens(header: str) -> list[tuple[str, str]]:
    opens: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(side: str, pair: str) -> None:
        key = (side.lower(), pair.upper())
        if key not in seen:
            seen.add(key)
            opens.append((side.lower(), pair.upper()))

    for match in _OPENED_SIDE_RE.finditer(header):
        add(match.group(1), match.group(2))
    for match in _OPENED_ADAPTIVE_SIDE_RE.finditer(header):
        add(match.group(1), match.group(2))
    for match in _OPENED_BOLD_PAIR_RE.finditer(header):
        add(match.group(2), match.group(1))
    for match in _OPENED_EMBEDDED_SIDE_RE.finditer(header):
        add(match.group(1), match.group(2))
    return opens


def _normalize_exit_reason(close_type: str | None) -> str:
    if not close_type:
        return "unknown"
    token = close_type.lower()
    if token in {"stop_loss", "gone_leg"}:
        return "stop_loss_close_proxy"
    if token == "take_profit":
        return "take_profit_close_proxy"
    if token == "flip_confirmed":
        return "flip_confirmed"
    if token == "session_end":
        return "session_end_proxy"
    return token


@dataclass
class _OpenLeg:
    entry_tick: int
    pair: str
    side: str
    entry_trigger: str
    entry_class: str


def _decision_line_for_tick(journal_text: str, tick: int) -> str:
    needle = f"**#{tick}**"
    for line in journal_text.splitlines():
        if _DECISION_RE.match(line) and needle in line:
            return line
    return ""


def extract_live_legs(
    journal_path: Path,
    session_dir: Path | None = None,
) -> list[LegRecord]:
    journal_text = journal_path.read_text(encoding="utf-8")
    session_dir = session_dir or journal_path.parent
    tick_map = parse_journal_ticks(journal_text, session_dir)

    tick_time_map = {tick: meta.timestamp for tick, meta in tick_map.items()}
    tick_headers: dict[int, str] = {}
    for line in journal_text.splitlines():
        tick_match = re.match(r"- tick#(\d+)\s+\|", line)
        if tick_match:
            tick_headers[int(tick_match.group(1))] = line

    open_by_pair: dict[str, list[_OpenLeg]] = {}
    completed: list[LegRecord] = []

    def record_open(
        tick: int,
        pair: str,
        side: str,
        entry_trigger: str,
        entry_class: str,
    ) -> None:
        open_by_pair.setdefault(pair, []).append(
            _OpenLeg(
                entry_tick=tick,
                pair=pair,
                side=side,
                entry_trigger=entry_trigger,
                entry_class=entry_class or "",
            )
        )

    def record_close(
        tick: int,
        pair: str,
        exit_reason: str,
        pnl_quote: float | None,
    ) -> None:
        stack = open_by_pair.get(pair)
        if not stack:
            return
        open_leg = stack.pop(0)
        completed.append(
            LegRecord(
                leg_num=len(completed) + 1,
                entry_tick=open_leg.entry_tick,
                exit_tick=tick,
                pair=open_leg.pair,
                side=open_leg.side,
                entry_trigger=open_leg.entry_trigger,
                entry_class=open_leg.entry_class,
                exit_reason=exit_reason,
                pnl_quote=pnl_quote,
            )
        )

    sorted_ticks = sorted(tick_map)
    if not sorted_ticks:
        return []

    for tick in sorted_ticks:
        meta = tick_map[tick]
        header = tick_headers.get(tick, "")
        decision_line = _decision_line_for_tick(journal_text, tick)

        trigger = None
        entry_class = meta.entry_class
        if decision_line:
            trigger_match = _TRIGGER_RE.search(decision_line)
            if trigger_match:
                trigger = trigger_match.group(1)
            entry_class_match = _ENTRY_CLASS_FIELD_RE.search(decision_line)
            if entry_class_match:
                entry_class = entry_class_match.group(1)

        plans = dict(meta.create_plans)
        if decision_line:
            parsed_decision = _parse_decision_line(decision_line, tick_time_map)
            if parsed_decision and parsed_decision.create_plans:
                plans.update(parsed_decision.create_plans)

        for pair, plan in plans.items():
            side = plan.side or "long"
            record_open(
                tick,
                pair,
                side,
                _infer_trigger(plan.entry_class or entry_class, side, trigger),
                plan.entry_class or entry_class or "",
            )

        opened_pairs = set(plans.keys())
        narrative_class = _narrative_entry_class(header, entry_class)
        for side, pair in _narrative_opens(header):
            if pair in opened_pairs:
                continue
            record_open(
                tick,
                pair,
                side,
                _infer_trigger(narrative_class, side, trigger),
                narrative_class,
            )

        if decision_line:
            for flip_match in _FLIP_EXIT_RE.finditer(decision_line):
                pair = f"{flip_match.group(1).upper()}-USD"
                pnl = meta.position_pnl_by_pair.get(pair)
                record_close(tick, pair, "flip_confirmed", pnl)

        for event in meta.barrier_closes:
            record_close(
                tick,
                event.pair,
                _normalize_exit_reason(event.close_type),
                event.pnl_quote,
            )

        for absent in _ABSENT_LEG_RE.finditer(header):
            pair = f"{absent.group(1).upper()}-USD"
            record_close(tick, pair, "stop_loss_close_proxy", None)

        for pair, side, pnl in _narrative_closes(header):
            record_close(tick, pair, "thesis_decay_exit", pnl)

    last_tick = sorted_ticks[-1]
    last_meta = tick_map[last_tick]
    for pair, stack in open_by_pair.items():
        while stack:
            open_leg = stack.pop(0)
            pnl = last_meta.position_pnl_by_pair.get(pair)
            if pnl is None and last_meta.monitored_pair == pair:
                pnl = last_meta.position_pnl_snapshot
            completed.append(
                LegRecord(
                    leg_num=len(completed) + 1,
                    entry_tick=open_leg.entry_tick,
                    exit_tick=last_tick,
                    pair=open_leg.pair,
                    side=open_leg.side,
                    entry_trigger=open_leg.entry_trigger,
                    entry_class=open_leg.entry_class,
                    exit_reason="session_end_proxy",
                    pnl_quote=pnl,
                )
            )

    return completed


def sim_trades_to_legs(trades: list[SimTrade]) -> list[LegRecord]:
    return [
        LegRecord(
            leg_num=index,
            entry_tick=trade.entry_tick,
            exit_tick=trade.exit_tick,
            pair=trade.pair,
            side=trade.side,
            entry_trigger=trade.entry_trigger,
            entry_class=trade.entry_class,
            exit_reason=trade.exit_reason,
            pnl_quote=trade.pnl_quote,
        )
        for index, trade in enumerate(trades, start=1)
    ]


def _leg_key(leg: LegRecord) -> tuple[str, int, str]:
    return (leg.pair, leg.entry_tick, leg.side)


def _exit_reasons_equivalent(live: str | None, sim: str | None) -> bool:
    if live is None or sim is None:
        return live == sim
    live_norm = _normalize_exit_reason(live)
    sim_norm = _normalize_exit_reason(sim)
    if live_norm == sim_norm:
        return True
    gone_like = {"stop_loss_close_proxy", "gone_leg", "unknown"}
    return live_norm in gone_like and sim_norm in gone_like


def compare_legs(
    live_legs: list[LegRecord],
    sim_legs: list[LegRecord],
) -> list[LegComparison]:
    sim_by_key = {_leg_key(leg): leg for leg in sim_legs}
    used_sim_keys: set[tuple[str, int, str]] = set()
    rows: list[LegComparison] = []

    for index, live in enumerate(live_legs, start=1):
        key = _leg_key(live)
        sim = sim_by_key.get(key)
        if sim is not None:
            used_sim_keys.add(key)
        rows.append(_comparison_row(index, live, sim))

    unmatched_sim = [leg for leg in sim_legs if _leg_key(leg) not in used_sim_keys]
    for sim in unmatched_sim:
        rows.append(_comparison_row(len(rows) + 1, None, sim))

    return rows


def _comparison_row(
    index: int,
    live: LegRecord | None,
    sim: LegRecord | None,
) -> LegComparison:
    pnl_delta = None
    if live and sim and live.pnl_quote is not None and sim.pnl_quote is not None:
        pnl_delta = round(sim.pnl_quote - live.pnl_quote, 2)
    return LegComparison(
        index=index,
        live=live,
        sim=sim,
        entry_tick_match=live.entry_tick == sim.entry_tick
        if live and sim
        else live is None and sim is None,
        exit_tick_match=live.exit_tick == sim.exit_tick
        if live and sim
        else live is None and sim is None,
        side_match=live.side == sim.side if live and sim else live is None and sim is None,
        trigger_match=live.entry_trigger == sim.entry_trigger
        or _normalize_trigger(live.entry_trigger) == _normalize_trigger(sim.entry_trigger)
        if live and sim
        else live is None and sim is None,
        exit_reason_match=_exit_reasons_equivalent(
            live.exit_reason if live else None,
            sim.exit_reason if sim else None,
        )
        if live and sim
        else live is None and sim is None,
        pnl_delta=pnl_delta,
    )


def format_leg(leg: LegRecord | None) -> str:
    if leg is None:
        return "(missing)"
    exit_tick = str(leg.exit_tick) if leg.exit_tick is not None else "?"
    pnl = f"{leg.pnl_quote:.2f}" if leg.pnl_quote is not None else "?"
    return (
        f"t{leg.entry_tick}->{exit_tick} {leg.side:5} {leg.pair:12} "
        f"{leg.entry_trigger:16} pnl={pnl:>7} exit={leg.exit_reason or '?'}"
    )


def format_comparison_report(
    session_num: int,
    live_pnl: float | None,
    sim_pnl: float,
    comparisons: list[LegComparison],
    *,
    policy_note: str | None = None,
) -> str:
    pnl_line = (
        f"PnL: live={live_pnl:.2f} sim={sim_pnl:.2f} delta={sim_pnl - live_pnl:.2f}"
        if live_pnl is not None
        else f"PnL: sim={sim_pnl:.2f}"
    )
    lines = [
        f"=== Session {session_num} position parity ===",
        pnl_line,
    ]
    if policy_note:
        lines.append(f"Policy: {policy_note}")
    lines.extend(
        [
            f"Legs: live={sum(1 for c in comparisons if c.live)} "
            f"sim={sum(1 for c in comparisons if c.sim)}",
            "",
            f"{'#':>2}  {'LIVE':<62}  {'SIM':<62}  FLAGS",
            "-" * 145,
        ]
    )
    mismatches = 0
    for row in comparisons:
        flags: list[str] = []
        if not row.entry_tick_match:
            flags.append("entry")
        if not row.exit_tick_match:
            flags.append("exit")
        if not row.side_match:
            flags.append("side")
        if not row.trigger_match:
            flags.append("trigger")
        if not row.exit_reason_match:
            flags.append("exit_reason")
        if row.pnl_delta is not None and abs(row.pnl_delta) > 0.05:
            flags.append(f"pnlΔ{row.pnl_delta:+.2f}")
        if flags:
            mismatches += 1
        flag_text = ",".join(flags) if flags else "ok"
        lines.append(
            f"{row.index:2d}  {format_leg(row.live):<62}  {format_leg(row.sim):<62}  {flag_text}"
        )
    aligned = len(comparisons) - mismatches
    lines.append("")
    lines.append(f"Summary: {aligned}/{len(comparisons)} legs fully aligned")
    return "\n".join(lines)
