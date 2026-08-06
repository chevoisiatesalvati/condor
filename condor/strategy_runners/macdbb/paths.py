"""MACDBB-owned filesystem roots (not under agents/)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STRATEGIES_SUBMODULE = REPO_ROOT / "strategies"
RUNS_ROOT = REPO_ROOT / "data" / "strategy_runs"

MACDBB_SLUG = "macdbb_scanner_aggressive_hl"
DEFAULT_TICK_LOG_RETENTION_DAYS = 7


def runs_dir(slug: str = MACDBB_SLUG) -> Path:
    path = RUNS_ROOT / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_dir(slug: str = MACDBB_SLUG) -> Path:
    path = runs_dir(slug) / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ticks_dir(slug: str = MACDBB_SLUG) -> Path:
    path = runs_dir(slug) / "ticks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def private_strategy_dir(slug: str = MACDBB_SLUG) -> Path:
    return STRATEGIES_SUBMODULE / slug


def resolve_presets_yaml(slug: str = MACDBB_SLUG) -> Path | None:
    """Prefer private submodule presets.yaml."""
    private = private_strategy_dir(slug) / "presets.yaml"
    if private.is_file():
        return private
    return None


def resolve_strategy_defaults_path(slug: str = MACDBB_SLUG) -> Path:
    """YAML file for persisted Strategies defaults (not Agents StrategyStore)."""
    private = private_strategy_dir(slug)
    strategy_yaml = private / "strategy.yaml"
    if strategy_yaml.is_file() or private.is_dir():
        return strategy_yaml
    # Fallback write location if submodule missing
    fallback = runs_dir(slug) / "strategy.yaml"
    return fallback
