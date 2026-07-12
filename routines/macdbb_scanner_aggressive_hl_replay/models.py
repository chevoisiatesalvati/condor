from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


@dataclass
class JournalSignal1h:
    pair: str
    bb_pos_pct: float
    macd: float
    signal_line: float
    histogram: float
    macd_gap_ratio: float
    hist_ratio: float
    trend: str
    momentum: str
    formal_long: bool
    formal_short: bool
    adaptive_long: bool
    adaptive_short: bool
    strength_long: float
    strength_short: float
    # Optional replay bands/crosses/price (signals_1h extension after sS)
    bb_mid: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bullish_cross: bool | None = None
    bearish_cross: bool | None = None
    price: float | None = None

    def has_replay_bands(self) -> bool:
        return (
            self.bb_mid is not None
            and self.bb_upper is not None
            and self.bb_mid > 0
            and self.bb_upper > 0
        )


@dataclass
class Filter4h:
    pair: str
    trend: str
    bb_pos_pct: float | None = None
    macd: float | None = None
    signal_line: float | None = None
    histogram: float | None = None
    passed: bool = False


@dataclass(frozen=True)
class BarrierCloseEvent:
    pair: str
    close_type: str
    pnl_quote: float | None = None


@dataclass(frozen=True)
class JournalCreatePlan:
    pair: str
    side: str | None = None
    entry_class: str | None = None
    notional_req: float | None = None
    notional_cap: float | None = None
    eff_sl: float | None = None
    eff_tp: float | None = None
    vol: float | None = None
    size_mult: float | None = None


@dataclass
class TickMeta:
    tick: int
    timestamp: dt.datetime
    macd_pairs: list[str]
    adaptive_activation_streak: int | None = None
    entry_class: str | None = None
    thesis_decay_streak: int | None = None
    tradeable_count: int | None = None
    scanner_analyzed: int | None = None
    scanner_regime: Literal["mature", "degen"] | None = None
    natr_floor_used: float | None = None
    best_score: float | None = None
    queue_total: list[str] = field(default_factory=list)
    natr_by_pair: dict[str, float] = field(default_factory=dict)
    signals_1h: dict[str, JournalSignal1h] = field(default_factory=dict)
    filter_4h: dict[str, Filter4h] = field(default_factory=dict)
    monitored_pair: str | None = None
    position_pnl_snapshot: float | None = None
    position_pnl_by_pair: dict[str, float] = field(default_factory=dict)
    barrier_closes: list[BarrierCloseEvent] = field(default_factory=list)
    create_plans: dict[str, JournalCreatePlan] = field(default_factory=dict)


@dataclass
class ReportMeta:
    report_id: str
    filename: str
    created_at: dt.datetime
    pair: str
    interval: str


@dataclass
class ParsedReport:
    pair: str
    interval: str
    signal: str
    price: float
    bb_pos_pct: float
    bb_mid: float
    bb_upper: float
    macd: float
    signal_line: float
    histogram: float
    trend: str
    momentum: str
    bullish_cross: bool
    price_le_mid: bool
    bearish_cross: bool
    price_ge_upper: bool
    macd_lt_zero: bool


@dataclass
class SignalSnapshot:
    pair: str
    price: float
    signal: str
    parsed: ParsedReport | None
    metrics: dict[str, float | bool]
    filter_4h_pass: bool | None
    filter_4h_trend: str | None
    source: str
    report_id: str = ""
    journal_fl: int | None = None
    journal_fs: int | None = None
    journal_al: int | None = None
    journal_as: int | None = None
    price_trusted: bool = False


@dataclass
class OpenPosition:
    entry_tick: int
    entry_time: dt.datetime
    pair: str
    side: str
    entry_price: float
    entry_class: str
    entry_trigger: str
    notional_quote: float
    entry_score_long: float
    entry_score_short: float
    entry_adaptive_activation_streak: int
    entry_bb_pos_pct: float = 0.0
    entry_price_trusted: bool = False
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    volatility_proxy_pct: float = 0.0
    sizing_multiplier: float = 1.0
    monitor_state: str = "thesis_intact"
    thesis_decay_streak: int = 0
    flip_streak: int = 0
    thesis_decay_extra_pending: bool = False


@dataclass
class SimTrade:
    session_num: int
    entry_tick: int
    exit_tick: int
    pair: str
    side: str
    entry_price: float
    exit_price: float
    hold_ticks: int
    exit_reason: str
    pnl_quote: float
    return_pct: float
    entry_class: str
    entry_trigger: str
    notional_quote: float
    entry_score_long: float
    entry_score_short: float
    entry_adaptive_activation_streak: int
    sl_pct_used: float = 0.0
    tp_pct_used: float = 0.0
    volatility_proxy_pct: float = 0.0
    sizing_multiplier: float = 1.0
    entry_time_utc: dt.datetime | None = None
    exit_time_utc: dt.datetime | None = None


