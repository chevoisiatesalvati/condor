"""UI metadata for macdbb_pullback_hl backtest config fields."""

from __future__ import annotations

from typing import Any

from pydantic_core import PydanticUndefined

from condor.strategy_runners.macdbb_pullback.params import (
    MacdbbPullbackHlParams,
    _schema_type_name,
)

PULLBACK_FIELD_GROUPS: list[str] = [
    "Preset & data",
    "Timeline",
    "Thesis",
    "Entry quality",
    "Pullback arm",
    "Barriers",
    "Position monitor",
    "Sizing",
    "Execution & output",
    "Candle prefetch",
]

PULLBACK_EXPANDED_GROUPS: list[str] = [
    "Preset & data",
    "Timeline",
]

_HIDDEN_FIELDS = frozenset(
    {
        "strategy_slug",
        "strategy_params",
        "replay_mode",
        "data_source",
        "sessions",
        "compare_journal_flags",
        "use_shared_decide",
        "pullback_timeout_ticks",
        "sl_cooldown_ticks",
        "thesis_decay_exit_ticks",
        "flip_cooldown_ticks",
        "thesis_decay_negative_grace_ticks",
        "auto_update_snapshots",
        "max_auto_snapshot_days",
        "write_csv",
        "hl_cache_dir",
        "price_source",
    }
)

FREQUENCY_OPTIONS = ["60", "300", "1800"]
FREQUENCY_LABELS = {
    "60": "1 minute",
    "300": "5 minutes",
    "1800": "30 minutes",
}

CANDLE_SOURCE_LABELS = {
    "hyperliquid": "Hyperliquid",
    "binance_perpetual": "Binance perpetual",
}

PULLBACK_FIELD_UI: dict[str, dict[str, Any]] = {
    "preset": {"group": "Preset & data"},
    "snapshot_dir": {
        "group": "Preset & data",
        "options_from": "replay_snapshot_dirs",
        "widget": "select",
    },
    "candle_source": {"group": "Preset & data"},
    "range_start_utc": {"group": "Timeline", "widget": "date"},
    "range_end_utc": {"group": "Timeline", "widget": "date"},
    "frequency_sec": {
        "group": "Timeline",
        "widget": "select",
        "options": FREQUENCY_OPTIONS,
        "option_labels": FREQUENCY_LABELS,
    },
    "time_window_min": {"group": "Timeline"},
    "max_open_executors": {"group": "Execution & output"},
    "total_amount_quote": {"group": "Execution & output"},
    "fee_bps": {"group": "Execution & output"},
    "slippage_bps": {"group": "Execution & output"},
    "amount_step": {"group": "Execution & output"},
    "min_tradeable_count": {"group": "Execution & output"},
    "live_equivalent_queue": {"group": "Execution & output"},
    "require_price_data": {"group": "Execution & output"},
    "hl_price_interval": {"group": "Candle prefetch"},
    "hl_barrier_interval": {"group": "Candle prefetch"},
    "hl_use_cache": {"group": "Candle prefetch"},
    "hl_refresh_cache": {"group": "Candle prefetch"},
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


def _apply_ui_entry(entry: dict[str, Any], ui: dict[str, Any]) -> None:
    if "group" in ui:
        entry["group"] = ui["group"]
    if "visible_when" in ui:
        entry["visible_when"] = ui["visible_when"]
    if "hidden_when" in ui:
        entry["hidden_when"] = ui["hidden_when"]
    if ui.get("hidden"):
        entry["hidden"] = True
    if "nullable" in ui:
        entry["nullable"] = ui["nullable"]
    if "options_from" in ui:
        entry["options_from"] = ui["options_from"]
        entry["widget"] = ui.get("widget", "select")
    if "options" in ui and isinstance(ui["options"], list):
        entry["options"] = ui["options"]
        entry["widget"] = ui.get("widget", "select")
    if "option_labels" in ui:
        entry["option_labels"] = ui["option_labels"]
    if "widget" in ui and "widget" not in entry:
        entry["widget"] = ui["widget"]


def build_pullback_replay_field_metadata(config_class: type) -> dict[str, dict[str, Any]]:
    strategy_fields = MacdbbPullbackHlParams.get_fields()
    fields: dict[str, dict[str, Any]] = {}
    for name, field_info in config_class.model_fields.items():
        annotation = field_info.annotation
        default = field_info.default
        if default is PydanticUndefined:
            default = None
        entry: dict[str, Any] = {
            "type": _schema_type_name(annotation),
            "default": default,
            "description": field_info.description or name,
        }
        if name == "preset":
            from condor.strategy_runners.macdbb_pullback.presets import (
                known_preset_names,
                preset_labels,
            )

            preset_options = sorted(
                known_preset_names(), key=lambda opt: (opt != "custom", opt)
            )
            labels = preset_labels()
            entry["widget"] = "select"
            entry["options"] = preset_options
            entry["option_labels"] = {
                opt: labels.get(opt, opt) for opt in preset_options
            }
        else:
            literal_options = _literal_field_options(annotation)
            if literal_options:
                entry["widget"] = "select"
                entry["options"] = literal_options
                if name == "candle_source":
                    entry["option_labels"] = {
                        opt: CANDLE_SOURCE_LABELS.get(opt, opt)
                        for opt in literal_options
                    }

        from typing import get_args, get_origin

        origin = get_origin(annotation)
        args = get_args(annotation) if origin is not None else ()
        if type(None) in args:
            entry["nullable"] = True

        extra = field_info.json_schema_extra
        if isinstance(extra, dict):
            if "widget" in extra:
                entry["widget"] = extra["widget"]
            if "options_from" in extra:
                entry["options_from"] = extra["options_from"]
            if "options" in extra and isinstance(extra["options"], list):
                entry["options"] = extra["options"]
                entry["widget"] = extra.get("widget", "select")

        strategy_ui = strategy_fields.get(name)
        if strategy_ui:
            if strategy_ui.get("description"):
                entry["description"] = strategy_ui["description"]
            if "group" in strategy_ui:
                entry["group"] = strategy_ui["group"]
            if strategy_ui.get("duration"):
                entry["duration"] = True
                if "effective_tick_key" in strategy_ui:
                    entry["effective_tick_key"] = strategy_ui["effective_tick_key"]

        _apply_ui_entry(entry, PULLBACK_FIELD_UI.get(name, {}))
        if name in _HIDDEN_FIELDS:
            entry["hidden"] = True
        fields[name] = entry
    return fields
