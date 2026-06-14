"""Tests for report sentiment parsing and section ordering."""

from condor.reports import ReportBuilder, _parse_signed_number, _sentiment_class


def test_sentiment_class_currency_positive():
    assert _sentiment_class("$+189.90") == "positive"


def test_sentiment_class_currency_negative():
    assert _sentiment_class("$-12.50") == "negative"


def test_sentiment_class_numeric():
    assert _sentiment_class(38) == "positive"
    assert _sentiment_class(-5.2) == "negative"
    assert _sentiment_class(0) == ""


def test_parse_signed_number_variants():
    assert _parse_signed_number("$+189.90") == 189.90
    assert _parse_signed_number("-12.5%") == -12.5
    assert _parse_signed_number("+3.2") == 3.2


def test_render_table_skips_session_column_sentiment():
    html = ReportBuilder._render_table(
        ["Session", "Sim PnL $"],
        [{"Session": 38, "Sim PnL $": 189.90}],
    )
    assert 'class="positive">38<' not in html
    assert 'class="positive">189.9' in html


def test_params_renders_collapsible_panel():
    builder = ReportBuilder("Test")
    builder.manual_order()
    builder.params({"preset": "custom", "sl_pct": 1.5})

    html = builder._render_sections()
    assert 'class="section params-panel"' in html
    assert "Run Parameters" in html
    assert "preset" in html
    assert "sl_pct" in html
    assert "<h3" not in html
    assert "params-grid" in html


def test_params_panel_can_start_open():
    html = ReportBuilder._render_params(
        {
            "title": "Run Parameters",
            "config": {"preset": "custom"},
            "open": True,
        }
    )
    assert 'class="section params-panel" open' in html
