"""Hydrate replay TickMeta from DeterministicRunner tick JSONL logs."""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from condor.strategy_runners.macdbb.paths import MACDBB_SLUG, ticks_dir
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    BarrierCloseEvent,
    JournalSignal1h,
    TickMeta,
)

logger = logging.getLogger(__name__)


def _parse_ts(raw: Any) -> dt.datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _signal_from_json(raw: dict[str, Any]) -> JournalSignal1h | None:
    pair = str(raw.get("pair") or "").strip()
    if not pair:
        return None
    try:
        bb_pos = float(raw.get("bb_pos_pct"))
        macd = float(raw.get("macd"))
        signal_line = float(raw.get("signal_line"))
        histogram = float(raw.get("histogram"))
    except (TypeError, ValueError):
        return None
    price_raw = raw.get("price")
    try:
        price = float(price_raw) if price_raw is not None else None
    except (TypeError, ValueError):
        price = None
    # Live tick logs omit formal/adaptive flags and BB bands; shared decide
    # recomputes those via candle monitor when reports are absent.
    return JournalSignal1h(
        pair=pair,
        bb_pos_pct=bb_pos,
        macd=macd,
        signal_line=signal_line,
        histogram=histogram,
        macd_gap_ratio=0.0,
        hist_ratio=0.0,
        trend=str(raw.get("trend") or "neutral"),
        momentum=str(raw.get("momentum") or "flat"),
        formal_long=bool(raw.get("formal_long")),
        formal_short=bool(raw.get("formal_short")),
        adaptive_long=bool(raw.get("adaptive_long")),
        adaptive_short=bool(raw.get("adaptive_short")),
        strength_long=float(raw.get("strength_long") or 0.0),
        strength_short=float(raw.get("strength_short") or 0.0),
        bb_mid=_optional_float(raw.get("bb_mid")),
        bb_upper=_optional_float(raw.get("bb_upper")),
        bb_lower=_optional_float(raw.get("bb_lower")),
        bullish_cross=raw.get("bullish_cross") if "bullish_cross" in raw else None,
        bearish_cross=raw.get("bearish_cross") if "bearish_cross" in raw else None,
        price=price,
    )


def _optional_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _barrier_events(raw_list: Any) -> list[BarrierCloseEvent]:
    events: list[BarrierCloseEvent] = []
    if not isinstance(raw_list, list):
        return events
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        pair = str(item.get("pair") or "").strip()
        close_type = str(item.get("close_type") or item.get("type") or "").strip()
        if not pair or not close_type:
            continue
        pnl = item.get("pnl_quote")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl_f = None
        events.append(
            BarrierCloseEvent(pair=pair, close_type=close_type, pnl_quote=pnl_f)
        )
    return events


def iter_session_tick_jsonl_paths(
    session_num: int,
    *,
    strategy_slug: str = MACDBB_SLUG,
    ticks_root: Path | None = None,
) -> list[Path]:
    root = ticks_root if ticks_root is not None else ticks_dir(strategy_slug)
    if not root.is_dir():
        return []
    paths: list[Path] = []
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir():
            continue
        candidate = day_dir / f"session_{session_num}_ticks.jsonl"
        if candidate.is_file():
            paths.append(candidate)
    return paths


def load_live_tick_records(
    session_num: int,
    *,
    strategy_slug: str = MACDBB_SLUG,
    ticks_root: Path | None = None,
) -> dict[int, dict[str, Any]]:
    """Return latest JSON record per tick number for a live session."""
    by_tick: dict[int, dict[str, Any]] = {}
    for path in iter_session_tick_jsonl_paths(
        session_num, strategy_slug=strategy_slug, ticks_root=ticks_root
    ):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                tick_num = int(obj.get("tick"))
            except (TypeError, ValueError):
                continue
            by_tick[tick_num] = obj
    return by_tick


def enrich_ticks_from_live_jsonl(
    tick_meta_map: dict[int, TickMeta],
    session_num: int,
    *,
    strategy_slug: str = MACDBB_SLUG,
    ticks_root: Path | None = None,
) -> dict[int, TickMeta]:
    """Merge DeterministicRunner tick JSONL into journal-derived TickMeta."""
    records = load_live_tick_records(
        session_num, strategy_slug=strategy_slug, ticks_root=ticks_root
    )
    if not records:
        return tick_meta_map

    enriched = dict(tick_meta_map)
    merged_count = 0
    for tick_num, record in records.items():
        signals_raw = record.get("signals") or []
        signals: dict[str, JournalSignal1h] = {}
        if isinstance(signals_raw, list):
            for raw in signals_raw:
                if not isinstance(raw, dict):
                    continue
                parsed = _signal_from_json(raw)
                if parsed is not None:
                    signals[parsed.pair] = parsed
        macd_pairs = list(signals.keys())
        queue_raw = record.get("queue_total") or record.get("queue") or []
        if isinstance(queue_raw, str):
            queue_total = [p.strip() for p in queue_raw.split(",") if p.strip()]
        elif isinstance(queue_raw, list):
            queue_total = [str(p).strip() for p in queue_raw if str(p).strip()]
        else:
            queue_total = []
        tradeable = record.get("tradeable_count")
        try:
            tradeable_count = int(tradeable) if tradeable is not None else None
        except (TypeError, ValueError):
            tradeable_count = None
        regime_raw = str(record.get("scanner_regime") or "").lower()
        scanner_regime = (
            regime_raw if regime_raw in {"mature", "degen"} else None
        )
        ts = _parse_ts(record.get("ts"))
        barriers = _barrier_events(record.get("barrier_closes"))
        decide = record.get("decide") if isinstance(record.get("decide"), dict) else {}
        apply = record.get("apply") if isinstance(record.get("apply"), dict) else {}
        created_ids = apply.get("created_ids") or []
        entry_class = record.get("entry_class") or decide.get("entry_class")
        if not entry_class and created_ids:
            entry_class = "regime_adaptive_half_size"
        if entry_class is not None:
            entry_class = str(entry_class)

        existing = enriched.get(tick_num)
        if existing is None:
            if ts is None:
                continue
            enriched[tick_num] = TickMeta(
                tick=tick_num,
                timestamp=ts,
                macd_pairs=macd_pairs,
                tradeable_count=tradeable_count,
                scanner_regime=scanner_regime,  # type: ignore[arg-type]
                queue_total=queue_total,
                signals_1h=signals,
                barrier_closes=barriers,
                entry_class=entry_class,
            )
            merged_count += 1
            continue

        enriched[tick_num] = replace(
            existing,
            timestamp=ts or existing.timestamp,
            macd_pairs=macd_pairs or existing.macd_pairs,
            tradeable_count=(
                tradeable_count
                if tradeable_count is not None
                else existing.tradeable_count
            ),
            scanner_regime=scanner_regime or existing.scanner_regime,
            queue_total=queue_total or existing.queue_total,
            signals_1h=signals or existing.signals_1h,
            barrier_closes=barriers or existing.barrier_closes,
            entry_class=entry_class or existing.entry_class,
        )
        merged_count += 1

    logger.info(
        "Live JSONL enrich session_%s: merged %d/%d tick records into %d metas",
        session_num,
        merged_count,
        len(records),
        len(enriched),
    )
    return enriched
