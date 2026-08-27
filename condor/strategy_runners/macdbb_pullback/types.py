"""Decision types for macdbb_pullback_hl (slim v1 + optional early exits)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Side = Literal["long", "short"]
EntryClass = Literal["immediate", "pullback"]
MonitorState = Literal[
    "thesis_intact",
    "flip_pending",
    "thesis_decay",
    "hold",
    "pending_unfilled",
]


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
    atr_pct: float | None = None
    impulse_signed_body_sum_pct: float | None = None
    impulse_long: bool = False
    impulse_short: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArmedThesis:
    pair: str
    side: Side
    armed_tick: int
    armed_price: float
    impulse_flag: bool
    bb_mid_at_arm: float


@dataclass
class EntryMeta:
    entry_class: EntryClass = "immediate"
    entry_bb_pos_pct: float = 0.0
    side: Side = "long"


@dataclass
class OpenPosition:
    executor_id: str
    pair: str
    side: Side
    entry_class: EntryClass
    pnl: float = 0.0
    entry_bb_pos_pct: float = 0.0
    filled: bool = True
    thesis_decay_streak: int = 0
    flip_streak: int = 0


@dataclass
class MacdbbPullbackState:
    armed_by_pair: dict[str, ArmedThesis] = field(default_factory=dict)
    sl_cooldown_until_tick: dict[str, int] = field(default_factory=dict)
    entry_meta_by_pair: dict[str, EntryMeta] = field(default_factory=dict)
    thesis_decay_by_pair: dict[str, int] = field(default_factory=dict)
    flip_streak_by_pair: dict[str, int] = field(default_factory=dict)
    thesis_decay_extra_pending_by_pair: dict[str, bool] = field(default_factory=dict)
    thesis_decay_grace_until_tick: dict[str, int] = field(default_factory=dict)
    flip_cooldown_until_tick: dict[str, int] = field(default_factory=dict)
    monitor_state_by_pair: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "armed_by_pair": {
                pair: asdict(armed) for pair, armed in self.armed_by_pair.items()
            },
            "sl_cooldown_until_tick": dict(self.sl_cooldown_until_tick),
            "entry_meta_by_pair": {
                pair: asdict(meta) for pair, meta in self.entry_meta_by_pair.items()
            },
            "thesis_decay_by_pair": dict(self.thesis_decay_by_pair),
            "flip_streak_by_pair": dict(self.flip_streak_by_pair),
            "thesis_decay_extra_pending_by_pair": dict(
                self.thesis_decay_extra_pending_by_pair
            ),
            "thesis_decay_grace_until_tick": dict(self.thesis_decay_grace_until_tick),
            "flip_cooldown_until_tick": dict(self.flip_cooldown_until_tick),
            "monitor_state_by_pair": dict(self.monitor_state_by_pair),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> MacdbbPullbackState:
        if not raw:
            return cls()
        armed: dict[str, ArmedThesis] = {}
        for pair, payload in (raw.get("armed_by_pair") or {}).items():
            if not isinstance(payload, dict):
                continue
            side = str(payload.get("side") or "long").lower()
            if side not in {"long", "short"}:
                side = "long"
            armed[str(pair)] = ArmedThesis(
                pair=str(payload.get("pair") or pair),
                side=side,  # type: ignore[arg-type]
                armed_tick=int(payload.get("armed_tick") or 0),
                armed_price=float(payload.get("armed_price") or 0),
                impulse_flag=bool(payload.get("impulse_flag")),
                bb_mid_at_arm=float(payload.get("bb_mid_at_arm") or 0),
            )
        entry_meta: dict[str, EntryMeta] = {}
        for pair, meta in (raw.get("entry_meta_by_pair") or {}).items():
            if not isinstance(meta, dict):
                continue
            entry_class = str(meta.get("entry_class") or "immediate")
            if entry_class not in {"immediate", "pullback"}:
                entry_class = "immediate"
            side = str(meta.get("side") or "long").lower()
            if side not in {"long", "short"}:
                side = "long"
            entry_meta[str(pair)] = EntryMeta(
                entry_class=entry_class,  # type: ignore[arg-type]
                entry_bb_pos_pct=float(meta.get("entry_bb_pos_pct") or 0),
                side=side,  # type: ignore[arg-type]
            )
        return cls(
            armed_by_pair=armed,
            sl_cooldown_until_tick={
                str(k): int(v)
                for k, v in (raw.get("sl_cooldown_until_tick") or {}).items()
            },
            entry_meta_by_pair=entry_meta,
            thesis_decay_by_pair={
                str(k): int(v)
                for k, v in (raw.get("thesis_decay_by_pair") or {}).items()
            },
            flip_streak_by_pair={
                str(k): int(v)
                for k, v in (raw.get("flip_streak_by_pair") or {}).items()
            },
            thesis_decay_extra_pending_by_pair={
                str(k): bool(v)
                for k, v in (raw.get("thesis_decay_extra_pending_by_pair") or {}).items()
            },
            thesis_decay_grace_until_tick={
                str(k): int(v)
                for k, v in (raw.get("thesis_decay_grace_until_tick") or {}).items()
            },
            flip_cooldown_until_tick={
                str(k): int(v)
                for k, v in (raw.get("flip_cooldown_until_tick") or {}).items()
            },
            monitor_state_by_pair={
                str(k): str(v)
                for k, v in (raw.get("monitor_state_by_pair") or {}).items()
            },
        )


@dataclass
class PullbackTickInput:
    tick_number: int
    tradeable_count: int
    signals: list[SignalSnapshot]
    open_positions: list[OpenPosition]
    barrier_closes: list[dict[str, Any]] = field(default_factory=list)
    total_amount_quote: float = 500.0
    strategy_params: dict[str, Any] = field(default_factory=dict)
    max_open_executors: int = 10
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    amount_step: float = 0.0
    inventory_available: bool = True
    frequency_sec: int = 60

    @property
    def formal_notional_quote(self) -> float:
        """Alias kept for older call sites."""
        return self.total_amount_quote


@dataclass
class CreateAction:
    pair: str
    side: Side
    entry_class: EntryClass
    notional_quote: float
    base_amount: float
    sl_pct: float
    tp_pct: float
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
class PullbackDecision:
    hold: bool
    hold_reason: str
    creates: list[CreateAction] = field(default_factory=list)
    stops: list[StopAction] = field(default_factory=list)
    notifications: list[NotifyAction] = field(default_factory=list)
    state: MacdbbPullbackState = field(default_factory=MacdbbPullbackState)
    journal_fields: dict[str, Any] = field(default_factory=dict)
