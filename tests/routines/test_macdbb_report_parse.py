"""Tests for MACD+BB saved report HTML parsing."""

from pathlib import Path

from routines.macdbb_replay.reports import load_parsed_report, parse_report_html
from routines.macdbb_replay.models import ReportMeta
import datetime as dt

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def test_parse_report_html_legacy_format():
    path = REPORTS_DIR / "20260611_142913_macdbb_hmstr_usd_1h_2c2a64.html"
    if not path.exists():
        return
    parsed = parse_report_html(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed.pair == "HMSTR-USD"
    assert parsed.interval == "1h"
    assert parsed.signal == "NEUTRAL"
    assert parsed.bb_pos_pct == 85.6


def test_parse_report_html_with_params_table_before_signal():
    path = REPORTS_DIR / "20260614_115332_macdbb_wld_usd_1h_3abbc0.html"
    if not path.exists():
        return
    parsed = parse_report_html(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed.pair == "WLD-USD"
    assert parsed.interval == "1h"
    assert parsed.signal == "NEUTRAL"
    assert parsed.price == 0.50265
    assert parsed.bb_pos_pct == 45.39
    assert parsed.price_le_mid is True


def test_load_parsed_report_from_meta():
    path = REPORTS_DIR / "20260614_115332_macdbb_wld_usd_1h_3abbc0.html"
    if not path.exists():
        return
    parsed = load_parsed_report(
        ReportMeta(
            report_id="3abbc0",
            filename=path.name,
            created_at=dt.datetime(2026, 6, 14, 11, 53, tzinfo=dt.timezone.utc),
            pair="WLD-USD",
            interval="1h",
        )
    )
    assert parsed is not None
    assert parsed.pair == "WLD-USD"


def test_parse_report_html_bullish_cross_from_conditions_table():
    path = REPORTS_DIR / "20260618_082153_macdbb_purr_usd_1h_2255a9.html"
    if not path.exists():
        return
    parsed = parse_report_html(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed.bullish_cross is True
    assert parsed.price_le_mid is True
    assert parsed.bearish_cross is False


def test_parse_report_html_rejects_params_table_rows():
    params_only = """
    <div class="section section-table"><table><thead><tr><th>Parameter</th><th>Value</th></tr></thead>
    <tbody><tr><td>bb_period</td><td>20</td></tr>
    <tr><td>connector_name</td><td>hyperliquid_perpetual</td></tr></tbody></table></div>
    """
    assert parse_report_html(params_only) is None
