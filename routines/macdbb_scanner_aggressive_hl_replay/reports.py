from __future__ import annotations

import bisect
import datetime as dt
import html
import json
import re
from dataclasses import dataclass

from routines.macdbb_scanner_aggressive_hl_replay.models import ParsedReport, ReportMeta
from routines.macdbb_scanner_aggressive_hl_replay.paths import REPORTS_DIR, REPORTS_INDEX_PATH

_SCANNER_TITLE_RE = re.compile(
    r"Hyperliquid Market Scanner\s*\((\d+)h",
    re.IGNORECASE,
)
_ANALYZED_RE = re.compile(
    r"Analyzed\s+(\d+)\s+Hyperliquid\s+pairs",
    re.IGNORECASE,
)
_SCANNER_TABLE_HEADERS = (
    "Pair",
    "Volume 24h",
    "24h Chg",
    "NATR",
    "NATR-CV",
    "Vol-CV",
    "Range",
)

_PAIR_TITLE_RE = re.compile(r"MACD\+BB:\s+([A-Z0-9:-]+)\s+\((1h|4h)\)")
_TABLE_SECTION_RE = re.compile(
    r'<div class="section section-table"><table><thead><tr>(?P<headers>.*?)</tr></thead>'
    r"<tbody>(?P<body>.*?)</tbody></table></div>",
    re.DOTALL,
)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_COND_ROW_RE = re.compile(
    r"<tr><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(True|False)</td></tr>",
    re.DOTALL,
)

_SIGNAL_TABLE_HEADERS = (
    "Pair",
    "Interval",
    "Signal",
    "Price",
    "BB Pos %",
    "BB Mid",
    "BB Upper",
    "MACD",
    "Signal Line",
    "Histogram",
    "Trend",
    "Momentum",
)
_CONDITION_TABLE_HEADERS = ("Rule", "Condition", "Met")
_PAIR_VALUE_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


@dataclass
class ScannerPairRow:
    pair: str
    volume_24h_usd: float
    price_change_24h: float
    natr_mean: float
    natr_cv: float
    bucket_cv: float
    price_range_pct: float = 0.0


@dataclass
class ScannerReportMeta:
    report_id: str
    filename: str
    created_at: dt.datetime
    lookback_hours: int | None = None
    total_analyzed: int | None = None


@dataclass
class ParsedScannerReport:
    total_analyzed: int
    mature: list[ScannerPairRow]
    degen: list[ScannerPairRow]
    lookback_hours: int | None = None


def parse_volume_usd(text: str) -> float:
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return 0.0
    if cleaned.endswith("B"):
        return float(cleaned[:-1]) * 1_000_000_000
    if cleaned.endswith("M"):
        return float(cleaned[:-1]) * 1_000_000
    if cleaned.endswith("K"):
        return float(cleaned[:-1]) * 1_000
    return float(cleaned)


def _parse_pct_change(text: str) -> float:
    cleaned = text.strip().replace("%", "").replace("+", "")
    return float(cleaned)


def _parse_pct_value(text: str) -> float:
    return float(text.strip().replace("%", ""))


def _parse_scanner_table_body(body: str) -> list[ScannerPairRow]:
    rows: list[ScannerPairRow] = []
    for row_match in re.finditer(r"<tr>(.*?)</tr>", body, re.DOTALL):
        values = [extract_td_value(value) for value in _TD_RE.findall(row_match.group(1))]
        if len(values) < 7:
            continue
        pair = values[0]
        if not _PAIR_VALUE_RE.match(pair):
            continue
        try:
            rows.append(
                ScannerPairRow(
                    pair=pair,
                    volume_24h_usd=parse_volume_usd(values[1]),
                    price_change_24h=_parse_pct_change(values[2]),
                    natr_mean=_parse_pct_value(values[3]),
                    natr_cv=float(values[4]),
                    bucket_cv=float(values[5]),
                    price_range_pct=_parse_pct_value(values[6]),
                )
            )
        except ValueError:
            continue
    return rows


def _parse_scanner_table_rows(report_html: str) -> list[ScannerPairRow]:
    rows: list[ScannerPairRow] = []
    for match in _TABLE_SECTION_RE.finditer(report_html):
        headers = _table_headers(match.group("headers"))
        if headers != list(_SCANNER_TABLE_HEADERS):
            continue
        rows.extend(_parse_scanner_table_body(match.group("body")))
    return rows


