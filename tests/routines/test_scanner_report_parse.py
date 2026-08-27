"""Tests for scanner report HTML parsing."""

from __future__ import annotations

from routines.macdbb_scanner_aggressive_hl_replay.reports import parse_scanner_report_html, parse_volume_usd


SAMPLE_SCANNER_HTML = """
<div class="section-md"><p>Analyzed 30 Hyperliquid pairs with 6h lookback on 1m candles</p></div>
<h3>Mature Markets</h3>
<div class="section section-table"><table><thead><tr>
<th>Pair</th><th>Volume 24h</th><th>24h Chg</th><th>NATR</th><th>NATR-CV</th><th>Vol-CV</th><th>Range</th>
</tr></thead><tbody>
<tr><td>BTC-USD</td><td>$2.1B</td><td>+1.2%</td><td>0.320%</td><td>0.15</td><td>0.20</td><td>1.5%</td></tr>
<tr><td>ETH-USD</td><td>$900.0M</td><td>-0.5%</td><td>0.410%</td><td>0.18</td><td>0.22</td><td>2.0%</td></tr>
</tbody></table></div>
<h3>Degen Markets</h3>
<div class="section section-table"><table><thead><tr>
<th>Pair</th><th>Volume 24h</th><th>24h Chg</th><th>NATR</th><th>NATR-CV</th><th>Vol-CV</th><th>Range</th>
</tr></thead><tbody>
<tr><td>PUMP-USD</td><td>$50.0M</td><td>+12.0%</td><td>1.250%</td><td>0.55</td><td>0.80</td><td>8.0%</td></tr>
</tbody></table></div>
"""


def test_parse_volume_usd():
    assert parse_volume_usd("$2.1B") == 2_100_000_000
    assert parse_volume_usd("$50.0M") == 50_000_000


def test_parse_scanner_report_html_fixture():
    parsed = parse_scanner_report_html(SAMPLE_SCANNER_HTML)
    assert parsed is not None
    assert parsed.total_analyzed == 30
    assert len(parsed.mature) == 2
    assert len(parsed.degen) == 1
    assert parsed.mature[0].pair == "BTC-USD"
    assert parsed.degen[0].natr_mean == 1.25


def test_raw_index_entries_read_per_source_catalog(tmp_path, monkeypatch):
    import json

    from routines.macdbb_scanner_aggressive_hl_replay import reports as replay_reports

    monkeypatch.setattr(replay_reports, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        replay_reports, "REPORTS_INDEX_PATH", tmp_path / "reports_index.json"
    )
    source_dir = tmp_path / "hyperliquid_market_scanner"
    source_dir.mkdir()
    (source_dir / "reports_index.json").write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "title": "Hyperliquid Market Scanner (6h)",
                    "filename": "hyperliquid_market_scanner/x.html",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "source_name": "hyperliquid_market_scanner",
                }
            ]
        ),
        encoding="utf-8",
    )
    loaded = replay_reports._raw_index_entries_for_source(
        "hyperliquid_market_scanner"
    )
    assert [entry["id"] for entry in loaded] == ["abc123"]
