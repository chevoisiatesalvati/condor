"""Tests for executor timestamp resolution."""

from __future__ import annotations

from condor.fetchers.executors import get_executor_close_timestamp, get_executor_timestamp


def test_timestamp_prefers_config():
    ex = {"config": {"timestamp": 1_700_000_000}, "timestamp": 0}
    assert get_executor_timestamp(ex) == 1_700_000_000


def test_timestamp_falls_back_to_created_at_iso():
    ex = {"created_at": "2024-06-15T12:30:00Z"}
    ts = get_executor_timestamp(ex)
    assert ts > 1_700_000_000


def test_timestamp_normalizes_milliseconds():
    ex = {"timestamp": 1_700_000_000_000}
    assert get_executor_timestamp(ex) == 1_700_000_000


def test_close_timestamp_falls_back_to_closed_at():
    ex = {"closed_at": "2024-06-15T13:00:00Z"}
    ts = get_executor_close_timestamp(ex)
    assert ts > 1_700_000_000
