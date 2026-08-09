"""Parameter schema for macdbb_pullback_hl (slim v1 + optional early exits)."""

from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, Field

# Maps duration fields to effective tick keys injected at resolve time.
DURATION_EFFECTIVE_TICK_KEYS: dict[str, str] = {
    "pullback_timeout_hours": "pullback_timeout_ticks",
    "sl_symbol_cooldown_hours": "sl_symbol_cooldown_ticks",
    "thesis_decay_exit_hours": "thesis_decay_exit_ticks",
    "flip_cooldown_hours": "flip_cooldown_ticks",
}


def _schema_type_name(annotation: Any) -> str:
    """Map a field annotation to a simple UI type name."""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    origin = get_origin(annotation)
    if origin is Union or isinstance(annotation, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _schema_type_name(args[0])
    return "str"


class MacdbbPullbackHlParams(BaseModel):
    """Thesis + impulse/chase + pullback arm + fixed SL/TP + optional early exits."""

    bb_proximity_epsilon_pct: float = Field(
        default=0.22,
        ge=0.0,
        le=5.0,
        description="Thesis BB mid / upper proximity tolerance (%)",
        json_schema_extra={"group": "Thesis"},
    )

    impulse_lookback_bars: int = Field(
        default=2,
        ge=1,
        le=6,
        description="Completed 1h bars for impulse body sum",
        json_schema_extra={"group": "Entry quality"},
    )
    impulse_atr_mult: float = Field(
        default=1.25,
        ge=0.1,
        le=5.0,
        description="Impulse if signed body sum ≥ mult × ATR%",
        json_schema_extra={"group": "Entry quality"},
    )
    atr_period: int = Field(
        default=14,
        ge=5,
        le=50,
        description="ATR period for impulse detection",
        json_schema_extra={"group": "Entry quality"},
    )
    chase_long_bb_pos_max: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Long chase if BB% above this",
        json_schema_extra={"group": "Entry quality"},
    )
    chase_short_bb_pos_min: float = Field(
        default=30.0,
        ge=0.0,
        le=100.0,
        description="Short chase if BB% below this",
        json_schema_extra={"group": "Entry quality"},
    )

    pullback_epsilon_pct: float = Field(
        default=0.35,
        ge=0.0,
        le=5.0,
        description="Pullback “near BB mid” band (%)",
        json_schema_extra={"group": "Pullback arm"},
    )
    pullback_timeout_hours: float = Field(
        default=12.0,
        ge=0.5,
        le=168.0,
        description="Max hours an armed thesis may wait",
        json_schema_extra={
            "group": "Pullback arm",
            "duration": True,
            "effective_tick_key": "pullback_timeout_ticks",
        },
    )

    sl_pct: float = Field(
        default=3.0,
        ge=0.1,
        le=25.0,
        description="Fixed stop-loss %",
        json_schema_extra={"group": "Barriers"},
    )
    tp_pct: float = Field(
        default=6.0,
        ge=0.1,
        le=50.0,
        description="Fixed take-profit %",
        json_schema_extra={"group": "Barriers"},
    )
    sl_symbol_cooldown_hours: float = Field(
        default=5.0,
        ge=0.0,
        le=168.0,
        description="Hours to block re-entry after stop-loss",
        json_schema_extra={
            "group": "Barriers",
            "duration": True,
            "effective_tick_key": "sl_symbol_cooldown_ticks",
        },
    )

    enable_flip_exit: bool = Field(
        default=False,
        description="Early-stop when opposing thesis confirms for flip_confirm_ticks",
        json_schema_extra={"group": "Position monitor"},
    )
    flip_confirm_ticks: int = Field(
        default=2,
        ge=1,
        le=48,
        description="Consecutive opposing-thesis ticks required to flip-exit",
        json_schema_extra={"group": "Position monitor"},
    )
    flip_cooldown_hours: float = Field(
        default=1.5,
        ge=0.0,
        le=168.0,
        description="Hours after a flip exit before re-entry / re-flip on the symbol",
        json_schema_extra={
            "group": "Position monitor",
            "duration": True,
            "effective_tick_key": "flip_cooldown_ticks",
        },
    )

    enable_thesis_decay_exit: bool = Field(
        default=False,
        description="Early-stop when NEUTRAL + adverse trend/BB drift persists",
        json_schema_extra={"group": "Position monitor"},
    )
    thesis_decay_exit_hours: float = Field(
        default=28.0,
        ge=0.0,
        le=168.0,
        description="Hours of thesis-decay NEUTRAL monitor ticks before closing",
        json_schema_extra={
            "group": "Position monitor",
            "duration": True,
            "effective_tick_key": "thesis_decay_exit_ticks",
        },
    )
    thesis_bb_drift_pts: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="BB% drift from entry that counts as thesis decay",
        json_schema_extra={"group": "Position monitor"},
    )

    min_notional_quote: float = Field(
        default=100.0,
        ge=0.0,
        description="Minimum notional quote per entry",
        json_schema_extra={"group": "Sizing"},
    )
    max_notional_quote: float | None = Field(
        default=1000.0,
        description="Maximum notional quote per entry",
        json_schema_extra={"group": "Sizing"},
    )

    @classmethod
    def get_fields(cls) -> dict[str, dict[str, Any]]:
        fields: dict[str, dict[str, Any]] = {}
        for name, field_info in cls.model_fields.items():
            entry: dict[str, Any] = {
                "type": _schema_type_name(field_info.annotation),
                "description": field_info.description or name,
            }
            extra = field_info.json_schema_extra
            if isinstance(extra, dict):
                if "group" in extra:
                    entry["group"] = extra["group"]
                if "widget" in extra:
                    entry["widget"] = extra["widget"]
                if extra.get("duration"):
                    entry["duration"] = True
                    if "effective_tick_key" in extra:
                        entry["effective_tick_key"] = extra["effective_tick_key"]
            fields[name] = entry
        return fields

    @classmethod
    def get_computed_fields(cls) -> dict[str, str]:
        return dict(DURATION_EFFECTIVE_TICK_KEYS)

    @classmethod
    def get_groups(cls) -> list[str]:
        seen: list[str] = []
        for field_info in cls.model_fields.values():
            extra = field_info.json_schema_extra
            if isinstance(extra, dict) and "group" in extra:
                group = str(extra["group"])
                if group not in seen:
                    seen.append(group)
        return seen


def default_strategy_params() -> dict[str, Any]:
    return MacdbbPullbackHlParams().model_dump()
