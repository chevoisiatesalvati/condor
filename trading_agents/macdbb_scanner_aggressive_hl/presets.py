from __future__ import annotations

from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from condor.trading_agent.strategy_paths import resolve_presets_yaml

PresetValue = float | int | bool | str | None

AGENT_SLUG = "macdbb_scanner_aggressive_hl"

PUBLIC_PRESET_LABELS: dict[str, str] = {
    "custom": "Custom",
    "hl_dynamic_session_parity": "Session parity",
    "hl_dynamic_timeline_public_fixture": "Public timeline test fixture",
}


def _load_private_preset_bundle() -> dict[str, Any]:
    path = resolve_presets_yaml(AGENT_SLUG)
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


_bundle_cache: dict[str, Any] | None = None
_bundle_mtime: float | None = None


def _private_preset_bundle() -> dict[str, Any]:
    """Load private presets.yaml, refreshing when the file changes."""
    global _bundle_cache, _bundle_mtime
    path = resolve_presets_yaml(AGENT_SLUG)
    if path is None:
        _bundle_cache = {}
        _bundle_mtime = None
        return {}
    mtime = path.stat().st_mtime
    if _bundle_cache is not None and _bundle_mtime == mtime:
        return _bundle_cache
    _bundle_cache = _load_private_preset_bundle()
    _bundle_mtime = mtime
    return _bundle_cache


def preset_labels() -> dict[str, str]:
    bundle = _private_preset_bundle()
    return {**PUBLIC_PRESET_LABELS, **(bundle.get("labels") or {})}


def agent_strategy_preset_names() -> tuple[str, ...]:
    bundle = _private_preset_bundle()
    return tuple(bundle.get("agent_strategy_preset_names") or ())


def default_agent_strategy_preset() -> str:
    bundle = _private_preset_bundle()
    return str(bundle.get("default_agent_strategy_preset") or "hl_dynamic_session_parity")


def private_presets_available() -> bool:
    """True when private presets.yaml (submodule or local override) is loaded."""
    return bool(_private_preset_bundle().get("dynamic_preset_overrides"))


def get_private_preset_bundle() -> dict[str, Any]:
    """Return parsed private presets.yaml bundle (empty when unavailable)."""
    return dict(_private_preset_bundle())


def current_winner_preset_name() -> str:
    """Named anchor preset for sweeps (private yaml or public session parity)."""
    bundle = _private_preset_bundle()
    return str(
        bundle.get("current_winner_preset")
        or bundle.get("default_agent_strategy_preset")
        or "hl_dynamic_session_parity"
    )


# Backward-compatible module attributes (prefer the functions above in new code).
PRESET_LABELS = preset_labels()
AGENT_STRATEGY_PRESET_NAMES = agent_strategy_preset_names()
DEFAULT_AGENT_STRATEGY_PRESET = default_agent_strategy_preset()

# Standard capital-at-risk reference for cap-norm PnL (sweep + routine).
# HL_SWEEP_BEST fixed replay, sessions 37-58: avg $ notional per trade.
# cap_norm_pnl = raw_pnl × (FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL / avg_notional).
# Re-measure after the session window or HL_SWEEP_BEST baseline changes.
FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL = 266.45


def capital_normalized_pnl(
    raw_pnl: float,
    avg_notional: float,
    benchmark_avg_notional: float = FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
) -> float:
    """Scale raw PnL to fixed-strategy avg position size for capital-at-risk comparison."""
    if avg_notional <= 0 or benchmark_avg_notional <= 0:
        return raw_pnl
    return raw_pnl * (benchmark_avg_notional / avg_notional)


PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {}

# Shared infra keys merged into dynamic presets (not mode-specific).
_DYNAMIC_PRESET_INFRA: dict[str, PresetValue] = {
    "formal_notional_quote": 500.0,
    "price_source": "auto",
    "hl_use_cache": True,
    "require_price_data": True,
}

_DRIVER_SESSION: dict[str, PresetValue] = {
    "replay_mode": "session_parity",
    "data_source": "reports_only",
    "config_source": "preset",
    "session_nums": "all",
    "time_window_min": 5,
    "use_journal_barriers": False,
    "write_csv": False,
}

