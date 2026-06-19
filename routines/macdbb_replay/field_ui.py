"""UI metadata for dynamic strategy replay config fields."""

from __future__ import annotations

from typing import Any

SESSION_MODE = {"replay_mode": "session_parity"}
TIMELINE_MODE = {"replay_mode": "timeline_backtest"}

REPLAY_FIELD_GROUPS: list[str] = [
    "Preset & mode",
    "Timeline",
    "Sessions",
    "Adaptive gates",
    "Entry barriers",
    "Dynamic sizing",
    "Dynamic barriers",
    "Execution & output",
    "HL price prefetch",
]

REPLAY_FIELD_UI: dict[str, dict[str, Any]] = {
    "preset": {"group": "Preset & mode"},
    "strategy_slug": {"group": "Preset & mode"},
    "replay_mode": {"group": "Preset & mode"},
    "data_source": {"group": "Preset & mode"},
    "tick_schedule": {"group": "Preset & mode"},
    "range_start_utc": {
        "group": "Timeline",
        "visible_when": TIMELINE_MODE,
        "widget": "date",
    },
    "range_end_utc": {
        "group": "Timeline",
        "visible_when": TIMELINE_MODE,
        "widget": "date",
    },
    "frequency_sec": {"group": "Timeline", "visible_when": TIMELINE_MODE},
    "session_nums": {"group": "Sessions", "visible_when": SESSION_MODE},
    "config_source": {"group": "Sessions", "visible_when": SESSION_MODE},
    "time_window_min": {"group": "Sessions"},
    "use_journal_barriers": {"group": "Sessions"},
    "activation_ticks": {"group": "Adaptive gates"},
    "thesis_bb_drift_pts": {"group": "Adaptive gates"},
    "adaptive_long_bb_pos_max": {"group": "Adaptive gates"},
    "adaptive_short_bb_pos_min": {"group": "Adaptive gates"},
    "adaptive_strong_long_bb_pos_max": {"group": "Adaptive gates"},
    "adaptive_strong_short_bb_pos_min": {"group": "Adaptive gates"},
    "adaptive_min_macd_gap_ratio": {"group": "Adaptive gates"},
    "adaptive_min_hist_ratio": {"group": "Adaptive gates"},
    "adaptive_score_open_min": {"group": "Adaptive gates"},
    "adaptive_score_open_min_extreme": {"group": "Adaptive gates"},
    "adaptive_hist_sign_bonus": {"group": "Adaptive gates"},
    "adaptive_hist_sign_penalty": {"group": "Adaptive gates"},
    "adaptive_momentum_bonus": {"group": "Adaptive gates"},
    "adaptive_momentum_penalty": {"group": "Adaptive gates"},
    "bb_proximity_epsilon_pct": {"group": "Adaptive gates"},
    "ignore_adaptive_4h_filter": {"group": "Adaptive gates"},
    "adaptive_requires_flat": {"group": "Adaptive gates"},
    "sl_pct": {"group": "Entry barriers"},
    "tp_pct": {"group": "Entry barriers"},
    "thesis_decay_exit_ticks": {"group": "Entry barriers"},
    "sl_cooldown_ticks": {"group": "Entry barriers"},
    "flip_cooldown_ticks": {"group": "Entry barriers"},
    "enable_dynamic_sizing": {"group": "Dynamic sizing"},
    "min_notional_quote": {"group": "Dynamic sizing"},
    "max_notional_quote": {"group": "Dynamic sizing"},
    "min_conviction_mult": {"group": "Dynamic sizing"},
    "max_conviction_mult": {"group": "Dynamic sizing"},
    "strength_mult_per_unit": {"group": "Dynamic sizing"},
    "extreme_displacement_mult": {"group": "Dynamic sizing"},
    "activation_streak_mult_per_tick": {"group": "Dynamic sizing"},
    "thin_universe_mult": {"group": "Dynamic sizing"},
    "mature_tape_low_vol_mult": {"group": "Dynamic sizing"},
    "vol_inverse_sizing": {"group": "Dynamic sizing"},
    "min_vol_mult": {"group": "Dynamic sizing"},
    "max_vol_mult": {"group": "Dynamic sizing"},
    "ref_volatility_pct": {"group": "Dynamic sizing"},
    "enable_dynamic_barriers": {"group": "Dynamic barriers"},
    "sl_vol_exponent": {"group": "Dynamic barriers"},
    "tp_vol_exponent": {"group": "Dynamic barriers"},
    "sl_min_pct": {"group": "Dynamic barriers"},
    "sl_max_pct": {"group": "Dynamic barriers"},
    "tp_min_pct": {"group": "Dynamic barriers"},
    "tp_max_pct": {"group": "Dynamic barriers"},
    "volatility_source": {"group": "Dynamic barriers"},
    "ignore_journal_barriers_when_dynamic": {"group": "Dynamic barriers"},
    "entry_modes": {"group": "Execution & output"},
    "max_open_executors": {"group": "Execution & output"},
    "formal_notional_quote": {"group": "Execution & output"},
    "min_tradeable_count": {"group": "Execution & output"},
    "ignore_risk_blocks": {"group": "Execution & output"},
    "write_csv": {"group": "Execution & output"},
    "compare_journal_flags": {
        "group": "Execution & output",
        "visible_when": SESSION_MODE,
    },
    "report_label": {"group": "Execution & output"},
    "price_source": {"group": "HL price prefetch"},
    "hl_price_interval": {"group": "HL price prefetch"},
    "hl_barrier_interval": {"group": "HL price prefetch"},
    "hl_max_concurrent": {"group": "HL price prefetch"},
    "hl_request_interval_ms": {"group": "HL price prefetch"},
    "hl_max_retries": {"group": "HL price prefetch"},
    "hl_use_cache": {"group": "HL price prefetch"},
    "hl_refresh_cache": {"group": "HL price prefetch"},
    "hl_cache_dir": {"group": "HL price prefetch"},
    "require_price_data": {"group": "HL price prefetch"},
    "scanner_lookback_hours": {"group": "HL price prefetch"},
}


def _literal_field_options(annotation: Any) -> list[str] | None:
    from typing import get_args, get_origin

    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        if type(None) in args:
            non_none = [arg for arg in args if arg is not type(None)]
            if len(non_none) == 1:
                return _literal_field_options(non_none[0])
        if str(origin).endswith("Literal"):
            return [str(arg) for arg in args]
    return None


def build_dynamic_replay_field_metadata(config_class: type) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for name, field_info in config_class.model_fields.items():
        annotation = field_info.annotation
        type_name = getattr(annotation, "__name__", str(annotation))
        entry: dict[str, Any] = {
            "type": type_name,
            "default": field_info.default,
            "description": field_info.description or name,
        }
        literal_options = _literal_field_options(annotation)
        if literal_options:
            entry["widget"] = "select"
            entry["options"] = literal_options

        extra = field_info.json_schema_extra
        if isinstance(extra, dict):
            if "widget" in extra:
                entry["widget"] = extra["widget"]
            if "options_from" in extra:
                entry["options_from"] = extra["options_from"]
            if "options" in extra and isinstance(extra["options"], list):
                entry["options"] = extra["options"]
                entry["widget"] = extra.get("widget", "select")

        ui = REPLAY_FIELD_UI.get(name, {})
        if "group" in ui:
            entry["group"] = ui["group"]
        if "visible_when" in ui:
            entry["visible_when"] = ui["visible_when"]
        if "widget" in ui and "widget" not in entry:
            entry["widget"] = ui["widget"]

        fields[name] = entry
    return fields
