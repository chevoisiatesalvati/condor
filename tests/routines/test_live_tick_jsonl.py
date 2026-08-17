"""Live JSONL overlay must replace reconstructed scanner queues."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from routines.macdbb_scanner_aggressive_hl_replay.live_tick_jsonl import (
    enrich_ticks_from_live_jsonl,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import TickMeta


def test_enrich_replaces_snapshot_queue_with_live_signal_pairs(tmp_path: Path, monkeypatch):
    tick_time = dt.datetime(2026, 8, 15, 13, 42, tzinfo=dt.timezone.utc)
    existing = {
        8813: TickMeta(
            tick=8813,
            timestamp=tick_time,
            macd_pairs=["HYPE-USD", "XMR-USD"],
            queue_total=["HYPE-USD", "XMR-USD", "KAITO-USD"],
        )
    }
    day_dir = tmp_path / "20260815"
    day_dir.mkdir()
    record = {
        "tick": 8813,
        "ts": "2026-08-15T13:42:59.013903+00:00",
        "tradeable_count": 30,
        "signals": [
            {"pair": "DOGE-USD", "price": 0.2, "bb_pos_pct": 40, "macd": 0.1, "signal_line": 0.0, "histogram": 0.1, "trend": "bullish", "momentum": "rising"},
            {"pair": "AAVE-USD", "price": 200, "bb_pos_pct": 30, "macd": 0.2, "signal_line": 0.0, "histogram": 0.2, "trend": "bullish", "momentum": "rising"},
        ],
    }
    (day_dir / "session_2_ticks.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.live_tick_jsonl.iter_session_tick_jsonl_paths",
        lambda *args, **kwargs: [day_dir / "session_2_ticks.jsonl"],
    )
    enriched = enrich_ticks_from_live_jsonl(existing, 2, strategy_slug="macdbb_pullback_hl")
    meta = enriched[8813]
    assert meta.macd_pairs == ["DOGE-USD", "AAVE-USD"]
    assert meta.queue_total == ["DOGE-USD", "AAVE-USD"]
    assert "HYPE-USD" not in meta.queue_total