# Default parquet snapshots for timeline mega best preset (UI + routine).
DEFAULT_TIMELINE_SNAPSHOT_DIR = "data/replay_snapshots_binance_1y"

_DRIVER_TIMELINE: dict[str, PresetValue] = {
    "replay_mode": "timeline_backtest",
    "data_source": "snapshots",
    "config_source": "preset",
    "frequency_sec": 1800,
    "time_window_min": 15,
    "use_journal_barriers": False,
    "write_csv": False,
    "candle_source": "binance_perpetual",
    "price_source": "reports",
    "snapshot_dir": DEFAULT_TIMELINE_SNAPSHOT_DIR,
}

_STRATEGY_SESSION_MEGA_BEST: dict[str, PresetValue] = {
    "enable_dynamic_sizing": True,
    "enable_dynamic_barriers": True,
    "ignore_journal_barriers_when_dynamic": True,
    "activation_ticks": 0,
    "sl_pct": 3.8,
    "tp_pct": 5.0,
    "thesis_decay_exit_ticks": 28,
    "thesis_bb_drift_pts": 78.0,
    "adaptive_long_bb_pos_max": 82.0,
    "adaptive_short_bb_pos_min": 88.0,
    "adaptive_strong_long_bb_pos_max": 26.0,
    "adaptive_strong_short_bb_pos_min": 82.0,
    "adaptive_min_macd_gap_ratio": 0.07,
    "adaptive_min_hist_ratio": 0.07,
    "adaptive_score_open_min": 0.8,
    "adaptive_score_open_min_extreme": 2.8,
    "adaptive_hist_sign_bonus": 0.48,
    "adaptive_hist_sign_penalty": 0.58,
    "adaptive_momentum_bonus": 0.22,
    "adaptive_momentum_penalty": 0.16,
    "bb_proximity_epsilon_pct": 0.11,
    "ignore_adaptive_4h_filter": True,
    "adaptive_requires_flat": False,
    "max_open_executors": 10,
    "min_tradeable_count": 1,
    "sl_cooldown_ticks": 2,
    "flip_cooldown_ticks": 8,
    "min_notional_quote": 200.0,
    "max_notional_quote": 1400.0,
    "min_conviction_mult": 0.7,
    "max_conviction_mult": 1.9,
    "strength_mult_per_unit": 0.42,
    "extreme_displacement_mult": 1.65,
    "activation_streak_mult_per_tick": 0.0,
    "thin_universe_mult": 0.96,
    "mature_tape_low_vol_mult": 0.99,
    "vol_inverse_sizing": True,
    "min_vol_mult": 0.58,
    "max_vol_mult": 1.05,
    "ref_volatility_pct": 0.75,
    "sl_vol_exponent": 1.05,
    "tp_vol_exponent": 1.35,
    "sl_min_pct": 1.4,
    "sl_max_pct": 7.0,
    "tp_min_pct": 3.5,
    "tp_max_pct": 11.0,
    "volatility_source": "bb_width",
}

