from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.models import (
    BarrierCloseEvent,
    Filter4h,
    JournalCreatePlan,
    JournalSignal1h,
    TickMeta,
)

_TICK_RE = re.compile(r"- tick#(\d+)\s+\|\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+\|")
_DECISION_RE = re.compile(r"- \*\*#(\d+)\*\*.*")
_DECISION_TICK_RE = re.compile(r"tick=(\d+)")
_PAIR_TOKEN = r"[A-Za-z0-9:-]+"
_CREATE_PLAN_RE = re.compile(r"create_plan=([^:\s]+):([^|\s]+)")
_MACD_PAIRS_RE = re.compile(rf"macd_pairs=({_PAIR_TOKEN}(?:,{_PAIR_TOKEN})*)")
_ADAPTIVE_ACTIVATION_STREAK_RE = re.compile(
    r"(?:adaptive_activation_streak|neutral_pressure_streak)=(\d+)"
)
_THESIS_DECAY_STREAK_RE = re.compile(
    r"(?:thesis_decay_streak|neutral_streak)=(\d+)"
)
_ENTRY_CLASS_RE = re.compile(r"entry_class=([a-zA-Z0-9_]+)")
_TRADEABLE_COUNT_RE = re.compile(r"tradeable_count=(\d+)")
_SCANNER_ANALYZED_RE = re.compile(r"scanner_analyzed=(\d+)")
_SCANNER_REGIME_RE = re.compile(r"scanner_regime=(mature|degen)", re.IGNORECASE)
_NATR_FLOOR_USED_RE = re.compile(r"natr_floor_used=([0-9.]+)")
_BEST_SCORE_RE = re.compile(r"best_score=([0-9.]+)")
_QUEUE_TOTAL_RE = re.compile(rf"queue_total=({_PAIR_TOKEN}(?:,{_PAIR_TOKEN})*)")
_JOURNAL_BLOB_NEXT_FIELD = (
    r"(?:\s(?:filter_4h|best_candidate|near_miss|create_plan|position_pnl_snapshot|"
    r"monitor_state|thesis_decay_streak|flip_streak|cooldown_remaining_ticks|"
    r"queue_primary|macd_reviewed|entry_class|pair|trigger|position_action|"
    r"hold_reason|strictness_mode|adaptive_activation_streak)=|$)"
)
_SIGNALS_1H_RE = re.compile(rf"signals_1h=(.+?){_JOURNAL_BLOB_NEXT_FIELD}")
_FILTER_4H_RE = re.compile(rf"filter_4h=(.+?){_JOURNAL_BLOB_NEXT_FIELD}")
_REVIEWED_MACD_LIST_RE = re.compile(
    r"reviewed 5 MACD 1h(?: pairs)?:\s*([A-Za-z0-9,\sk]+?)\s*(?:—| - |\.|$)",
    re.IGNORECASE,
)
_PAREN_MACD_REVIEWS_RE = re.compile(
    r"five 1h reviews?\s*\(([A-Za-z0-9,\sk/]+)\)",
    re.IGNORECASE,
)
_TICK_SUMMARY_ENTRY_RE = re.compile(
    r"\*\*Tick #\d+ —\s*(HOLD|OPENED LONG(?:\s+[A-Z0-9:-]+)?|OPENED SHORT(?:\s+[A-Z0-9:-]+)?)",
    re.IGNORECASE,
)
_TICK_STREAK_RE = re.compile(
    r"(?:adaptive_activation_streak|adaptive_activation_streak)(?:`|')?(?:=| reaches | is )[\s*]*(\d+)",
    re.IGNORECASE,
)
_THESIS_DECAY_TICK_STREAK_RE = re.compile(
    r"(?:thesis_decay_streak|neutral_streak)(?:`|')?(?:=| reaches | is )[\s*]*(\d+)",
    re.IGNORECASE,
)
_TICK_STREAK_ALT_RE = re.compile(r"Adaptive streak \*\*(\d+)\*\*", re.IGNORECASE)
_TICK_STREAK_PAREN_RE = re.compile(r"\(streak (\d+)\)", re.IGNORECASE)
_PRE_OPEN_STREAK_RE = re.compile(
    r"neutral streak\s*\*?\*?(\d+)\*?\*?(?:/6|→| -|→reset)",
    re.IGNORECASE,
)
_SYMBOL_USD_RE = re.compile(r"\b([A-Z][A-Z0-9]*-USD)\b")
_BOLD_PAIR_LIST_RE = re.compile(
    r"\*\*([A-Z][A-Z0-9]*(?:,\s*[A-Z][A-Z0-9]*)+)\*\*"
)
_OPENED_PAIR_RE = re.compile(
    r"OPENED\s+(?:LONG|SHORT)\s+([A-Z][A-Z0-9]*-USD)",
    re.IGNORECASE,
)

