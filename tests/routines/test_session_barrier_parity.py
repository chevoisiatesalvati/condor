"""Session parity after barrier exit fixes — sim PnL vs live journal."""

from __future__ import annotations

import asyncio

import pytest

from routines.macdbb_replay.dynamic_policy import DynamicReplayPolicy
from routines.macdbb_replay.replay_data import should_prefetch_replay_candles
from routines.macdbb_replay.hl_prices import hl_prefetch_settings_from_config, prefetch_replay_hl_prices
from routines.macdbb_replay.live_ledger import parse_journal_live_pnl
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR
from routines.macdbb_replay.replay_loader import load_replay_sessions
from routines.macdbb_replay.reports import build_reports_by_pair, load_reports_index
from routines.macdbb_replay.simulator import simulate_strategy_session

STRATEGY_SLUG = "macdbb_scanner_aggressive_hl"
# Sessions with stable journal parity (exclude manual-stop / session-end gaps).
PARITY_SESSIONS = (58, 59, 60, 61)
MAX_PNL_DELTA = 3.0


def test_timeline_snapshot_config_prefetches_barrier_candles():
    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_timeline_v5_staged_abc_winner_binance_1y",
        data_source="snapshots",
        replay_mode="timeline_backtest",
    )
    assert should_prefetch_replay_candles(config)


def test_hydrated_timeline_prefetch_discovers_pairs():
    from routines.macdbb_replay.hl_prices import _aggregate_pair_requests
    from routines.macdbb_replay.replay_data import configure_replay_data_sources
    from routines.macdbb_replay.snapshot_store import load_manifest
    from routines.macdbb_replay.timeline_sweep import merge_timeline_config
    from routines.macdbb_replay.presets import resolve_config_with_preset

    snapshot_dir = "data/replay_snapshots_binance_1y"
    from pathlib import Path

    from routines.macdbb_replay.snapshot_store import configure_snapshot_dir

    configure_snapshot_dir(Path(snapshot_dir))
    manifest = load_manifest(snapshot_dir=snapshot_dir) or {}
    overrides = merge_timeline_config(
        {
            "preset": "hl_dynamic_timeline_refine_v5_winner_binance_1y",
            "snapshot_dir": snapshot_dir,
            "replay_mode": "timeline_backtest",
            "data_source": "snapshots",
        },
        range_start_utc=manifest.get("range_start_utc"),
        range_end_utc=manifest.get("range_end_utc"),
    )
    config = resolve_config_with_preset(DynamicStrategyReplayConfig(**overrides))
    configure_replay_data_sources(config)
    tick_maps, _, selected = load_replay_sessions(config)
    if not selected:
        pytest.skip("timeline snapshot data unavailable")
    pair_requests = _aggregate_pair_requests({selected[0]: tick_maps[selected[0]]})
    assert len(pair_requests) > 0, "hydrated timeline should expose queue pairs for prefetch"


def _session_dir(session_num: int):
    return TRADING_AGENTS_DIR / STRATEGY_SLUG / f"sessions/session_{session_num}"


@pytest.mark.parametrize("session_num", PARITY_SESSIONS)
def test_session_parity_pnl_within_tolerance(session_num: int):
    session_dir = _session_dir(session_num)
    if not (session_dir / "journal.md").is_file():
        pytest.skip(f"session_{session_num} journal missing")

    config = DynamicStrategyReplayConfig(
        preset="hl_dynamic_session_parity",
        strategy_slug=STRATEGY_SLUG,
        session_nums=str(session_num),
        replay_mode="session_parity",
        data_source="reports_only",
        config_source="session",
        write_csv=False,
    )
    from routines.macdbb_replay.replay_data import configure_replay_data_sources

    configure_replay_data_sources(config)
    tick_maps, session_configs, selected = load_replay_sessions(config)
    assert session_num in selected

    reports_by_pair = build_reports_by_pair(load_reports_index())
    subset = {session_num: tick_maps[session_num]}
    hl_caches, hl_candle, hl_barrier, hl_vol = asyncio.run(
        prefetch_replay_hl_prices(
            subset,
            settings=hl_prefetch_settings_from_config(config),
        )
    )

    session_config = session_configs[session_num]
    _, _, trades, summary = simulate_strategy_session(
        session_num=session_num,
        tick_meta_map=tick_maps[session_num],
        reports_by_pair=reports_by_pair,
        config=session_config,
        hl_price_cache=hl_caches.get(session_num),
        hl_candle_cache=hl_candle,
        hl_barrier_candle_cache=hl_barrier,
        hl_vol_candle_cache=hl_vol,
        replay_policy=DynamicReplayPolicy(session_config),
    )

    live_pnl = parse_journal_live_pnl(session_dir / "journal.md")
    assert live_pnl is not None, f"session_{session_num} journal PnL missing"
    sim_pnl = float(summary.get("net_pnl_quote") or sum(t.pnl_quote for t in trades))
    delta = abs(sim_pnl - live_pnl)
    assert delta <= MAX_PNL_DELTA, (
        f"session_{session_num}: live={live_pnl:.2f} sim={sim_pnl:.2f} delta={delta:.2f}"
    )
