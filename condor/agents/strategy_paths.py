"""Resolve private strategy assets (agent.md, presets.yaml) across public + submodule paths."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


def strategies_dir() -> Path:
    override = os.environ.get("CONDOR_STRATEGIES_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "strategies"


def agent_dir(slug: str) -> Path:
    """Public agent folder (routines, strategies/, presets)."""
    return AGENTS_DIR / slug


def private_strategy_dir(slug: str) -> Path:
    """Private strategy folder inside the strategies submodule (or override)."""
    return strategies_dir() / slug


def resolve_agent_md(slug: str) -> Path | None:
    """Return the active private agent.md path, or None if missing."""
    candidate = private_strategy_dir(slug) / "agent.md"
    return candidate if candidate.is_file() else None


def resolve_agent_md_for_read(slug: str) -> Path | None:
    """Read path including public example template for fresh clones."""
    active = resolve_agent_md(slug)
    if active is not None:
        return active
    public_strategy = agent_dir(slug) / "strategies" / slug / "strategy.md"
    return public_strategy if public_strategy.is_file() else None


def agent_md_write_path(slug: str) -> Path:
    """Preferred write target for agent.md (private submodule, then local override)."""
    private_dir = private_strategy_dir(slug)
    if private_dir.exists() or strategies_dir().is_dir():
        return private_dir / "agent.md"
    return agent_dir(slug) / "strategies" / slug / "strategy.md"


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
    """Union of slug directories under agents/ and strategies/."""
    slugs: set[str] = set()
    if AGENTS_DIR.is_dir():
        for path in AGENTS_DIR.iterdir():
            if path.is_dir() and not path.name.startswith("_") and path.name != "strategies":
                slugs.add(path.name)
    strategies_root = strategies_dir()
    if strategies_root.is_dir():
        for path in strategies_root.iterdir():
            if path.is_dir() and not path.name.startswith("."):
                slugs.add(path.name)
    return sorted(slugs)


def resolve_strategy_data_dir(agent_slug: str, sslug: str) -> Path:
    """Session/journal root under the canonical agents/ tree."""
    return REPO_ROOT / "agents" / agent_slug / "strategies" / sslug


def _materialize_path(path: Path, *, is_dir: bool) -> None:
    """Replace broken symlinks with a real directory or file."""
    if path.is_symlink() and not path.exists():
        path.unlink()
    if is_dir:
        path.mkdir(parents=True, exist_ok=True)
        return
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Learnings\n\n## Execution Notes\n\n")


def ensure_strategy_data_dir(agent_slug: str, sslug: str) -> Path:
    """Ensure strategy operational dirs exist under agents/ (never via symlinks)."""
    strategy_dir = resolve_strategy_data_dir(agent_slug, sslug)
    strategy_dir.mkdir(parents=True, exist_ok=True)
    _materialize_path(strategy_dir / "sessions", is_dir=True)
    _materialize_path(strategy_dir / "learnings.md", is_dir=False)
    return strategy_dir