_SIGNAL_TUPLE_RE = re.compile(
    rf"(?:^|\s*\|\s*)({_PAIR_TOKEN}):bb=([^,]+),macd=([^,]+),sig=([^,]+),hist=([^,]+),"
    r"gap=([^,]+),hr=([^,]+),tr=([^,]+),mom=([^,]+),"
    r"fL=([^,]+),fS=([^,]+),aL=([^,]+),aS=([^,]+),sL=([^,]+),sS=([^,|;\s]+)"
    r"(?:,mid=([^,]+),up=([^,]+)(?:,lo=([^,]+))?(?:,bX=([^,]+),sX=([^,]+),p=([^,|;\s]+))?)?"
)
_FILTER_4H_TUPLE_RE = re.compile(
    rf"(?:^|\s*\|\s*)({_PAIR_TOKEN}):tr=([^,]+)(?:,bb=([^,]+))?(?:,macd=([^,]+))?"
    r"(?:,sig=([^,]+))?(?:,hist=([^,]+))?,pass=([01])"
)
_MONITORED_PAIR_RE = re.compile(r"pair=([A-Z0-9:-]+)")
_POSITION_PNL_RE = re.compile(r"position_pnl_snapshot=([-+]?[0-9.]+)")
_POSITION_PNL_PAIR_RE = re.compile(
    r"position_pnl_snapshot=([A-Z0-9]+:[-+]?[0-9.]+(?:,[A-Z0-9]+:[-+]?[0-9.]+)*)"
)
_BARRIER_CLOSE_TYPE_RE = re.compile(r"close_type=(STOP_LOSS|TAKE_PROFIT)", re.IGNORECASE)
_BARRIER_PNL_RE = re.compile(r"pnl=([-+]?[0-9.]+)")
_BOGUS_BARRIER_PAIR_BASES = frozenset(
    {
        "class",
        "tick",
        "between",
        "entry",
        "open",
        "pair",
        "hold",
        "regime",
        "adaptive",
        "long",
        "short",
        "stop",
        "loss",
        "hit",
    }
)
_BARRIER_CLOSE_FIELD_RE = re.compile(
    r"barrier_close=([A-Z0-9:-]+):(STOP_LOSS|TAKE_PROFIT):pnl=([-+]?[0-9.]+)",
    re.IGNORECASE,
)
_BARRIER_TABLE_RE = re.compile(
    r"-\s*(?P<pair>[A-Z][A-Z0-9:-]+)\s+\d+\s*\|\s*(?P<close>STOP_LOSS|TAKE_PROFIT)\s*\|\s*PnL\s*\$?([-+]?[0-9.]+)",
    re.IGNORECASE,
)
_BARRIER_HIT_RE = re.compile(
    r"\b(?P<pair>[A-Z][A-Z0-9]{2,10})(?:-USD)?\s+hit\s+(?P<close>STOP_LOSS|TAKE_PROFIT)",
    re.IGNORECASE,
)
_BARRIER_PAIR_HIT_BOLD_RE = re.compile(
    r"\b(?P<pair>[A-Z][A-Z0-9]{2,10}-USD)\s+(?:LONG|SHORT)\s+hit\s+\*\*(?P<close>STOP_LOSS|TAKE_PROFIT)\*\*",
    re.IGNORECASE,
)
_SNAPSHOT_FILENAME_RE = re.compile(r"snapshot_(\d+)\.md$", re.IGNORECASE)
_BARRIER_CLOSES_SECTION_HEADER = "[BARRIER CLOSES SINCE LAST TICK]"
_SESSION_PNL_RE = re.compile(
    r"Session PnL \*\*(-?\$?[0-9.]+)\*\*",
    re.IGNORECASE,
)
_GONE_LEG_RE = re.compile(
    r"([A-Z0-9]+)(?:-USD)?(?:\s+(?:adaptive|formal))?\s+leg\s+from\s+tick\s+#\d+\s+(?:is\s+gone|gone\b)",
    re.IGNORECASE,
)


def _parse_session_pnl_token(raw: str) -> float:
    return float(raw.replace("$", ""))


def parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


def _normalize_journal_pair_token(raw: str) -> str:
    token = raw.strip()
    if not token or token.lower() in {"all", "none", "hold"}:
        return ""
    if token.endswith("-USD"):
        return token
    return f"{token}-USD"


def _normalize_journal_pair_list(raw: str) -> list[str]:
    pairs: list[str] = []
    for part in re.split(r"[,/]", raw):
        normalized = _normalize_journal_pair_token(part)
        if normalized and normalized not in pairs:
            pairs.append(normalized)
    return pairs


