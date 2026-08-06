"""Tick audit log TTL + write/read."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from condor.strategy_runners.macdbb import tick_log


def test_write_and_list_ticks(tmp_path, monkeypatch):
    monkeypatch.setattr(tick_log, "ticks_dir", lambda slug="x": tmp_path / "ticks")
    tick_log._last_cleanup_at.clear()

    path = tick_log.write_tick_log(
        slug="macdbb_scanner_aggressive_hl",
        session_num=3,
        tick_number=1,
        payload={
            "tradeable_count": 4,
            "signal_count": 2,
            "decide": {"hold_reason": "no_signal", "creates": 0, "stops": 0},
            "apply": {"ok": True, "error": "", "created_ids": [], "stopped_ids": []},
            "summary": "hold",
        },
        config={"tick_log_enabled": True, "tick_log_retention_days": 7},
    )
    assert path is not None
    assert path.is_file()

    rows = tick_log.list_recent_ticks("macdbb_scanner_aggressive_hl", session=3, limit=10)
    assert len(rows) == 1
    assert rows[0]["tick"] == 1
    assert rows[0]["tradeable_count"] == 4


def test_cleanup_removes_old_days(tmp_path, monkeypatch):
    root = tmp_path / "ticks"
    monkeypatch.setattr(tick_log, "ticks_dir", lambda slug="x": root)
    tick_log._last_cleanup_at.clear()

    old_day = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y%m%d")
    new_day = datetime.now(timezone.utc).strftime("%Y%m%d")
    old_dir = root / old_day
    new_dir = root / new_day
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "session_1_ticks.jsonl").write_text('{"tick":1}\n', encoding="utf-8")
    (new_dir / "session_1_ticks.jsonl").write_text('{"tick":2}\n', encoding="utf-8")

    removed = tick_log.maybe_cleanup(
        "macdbb_scanner_aggressive_hl",
        config={"tick_log_retention_days": 7},
        force=True,
    )
    assert removed >= 1
    assert not old_dir.exists()
    assert (new_dir / "session_1_ticks.jsonl").is_file()


def test_disabled_skips_write(tmp_path, monkeypatch):
    monkeypatch.setattr(tick_log, "ticks_dir", lambda slug="x": tmp_path / "ticks")
    path = tick_log.write_tick_log(
        slug="macdbb_scanner_aggressive_hl",
        session_num=1,
        tick_number=1,
        payload={"summary": "x"},
        config={"tick_log_enabled": False},
    )
    assert path is None