def parse_scanner_report_html(report_html: str) -> ParsedScannerReport | None:
    analyzed_match = _ANALYZED_RE.search(report_html)
    title_match = _SCANNER_TITLE_RE.search(report_html)
    if not analyzed_match:
        return None

    mature: list[ScannerPairRow] = []
    degen: list[ScannerPairRow] = []
    mature_marker = report_html.lower().find("mature markets")
    degen_marker = report_html.lower().find("degen markets")

    for match in _TABLE_SECTION_RE.finditer(report_html):
        headers = _table_headers(match.group("headers"))
        if headers != list(_SCANNER_TABLE_HEADERS):
            continue
        pos = match.start()
        if degen_marker >= 0 and pos > degen_marker:
            degen.extend(_parse_scanner_table_body(match.group("body")))
        elif mature_marker >= 0 and pos > mature_marker:
            mature.extend(_parse_scanner_table_body(match.group("body")))

    if not mature and not degen:
        all_rows = _parse_scanner_table_rows(report_html)
        if not all_rows:
            return None
        midpoint = max(1, len(all_rows) // 2)
        mature = all_rows[:midpoint]
        degen = all_rows[midpoint:]

    return ParsedScannerReport(
        total_analyzed=int(analyzed_match.group(1)),
        mature=mature,
        degen=degen,
        lookback_hours=int(title_match.group(1)) if title_match else None,
    )


def parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


def extract_td_value(raw_value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw_value)).strip()


def _table_headers(raw_headers: str) -> list[str]:
    return [extract_td_value(header) for header in _TH_RE.findall(raw_headers)]


def _first_table_row_values(raw_body: str) -> list[str] | None:
    first_row_match = re.search(r"<tr>(.*?)</tr>", raw_body, re.DOTALL)
    if not first_row_match:
        return None
    return [extract_td_value(value) for value in _TD_RE.findall(first_row_match.group(1))]


def _looks_like_signal_row(values: list[str]) -> bool:
    if len(values) < 12:
        return False
    pair, interval, signal = values[0], values[1], values[2]
    if not _PAIR_VALUE_RE.match(pair):
        return False
    if interval not in ("1h", "4h"):
        return False
    if signal not in ("LONG", "SHORT", "NEUTRAL"):
        return False
    return True


def _parse_condition_map(report_html: str) -> dict[tuple[str, str], bool]:
    """Parse Rule/Condition/Met table without matching across other tables."""
    condition_map: dict[tuple[str, str], bool] = {}
    for match in _TABLE_SECTION_RE.finditer(report_html):
        headers = _table_headers(match.group("headers"))
        if headers != list(_CONDITION_TABLE_HEADERS):
            continue
        for row_match in re.finditer(r"<tr>(.*?)</tr>", match.group("body"), re.DOTALL):
            values = [extract_td_value(value) for value in _TD_RE.findall(row_match.group(1))]
            if len(values) < 3:
                continue
            rule, condition, met = values[0], values[1], values[2]
            condition_map[(rule, condition)] = met.strip().lower() == "true"
    return condition_map


def _find_signal_row(report_html: str) -> list[str] | None:
    for match in _TABLE_SECTION_RE.finditer(report_html):
        headers = _table_headers(match.group("headers"))
        if headers != list(_SIGNAL_TABLE_HEADERS):
            continue
        values = _first_table_row_values(match.group("body"))
        if values and _looks_like_signal_row(values):
            return values
    return None


def parse_report_html(report_html: str) -> ParsedReport | None:
    values = _find_signal_row(report_html)
    if not values or len(values) < 12:
        return None

    pair = values[0]
    interval = values[1]
    signal = values[2]
    try:
        price = float(values[3])
        bb_pos_pct = float(values[4])
        bb_mid = float(values[5])
        bb_upper = float(values[6])
        macd = float(values[7])
        signal_line = float(values[8])
        histogram = float(values[9])
    except ValueError:
        return None
    trend = values[10].lower()
    momentum = values[11].lower()

    condition_map = _parse_condition_map(report_html)

    return ParsedReport(
        pair=pair,
        interval=interval,
        signal=signal,
        price=price,
        bb_pos_pct=bb_pos_pct,
        bb_mid=bb_mid,
        bb_upper=bb_upper,
        macd=macd,
        signal_line=signal_line,
        histogram=histogram,
        trend=trend,
        momentum=momentum,
        bullish_cross=condition_map.get(("LONG (2/2)", "Bullish crossover"), False),
        price_le_mid=condition_map.get(("LONG (2/2)", "Price <= midBB"), False),
        bearish_cross=condition_map.get(("SHORT (3/3)", "Bearish crossover"), False),
        price_ge_upper=condition_map.get(("SHORT (3/3)", "Price >= upperBB"), False),
        macd_lt_zero=condition_map.get(("SHORT (3/3)", "MACD < 0"), False),
    )