def _parse_position_pnl_fields(
    line: str,
    monitored_pair: str | None,
) -> tuple[float | None, dict[str, float]]:
    pair_match = _POSITION_PNL_PAIR_RE.search(line)
    if pair_match:
        by_pair: dict[str, float] = {}
        for token in pair_match.group(1).split(","):
            pair_raw, pnl_raw = token.split(":", 1)
            pair = _normalize_journal_pair_token(pair_raw)
            if pair:
                by_pair[pair] = float(pnl_raw)
        snapshot = None
        if monitored_pair and monitored_pair in by_pair:
            snapshot = by_pair[monitored_pair]
        elif len(by_pair) == 1:
            snapshot = next(iter(by_pair.values()))
        return snapshot, by_pair
    single_match = _POSITION_PNL_RE.search(line)
    if single_match:
        value = float(single_match.group(1))
        by_pair = {monitored_pair: value} if monitored_pair else {}
        return value, by_pair
    return None, {}


def _normalize_trend(value: str) -> str:
    token = value.strip().lower()
    if token in {"bull", "bullish"}:
        return "bullish"
    if token in {"bear", "bearish"}:
        return "bearish"
    return token


def _normalize_momentum(value: str) -> str:
    token = value.strip().lower()
    if token in {"inc", "increasing"}:
        return "increasing"
    if token in {"dec", "decreasing"}:
        return "decreasing"
    return token


def _optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"unknown", "none", "nan", "null", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_bool_flag(value: str) -> bool:
    return value.strip() in {"1", "true", "True"}


def _parse_signals_1h(raw: str) -> dict[str, JournalSignal1h]:
    signals: dict[str, JournalSignal1h] = {}
    for match in _SIGNAL_TUPLE_RE.finditer(raw):
        pair = match.group(1)
        bb_mid_raw = match.group(16)
        bb_upper_raw = match.group(17)
        bb_lower_raw = match.group(18)
        bX_raw = match.group(19)
        sX_raw = match.group(20)
        price_raw = match.group(21)
        signals[pair] = JournalSignal1h(
            pair=pair,
            bb_pos_pct=float(match.group(2)),
            macd=float(match.group(3)),
            signal_line=float(match.group(4)),
            histogram=float(match.group(5)),
            macd_gap_ratio=float(match.group(6)),
            hist_ratio=float(match.group(7)),
            trend=_normalize_trend(match.group(8)),
            momentum=_normalize_momentum(match.group(9)),
            formal_long=_parse_bool_flag(match.group(10)),
            formal_short=_parse_bool_flag(match.group(11)),
            adaptive_long=_parse_bool_flag(match.group(12)),
            adaptive_short=_parse_bool_flag(match.group(13)),
            strength_long=float(match.group(14)),
            strength_short=float(match.group(15)),
            bb_mid=float(bb_mid_raw) if bb_mid_raw else None,
            bb_upper=float(bb_upper_raw) if bb_upper_raw else None,
            bb_lower=float(bb_lower_raw) if bb_lower_raw else None,
            bullish_cross=_parse_bool_flag(bX_raw) if bX_raw else None,
            bearish_cross=_parse_bool_flag(sX_raw) if sX_raw else None,
            price=float(price_raw) if price_raw else None,
        )
    return signals


def _parse_filter_4h(raw: str) -> dict[str, Filter4h]:
    filters: dict[str, Filter4h] = {}
    for match in _FILTER_4H_TUPLE_RE.finditer(raw):
        pair = match.group(1)
        bb_raw = match.group(3)
        macd_raw = match.group(4)
        sig_raw = match.group(5)
        hist_raw = match.group(6)
        filters[pair] = Filter4h(
            pair=pair,
            trend=_normalize_trend(match.group(2)),
            bb_pos_pct=_optional_float(bb_raw),
            macd=_optional_float(macd_raw),
            signal_line=_optional_float(sig_raw),
            histogram=_optional_float(hist_raw),
            passed=match.group(7) == "1",
        )
    return filters


def _extract_pairs_from_tick_narrative(line: str) -> list[str]:
    for pattern in (_REVIEWED_MACD_LIST_RE, _PAREN_MACD_REVIEWS_RE):
        match = pattern.search(line)
        if match:
            pairs = _normalize_journal_pair_list(match.group(1))
            if len(pairs) >= 2:
                return pairs
    bold_match = _BOLD_PAIR_LIST_RE.search(line)
    if bold_match:
        pairs = _normalize_journal_pair_list(bold_match.group(1))
        if pairs:
            return pairs[:8]
    opened_match = _OPENED_PAIR_RE.search(line)
    pairs: list[str] = []
    if opened_match:
        pairs.append(opened_match.group(1).upper())
    for symbol_match in _SYMBOL_USD_RE.finditer(line):
        pair = symbol_match.group(1)
        if pair not in pairs:
            pairs.append(pair)
    if pairs:
        return pairs[:8]
    return []


