from __future__ import annotations

from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from condor.strategy_runners.macdbb.paths import resolve_presets_yaml

PresetValue = float | int | bool | str | None

AGENT_SLUG = "macdbb_scanner_aggressive_hl"

# Budget / per-entry notional clamps belong on the deployment (strategy.yaml /
# session), not in named strategy presets.
PRESET_CAPITAL_KEYS = frozenset(
    {
        "formal_notional_quote",
        "total_amount_quote",
        "min_notional_quote",
        "max_notional_quote",
    }
)


def strip_preset_capital_keys(values: dict[str, Any]) -> dict[str, Any]:
    """Drop budget/sizing-clamp keys so presets stay strategy-logic-only."""
    return {key: value for key, value in values.items() if key not in PRESET_CAPITAL_KEYS}

PUBLIC_PRESET_LABELS: dict[str, str] = {
    "custom": "Custom",
    "hl_dynamic_session_parity": "Session parity",
    "hl_dynamic_timeline_public_fixture": "Public timeline test fixture",
}

REFINE_LEAD_013_PRESET = "hl_dynamic_timeline_refine_lead_013"
REFINE_LEAD_013_60S_PRESET = "hl_dynamic_timeline_refine_lead_013_60s"


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


def invalidate_preset_cache() -> None:
    """Clear mtime cache after presets.yaml is written externally."""
    global _bundle_cache, _bundle_mtime
    _bundle_cache = None
    _bundle_mtime = None


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
    labels = {**PUBLIC_PRESET_LABELS, **(bundle.get("labels") or {})}
    labels.setdefault(
        REFINE_LEAD_013_60S_PRESET,
        "Refine lead_013 winner (60s ticks)",
    )
    return labels


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


PRESET_LABELS = preset_labels()
AGENT_STRATEGY_PRESET_NAMES = agent_strategy_preset_names()
DEFAULT_AGENT_STRATEGY_PRESET = default_agent_strategy_preset()

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

_DYNAMIC_PRESET_INFRA: dict[str, PresetValue] = {
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

DEFAULT_TIMELINE_SNAPSHOT_DIR = "data/replay_snapshots_binance_1y"
# Parity / live-equivalent 60s validation uses HL volume ranking + HL candles.
DEFAULT_60S_TIMELINE_SNAPSHOT_DIR = "data/replay_snapshots_hl_60s"
# Legacy Binance 60s store retained for old sweeps.
LEGACY_BINANCE_60S_TIMELINE_SNAPSHOT_DIR = "data/replay_snapshots_binance_60s"

# Snapshot stores are frequency-specific; do not mix grids in one directory.
SNAPSHOT_DIR_BY_FREQUENCY: dict[int, str] = {
    1800: DEFAULT_TIMELINE_SNAPSHOT_DIR,
    60: DEFAULT_60S_TIMELINE_SNAPSHOT_DIR,
}

# Replay tick fields that represent wall-clock duration (calibrated at preset frequency_sec).
DURATION_TICK_FIELDS: tuple[str, ...] = (
    "activation_ticks",
    "thesis_decay_exit_ticks",
    "sl_cooldown_ticks",
    "flip_cooldown_ticks",
)

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
        str(name): strip_preset_capital_keys(
            {str(k): v for k, v in overrides.items()}
        )
        for name, overrides in raw.items()
        if isinstance(overrides, dict)
    }


def rescale_duration_tick_value(
    ticks: int | float,
    from_frequency_sec: int,
    to_frequency_sec: int,
) -> int:
    """Keep wall-clock duration when moving tick-denominated params across frequencies."""
    from condor.agents.strategy_configs.registry import duration_to_ticks, ticks_to_hours

    from_freq = max(1, int(from_frequency_sec))
    to_freq = max(1, int(to_frequency_sec))
    if from_freq == to_freq:
        return int(ticks)
    hours = ticks_to_hours(ticks, from_freq)
    return duration_to_ticks(hours, to_freq)


