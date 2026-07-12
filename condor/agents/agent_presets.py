"""Apply agent-owned strategy presets at session start."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

AGENT_PRESET_LOADERS: dict[str, str] = {
    "macdbb_scanner_aggressive_hl": "agents.macdbb_scanner_aggressive_hl.presets",
}


def get_agent_strategy_preset_catalog(slug: str) -> list[dict[str, str]] | None:
    module_path = AGENT_PRESET_LOADERS.get(slug)
    if not module_path:
        return None
    import importlib

    module = importlib.import_module(module_path)
    catalog_fn = getattr(module, "agent_preset_catalog", None)
    if not callable(catalog_fn):
        return None
    return catalog_fn()


def get_default_agent_strategy_preset(slug: str) -> str | None:
    module_path = AGENT_PRESET_LOADERS.get(slug)
    if not module_path:
        return None
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, "DEFAULT_AGENT_STRATEGY_PRESET", None)


def strategy_params_for_preset(
    slug: str,
    preset: str,
    *,
    frequency_sec: int,
) -> dict[str, Any] | None:
    if not preset or preset == "custom":
        return None
    module_path = AGENT_PRESET_LOADERS.get(slug)
    if not module_path:
        return None
    import importlib

    module = importlib.import_module(module_path)
    fn = getattr(module, "strategy_params_from_preset", None)
    if not callable(fn):
        return None
    return fn(preset, frequency_sec=frequency_sec)


def apply_agent_strategy_preset(
    slug: str,
    config: dict[str, Any],
    *,
    preset: str | None = None,
) -> dict[str, Any]:
    """Overlay preset strategy_params (and related risk limits) onto config."""
    result = dict(config)
    selected = (preset or result.get("strategy_preset") or "custom").strip()
    result["strategy_preset"] = selected
    if selected == "custom":
        return result

    freq = int(result.get("frequency_sec") or 60)
    try:
        preset_params = strategy_params_for_preset(slug, selected, frequency_sec=freq)
    except Exception:
        log.exception("strategy_params_for_preset(%s, %s) failed", slug, selected)
        preset_params = None
    if not preset_params:
        return result

    existing = result.get("strategy_params")
    merged_params = dict(existing) if isinstance(existing, dict) else {}
    merged_params.update(preset_params)
    result["strategy_params"] = merged_params

    from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
    from agents.macdbb_scanner_aggressive_hl.presets import resolve_config_with_preset

    try:
        replay_cfg = resolve_config_with_preset(
            DynamicStrategyReplayConfig(preset=selected, frequency_sec=freq)
        )
        risk = dict(result.get("risk_limits") or {})
        risk["max_open_executors"] = int(replay_cfg.max_open_executors)
        result["risk_limits"] = risk
    except Exception:
        log.exception("resolve_config_with_preset(%s) failed for live config", selected)

    return result