def _extract_pre_open_streak_from_narrative(line: str) -> int | None:
    match = _PRE_OPEN_STREAK_RE.search(line)
    if match:
        return int(match.group(1))
    return None


def _extract_streak_from_tick_narrative(line: str) -> int | None:
    for pattern in (
        _TICK_STREAK_RE,
        _TICK_STREAK_ALT_RE,
        _TICK_STREAK_PAREN_RE,
    ):
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def _extract_thesis_decay_streak_from_tick_narrative(line: str) -> int | None:
    match = _THESIS_DECAY_TICK_STREAK_RE.search(line)
    if match:
        return int(match.group(1))
    return None


def _extract_entry_class_from_tick_narrative(line: str) -> str | None:
    entry_class_match = _ENTRY_CLASS_RE.search(line)
    if entry_class_match:
        return entry_class_match.group(1)
    summary_match = _TICK_SUMMARY_ENTRY_RE.search(line)
    if not summary_match:
        return None
    summary = summary_match.group(1).upper()
    if summary.startswith("OPENED LONG"):
        return "opened_long"
    if summary.startswith("OPENED SHORT"):
        return "opened_short"
    return "hold"


def _barrier_pair_allowed(pair: str) -> bool:
    if not pair or not pair.endswith("-USD"):
        return False
    base = pair[:-4].lower()
    return base not in _BOGUS_BARRIER_PAIR_BASES and len(base) >= 2


def _barrier_pnl_from_tail(tail: str) -> float | None:
    pnl_paren = re.search(r"\(-?\$?([0-9.]+)\)", tail)
    if pnl_paren:
        return -float(pnl_paren.group(1))
    pnl_bold = re.search(r"PnL\s*\*\*(-?\$?([0-9.]+))\*\*", tail, re.IGNORECASE)
    if pnl_bold:
        token = pnl_bold.group(1).replace("$", "")
        return float(token)
    return None


def _append_barrier_event(
    events: list[BarrierCloseEvent],
    seen: set[tuple[str, str]],
    pair_raw: str,
    close_type_raw: str,
    pnl_quote: float | None,
) -> None:
    pair = _normalize_journal_pair_token(pair_raw)
    close_type = close_type_raw.lower()
    if not _barrier_pair_allowed(pair):
        return
    key = (pair, close_type)
    if key in seen:
        if pnl_quote is not None:
            for index, event in enumerate(events):
                if (
                    event.pair == pair
                    and event.close_type == close_type
                    and event.pnl_quote is None
                ):
                    events[index] = BarrierCloseEvent(
                        pair=pair,
                        close_type=close_type,
                        pnl_quote=pnl_quote,
                    )
                    break
        return
    seen.add(key)
    events.append(
        BarrierCloseEvent(
            pair=pair,
            close_type=close_type,
            pnl_quote=pnl_quote,
        )
    )


def _parse_barrier_events(line: str) -> list[BarrierCloseEvent]:
    events: list[BarrierCloseEvent] = []
    seen: set[tuple[str, str]] = set()

    for match in _BARRIER_CLOSE_FIELD_RE.finditer(line):
        _append_barrier_event(
            events,
            seen,
            match.group(1),
            match.group(2),
            float(match.group(3)),
        )

    for match in _BARRIER_TABLE_RE.finditer(line):
        _append_barrier_event(
            events,
            seen,
            match.group("pair"),
            match.group("close"),
            float(match.group(3)),
        )

    close_type_match = _BARRIER_CLOSE_TYPE_RE.search(line)
    pair_match = _MONITORED_PAIR_RE.search(line)
    pnl_match = _BARRIER_PNL_RE.search(line)
    if (
        close_type_match
        and pair_match
        and "barrier_close" in line.lower()
        and ",open" not in line.lower()
    ):
        pnl_quote = float(pnl_match.group(1)) if pnl_match else None
        _append_barrier_event(
            events,
            seen,
            pair_match.group(1),
            close_type_match.group(1),
            pnl_quote,
        )

    for pattern in (_BARRIER_HIT_RE, _BARRIER_PAIR_HIT_BOLD_RE):
        for match in pattern.finditer(line):
            tail = line[match.end() : match.end() + 120]
            _append_barrier_event(
                events,
                seen,
                match.group("pair"),
                match.group("close"),
                _barrier_pnl_from_tail(tail),
            )

    return events