class ReplayConfigBase(BaseModel):
    """Shared threshold and simulation settings for MACD+BB replay."""

    preset: str = Field(
        default="custom",
        description="Named parameter profile",
    )

    @field_validator("preset")
    @classmethod
    def _validate_preset(cls, value: str) -> str:
        from agents.macdbb_scanner_aggressive_hl.presets import known_preset_names

        allowed = known_preset_names()
        if value not in allowed:
            raise ValueError(f"Unknown preset {value!r}")
        return value
    strategy_slug: str = Field(
        default="macdbb_scanner_aggressive_hl",
        description="Strategy slug under agents/{slug}/strategies/",
    )
    session_nums: str = Field(
        default="all",
        description="Sessions to replay (all or list)",
    )
    time_window_min: int = Field(
        default=5,
        description="Report-to-tick match window (minutes)",
    )
    data_source: Literal[
        "journal_first",
        "journal_recompute",
        "html_only",
        "reports_only",
        "snapshots",
    ] = Field(
        default="journal_first",
        description="Signal and market data source",
    )
    replay_mode: Literal["session_parity", "timeline_backtest"] = Field(
        default="session_parity",
        description="Session journal or timeline range",
    )
    tick_schedule: Literal["journal_ticks", "date_range"] | None = Field(
        default=None,
        description="Tick schedule (auto if unset)",
    )
    config_source: Literal["session", "preset", "override"] = Field(
        default="preset",
        description="Where replay thresholds come from",
    )
    range_start_utc: str | None = Field(
        default=None,
        description="Timeline start (UTC, inclusive)",
    )
    range_end_utc: str | None = Field(
        default=None,
        description="Timeline end (UTC, inclusive)",
    )
    frequency_sec: int = Field(
        default=1800,
        description="Synthetic tick interval (seconds)",
    )
    use_journal_barriers: bool = Field(
        default=True,
        description="Apply journal SL/TP closes",
    )
    activation_ticks: int = Field(
        default=6,
        description="Adaptive activation streak threshold",
    )
    thesis_bb_drift_pts: float = Field(
        default=25.0,
        description="Formal entry BB drift for decay",
    )
    adaptive_long_bb_pos_max: float = Field(default=48.0)
    adaptive_short_bb_pos_min: float = Field(default=72.0)
    adaptive_strong_long_bb_pos_max: float = Field(default=35.0)
    adaptive_strong_short_bb_pos_min: float = Field(default=85.0)
    adaptive_min_macd_gap_ratio: float = Field(default=0.08)
    adaptive_min_hist_ratio: float = Field(default=0.12)
    adaptive_score_open_min: float = Field(default=2.40)
    adaptive_score_open_min_extreme: float = Field(default=2.15)
    adaptive_hist_sign_bonus: float = Field(default=0.35)
    adaptive_hist_sign_penalty: float = Field(default=0.35)
    adaptive_momentum_bonus: float = Field(default=0.20)
    adaptive_momentum_penalty: float = Field(default=0.10)
    bb_proximity_epsilon_pct: float = Field(
        default=0.10,
        description="BB proximity epsilon for formal gates",
    )
    sl_pct: float = Field(default=1.5)
    tp_pct: float = Field(default=3.0)
    write_csv: bool = Field(default=True)
    compare_journal_flags: bool = Field(
        default=False,
        description="Include journal flag mismatch columns",
    )
    price_source: Literal["auto", "reports", "hl_candles", "binance_candles"] = Field(
        default="auto",
        description="Historical price resolution mode",
    )
    candle_source: Literal["hyperliquid", "binance_perpetual"] = Field(
        default="hyperliquid",
        description="Exchange for OHLCV prefetch/cache",
    )
    hl_price_interval: str = Field(
        default="5m",
        description="HL candle interval for tick prices",
    )
    hl_barrier_interval: str = Field(
        default="1m",
        description="HL candle interval for intrabar SL/TP",
    )
    hl_max_concurrent: int = Field(
        default=1,
        description="Max parallel HL candle requests",
    )
    hl_request_interval_ms: int = Field(
        default=400,
        description="Min ms between HL REST requests",
    )
    hl_max_retries: int = Field(
        default=6,
        description="HL candle fetch retry count",
    )
    hl_use_cache: bool = Field(
        default=True,
        description="Use local HL candle cache",
    )
    hl_refresh_cache: bool = Field(
        default=False,
        description="Ignore cache and refetch candles",
    )
    hl_cache_dir: str | None = Field(
        default=None,
        description="Override default candle cache directory",
    )
    candle_prefetch_mode: Literal["full", "lazy"] = Field(
        default="full",
        description="Prefetch all candle series (full) or session prices only with lazy loads (lazy)",
    )
    snapshot_dir: str | None = Field(
        default=None,
        description="Parquet snapshot directory",
    )
    auto_update_snapshots: bool = Field(
        default=True,
        description="Build missing snapshot ticks before timeline replay",
    )
    max_auto_snapshot_days: int = Field(
        default=14,
        description="Max gap (days) for automatic snapshot builds",
    )
    require_price_data: bool = Field(
        default=True,
        description="Skip entries without trusted prices",
    )
    scanner_lookback_hours: int = Field(
        default=6,
        description="Scanner NATR lookback (hours)",
    )