_STRATEGY_TIMELINE_MEGA_BEST: dict[str, PresetValue] = {
    "enable_dynamic_sizing": True,
    "enable_dynamic_barriers": True,
    "ignore_journal_barriers_when_dynamic": False,
    "activation_ticks": 0,
    "sl_pct": 4.5,
    "tp_pct": 6.2,
    "thesis_decay_exit_ticks": 28,
    "thesis_bb_drift_pts": 18.0,
    "adaptive_long_bb_pos_max": 82.0,
    "adaptive_short_bb_pos_min": 88.0,
    "adaptive_strong_long_bb_pos_max": 38.0,
    "adaptive_strong_short_bb_pos_min": 95.0,
    "adaptive_min_macd_gap_ratio": 0.15,
    "adaptive_min_hist_ratio": 0.07,
    "adaptive_score_open_min": 1.8,
    "adaptive_score_open_min_extreme": 1.8,
    "adaptive_hist_sign_bonus": 0.48,
    "adaptive_hist_sign_penalty": 0.28,
    "adaptive_momentum_bonus": 0.38,
    "adaptive_momentum_penalty": 0.06,
    "bb_proximity_epsilon_pct": 0.06,
    "ignore_adaptive_4h_filter": True,
    "adaptive_requires_flat": False,
    "max_open_executors": 10,
    "min_tradeable_count": 1,
    "sl_cooldown_ticks": 2,
    "flip_cooldown_ticks": 8,
    "min_notional_quote": 200.0,
    "max_notional_quote": 1100.0,
    "min_conviction_mult": 0.7,
    "max_conviction_mult": 1.4,
    "strength_mult_per_unit": 0.16,
    "extreme_displacement_mult": 1.35,
    "activation_streak_mult_per_tick": 0.0,
    "thin_universe_mult": 0.82,
    "mature_tape_low_vol_mult": 1.12,
    "vol_inverse_sizing": True,
    "min_vol_mult": 0.82,
    "max_vol_mult": 1.75,
    "ref_volatility_pct": 0.68,
    "sl_vol_exponent": 1.05,
    "tp_vol_exponent": 0.75,
    "sl_min_pct": 2.2,
    "sl_max_pct": 7.5,
    "tp_min_pct": 5.5,
    "tp_max_pct": 11.0,
    "volatility_source": "bb_width",
}


def _build_timeline_driver() -> dict[str, PresetValue]:
    """Timeline driver with default snapshot dir and full snapshot date span."""
    driver = dict(_DRIVER_TIMELINE)
    try:
        from routines.macdbb_scanner_aggressive_hl_replay.replay_range import timeline_range_from_snapshots

        range_start, range_end = timeline_range_from_snapshots(
            DEFAULT_TIMELINE_SNAPSHOT_DIR
        )
        driver["range_start_utc"] = range_start
        driver["range_end_utc"] = range_end
    except ValueError:
        pass
    return driver


def _merge_preset_layers(*layers: dict[str, PresetValue]) -> dict[str, PresetValue]:
    merged: dict[str, PresetValue] = {}
    for layer in layers:
        merged.update(layer)
    return merged


# Dynamic replay presets: public session parity + private yaml winners.
PUBLIC_DYNAMIC_PRESET_OVERRIDES: dict[str, dict[str, PresetValue]] = {
    "hl_dynamic_session_parity": _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _DRIVER_SESSION,
        _STRATEGY_SESSION_MEGA_BEST,
        {
            "config_source": "session",
            "compare_journal_flags": False,
        },
    ),
    "hl_dynamic_timeline_public_fixture": _merge_preset_layers(
        _DYNAMIC_PRESET_INFRA,
        _build_timeline_driver(),
        _STRATEGY_TIMELINE_MEGA_BEST,
    ),
}


def _private_dynamic_overrides() -> dict[str, dict[str, PresetValue]]:
    raw = _private_preset_bundle().get("dynamic_preset_overrides") or {}
    return {
        str(name): {str(k): v for k, v in overrides.items()}
        for name, overrides in raw.items()
        if isinstance(overrides, dict)
    }


def get_dynamic_preset_overrides() -> dict[str, dict[str, PresetValue]]:
    return {
        **PUBLIC_DYNAMIC_PRESET_OVERRIDES,
        **_private_dynamic_overrides(),
    }


DYNAMIC_PRESET_OVERRIDES = get_dynamic_preset_overrides()


def known_preset_names() -> frozenset[str]:
    return frozenset({"custom", *get_dynamic_preset_overrides().keys()})


def backtest_preset_names() -> frozenset[str]:
    """Preset ids for replay/backtest UI when private yaml is present."""
    private = _private_preset_bundle().get("dynamic_preset_overrides") or {}
    if private:
        return frozenset({"custom", *private.keys()})
    return frozenset({"custom", *PUBLIC_DYNAMIC_PRESET_OVERRIDES.keys()})

ConfigT = TypeVar("ConfigT", bound=BaseModel)

# Form values that should win over named preset defaults when explicitly set.
USER_WINS_AFTER_PRESET_KEYS = frozenset(
    {
        "snapshot_dir",
        "hl_cache_dir",
        "range_start_utc",
        "range_end_utc",
    }
)


