"""Deterministic scanner queue builder (agent.md Steps 2 / 2b)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from routines.macdbb_scanner_aggressive_hl_replay.reports import ParsedScannerReport, ScannerPairRow


@dataclass(frozen=True)
class ScannerQueueResult:
    regime: Literal["mature", "degen"]
    natr_floor_used: float
    tradeable_pairs: list[str]
    queue_primary: list[str]
    queue_total: list[str]
    macd_pairs: list[str]
    tradeable_count: int
    scanner_analyzed: int
    natr_by_pair: dict[str, float]


def _param(params: dict[str, Any], key: str, default: Any) -> Any:
    value = params.get(key)
    return default if value is None else value


def _tradeable_pair_rows(parsed: ParsedScannerReport) -> list[ScannerPairRow]:
    seen: set[str] = set()
    rows: list[ScannerPairRow] = []
    for row in parsed.mature + parsed.degen:
        if row.pair in seen:
            continue
        seen.add(row.pair)
        rows.append(row)
    return rows


def _infer_regime(parsed: ParsedScannerReport) -> Literal["mature", "degen"]:
    """Volatile tape (higher degen NATR-CV) → degen-first; else mature-first."""
    mature_cvs = [row.natr_cv for row in parsed.mature if row.natr_cv >= 0]
    degen_cvs = [row.natr_cv for row in parsed.degen if row.natr_cv >= 0]
    mature_avg = sum(mature_cvs) / len(mature_cvs) if mature_cvs else 0.0
    degen_avg = sum(degen_cvs) / len(degen_cvs) if degen_cvs else 0.0
    if degen_avg > mature_avg:
        return "degen"
    return "mature"


def _ordered_tradeable(
    parsed: ParsedScannerReport,
    regime: Literal["mature", "degen"],
) -> list[ScannerPairRow]:
    """Preserve scanner table rank within each bucket; degen/mature-first per regime."""
    mature_pairs = {row.pair for row in parsed.mature}
    degen_pairs = {row.pair for row in parsed.degen}
    if regime == "degen":
        primary = list(parsed.degen) + [
            row for row in parsed.mature if row.pair not in degen_pairs
        ]
    else:
        primary = list(parsed.mature) + [
            row for row in parsed.degen if row.pair not in mature_pairs
        ]
    seen: set[str] = set()
    ordered: list[ScannerPairRow] = []
    for row in primary:
        if row.pair in seen:
            continue
        seen.add(row.pair)
        ordered.append(row)
    return ordered


def build_scanner_queue(
    parsed: ParsedScannerReport,
    strategy_params: dict[str, Any],
    *,
    open_pairs: list[str] | None = None,
) -> ScannerQueueResult:
    """Build MACD queue from archived scanner report tables + strategy params."""
    params = strategy_params or {}
    regime = _infer_regime(parsed)
    natr_floor = float(
        _param(
            params,
            "natr_floor_mature_pct" if regime == "mature" else "natr_floor_degen_pct",
            0.08 if regime == "mature" else 0.1,
        )
    )
    primary_size = int(_param(params, "macd_queue_primary_size", 8))
    pass2_min = int(_param(params, "macd_queue_pass2_min", 8))
    pass2_max = int(_param(params, "macd_queue_pass2_max", 12))
    total_cap = int(_param(params, "macd_queue_total_cap", 20))
    review_count = int(_param(params, "macd_primary_review_count", 5))
    min_tradeable = int(_param(params, "min_tradeable_for_adaptive", 1))
    min_analyzed = int(_param(params, "min_scanner_analyzed", 3))

    ordered = _ordered_tradeable(parsed, regime)

    filtered = [row for row in ordered if row.natr_mean >= natr_floor]
    tradeable_pairs = [row.pair for row in filtered]

    queue_primary = tradeable_pairs[:primary_size]
    queue_total = list(queue_primary)

    if len(queue_primary) < min_tradeable:
        remaining = [row for row in filtered if row.pair not in queue_primary]
        pass2_target = min(pass2_max, max(pass2_min, min_tradeable))
        for row in remaining:
            if len(queue_total) >= total_cap:
                break
            if len(queue_total) >= pass2_target and len(queue_total) >= min_tradeable:
                break
            queue_total.append(row.pair)

    open_legs = open_pairs or []
    for pair in open_legs:
        if pair and pair not in queue_total:
            queue_total.append(pair)

    macd_pairs = queue_total[:review_count]
    if len(macd_pairs) < len(queue_total) and len(queue_total) <= review_count:
        macd_pairs = list(queue_total)

    natr_by_pair = {row.pair: row.natr_mean for row in filtered}
    scanner_analyzed = parsed.total_analyzed
    if scanner_analyzed < min_analyzed:
        tradeable_count = len(tradeable_pairs)
    else:
        tradeable_count = len(tradeable_pairs)

    return ScannerQueueResult(
        regime=regime,
        natr_floor_used=natr_floor,
        tradeable_pairs=tradeable_pairs,
        queue_primary=queue_primary,
        queue_total=queue_total,
        macd_pairs=macd_pairs,
        tradeable_count=tradeable_count,
        scanner_analyzed=scanner_analyzed,
        natr_by_pair=natr_by_pair,
    )