def rescale_duration_tick_fields(
    values: dict[str, Any],
    from_frequency_sec: int,
    to_frequency_sec: int,
) -> dict[str, Any]:
    """Return a shallow copy with DURATION_TICK_FIELDS rescaled to ``to_frequency_sec``."""
    from_freq = max(1, int(from_frequency_sec))
    to_freq = max(1, int(to_frequency_sec))
    if from_freq == to_freq:
        return dict(values)
    out = dict(values)
    for key in DURATION_TICK_FIELDS:
        if key not in out or out[key] is None:
            continue
        try:
            out[key] = rescale_duration_tick_value(out[key], from_freq, to_freq)
        except (TypeError, ValueError):
            continue
    return out


def _with_run_frequency(
    overrides: dict[str, PresetValue],
    frequency_sec: int,
) -> dict[str, PresetValue]:
    """Clone a preset for a different tick frequency (duration-preserving)."""
    calibration_freq = max(1, int(overrides.get("frequency_sec") or 1800))
    run_freq = max(1, int(frequency_sec))
    out = rescale_duration_tick_fields(overrides, calibration_freq, run_freq)
    out["frequency_sec"] = run_freq
    calibration_dir = SNAPSHOT_DIR_BY_FREQUENCY.get(calibration_freq)
    run_dir = SNAPSHOT_DIR_BY_FREQUENCY.get(run_freq)
    if (
        run_dir
        and out.get("snapshot_dir") in (None, calibration_dir, DEFAULT_TIMELINE_SNAPSHOT_DIR)
    ):
        out["snapshot_dir"] = run_dir
    if run_freq <= 60:
        out["time_window_min"] = min(int(out.get("time_window_min") or 15), 1)
        # Parity 60s store is HL-aligned with live-equivalent queue semantics.
        if out.get("snapshot_dir") == DEFAULT_60S_TIMELINE_SNAPSHOT_DIR:
            out["candle_source"] = "hyperliquid"
            out.setdefault("hl_cache_dir", "data/hl_candles")
            out["live_equivalent_queue"] = True
            out["price_source"] = "auto"
    return out


def get_dynamic_preset_overrides() -> dict[str, dict[str, PresetValue]]:
    overrides = {
        **PUBLIC_DYNAMIC_PRESET_OVERRIDES,
        **_private_dynamic_overrides(),
    }
    lead_013 = overrides.get(REFINE_LEAD_013_PRESET)
    if lead_013 is not None and REFINE_LEAD_013_60S_PRESET not in overrides:
        overrides[REFINE_LEAD_013_60S_PRESET] = _with_run_frequency(lead_013, 60)
    return overrides


DYNAMIC_PRESET_OVERRIDES = get_dynamic_preset_overrides()


def known_preset_names() -> frozenset[str]:
    return frozenset({"custom", *get_dynamic_preset_overrides().keys()})


def backtest_preset_names() -> frozenset[str]:
    """Preset ids for replay/backtest UI when private yaml is present."""
    private = _private_preset_bundle().get("dynamic_preset_overrides") or {}
    if private:
        names = {"custom", *private.keys()}
        # Generated 60s alias (not necessarily written into private yaml).
        if REFINE_LEAD_013_PRESET in private:
            names.add(REFINE_LEAD_013_60S_PRESET)
        return frozenset(names)
    return frozenset({"custom", *PUBLIC_DYNAMIC_PRESET_OVERRIDES.keys()})


ConfigT = TypeVar("ConfigT", bound=BaseModel)

USER_WINS_AFTER_PRESET_KEYS = frozenset(
    {
        "snapshot_dir",
        "hl_cache_dir",
        "range_start_utc",
        "range_end_utc",
    }
)

# frequency_sec is handled separately: only win when the caller explicitly set it
# (Pydantic model_fields_set). Otherwise the model default of 1800 would clobber
# 60s presets constructed as DynamicStrategyReplayConfig(preset="…_60s").
_EXPLICIT_USER_WIN_KEYS = frozenset({"frequency_sec"})


def _preserve_user_overrides(original: ConfigT, merged: ConfigT) -> ConfigT:
    """Re-apply non-empty user infra fields overwritten by preset merge."""
    if getattr(original, "preset", "custom") != getattr(merged, "preset", "custom"):
        return merged
    updates: dict[str, PresetValue] = {}
    fields_set = getattr(original, "model_fields_set", frozenset())
    for key in USER_WINS_AFTER_PRESET_KEYS:
        user_val = getattr(original, key, None)
        if user_val is None or user_val == "":
            continue
        if user_val != getattr(merged, key, None):
            updates[key] = user_val
    for key in _EXPLICIT_USER_WIN_KEYS:
        if key not in fields_set:
            continue
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


