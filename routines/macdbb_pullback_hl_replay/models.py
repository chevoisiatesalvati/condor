"""Replay config for macdbb_pullback_hl timeline backtests (slim v1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from condor.strategy_runners.macdbb_pullback.params import minutes_to_ticks


class PullbackReplayConfig(BaseModel):
    """Timeline backtest for macdbb_pullback_hl (thesis + staged pullback entries)."""

    preset: str = Field(
        default="pullback_decay_2h_60s",
        description="Named parameter profile",
    )
    strategy_slug: str = Field(
        default="macdbb_pullback_hl",
        description="Strategy slug",
    )
    replay_mode: Literal["timeline_backtest", "session_parity"] = Field(
        default="timeline_backtest",
        description="Timeline range or session journal",
    )
    data_source: str = Field(
        default="snapshots",
        description="Signal and market data source",
    )
    candle_source: Literal["hyperliquid", "binance_perpetual"] = Field(
        default="hyperliquid",
        description="Exchange for OHLCV candles",
    )
    price_source: Literal["auto", "reports", "hl_candles", "binance_candles"] = Field(
        default="hl_candles",
        description="Historical price resolution mode",
    )
    snapshot_dir: str = Field(
        default="data/replay_snapshots_hl_60s",
        description="Parquet snapshot directory",
    )
    frequency_sec: int = Field(
        default=60,
        description="Tick interval",
    )
    range_start_utc: str = Field(
        default="",
        description="Timeline start (UTC, inclusive)",
    )
    range_end_utc: str = Field(
        default="",
        description="Timeline end (UTC, inclusive)",
    )
    time_window_min: int = Field(
        default=1,
        description="Report-to-tick match window (minutes)",
    )
    require_price_data: bool = Field(
        default=True,
        description="Skip entries without trusted prices",
    )
    write_csv: bool = Field(default=False, description="Write CSV artifacts")
    auto_update_snapshots: bool = Field(
        default=False,
        description="Build missing snapshot ticks before replay",
    )
    max_auto_snapshot_days: int = Field(
        default=14,
        description="Max gap (days) for automatic snapshot builds",
    )
    hl_price_interval: Literal["1m", "5m", "15m", "1h"] = Field(
        default="5m",
        description="Candle interval for tick prices",
    )
    hl_barrier_interval: Literal["1m", "5m", "15m", "1h"] = Field(
        default="1m",
        description="Candle interval for stop-loss / take-profit",
    )
    hl_cache_dir: str | None = Field(
        default="data/hl_candles",
        description="Local candle cache directory",
    )
    hl_use_cache: bool = Field(
        default=True,
        description="Use local candle cache",
    )
    hl_refresh_cache: bool = Field(
        default=False,
        description="Ignore cache and refetch candles",
    )
    max_open_executors: int = Field(
        default=10,
        description="Maximum open positions",
    )
    total_amount_quote: float = Field(
        default=100.0,
        description="Per-entry notional (USDT)",
    )
    fee_bps: float = Field(default=0.0, description="Fee (basis points)")
    slippage_bps: float = Field(default=0.0, description="Slippage (basis points)")
    amount_step: float = Field(default=0.0, description="Amount rounding step")
    min_tradeable_count: int = Field(
        default=1,
        description="Minimum tradeable pairs per tick",
    )
    use_shared_decide: bool = Field(
        default=True,
        description="Use shared decide() for entries",
    )
    live_equivalent_queue: bool = Field(
        default=True,
        description="Match live Strategies queue (mature-first 8 + open legs)",
    )
    compare_journal_flags: bool = Field(
        default=False,
        description="Include journal flag mismatch columns",
    )
    sessions: str = Field(default="", description="Sessions to replay")
    strategy_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw strategy parameter overlay",
    )
    bb_proximity_epsilon_pct: float = 0.22
    impulse_lookback_bars: int = 2
    impulse_atr_mult: float = 1.25
    atr_period: int = 14
    chase_long_bb_pos_max: float = 70.0
    chase_short_bb_pos_min: float = 30.0
    pullback_epsilon_pct: float = 0.35
    pullback_timeout_hours: float = 12.0
    sl_pct: float = 3.0
    tp_pct: float = 6.0
    enable_dynamic_barriers: bool = False
    ref_volatility_pct: float = 1.0
    sl_vol_exponent: float = 1.0
    tp_vol_exponent: float = 1.0
    sl_min_pct: float = 2.0
    sl_max_pct: float = 6.0
    tp_min_pct: float = 4.0
    tp_max_pct: float = 12.0
    min_notional_quote: float = 10.0
    max_notional_quote: float | None = 1000.0
    enable_dynamic_sizing: bool = False
    min_vol_mult: float = 0.5
    max_vol_mult: float = 1.5
    sl_symbol_cooldown_hours: float = 5.0
    enable_flip_exit: bool = False
    flip_confirm_ticks: int = 2
    flip_cooldown_hours: float = 1.5
    enable_thesis_decay_exit: bool = False
    thesis_decay_exit_hours: float = 28.0
    thesis_bb_drift_pts: float = 20.0
    thesis_decay_negative_grace_minutes: float = 30.0
    pullback_timeout_ticks: int = 0
    sl_cooldown_ticks: int = 0
    thesis_decay_exit_ticks: int = 0
    flip_cooldown_ticks: int = 0
    thesis_decay_negative_grace_ticks: int = 0

    @property
    def formal_notional_quote(self) -> float:
        return self.total_amount_quote

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, value: str) -> str:
        from condor.strategy_runners.macdbb_pullback.presets import known_preset_names

        if value not in known_preset_names():
            raise ValueError(f"Unknown preset {value!r}")
        return value

    @classmethod
    def get_routine_fields(cls) -> dict[str, dict[str, Any]]:
        from routines.macdbb_pullback_hl_replay.field_ui import (
            build_pullback_replay_field_metadata,
        )

        return build_pullback_replay_field_metadata(cls)

    @classmethod
    def get_routine_groups(cls) -> list[str]:
        from routines.macdbb_pullback_hl_replay.field_ui import PULLBACK_FIELD_GROUPS

        return list(PULLBACK_FIELD_GROUPS)

    @classmethod
    def get_routine_expanded_groups(cls) -> list[str]:
        from routines.macdbb_pullback_hl_replay.field_ui import PULLBACK_EXPANDED_GROUPS

        return list(PULLBACK_EXPANDED_GROUPS)


def strategy_params_from_config(config: PullbackReplayConfig) -> dict[str, Any]:
    if config.strategy_params:
        params = dict(config.strategy_params)
    else:
        from condor.strategy_runners.macdbb_pullback.presets import strategy_params_from_preset

        params = strategy_params_from_preset(
            config.preset, frequency_sec=config.frequency_sec
        )
    for key in (
        "bb_proximity_epsilon_pct",
        "impulse_lookback_bars",
        "impulse_atr_mult",
        "atr_period",
        "chase_long_bb_pos_max",
        "chase_short_bb_pos_min",
        "pullback_epsilon_pct",
        "pullback_timeout_hours",
        "sl_pct",
        "tp_pct",
        "enable_dynamic_barriers",
        "ref_volatility_pct",
        "sl_vol_exponent",
        "tp_vol_exponent",
        "sl_min_pct",
        "sl_max_pct",
        "tp_min_pct",
        "tp_max_pct",
        "min_notional_quote",
        "max_notional_quote",
        "enable_dynamic_sizing",
        "min_vol_mult",
        "max_vol_mult",
        "sl_symbol_cooldown_hours",
        "enable_flip_exit",
        "flip_confirm_ticks",
        "flip_cooldown_hours",
        "enable_thesis_decay_exit",
        "thesis_decay_exit_hours",
        "thesis_bb_drift_pts",
        "thesis_decay_negative_grace_minutes",
    ):
        value = getattr(config, key, None)
        if value is not None:
            params[key] = value
    freq = max(1, int(config.frequency_sec or 60))
    if not params.get("pullback_timeout_ticks"):
        params["pullback_timeout_ticks"] = max(
            1, int(round(float(params.get("pullback_timeout_hours") or 12) * 3600 / freq))
        )
    if not params.get("sl_symbol_cooldown_ticks"):
        params["sl_symbol_cooldown_ticks"] = max(
            1,
            int(round(float(params.get("sl_symbol_cooldown_hours") or 5) * 3600 / freq)),
        )
    if not params.get("thesis_decay_exit_ticks"):
        params["thesis_decay_exit_ticks"] = max(
            0,
            int(round(float(params.get("thesis_decay_exit_hours") or 0) * 3600 / freq)),
        )
    if not params.get("flip_cooldown_ticks"):
        params["flip_cooldown_ticks"] = max(
            0,
            int(round(float(params.get("flip_cooldown_hours") or 0) * 3600 / freq)),
        )
    grace_minutes = params.get("thesis_decay_negative_grace_minutes")
    if grace_minutes is None:
        grace_minutes = 30.0
    params["thesis_decay_negative_grace_ticks"] = minutes_to_ticks(
        float(grace_minutes), freq
    )
    return params