class StrategyReplayConfig(ReplayConfigBase):
    """Replay full MACD+BB strategy (formal + adaptive entries, exits, flips) from session journals."""

    entry_modes: Literal["all", "formal", "adaptive"] = Field(
        default="all",
        description="Simulated entry paths",
    )
    max_open_executors: int = Field(default=3)
    formal_notional_quote: float = Field(default=200.0)
    thesis_decay_exit_ticks: int = Field(default=3)
    sl_cooldown_ticks: int = Field(default=3)
    flip_cooldown_ticks: int = Field(default=2)
    min_tradeable_count: int = Field(
        default=3,
        description="Minimum tradeable pairs per tick",
    )
    ignore_risk_blocks: bool = Field(default=True)
    ignore_adaptive_4h_filter: bool = Field(
        default=False,
        description="Skip 4h filter for adaptive entries",
    )
    adaptive_requires_flat: bool = Field(
        default=True,
        description="Require flat book for adaptive entries",
    )
    report_label: str = Field(
        default="",
        description="Optional saved report title label",
    )


class DynamicStrategyReplayConfig(StrategyReplayConfig):
    """Backtest macdbb_scanner_aggressive_hl with dynamic sizing and volatility-aware barriers."""

    preset: str = Field(
        default="hl_dynamic_session_parity",
        description="Named parameter profile",
    )

    enable_dynamic_sizing: bool = Field(
        default=True,
        description="Scale notional by conviction/volatility",
    )
    enable_dynamic_barriers: bool = Field(
        default=True,
        description="Scale SL/TP by volatility proxy",
    )
    min_notional_quote: float = Field(default=75.0)
    max_notional_quote: float = Field(default=750.0)
    min_conviction_mult: float = Field(default=0.75)
    max_conviction_mult: float = Field(default=1.35)
    strength_mult_per_unit: float = Field(
        default=0.08,
        description="Notional bump per strength unit",
    )
    extreme_displacement_mult: float = Field(default=1.10)
    activation_streak_mult_per_tick: float = Field(
        default=0.0,
        description="Extra size per activation tick",
    )
    thin_universe_mult: float = Field(
        default=0.85,
        description="Multiplier when tradeables ≤ 2",
    )
    mature_tape_low_vol_mult: float = Field(
        default=0.95,
        description="Size cut on low-vol mature tape",
    )
    vol_inverse_sizing: bool = Field(
        default=True,
        description="Shrink size on high volatility",
    )
    min_vol_mult: float = Field(default=0.60)
    max_vol_mult: float = Field(default=1.25)
    ref_volatility_pct: float = Field(
        default=0.50,
        description="Anchor volatility for sizing/barriers",
    )
    sl_vol_exponent: float = Field(default=0.70)
    tp_vol_exponent: float = Field(default=1.00)
    sl_min_pct: float = Field(default=0.8)
    sl_max_pct: float = Field(default=4.0)
    tp_min_pct: float = Field(default=3.0)
    tp_max_pct: float = Field(default=15.0)
    volatility_source: Literal["auto", "bb_width", "natr", "static_tier"] = Field(
        default="auto",
        description="Volatility estimate source",
    )
    ignore_journal_barriers_when_dynamic: bool = Field(
        default=True,
        description="Skip journal barriers when dynamic",
    )

    @classmethod
    def get_routine_fields(cls) -> dict[str, dict[str, Any]]:
        from routines.macdbb_scanner_aggressive_hl_replay.field_ui import build_dynamic_replay_field_metadata

        return build_dynamic_replay_field_metadata(cls)

    @classmethod
    def get_routine_groups(cls) -> list[str]:
        from routines.macdbb_scanner_aggressive_hl_replay.field_ui import REPLAY_FIELD_GROUPS

        return list(REPLAY_FIELD_GROUPS)


class AdaptiveReplayConfig(ReplayConfigBase):
    """Legacy adaptive-only replay with single position."""

    notional_quote: float = Field(
        default=200.0,
        description="Notional per simulated adaptive trade in quote currency",
    )
    exit_on_opposite_formal: bool = Field(
        default=True,
        description="Exit position if opposite formal signal appears",
    )


def compute_return_pct(side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    if side == "long":
        return (current_price / entry_price) - 1.0
    return (entry_price / current_price) - 1.0


def write_csv(path: Any, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    import csv
    from pathlib import Path

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_session_selector(session_selector: str, sessions_dir: Any) -> list[int]:
    from pathlib import Path

    sessions_path = Path(sessions_dir)
    if session_selector.strip().lower() == "all":
        session_numbers: list[int] = []
        for session_path in sessions_path.iterdir():
            if session_path.is_dir() and session_path.name.startswith("session_"):
                try:
                    session_numbers.append(int(session_path.name.split("_", 1)[1]))
                except ValueError:
                    continue
        return sorted(session_numbers)
    parsed_numbers: list[int] = []
    for value in session_selector.split(","):
        value = value.strip()
        if not value:
            continue
        parsed_numbers.append(int(value))
    return sorted(set(parsed_numbers))