def _retarget_config_frequency(
    merged: ConfigT,
    *,
    calibration_frequency_sec: int,
    run_frequency_sec: int,
) -> ConfigT:
    """Rescale duration ticks and retarget known snapshot dirs for a new tick clock."""
    cal_freq = max(1, int(calibration_frequency_sec))
    run_freq = max(1, int(run_frequency_sec))
    if cal_freq == run_freq:
        return merged

    payload = merged.model_dump()
    payload.update(
        rescale_duration_tick_fields(payload, cal_freq, run_freq)
    )
    payload["frequency_sec"] = run_freq

    cal_dir = SNAPSHOT_DIR_BY_FREQUENCY.get(cal_freq)
    run_dir = SNAPSHOT_DIR_BY_FREQUENCY.get(run_freq)
    if run_dir and payload.get("snapshot_dir") in (
        None,
        cal_dir,
        DEFAULT_TIMELINE_SNAPSHOT_DIR,
    ):
        payload["snapshot_dir"] = run_dir
    if run_freq <= 60:
        payload["time_window_min"] = min(int(payload.get("time_window_min") or 15), 1)

    config_type = type(merged)
    allowed = set(config_type.model_fields)
    filtered = {key: value for key, value in payload.items() if key in allowed}
    return config_type(**filtered)


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

        try:
            range_start, range_end = timeline_range_from_reports()
        except ValueError:
            range_start = None
            range_end = None
    if not range_start or not range_end:
        return config
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

    Preset ``*_ticks`` duration fields are calibrated at the preset's
    ``frequency_sec``. If the caller overrides ``frequency_sec`` (e.g. 60 vs
    1800), those tick fields are rescaled to preserve wall-clock duration and
    known snapshot dirs are retargeted.
    """
    preset = getattr(config, "preset", "custom")
    if preset == "custom":
        return resolve_timeline_range(config)
    overrides = PRESET_OVERRIDES.get(preset) or get_dynamic_preset_overrides().get(preset)
    if not overrides:
        return resolve_timeline_range(config)
    calibration_freq = max(
        1,
        int(overrides.get("frequency_sec") or getattr(config, "frequency_sec", 1800) or 1800),
    )
    config_type = type(config)
    allowed = set(config_type.model_fields)
    filtered = strip_preset_capital_keys(
        {key: value for key, value in overrides.items() if key in allowed}
    )
    merged = config_type(**{**config.model_dump(), **filtered, "preset": preset})
    merged = _preserve_user_overrides(config, merged)
    run_freq = max(1, int(getattr(merged, "frequency_sec", calibration_freq) or calibration_freq))
    if run_freq != calibration_freq:
        merged = _retarget_config_frequency(
            merged,
            calibration_frequency_sec=calibration_freq,
            run_frequency_sec=run_freq,
        )
    return resolve_timeline_range(merged)


def strategy_params_from_preset(
    preset: str,
    *,
    frequency_sec: int | None = None,
) -> dict[str, Any]:
    """Map a named replay preset to live agent strategy_params (duration hours).

    When ``frequency_sec`` is omitted, the preset's own tick frequency is used.
    Pass an explicit value to retarget duration ticks (e.g. live 60s from an
    1800s-calibrated sweep preset).
    """
    if preset == "custom":
        return {}
    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
    from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import (
        replay_config_to_agent_strategy_params,
    )

    payload: dict[str, Any] = {"preset": preset}
    if frequency_sec is not None:
        payload["frequency_sec"] = int(frequency_sec)
    config = resolve_config_with_preset(DynamicStrategyReplayConfig(**payload))
    # After resolve, tick fields match config.frequency_sec wall-clock.
    return strip_preset_capital_keys(
        replay_config_to_agent_strategy_params(
            config, frequency_sec=int(config.frequency_sec)
        )
    )


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
