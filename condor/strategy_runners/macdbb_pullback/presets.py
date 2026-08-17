"""Presets for macdbb_pullback_hl backtests and live defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from condor.strategy_runners.macdbb.presets import (
    PRESET_CAPITAL_KEYS,
    strip_preset_capital_keys,
)
from condor.strategy_runners.macdbb_pullback.params import default_strategy_params

AGENT_SLUG = "macdbb_pullback_hl"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESETS_YAML = _REPO_ROOT / "strategies" / AGENT_SLUG / "presets.yaml"


def _resolve_presets_yaml() -> Path | None:
    return _PRESETS_YAML if _PRESETS_YAML.is_file() else None

DEFAULT_TIMELINE_PRESET = "pullback_timeline_v1"
DEFAULT_TIMELINE_60S_PRESET = "pullback_timeline_v1_60s"
# Month sweep winner (Binance 60s Jul–Aug 2026): thesis-decay @ 2h.
DEFAULT_WINNER_PRESET = "pullback_decay_2h_60s"
# Backtest *run* defaults (not part of the strategy preset). Live is HL.
DEFAULT_HL_60S_SNAPSHOT_DIR = "data/replay_snapshots_hl_60s"
DEFAULT_HL_CANDLE_CACHE_DIR = "data/hl_candles"
# Kept for Binance timeline experiments / import compatibility.
DEFAULT_BINANCE_60S_SNAPSHOT_DIR = "data/replay_snapshots_binance_60s"
DEFAULT_BINANCE_1800S_SNAPSHOT_DIR = "data/replay_snapshots_binance_1y"
DEFAULT_BINANCE_CANDLE_CACHE_DIR = "data/binance_candles"

_PUBLIC_PRESET_LABELS: dict[str, str] = {
    "custom": "Custom",
    DEFAULT_TIMELINE_PRESET: "Pullback timeline v1 (1800s)",
    DEFAULT_TIMELINE_60S_PRESET: "Pullback timeline v1 (60s)",
    DEFAULT_WINNER_PRESET: "Pullback decay 2h (60s)",
}

_bundle_cache: dict[str, Any] | None = None
_bundle_mtime: float | None = None


def _load_private_preset_bundle() -> dict[str, Any]:
    path = _resolve_presets_yaml()
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _private_preset_bundle() -> dict[str, Any]:
    global _bundle_cache, _bundle_mtime
    path = _resolve_presets_yaml()
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
    return {**_PUBLIC_PRESET_LABELS, **(bundle.get("labels") or {})}


def agent_strategy_preset_names() -> tuple[str, ...]:
    bundle = _private_preset_bundle()
    names = bundle.get("agent_strategy_preset_names")
    if names:
        return tuple(str(x) for x in names)
    return (
        DEFAULT_TIMELINE_PRESET,
        DEFAULT_TIMELINE_60S_PRESET,
        DEFAULT_WINNER_PRESET,
    )


def default_agent_strategy_preset() -> str:
    bundle = _private_preset_bundle()
    return str(
        bundle.get("default_agent_strategy_preset")
        or bundle.get("current_winner_preset")
        or DEFAULT_WINNER_PRESET
    )


def agent_preset_catalog() -> list[dict[str, str]]:
    """Preset options for Strategies UI / start defaults."""
    labels = preset_labels()
    return [
        {"id": "custom", "label": labels.get("custom", "Custom")},
        *[
            {"id": name, "label": labels.get(name, name)}
            for name in agent_strategy_preset_names()
        ],
    ]


# Kept for import compatibility with older call sites.
PRESET_LABELS = _PUBLIC_PRESET_LABELS

_BASE_STRATEGY = strip_preset_capital_keys(default_strategy_params())

_DECAY_2H_STRATEGY: dict[str, Any] = {
    **_BASE_STRATEGY,
    "enable_flip_exit": False,
    "enable_thesis_decay_exit": True,
    "thesis_decay_exit_hours": 2.0,
    "thesis_bb_drift_pts": 20.0,
    "flip_confirm_ticks": 2,
    "flip_cooldown_hours": 1.5,
}

# Strategy-only. Candle venue, snapshot dir, and per-entry notional are
# backtest/live run fields — not part of the named preset.
PRESET_OVERRIDES: dict[str, dict[str, Any]] = {
    DEFAULT_TIMELINE_PRESET: {
        "frequency_sec": 1800,
        "time_window_min": 15,
        "strategy_params": dict(_BASE_STRATEGY),
        **{k: v for k, v in _BASE_STRATEGY.items()},
    },
    DEFAULT_TIMELINE_60S_PRESET: {
        "frequency_sec": 60,
        "time_window_min": 1,
        "strategy_params": dict(_BASE_STRATEGY),
        **{k: v for k, v in _BASE_STRATEGY.items()},
    },
    DEFAULT_WINNER_PRESET: {
        "frequency_sec": 60,
        "time_window_min": 1,
        "strategy_params": dict(_DECAY_2H_STRATEGY),
        **{k: v for k, v in _DECAY_2H_STRATEGY.items()},
    },
}


def known_preset_names() -> set[str]:
    return set(PRESET_OVERRIDES) | {"custom"}


def strategy_params_from_preset(
    preset: str,
    *,
    frequency_sec: int | None = None,
) -> dict[str, Any]:
    overrides = PRESET_OVERRIDES.get(preset) or {}
    params = strip_preset_capital_keys(
        dict(overrides.get("strategy_params") or default_strategy_params())
    )
    freq = int(frequency_sec or overrides.get("frequency_sec") or 60)
    params["pullback_timeout_ticks"] = max(
        1, int(round(float(params.get("pullback_timeout_hours") or 12) * 3600 / freq))
    )
    params["sl_symbol_cooldown_ticks"] = max(
        1, int(round(float(params.get("sl_symbol_cooldown_hours") or 5) * 3600 / freq))
    )
    params["thesis_decay_exit_ticks"] = max(
        0,
        int(round(float(params.get("thesis_decay_exit_hours") or 0) * 3600 / freq)),
    )
    params["flip_cooldown_ticks"] = max(
        0,
        int(round(float(params.get("flip_cooldown_hours") or 0) * 3600 / freq)),
    )
    return params


def resolve_config_dict(preset: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = strip_preset_capital_keys(
        dict(PRESET_OVERRIDES.get(preset) or PRESET_OVERRIDES[DEFAULT_WINNER_PRESET])
    )
    if overrides:
        # Caller may still supply budget/sizing for a single run; keep those.
        base.update({k: v for k, v in overrides.items() if v is not None})
    freq = int(base.get("frequency_sec") or 60)
    base["strategy_params"] = strategy_params_from_preset(preset, frequency_sec=freq)
    for key, value in base["strategy_params"].items():
        if key in PRESET_CAPITAL_KEYS:
            continue
        base.setdefault(key, value)
    base["preset"] = preset
    base["strategy_slug"] = AGENT_SLUG
    return base
