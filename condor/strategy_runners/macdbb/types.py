"""MACDBB decision types shared by live DeterministicRunner and research callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Side = Literal["long", "short"]
EntryClass = Literal["formal", "regime_adaptive_half_size", "hold"]


@dataclass
class SignalSnapshot:
    pair: str
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
    bearish_cross: bool
    natr_mean_pct: float | None = None
    # Precomputed metrics (optional — decide() fills via macdbb_metrics).
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpenPosition:
    executor_id: str
    pair: str
    side: Side
    entry_class: EntryClass
    pnl: float = 0.0
    thesis_decay_streak: int = 0
    flip_streak: int = 0


@dataclass
class MacdbbState:
    adaptive_activation_streak: int = 0
    thesis_decay_by_pair: dict[str, int] = field(default_factory=dict)
    flip_streak_by_pair: dict[str, int] = field(default_factory=dict)
    sl_cooldown_until_tick: dict[str, int] = field(default_factory=dict)


@dataclass
class MacdbbTickInput:
    tick_number: int
    scanner_regime: Literal["mature", "degen"] | None
    tradeable_count: int
    signals: list[SignalSnapshot]
    open_positions: list[OpenPosition]
    barrier_closes: list[dict[str, Any]] = field(default_factory=list)
    formal_notional_quote: float = 500.0
    strategy_params: dict[str, Any] = field(default_factory=dict)
    max_open_executors: int = 10
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    amount_step: float = 0.0


@dataclass
class CreateAction:
    pair: str
    side: Side
    entry_class: EntryClass
    notional_quote: float
    base_amount: float
    sl_pct: float
    tp_pct: float
    volatility_proxy_pct: float
    sizing_multiplier: float
    score: float = 0.0


@dataclass
class StopAction:
    executor_id: str
    pair: str
    reason: str
    close_type: str = "EARLY_STOP"


@dataclass
class NotifyAction:
    text: str


@dataclass
class MacdbbDecision:
    hold: bool
    hold_reason: str
    creates: list[CreateAction] = field(default_factory=list)
    stops: list[StopAction] = field(default_factory=list)
    notifications: list[NotifyAction] = field(default_factory=list)
    state: MacdbbState = field(default_factory=MacdbbState)
    journal_fields: dict[str, Any] = field(default_factory=dict)
