"""Resolve private strategy assets (agent.md, presets.yaml) across public + submodule paths."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRADING_AGENTS_DIR = REPO_ROOT / "trading_agents"


def strategies_dir() -> Path:
    override = os.environ.get("CONDOR_STRATEGIES_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "strategies"


def agent_dir(slug: str) -> Path:
    """Public agent folder (routines, sessions, dry runs)."""
    return TRADING_AGENTS_DIR / slug


def private_strategy_dir(slug: str) -> Path:
    """Private strategy folder inside the strategies submodule (or override)."""
    return strategies_dir() / slug


def resolve_agent_md(slug: str) -> Path | None:
    """Return the active private agent.md path, or None if missing."""
    for candidate in (
        private_strategy_dir(slug) / "agent.md",
        agent_dir(slug) / "agent.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def resolve_agent_md_for_read(slug: str) -> Path | None:
    """Read path including public example template for fresh clones."""
    active = resolve_agent_md(slug)
    if active is not None:
        return active
    example = agent_dir(slug) / "agent.example.md"
    if example.is_file():
        return example
    return None


def agent_md_write_path(slug: str) -> Path:
    """Preferred write target for agent.md (private submodule, then local override)."""
    private_dir = private_strategy_dir(slug)
    if private_dir.exists() or strategies_dir().is_dir():
        return private_dir / "agent.md"
    return agent_dir(slug) / "agent.md"


def resolve_presets_yaml(slug: str) -> Path | None:
    """Return presets yaml from submodule or gitignored local override."""
    for candidate in (
        private_strategy_dir(slug) / "presets.yaml",
        agent_dir(slug) / "presets.private.yaml",
    ):
        if candidate.is_file():
            return candidate
    return None


def iter_strategy_slugs() -> list[str]:
    """Union of slug directories under trading_agents/ and strategies/."""
    slugs: set[str] = set()
    if TRADING_AGENTS_DIR.is_dir():
        for path in TRADING_AGENTS_DIR.iterdir():
            if path.is_dir() and not path.name.startswith("_") and path.name != "strategies":
                slugs.add(path.name)
    strategies_root = strategies_dir()
    if strategies_root.is_dir():
        for path in strategies_root.iterdir():
            if path.is_dir() and not path.name.startswith("."):
                slugs.add(path.name)
    return sorted(slugs)