def _preserve_user_overrides(original: ConfigT, merged: ConfigT) -> ConfigT:
    """Re-apply non-empty user infra fields overwritten by preset merge."""
    if getattr(original, "preset", "custom") != getattr(merged, "preset", "custom"):
        return merged
    updates: dict[str, PresetValue] = {}
    for key in USER_WINS_AFTER_PRESET_KEYS:
        user_val = getattr(original, key, None)
        if user_val is None or user_val == "":
            continue
        if user_val != getattr(merged, key, None):
            updates[key] = user_val
    if not updates:
        return merged
    config_type = type(merged)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in updates.items() if key in allowed}
    return config_type(**{**merged.model_dump(), **filtered})


def resolve_timeline_range(config: ConfigT) -> ConfigT:
    """Fill missing timeline range from snapshots or scanner report index."""
    replay_mode = getattr(config, "replay_mode", None)
    if replay_mode != "timeline_backtest":
        return config
    start = getattr(config, "range_start_utc", None)
    end = getattr(config, "range_end_utc", None)
    if start and end:
        return config
    data_source = getattr(config, "data_source", None)
    snapshot_dir = getattr(config, "snapshot_dir", None)
    range_start: str | None = None
    range_end: str | None = None
    if data_source == "snapshots":
        try:
            from routines.macdbb_scanner_aggressive_hl_replay.replay_range import (
                timeline_range_from_snapshots,
            )

            range_start, range_end = timeline_range_from_snapshots(snapshot_dir)
        except ValueError:
            range_start = None
            range_end = None
    if not range_start or not range_end:
        from routines.macdbb_scanner_aggressive_hl_replay.replay_range import timeline_range_from_reports

        range_start, range_end = timeline_range_from_reports()
    updates: dict[str, PresetValue] = {}
    if not start:
        updates["range_start_utc"] = range_start
    if not end:
        updates["range_end_utc"] = range_end
    if not updates:
        return config
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in updates.items() if key in allowed}
    return config_type(**{**config.model_dump(), **filtered})


def resolve_config_with_preset(config: ConfigT) -> ConfigT:
    """Apply a named preset profile on top of the submitted config.

    When preset is not ``custom``, keys defined in PRESET_OVERRIDES always win
    over form/default values (the UI sends every field, so exclude_unset would
    not help). Fields outside the preset dict — e.g. session_nums, sl_pct —
    still come from the form. Infra paths/ranges in USER_WINS_AFTER_PRESET_KEYS
    are preserved when the user explicitly sets them.
    """
    preset = getattr(config, "preset", "custom")
    if preset == "custom":
        return resolve_timeline_range(config)
    overrides = PRESET_OVERRIDES.get(preset) or get_dynamic_preset_overrides().get(preset)
    if not overrides:
        return resolve_timeline_range(config)
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in overrides.items() if key in allowed}
    merged = config_type(**{**config.model_dump(), **filtered, "preset": preset})
    merged = _preserve_user_overrides(config, merged)
    return resolve_timeline_range(merged)


def strategy_params_from_preset(
    preset: str,
    *,
    frequency_sec: int = 1800,
) -> dict[str, Any]:
    """Map a named replay preset to live agent strategy_params."""
    if preset == "custom":
        return {}
    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
    from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
        replay_config_to_agent_strategy_params,
    )

    config = resolve_config_with_preset(
        DynamicStrategyReplayConfig(preset=preset, frequency_sec=frequency_sec)
    )
    return replay_config_to_agent_strategy_params(config, frequency_sec=frequency_sec)


def agent_preset_catalog() -> list[dict[str, str]]:
    """Preset options for live agent start / defaults UI."""
    labels = preset_labels()
    names = agent_strategy_preset_names()
    if names:
        catalog_names = names
    else:
        catalog_names = tuple(
            name
            for name in PUBLIC_DYNAMIC_PRESET_OVERRIDES
            if name != "hl_dynamic_timeline_public_fixture"
        )
    return [
        {"id": "custom", "label": labels["custom"]},
        *[
            {"id": name, "label": labels[name]}
            for name in catalog_names
        ],
    ]