def _merge_barrier_events(
    existing: list[BarrierCloseEvent],
    incoming: list[BarrierCloseEvent],
) -> list[BarrierCloseEvent]:
    merged = list(existing)
    seen = {(event.pair, event.close_type) for event in merged}
    for event in incoming:
        key = (event.pair, event.close_type)
        if key in seen:
            if event.pnl_quote is not None:
                for index, current in enumerate(merged):
                    if (
                        current.pair == event.pair
                        and current.close_type == event.close_type
                        and current.pnl_quote is None
                    ):
                        merged[index] = event
                        break
            continue
        merged.append(event)
        seen.add(key)
    return merged


def _parse_snapshot_barrier_closes(snapshot_text: str) -> list[BarrierCloseEvent]:
    """Parse [BARRIER CLOSES SINCE LAST TICK] rows from a tick snapshot file."""
    events: list[BarrierCloseEvent] = []
    in_section = False

    for line in snapshot_text.splitlines():
        stripped = line.strip()
        if stripped.upper() == _BARRIER_CLOSES_SECTION_HEADER:
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if stripped.startswith("-"):
            events = _merge_barrier_events(events, _parse_barrier_events(line))

    return events


def _parse_scanner_regime(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.strip().lower()
    if token in {"mature", "degen"}:
        return token
    return None


def _parse_create_plan(raw: str) -> JournalCreatePlan | None:
    match = _CREATE_PLAN_RE.search(raw)
    if not match:
        return None
    pair = _normalize_journal_pair_token(match.group(1))
    side: str | None = None
    entry_class: str | None = None
    notional_req: float | None = None
    notional_cap: float | None = None
    eff_sl: float | None = None
    eff_tp: float | None = None
    vol: float | None = None
    size_mult: float | None = None
    for token in match.group(2).split(","):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "side":
            side = value
        elif key == "entry_class":
            entry_class = value
        else:
            try:
                parsed = float(value)
            except ValueError:
                continue
            if key == "notional_req":
                notional_req = parsed
            elif key == "notional_cap":
                notional_cap = parsed
            elif key == "eff_sl":
                eff_sl = parsed
            elif key == "eff_tp":
                eff_tp = parsed
            elif key == "vol":
                vol = parsed
            elif key == "size_mult":
                size_mult = parsed
    return JournalCreatePlan(
        pair=pair,
        side=side,
        entry_class=entry_class,
        notional_req=notional_req,
        notional_cap=notional_cap,
        eff_sl=eff_sl,
        eff_tp=eff_tp,
        vol=vol,
        size_mult=size_mult,
    )


def _parse_decision_line(line: str, tick_time_map: dict[int, dt.datetime]) -> TickMeta | None:
    decision_match = _DECISION_RE.match(line)
    if not decision_match:
        return None
    tick_field_match = _DECISION_TICK_RE.search(line)
    tick_number = (
        int(tick_field_match.group(1))
        if tick_field_match
        else int(decision_match.group(1))
    )
    if tick_number not in tick_time_map:
        return None

    pairs_match = _MACD_PAIRS_RE.search(line)
    reviewed_pairs = pairs_match.group(1).split(",") if pairs_match else []
    streak_match = _ADAPTIVE_ACTIVATION_STREAK_RE.search(line)
    thesis_decay_match = _THESIS_DECAY_STREAK_RE.search(line)
    entry_class_match = _ENTRY_CLASS_RE.search(line)
    tradeable_match = _TRADEABLE_COUNT_RE.search(line)
    analyzed_match = _SCANNER_ANALYZED_RE.search(line)
    regime_match = _SCANNER_REGIME_RE.search(line)
    natr_floor_match = _NATR_FLOOR_USED_RE.search(line)
    best_score_match = _BEST_SCORE_RE.search(line)
    queue_match = _QUEUE_TOTAL_RE.search(line)
    signals_match = _SIGNALS_1H_RE.search(line)
    filter_match = _FILTER_4H_RE.search(line)
    monitored_match = _MONITORED_PAIR_RE.search(line)

    monitored_pair = (
        _normalize_journal_pair_token(monitored_match.group(1))
        if monitored_match
        else None
    )
    position_pnl_snapshot, position_pnl_by_pair = _parse_position_pnl_fields(
        line,
        monitored_pair,
    )
    create_plan = _parse_create_plan(line)
    create_plans = {create_plan.pair: create_plan} if create_plan else {}
    return TickMeta(
        tick=tick_number,
        timestamp=tick_time_map[tick_number],
        macd_pairs=reviewed_pairs,
        adaptive_activation_streak=int(streak_match.group(1)) if streak_match else None,
        thesis_decay_streak=int(thesis_decay_match.group(1))
        if thesis_decay_match
        else None,
        entry_class=entry_class_match.group(1) if entry_class_match else None,
        tradeable_count=int(tradeable_match.group(1)) if tradeable_match else None,
        scanner_analyzed=int(analyzed_match.group(1)) if analyzed_match else None,
        scanner_regime=_parse_scanner_regime(regime_match.group(1))
        if regime_match
        else None,
        natr_floor_used=float(natr_floor_match.group(1)) if natr_floor_match else None,
        best_score=float(best_score_match.group(1)) if best_score_match else None,
        queue_total=_normalize_journal_pair_list(queue_match.group(1))
        if queue_match
        else [],
        signals_1h=_parse_signals_1h(signals_match.group(1)) if signals_match else {},
        filter_4h=_parse_filter_4h(filter_match.group(1)) if filter_match else {},
        monitored_pair=monitored_pair or None,
        position_pnl_snapshot=position_pnl_snapshot,
        position_pnl_by_pair=position_pnl_by_pair,
        barrier_closes=_parse_barrier_events(line),
        create_plans=create_plans,
    )


def parse_journal_ticks(
    journal_text: str,
    session_dir: Path | None = None,
) -> dict[int, TickMeta]:
    tick_time_map: dict[int, dt.datetime] = {}
    tick_header_lines: dict[int, str] = {}
    for line in journal_text.splitlines():
        tick_match = _TICK_RE.match(line)
        if not tick_match:
            continue
        tick_number = int(tick_match.group(1))
        tick_time_map[tick_number] = parse_dt(tick_match.group(2))
        tick_header_lines[tick_number] = line

    tick_meta_map: dict[int, TickMeta] = {}
    for line in journal_text.splitlines():
        parsed = _parse_decision_line(line, tick_time_map)
        if parsed is not None:
            tick_meta_map[parsed.tick] = parsed

    for tick_number, line in tick_header_lines.items():
        if tick_number in tick_meta_map:
            continue
        tick_meta_map[tick_number] = TickMeta(
            tick=tick_number,
            timestamp=tick_time_map[tick_number],
            macd_pairs=_extract_pairs_from_tick_narrative(line),
            adaptive_activation_streak=_extract_streak_from_tick_narrative(line),
            thesis_decay_streak=_extract_thesis_decay_streak_from_tick_narrative(line),
            entry_class=_extract_entry_class_from_tick_narrative(line),
        )

    if session_dir is not None:
        tick_meta_map = enrich_ticks_from_snapshots(tick_meta_map, session_dir)

    last_pairs: list[str] = []
    last_signals: dict[str, JournalSignal1h] = {}
    last_filter_4h: dict[str, Filter4h] = {}
    last_tradeable_count: int | None = None
    last_scanner_analyzed: int | None = None
    last_scanner_regime: str | None = None
    last_natr_floor_used: float | None = None
    last_best_score: float | None = None
    last_queue_total: list[str] = []

    for tick_number in sorted(tick_meta_map):
        meta = tick_meta_map[tick_number]
        header_line = tick_header_lines.get(tick_number, "")

        pre_open_streak = _extract_pre_open_streak_from_narrative(header_line)
        if pre_open_streak is not None and (
            meta.adaptive_activation_streak is None
            or meta.adaptive_activation_streak < pre_open_streak
        ):
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                adaptive_activation_streak=pre_open_streak,
                thesis_decay_streak=meta.thesis_decay_streak,
                entry_class=meta.entry_class,
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                scanner_regime=meta.scanner_regime,
                natr_floor_used=meta.natr_floor_used,
                best_score=meta.best_score,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=meta.filter_4h,
                monitored_pair=meta.monitored_pair,
                position_pnl_snapshot=meta.position_pnl_snapshot,
                barrier_closes=list(meta.barrier_closes),
                create_plans=dict(meta.create_plans),
            )
            tick_meta_map[tick_number] = meta

        opened_match = _OPENED_PAIR_RE.search(header_line)
        if opened_match and "4h bullish" in header_line.lower():
            opened_pair = opened_match.group(1).upper()
            filter_map = dict(meta.filter_4h)
            filter_map[opened_pair] = Filter4h(
                pair=opened_pair,
                trend="bullish",
                passed=True,
            )
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                adaptive_activation_streak=meta.adaptive_activation_streak,
                thesis_decay_streak=meta.thesis_decay_streak,
                entry_class=meta.entry_class or "opened_long",
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                scanner_regime=meta.scanner_regime,
                natr_floor_used=meta.natr_floor_used,
                best_score=meta.best_score,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=filter_map,
                monitored_pair=meta.monitored_pair,
                position_pnl_snapshot=meta.position_pnl_snapshot,
                barrier_closes=list(meta.barrier_closes),
                create_plans=dict(meta.create_plans),
            )
            tick_meta_map[tick_number] = meta
        elif opened_match and "4h bearish" in header_line.lower():
            opened_pair = opened_match.group(1).upper()
            filter_map = dict(meta.filter_4h)
            filter_map[opened_pair] = Filter4h(
                pair=opened_pair,
                trend="bearish",
                passed=True,
            )
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                adaptive_activation_streak=meta.adaptive_activation_streak,
                thesis_decay_streak=meta.thesis_decay_streak,
                entry_class=meta.entry_class or "opened_short",
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                scanner_regime=meta.scanner_regime,
                natr_floor_used=meta.natr_floor_used,
                best_score=meta.best_score,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=filter_map,
                monitored_pair=meta.monitored_pair,
                position_pnl_snapshot=meta.position_pnl_snapshot,
                barrier_closes=list(meta.barrier_closes),
                create_plans=dict(meta.create_plans),
            )
            tick_meta_map[tick_number] = meta

        if meta.macd_pairs:
            if len(meta.macd_pairs) >= len(last_pairs):
                last_pairs = meta.macd_pairs
        if meta.signals_1h:
            last_signals = dict(meta.signals_1h)
        if meta.filter_4h:
            last_filter_4h = dict(meta.filter_4h)
        if meta.tradeable_count is not None:
            last_tradeable_count = meta.tradeable_count
        if meta.scanner_analyzed is not None:
            last_scanner_analyzed = meta.scanner_analyzed
        if meta.scanner_regime is not None:
            last_scanner_regime = meta.scanner_regime
        if meta.natr_floor_used is not None:
            last_natr_floor_used = meta.natr_floor_used
        if meta.best_score is not None:
            last_best_score = meta.best_score
        if meta.queue_total:
            last_queue_total = list(meta.queue_total)

        needs_carry = (
            not meta.macd_pairs
            or not meta.signals_1h
            or meta.tradeable_count is None
        )
        if not needs_carry:
            continue

        tick_meta_map[tick_number] = TickMeta(
            tick=meta.tick,
            timestamp=meta.timestamp,
            macd_pairs=list(meta.macd_pairs or last_pairs),
            adaptive_activation_streak=meta.adaptive_activation_streak,
            thesis_decay_streak=meta.thesis_decay_streak,
            entry_class=meta.entry_class,
            tradeable_count=meta.tradeable_count
            if meta.tradeable_count is not None
            else last_tradeable_count,
            scanner_analyzed=meta.scanner_analyzed
            if meta.scanner_analyzed is not None
            else last_scanner_analyzed,
            scanner_regime=meta.scanner_regime
            if meta.scanner_regime is not None
            else last_scanner_regime,
            natr_floor_used=meta.natr_floor_used
            if meta.natr_floor_used is not None
            else last_natr_floor_used,
            best_score=meta.best_score if meta.best_score is not None else last_best_score,
            queue_total=list(meta.queue_total or last_queue_total),
            signals_1h=dict(meta.signals_1h or last_signals),
            filter_4h=dict(meta.filter_4h or last_filter_4h),
            monitored_pair=meta.monitored_pair,
            position_pnl_snapshot=meta.position_pnl_snapshot,
            barrier_closes=list(meta.barrier_closes),
            create_plans=dict(meta.create_plans),
        )

        if meta.adaptive_activation_streak is None:
            streak = _extract_streak_from_tick_narrative(header_line)
            if streak is not None:
                carried = tick_meta_map[tick_number]
                tick_meta_map[tick_number] = TickMeta(
                    tick=carried.tick,
                    timestamp=carried.timestamp,
                    macd_pairs=carried.macd_pairs,
                    adaptive_activation_streak=streak,
                    thesis_decay_streak=carried.thesis_decay_streak,
                    entry_class=carried.entry_class,
                    tradeable_count=carried.tradeable_count,
                    scanner_analyzed=carried.scanner_analyzed,
                    scanner_regime=carried.scanner_regime,
                    natr_floor_used=carried.natr_floor_used,
                    best_score=carried.best_score,
                    queue_total=carried.queue_total,
                    signals_1h=carried.signals_1h,
                    filter_4h=carried.filter_4h,
                    monitored_pair=carried.monitored_pair,
                    position_pnl_snapshot=carried.position_pnl_snapshot,
                    barrier_closes=list(carried.barrier_closes),
                )

    for tick_number, line in tick_header_lines.items():
        meta = tick_meta_map.get(tick_number)
        if meta is None:
            continue
        header_barriers = _parse_barrier_events(line)
        if not header_barriers:
            continue
        tick_meta_map[tick_number] = replace(
            meta,
            barrier_closes=_merge_barrier_events(meta.barrier_closes, header_barriers),
        )

    session_pnls: dict[int, float] = {}
    for tick_number, line in tick_header_lines.items():
        pnl_match = _SESSION_PNL_RE.search(line)
        if pnl_match is not None:
            session_pnls[tick_number] = _parse_session_pnl_token(pnl_match.group(1))

    for tick_number, line in tick_header_lines.items():
        meta = tick_meta_map.get(tick_number)
        if meta is None:
            continue
        gone_barriers: list[BarrierCloseEvent] = []
        for gone_match in _GONE_LEG_RE.finditer(line):
            pair = _normalize_journal_pair_token(gone_match.group(1))
            if not pair or any(event.pair == pair for event in meta.barrier_closes):
                continue
            cur_pnl = session_pnls.get(tick_number)
            prev_pnl = session_pnls.get(tick_number - 1)
            leg_pnl = (
                round(cur_pnl - prev_pnl, 2)
                if cur_pnl is not None and prev_pnl is not None
                else None
            )
            gone_barriers.append(
                BarrierCloseEvent(
                    pair=pair,
                    close_type="gone_leg",
                    pnl_quote=leg_pnl,
                )
            )
        if gone_barriers:
            tick_meta_map[tick_number] = replace(
                meta,
                barrier_closes=_merge_barrier_events(meta.barrier_closes, gone_barriers),
            )

    return tick_meta_map