def load_reports_index() -> list[ReportMeta]:
    from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

    if snapshot_store.is_snapshot_store_active():
        return snapshot_store.load_macdbb_index()
    if not REPORTS_INDEX_PATH.exists():
        return []
    raw_entries = json.loads(REPORTS_INDEX_PATH.read_text(encoding="utf-8"))
    reports: list[ReportMeta] = []
    for entry in raw_entries:
        if entry.get("source_name") != "macd_bb_analysis":
            continue
        title_match = _PAIR_TITLE_RE.search(entry.get("title", ""))
        if not title_match:
            continue
        reports.append(
            ReportMeta(
                report_id=entry["id"],
                filename=entry["filename"],
                created_at=parse_dt(entry["created_at"]),
                pair=title_match.group(1),
                interval=title_match.group(2),
            )
        )
    return reports


def build_reports_by_pair(reports: list[ReportMeta]) -> dict[str, list[ReportMeta]]:
    by_pair: dict[str, list[ReportMeta]] = {}
    for report in reports:
        by_pair.setdefault(report.pair, []).append(report)
    for pair in by_pair:
        by_pair[pair].sort(key=lambda item: item.created_at)
    return by_pair


_PAIR_INTERVAL_INDEX: dict[int, dict[tuple[str, str], list[ReportMeta]]] = {}


def _reports_by_pair_interval(
    reports_by_pair: dict[str, list[ReportMeta]],
) -> dict[tuple[str, str], list[ReportMeta]]:
    cache_key = id(reports_by_pair)
    cached = _PAIR_INTERVAL_INDEX.get(cache_key)
    if cached is not None:
        return cached
    by_key: dict[tuple[str, str], list[ReportMeta]] = {}
    for pair, pair_reports in reports_by_pair.items():
        for report in pair_reports:
            by_key.setdefault((pair, report.interval), []).append(report)
    for reports in by_key.values():
        reports.sort(key=lambda item: item.created_at)
    _PAIR_INTERVAL_INDEX[cache_key] = by_key
    return by_key


def nearest_report(
    reports_by_pair: dict[str, list[ReportMeta]],
    pair: str,
    tick_time: dt.datetime,
    max_window_minutes: int,
    interval: str = "1h",
) -> ReportMeta | None:
    candidates = _reports_by_pair_interval(reports_by_pair).get((pair, interval), [])
    if not candidates:
        return None
    max_delta = dt.timedelta(minutes=max_window_minutes)
    times = [report.created_at for report in candidates]
    position = bisect.bisect_left(times, tick_time)
    nearest: tuple[dt.timedelta, ReportMeta] | None = None
    for index in (position - 1, position):
        if index < 0 or index >= len(candidates):
            continue
        candidate = candidates[index]
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def load_parsed_report(report_meta: ReportMeta) -> ParsedReport | None:
    if report_meta.filename.startswith("snapshot://"):
        from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

        return snapshot_store.load_parsed_macdbb_snapshot(report_meta)
    report_path = REPORTS_DIR / report_meta.filename
    if not report_path.exists():
        return None
    try:
        return parse_report_html(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_scanner_reports_index() -> list[ScannerReportMeta]:
    from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

    if snapshot_store.is_snapshot_store_active():
        return snapshot_store.load_scanner_index()
    if not REPORTS_INDEX_PATH.exists():
        return []
    raw_entries = json.loads(REPORTS_INDEX_PATH.read_text(encoding="utf-8"))
    reports: list[ScannerReportMeta] = []
    for entry in raw_entries:
        if entry.get("source_name") != "hyperliquid_market_scanner":
            continue
        title = entry.get("title", "")
        lookback: int | None = None
        title_match = _SCANNER_TITLE_RE.search(title)
        if title_match:
            lookback = int(title_match.group(1))
        reports.append(
            ScannerReportMeta(
                report_id=entry["id"],
                filename=entry["filename"],
                created_at=parse_dt(entry["created_at"]),
                lookback_hours=lookback,
            )
        )
    reports.sort(key=lambda item: item.created_at)
    return reports


def nearest_scanner_report(
    scanner_reports: list[ScannerReportMeta],
    tick_time: dt.datetime,
    max_window_minutes: int,
) -> ScannerReportMeta | None:
    if not scanner_reports:
        return None
    max_delta = dt.timedelta(minutes=max_window_minutes)
    times = [report.created_at for report in scanner_reports]
    position = bisect.bisect_left(times, tick_time)
    nearest: tuple[dt.timedelta, ScannerReportMeta] | None = None
    for index in (position - 1, position):
        if index < 0 or index >= len(scanner_reports):
            continue
        candidate = scanner_reports[index]
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def load_parsed_scanner_report(
    report_meta: ScannerReportMeta,
) -> ParsedScannerReport | None:
    if report_meta.filename.startswith("snapshot://"):
        from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

        return snapshot_store.load_parsed_scanner_snapshot(report_meta)
    report_path = REPORTS_DIR / report_meta.filename
    if not report_path.exists():
        return None
    try:
        return parse_scanner_report_html(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
