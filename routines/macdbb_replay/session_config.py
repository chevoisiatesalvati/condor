"""Load session config.yml into replay-compatible DynamicStrategyReplayConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from condor.trading_agent.policies.macdbb_dynamic import live_policy_config_from_params
from condor.trading_agent.strategy_configs.registry import resolve_effective_strategy_params
from routines.macdbb_replay.models import DynamicStrategyReplayConfig

# Replay driver fields must not be overwritten by session/config.yml defaults.
_REPLAY_DRIVER_FIELDS = (
    "data_source",
    "replay_mode",
    "time_window_min",
    "session_nums",
    "range_start_utc",
    "range_end_utc",
    "tick_schedule",
    "write_csv",
    "use_journal_barriers",
    "strategy_slug",
)


def load_session_yaml(session_dir: Path) -> dict[str, Any]:
    config_path = session_dir / "config.yml"
    if not config_path.is_file():
        return {}
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def resolve_session_strategy_params(
    strategy_slug: str,
    session_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (raw config.yml dict, resolved strategy_params with effective ticks)."""
    raw = load_session_yaml(session_dir)
    frequency_sec = max(1, int(raw.get("frequency_sec") or 1800))
    params = resolve_effective_strategy_params(
        strategy_slug,
        raw.get("strategy_params"),
        frequency_sec,
    )
    return raw, params


def replay_config_from_session(
    session_dir: Path,
    strategy_slug: str,
    *,
    base: DynamicStrategyReplayConfig | None = None,
) -> tuple[DynamicStrategyReplayConfig, dict[str, Any]]:
    """Build replay config + resolved strategy_params from session/config.yml."""
    raw, params = resolve_session_strategy_params(strategy_slug, session_dir)
    formal_notional = float(raw.get("total_amount_quote") or 500.0)
    config = live_policy_config_from_params(
        params,
        formal_notional_quote=formal_notional,
    )

    risk = raw.get("risk_limits") or {}
    frequency_sec = max(1, int(raw.get("frequency_sec") or 1800))
    updates: dict[str, Any] = {
        "preset": "custom",
        "config_source": "session",
        "max_open_executors": int(
            risk.get("max_open_executors", config.max_open_executors)
        ),
        "frequency_sec": frequency_sec,
    }

    if params.get("thesis_decay_exit_ticks") is not None:
        updates["thesis_decay_exit_ticks"] = int(params["thesis_decay_exit_ticks"])
    if params.get("sl_symbol_cooldown_ticks") is not None:
        updates["sl_cooldown_ticks"] = int(params["sl_symbol_cooldown_ticks"])
    if params.get("flip_cooldown_ticks") is not None:
        updates["flip_cooldown_ticks"] = int(params["flip_cooldown_ticks"])
    if params.get("adaptive_skip_4h_filter") is not None:
        updates["ignore_adaptive_4h_filter"] = bool(params["adaptive_skip_4h_filter"])
    if params.get("adaptive_requires_flat") is not None:
        updates["adaptive_requires_flat"] = bool(params["adaptive_requires_flat"])
    if params.get("min_tradeable_for_adaptive") is not None:
        updates["min_tradeable_count"] = int(params["min_tradeable_for_adaptive"])

    merged: dict[str, Any] = config.model_dump()
    replay_driver: dict[str, Any] = {}
    if base is not None:
        base_dump = base.model_dump()
        for key in _REPLAY_DRIVER_FIELDS:
            if key in base_dump:
                replay_driver[key] = base_dump[key]
        merged = {**base_dump, **merged}
    merged.update(updates)
    merged.update(replay_driver)
    return DynamicStrategyReplayConfig(**merged), params


def session_has_dynamic_policy(params: dict[str, Any]) -> bool:
    """True when session config.yml explicitly enabled dynamic sizing or barriers."""
    return bool(params.get("enable_dynamic_sizing")) or bool(
        params.get("enable_dynamic_barriers")
    )


def apply_policy_override(
    config: DynamicStrategyReplayConfig,
    *,
    policy_mode: str,
) -> DynamicStrategyReplayConfig:
    """Force fixed or dynamic entry policy regardless of session snapshot."""
    if policy_mode == "session":
        return config
    if policy_mode == "fixed":
        return config.model_copy(
            update={
                "enable_dynamic_sizing": False,
                "enable_dynamic_barriers": False,
            }
        )
    if policy_mode == "dynamic":
        return config.model_copy(
            update={
                "enable_dynamic_sizing": True,
                "enable_dynamic_barriers": True,
            }
        )
    raise ValueError(f"unknown policy_mode: {policy_mode}")
