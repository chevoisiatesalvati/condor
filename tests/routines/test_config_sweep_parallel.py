"""Parallel sweep runner parity and snapshot cache tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from routines.macdbb_scanner_aggressive_hl_replay.config_sweep import (
    SweepResult,
    SweepRunContext,
    run_sweep_config_batch,
)
from routines.macdbb_scanner_aggressive_hl_replay.hydrated_ticks_cache import (
    hydrated_ticks_cache_key,
    load_hydrated_timeline_ticks,
    save_hydrated_timeline_ticks,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig, TickMeta
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import configure_replay_data_sources
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import load_timeline_strategy_params
from routines.macdbb_scanner_aggressive_hl_replay.snapshot_store import (
    warm_snapshot_caches,
    load_macdbb_index,
    _ensure_parsed_macdbb_cache,
)


def _fake_sweep_result(name: str, pnl: float) -> SweepResult:
    return SweepResult(
        name=name,
        pnl=pnl,
        trades=10,
        formal=1,
        adaptive=0,
        win_rate=0.5,
        capital_normalized_pnl=pnl,
    )


def test_run_sweep_config_batch_parallel_matches_sequential(monkeypatch):
    config_items = [
        ("cfg_a", {"sl_pct": 3.0}),
        ("cfg_b", {"sl_pct": 3.5}),
        ("cfg_c", {"sl_pct": 4.0}),
    ]
    ctx = SweepRunContext(
        dynamic_mode="both_on",
        parsed_sessions={0: {1: object()}},
        hl_caches_by_session={},
        hl_candle_cache={},
        hl_barrier_candle_cache={},
        hl_vol_candle_cache={},
        reports_by_pair={},
        parent_overrides=None,
        benchmark_avg_notional=500.0,
    )

    def fake_run(name, overrides, dynamic_mode, *args, **kwargs):
        return _fake_sweep_result(name, float(overrides["sl_pct"]) * 100.0)

    monkeypatch.setattr(
        "routines.macdbb_scanner_aggressive_hl_replay.config_sweep._run_dynamic_config",
        fake_run,
    )

    sequential = run_sweep_config_batch(config_items, ctx, workers=1)
    parallel = run_sweep_config_batch(config_items, ctx, workers=2)

    by_name_seq = {row.name: row for row in sequential}
    by_name_par = {row.name: row for row in parallel}
    assert set(by_name_seq) == set(by_name_par)
    for name in by_name_seq:
        assert by_name_seq[name].pnl == by_name_par[name].pnl
        assert by_name_seq[name].capital_normalized_pnl == by_name_par[name].capital_normalized_pnl


def test_warm_snapshot_caches_builds_macdbb_index_once(tmp_path, monkeypatch):
    import pandas as pd

    from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

    rows = [
        {
            "tick_ts_iso": "2025-07-07T00:00:00+00:00",
            "tick_id": "20250707_000000",
            "pair": "BTC-USDT",
            "interval": "1h",
            "signal": "NEUTRAL",
            "price": 100.0,
            "bb_pos_pct": 50.0,
            "bb_mid": 99.0,
            "bb_upper": 101.0,
            "macd": 0.1,
            "signal_line": 0.0,
            "histogram": 0.1,
            "trend": "bullish",
            "momentum": "positive",
            "bullish_cross": False,
            "price_le_mid": False,
            "bearish_cross": False,
            "price_ge_upper": False,
            "macd_lt_zero": False,
        }
    ]
    pd.DataFrame(rows).to_parquet(tmp_path / "macdbb.parquet", index=False)
    monkeypatch.setattr(snapshot_store, "DEFAULT_SNAPSHOT_DIR", tmp_path)
    snapshot_store.configure_snapshot_dir(tmp_path)

    warm_snapshot_caches(tmp_path)
    index = load_macdbb_index(snapshot_dir=tmp_path)
    cache = _ensure_parsed_macdbb_cache(tmp_path.resolve())

    assert len(index) == 1
    assert len(cache) == 1
    assert index[0].report_id in cache


def test_hydrated_ticks_disk_cache_roundtrip(tmp_path, monkeypatch):
    from routines.macdbb_scanner_aggressive_hl_replay import snapshot_store

    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(snapshot_store, "DEFAULT_SNAPSHOT_DIR", tmp_path)
    snapshot_store.configure_snapshot_dir(tmp_path)

    config = DynamicStrategyReplayConfig(
        preset="custom",
        data_source="snapshots",
        snapshot_dir=str(tmp_path),
        replay_mode="timeline_backtest",
        range_start_utc="2025-07-07T00:00:00Z",
        range_end_utc="2025-07-07T01:00:00Z",
        frequency_sec=1800,
    )
    params = load_timeline_strategy_params(config)
    tick_map = {
        1: TickMeta(tick=1, timestamp=__import__("datetime").datetime(2025, 7, 7, tzinfo=__import__("datetime").timezone.utc), macd_pairs=["BTC-USDT"]),
    }

    assert load_hydrated_timeline_ticks(config, params) is None
    save_hydrated_timeline_ticks(config, params, tick_map)
    loaded = load_hydrated_timeline_ticks(config, params)
    assert loaded is not None
    assert loaded[1].macd_pairs == ["BTC-USDT"]
    cache_path = tmp_path / f"hydrated_ticks_{hydrated_ticks_cache_key(config, params)}.pkl"
    assert cache_path.is_file()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_v5_winner_preset_pnl_unchanged_after_optimizations():
    snapshot_dir = Path("data/replay_snapshots_binance_1y")
    if not (snapshot_dir / "macdbb.parquet").is_file():
        pytest.skip("binance_1y snapshot not available")

    from routines.macdbb_scanner_aggressive_hl_replay.dynamic_policy import DynamicReplayPolicy
    from routines.macdbb_scanner_aggressive_hl_replay.hl_prices import (
        hl_prefetch_settings_from_config,
        prefetch_replay_hl_prices,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.presets import resolve_config_with_preset
    from routines.macdbb_scanner_aggressive_hl_replay.replay_loader import load_replay_sessions
    from routines.macdbb_scanner_aggressive_hl_replay.reports import build_reports_by_pair, load_reports_index
    from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session

    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset="hl_dynamic_timeline_refine_v5_winner_binance_1y")
    )
    configure_replay_data_sources(config)
    sessions, _, _ = load_replay_sessions(config)
    reports_by_pair = build_reports_by_pair(load_reports_index())
    prefetch = await prefetch_replay_hl_prices(
        sessions,
        settings=hl_prefetch_settings_from_config(config),
    )
    hl_caches, hl_candle, hl_barrier, hl_vol = prefetch
    _, _, trades, summary = simulate_strategy_session(
        session_num=0,
        tick_meta_map=sessions[0],
        reports_by_pair=reports_by_pair,
        config=config,
        hl_price_cache=hl_caches.get(0),
        hl_candle_cache=hl_candle,
        hl_barrier_candle_cache=hl_barrier,
        hl_vol_candle_cache=hl_vol,
        replay_policy=DynamicReplayPolicy(config),
    )
    assert summary.get("status") == "ok"
    assert len(trades) == 3037
    assert summary.get("net_pnl_quote") == pytest.approx(8036.38, rel=0, abs=0.01)
