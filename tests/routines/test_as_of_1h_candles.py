"""As-of 1h series: completed hours + 1m forming bar (no hour look-ahead)."""

from routines.lib.as_of_1h_candles import (
    HOUR_MS,
    OhlcvArrays,
    as_of_1h_candles,
    as_of_1h_from_arrays,
    completed_1h_bars,
    forming_1h_from_1m,
    forming_1h_from_1m_arrays,
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


def test_as_of_from_arrays_matches_list_path():
    hour = (1_700_000_000_000 // HOUR_MS) * HOUR_MS
    h1 = [_bar(hour - i * HOUR_MS, 100.0 + i) for i in range(8, 0, -1)]
    h1.append(_bar(hour, 999.0))
    m1 = [
        _bar(hour + minute * 60_000, 200.0 + minute, high=210.0 + minute, low=190.0)
        for minute in range(0, 50, 5)
    ]
    as_of_times = [hour + 1 * 60_000, hour + 12 * 60_000, hour + 47 * 60_000]
    h1_arr = OhlcvArrays.from_candles(h1)
    m1_arr = OhlcvArrays.from_candles(m1)
    for as_of in as_of_times:
        list_series = as_of_1h_candles(h1, m1, as_of, max_records=4)
        array_series = as_of_1h_from_arrays(h1_arr, m1_arr, as_of, max_records=4)
        assert [round(c["close"], 10) for c in array_series] == [
            round(c["close"], 10) for c in list_series
        ]
        assert [int(c["timestamp_ms"]) for c in array_series] == [
            int(c["timestamp_ms"]) for c in list_series
        ]
        list_forming = forming_1h_from_1m(m1, as_of)
        array_forming = forming_1h_from_1m_arrays(m1_arr, as_of)
        assert array_forming is not None and list_forming is not None
        assert array_forming["close"] == list_forming["close"]
        assert array_forming["high"] == list_forming["high"]
        assert array_forming["low"] == list_forming["low"]
        assert array_forming["volume"] == list_forming["volume"]
