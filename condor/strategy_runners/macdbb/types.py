"""MACDBB decision types shared by live DeterministicRunner and research callers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Side = Literal["long", "short"]
EntryClass = Literal["formal", "regime_adaptive_half_size", "hold"]
MonitorState = Literal["thesis_intact", "flip_pending", "thesis_decay", "hold"]


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
    filter_4h_trend: str | None = None
    filter_4h_pass: bool | None = None
    # Precomputed metrics (optional — decide() fills via macdbb_metrics).
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryMeta:
    """Per-leg entry metadata persisted across ticks for Step 5 monitoring."""

    entry_class: EntryClass = "formal"
    entry_bb_pos_pct: float = 0.0
    side: Side = "long"


@dataclass
class OpenPosition:
    executor_id: str
    pair: str
    side: Side
    entry_class: EntryClass
    pnl: float = 0.0
    thesis_decay_streak: int = 0
    flip_streak: int = 0
    entry_bb_pos_pct: float = 0.0
    # False for pending/unconfirmed opens — occupy a slot but skip thesis monitoring.
    filled: bool = True


@dataclass
class MacdbbState:
    adaptive_activation_streak: int = 0
    thesis_decay_by_pair: dict[str, int] = field(default_factory=dict)
    flip_streak_by_pair: dict[str, int] = field(default_factory=dict)
    sl_cooldown_until_tick: dict[str, int] = field(default_factory=dict)
    flip_cooldown_until_tick: dict[str, int] = field(default_factory=dict)
    thesis_decay_extra_pending_by_pair: dict[str, bool] = field(default_factory=dict)
    entry_meta_by_pair: dict[str, EntryMeta] = field(default_factory=dict)
    monitor_state_by_pair: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adaptive_activation_streak": self.adaptive_activation_streak,
            "thesis_decay_by_pair": dict(self.thesis_decay_by_pair),
            "flip_streak_by_pair": dict(self.flip_streak_by_pair),
            "sl_cooldown_until_tick": dict(self.sl_cooldown_until_tick),
            "flip_cooldown_until_tick": dict(self.flip_cooldown_until_tick),
            "thesis_decay_extra_pending_by_pair": dict(
                self.thesis_decay_extra_pending_by_pair
            ),
            "entry_meta_by_pair": {
                pair: asdict(meta) for pair, meta in self.entry_meta_by_pair.items()
            },
            "monitor_state_by_pair": dict(self.monitor_state_by_pair),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> MacdbbState:
        if not raw:
            return cls()
        entry_meta: dict[str, EntryMeta] = {}
        for pair, meta in (raw.get("entry_meta_by_pair") or {}).items():
            if not isinstance(meta, dict):
                continue
            entry_class = str(meta.get("entry_class") or "formal")
            if entry_class not in {"formal", "regime_adaptive_half_size", "hold"}:
                entry_class = "formal"
            side = str(meta.get("side") or "long").lower()
            if side not in {"long", "short"}:
                side = "long"
            entry_meta[str(pair)] = EntryMeta(
                entry_class=entry_class,  # type: ignore[arg-type]
                entry_bb_pos_pct=float(meta.get("entry_bb_pos_pct") or 0),
                side=side,  # type: ignore[arg-type]
            )
        return cls(
            adaptive_activation_streak=int(raw.get("adaptive_activation_streak") or 0),
            thesis_decay_by_pair={
                str(k): int(v)
                for k, v in (raw.get("thesis_decay_by_pair") or {}).items()
            },
            flip_streak_by_pair={
                str(k): int(v)
                for k, v in (raw.get("flip_streak_by_pair") or {}).items()
            },
            sl_cooldown_until_tick={
                str(k): int(v)
                for k, v in (raw.get("sl_cooldown_until_tick") or {}).items()
            },
            flip_cooldown_until_tick={
                str(k): int(v)
                for k, v in (raw.get("flip_cooldown_until_tick") or {}).items()
            },
            thesis_decay_extra_pending_by_pair={
                str(k): bool(v)
                for k, v in (raw.get("thesis_decay_extra_pending_by_pair") or {}).items()
            },
            entry_meta_by_pair=entry_meta,
            monitor_state_by_pair={
                str(k): str(v)
                for k, v in (raw.get("monitor_state_by_pair") or {}).items()
            },
        )


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
    # When False, decide() must not emit creates (fail closed on inventory errors).
    inventory_available: bool = True


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
