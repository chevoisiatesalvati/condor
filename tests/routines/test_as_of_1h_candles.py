"""As-of 1h series: completed hours + 1m forming bar (no hour look-ahead)."""

from routines.lib.as_of_1h_candles import (
    HOUR_MS,
    as_of_1h_candles,
    completed_1h_bars,
    forming_1h_from_1m,
)


def _bar(ts_ms: int, close: float, high: float | None = None, low: float | None = None) -> dict:
    return {
        "timestamp_ms": float(ts_ms),
        "open": close - 1.0,
        "high": close + 1.0 if high is None else high,
        "low": close - 2.0 if low is None else low,
        "close": close,
        "volume": 10.0,
    }


def test_completed_1h_drops_open_hour():
    hour = 1_000_000_000_000  # aligned-ish
    hour = (hour // HOUR_MS) * HOUR_MS
    candles = [_bar(hour - HOUR_MS, 100.0), _bar(hour, 999.0)]
    as_of = hour + 43 * 60_000
    done = completed_1h_bars(candles, as_of)
    assert [c["close"] for c in done] == [100.0]


def test_forming_1h_uses_1m_close_not_hour_close():
    hour = (1_000_000_000_000 // HOUR_MS) * HOUR_MS
    as_of = hour + 43 * 60_000
    minutes = [
        _bar(hour, 110.0, high=111.0, low=109.0),
        _bar(hour + 42 * 60_000, 120.0, high=121.0, low=108.0),
    ]
    forming = forming_1h_from_1m(minutes, as_of)
    assert forming is not None
    assert forming["timestamp_ms"] == float(hour)
    assert forming["close"] == 120.0
    assert forming["high"] == 121.0
    assert forming["low"] == 108.0


def test_as_of_1h_appends_forming_and_caps_lookback():
    hour = (1_000_000_000_000 // HOUR_MS) * HOUR_MS
    as_of = hour + 10 * 60_000
    h1 = [_bar(hour - i * HOUR_MS, float(i)) for i in range(5, 0, -1)]
    h1.append(_bar(hour, 999.0))
    m1 = [_bar(hour, 50.0), _bar(hour + 9 * 60_000, 55.0)]
    series = as_of_1h_candles(h1, m1, as_of, max_records=3)
    assert len(series) == 3
    assert series[-1]["close"] == 55.0
    assert 999.0 not in [c["close"] for c in series]
