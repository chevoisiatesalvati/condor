"""Unit tests for Condor deterministic strategy runners."""

from __future__ import annotations

from pathlib import Path

import pytest

from condor.strategy_runners.catalog import (
    get_strategy,
    is_deterministic_strategy_slug,
    list_strategies,
)
from condor.strategy_runners.macdbb import decide
from condor.strategy_runners.macdbb.types import (
    MacdbbState,
    MacdbbTickInput,
    SignalSnapshot,
)
from condor.strategy_runners.promote import (
    ENGINE_VERSION,
    assert_promoted_or_raise,
    hash_preset,
    promote,
)
from condor.strategy_runners.quantize import apply_fee_slippage, quote_to_base_amount


def test_catalog_registers_macdbb():
    strategies = list_strategies()
    assert any(s.slug == "macdbb_scanner_aggressive_hl" for s in strategies)
    assert any(s.slug == "macdbb_pullback_hl" for s in strategies)
    assert is_deterministic_strategy_slug("macdbb_scanner_aggressive_hl")
    assert is_deterministic_strategy_slug("macdbb_pullback_hl")
    assert not is_deterministic_strategy_slug("claude-code")
    assert get_strategy("macdbb_scanner_aggressive_hl") is not None
    assert get_strategy("macdbb_pullback_hl") is not None


def test_quantize_and_fee_helpers():
    inflated = apply_fee_slippage(100.0, fee_bps=10, slippage_bps=5)
    assert inflated == pytest.approx(100.15)
    q = quote_to_base_amount(
        notional_quote=100.0,
        price=50.0,
        amount_step=0.1,
    )
    assert q.base_amount == pytest.approx(2.0)
    assert q.notional_quote == pytest.approx(100.0)


def test_decide_hold_without_signals():
    tick = MacdbbTickInput(
        tick_number=1,
        scanner_regime=None,
        tradeable_count=0,
        signals=[],
        open_positions=[],
        formal_notional_quote=200.0,
        strategy_params={"adaptive_activation_ticks": 3},
        max_open_executors=3,
    )
    decision = decide(tick, MacdbbState())
    assert decision.hold is True
    assert decision.creates == []


def test_decide_formal_long_create():
    params = {
        "adaptive_activation_ticks": 3,
        "bb_proximity_epsilon_pct": 0.5,
        "adaptive_long_bb_pos_max": 45.0,
        "adaptive_short_bb_pos_min": 55.0,
        "adaptive_strong_long_bb_pos_max": 25.0,
        "adaptive_strong_short_bb_pos_min": 75.0,
        "adaptive_min_macd_gap_ratio": 0.05,
        "adaptive_min_hist_ratio": 0.1,
        "adaptive_score_open_min": 1.5,
        "adaptive_score_open_min_extreme": 1.0,
        "adaptive_hist_sign_bonus": 0.25,
        "adaptive_hist_sign_penalty": 0.25,
        "adaptive_momentum_bonus": 0.15,
        "adaptive_momentum_penalty": 0.15,
        "sl_pct": 2.0,
        "tp_pct": 6.0,
        "enable_dynamic_sizing": False,
        "enable_dynamic_barriers": False,
    }
    signal = SignalSnapshot(
        pair="BTC-USD",
        price=100.0,
        bb_pos_pct=30.0,
        bb_mid=98.0,
        bb_upper=102.0,
        macd=1.0,
        signal_line=0.5,
        histogram=0.5,
        trend="bullish",
        momentum="increasing",
        bullish_cross=True,
        bearish_cross=False,
    )
    tick = MacdbbTickInput(
        tick_number=1,
        scanner_regime="mature",
        tradeable_count=5,
        signals=[signal],
        open_positions=[],
        formal_notional_quote=200.0,
        strategy_params=params,
        max_open_executors=3,
    )
    decision = decide(tick, MacdbbState())
    assert decision.creates, decision.hold_reason
    assert decision.creates[0].pair == "BTC-USD"
    assert decision.creates[0].side == "long"
    assert decision.creates[0].entry_class == "formal"
    assert decision.creates[0].notional_quote > 0


def test_promote_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "condor.strategy_runners.promote.PROMOTIONS_DIR",
        tmp_path / "promotions",
    )
    slug = "macdbb_scanner_aggressive_hl"
    params = {"sl_pct": 2.0, "tp_pct": 6.0}
    with pytest.raises(PermissionError):
        assert_promoted_or_raise(
            slug,
            preset="hl_dynamic_timeline_refine_lead_013",
            strategy_params=params,
            require_promoted=True,
        )
    manifest = promote(
        slug,
        preset="hl_dynamic_timeline_refine_lead_013",
        strategy_params=params,
        venue="hyperliquid_perpetual",
        notes="unit test",
    )
    assert manifest.engine_version == ENGINE_VERSION
    assert manifest.preset_hash == hash_preset(
        "hl_dynamic_timeline_refine_lead_013", params
    )
    assert_promoted_or_raise(
        slug,
        preset="hl_dynamic_timeline_refine_lead_013",
        strategy_params=params,
        require_promoted=True,
    )
    path = Path(tmp_path / "promotions" / f"{slug}.json")
    assert path.is_file()


def test_replay_bridge_imports():
    from condor.strategy_runners.macdbb.replay_bridge import decide_from_sim_tick

    assert callable(decide_from_sim_tick)