def enrich_ticks_from_snapshots(
    tick_meta_map: dict[int, TickMeta],
    session_dir: Path,
) -> dict[int, TickMeta]:
    """Merge structured decision telemetry from snapshot files when journal rows are missing."""
    snapshots_dir = session_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return tick_meta_map

    tick_time_map = {tick: meta.timestamp for tick, meta in tick_meta_map.items()}
    enriched = dict(tick_meta_map)

    for snapshot_path in sorted(snapshots_dir.glob("snapshot_*.md")):
        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        filename_match = _SNAPSHOT_FILENAME_RE.search(snapshot_path.name)
        if filename_match is not None:
            snapshot_tick = int(filename_match.group(1))
            snapshot_barriers = _parse_snapshot_barrier_closes(snapshot_text)
            if snapshot_barriers and snapshot_tick in enriched:
                existing = enriched[snapshot_tick]
                enriched[snapshot_tick] = replace(
                    existing,
                    barrier_closes=_merge_barrier_events(
                        existing.barrier_closes,
                        snapshot_barriers,
                    ),
                )

        for line in snapshot_text.splitlines():
            parsed = _parse_decision_line(line, tick_time_map)
            if parsed is None:
                continue
            existing = enriched.get(parsed.tick)
            if existing is None:
                enriched[parsed.tick] = parsed
                continue
            use_snapshot = (
                len(parsed.signals_1h) > len(existing.signals_1h)
                or (
                    parsed.filter_4h
                    and not existing.filter_4h
                )
                or (
                    parsed.adaptive_activation_streak is not None
                    and existing.adaptive_activation_streak is None
                )
                or (
                    parsed.thesis_decay_streak is not None
                    and existing.thesis_decay_streak is None
                )
                or (
                    parsed.monitored_pair
                    and not existing.monitored_pair
                )
            )
            if not use_snapshot:
                continue
            merged_barriers = _merge_barrier_events(
                existing.barrier_closes,
                parsed.barrier_closes,
            )
            enriched[parsed.tick] = TickMeta(
                tick=parsed.tick,
                timestamp=existing.timestamp,
                macd_pairs=parsed.macd_pairs or existing.macd_pairs,
                adaptive_activation_streak=parsed.adaptive_activation_streak
                if parsed.adaptive_activation_streak is not None
                else existing.adaptive_activation_streak,
                thesis_decay_streak=parsed.thesis_decay_streak
                if parsed.thesis_decay_streak is not None
                else existing.thesis_decay_streak,
                entry_class=parsed.entry_class or existing.entry_class,
                tradeable_count=parsed.tradeable_count
                if parsed.tradeable_count is not None
                else existing.tradeable_count,
                scanner_analyzed=parsed.scanner_analyzed
                if parsed.scanner_analyzed is not None
                else existing.scanner_analyzed,
                scanner_regime=parsed.scanner_regime
                if parsed.scanner_regime is not None
                else existing.scanner_regime,
                natr_floor_used=parsed.natr_floor_used
                if parsed.natr_floor_used is not None
                else existing.natr_floor_used,
                best_score=parsed.best_score
                if parsed.best_score is not None
                else existing.best_score,
                queue_total=parsed.queue_total or existing.queue_total,
                signals_1h=parsed.signals_1h or existing.signals_1h,
                filter_4h={**existing.filter_4h, **parsed.filter_4h},
                monitored_pair=parsed.monitored_pair or existing.monitored_pair,
                position_pnl_snapshot=(
                    parsed.position_pnl_snapshot
                    if parsed.position_pnl_snapshot is not None
                    else existing.position_pnl_snapshot
                ),
                position_pnl_by_pair={
                    **existing.position_pnl_by_pair,
                    **parsed.position_pnl_by_pair,
                },
                barrier_closes=merged_barriers,
            )

    return enriched
