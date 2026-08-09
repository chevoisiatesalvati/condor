"""Replay config for macdbb_pullback_hl timeline backtests (slim v1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PullbackReplayConfig(BaseModel):
    preset: str = Field(default="pullback_decay_2h_60s")
    strategy_slug: str = "macdbb_pullback_hl"
    replay_mode: Literal["timeline_backtest", "session_parity"] = "timeline_backtest"
    data_source: str = "snapshots"
    candle_source: str = "hyperliquid"
    price_source: str = "auto"
    snapshot_dir: str = "data/replay_snapshots_hl_60s"
    frequency_sec: int = 60
    range_start_utc: str = ""
    range_end_utc: str = ""
    time_window_min: int = 1
    require_price_data: bool = True
    write_csv: bool = False
    auto_update_snapshots: bool = False
    max_auto_snapshot_days: int = 14
    hl_price_interval: str = "5m"
    hl_barrier_interval: str = "1m"
    hl_cache_dir: str | None = "data/hl_candles"
    hl_use_cache: bool = True
    hl_refresh_cache: bool = False
    max_open_executors: int = 10
    total_amount_quote: float = 500.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    amount_step: float = 0.0
    min_tradeable_count: int = 1
    use_shared_decide: bool = True
    live_equivalent_queue: bool = True
    compare_journal_flags: bool = False
    sessions: str = ""
    strategy_params: dict[str, Any] = Field(default_factory=dict)
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
    min_notional_quote: float = 100.0
    max_notional_quote: float | None = 1000.0
    sl_symbol_cooldown_hours: float = 5.0
    enable_flip_exit: bool = False
    flip_confirm_ticks: int = 2
    flip_cooldown_hours: float = 1.5
    enable_thesis_decay_exit: bool = False
    thesis_decay_exit_hours: float = 28.0
    thesis_bb_drift_pts: float = 20.0
    pullback_timeout_ticks: int = 0
    sl_cooldown_ticks: int = 0
    thesis_decay_exit_ticks: int = 0
    flip_cooldown_ticks: int = 0

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
        "min_notional_quote",
        "max_notional_quote",
        "sl_symbol_cooldown_hours",
        "enable_flip_exit",
        "flip_confirm_ticks",
        "flip_cooldown_hours",
        "enable_thesis_decay_exit",
        "thesis_decay_exit_hours",
        "thesis_bb_drift_pts",
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
    return params
