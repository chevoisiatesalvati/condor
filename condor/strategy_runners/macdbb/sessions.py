"""Session / journal roots under data/strategy_runs (not agents/)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from condor.agents.journal import JournalManager, next_session_number
from condor.strategy_runners.macdbb.paths import (
    MACDBB_SLUG,
    REPO_ROOT,
    resolve_strategy_defaults_path,
    runs_dir,
    sessions_dir,
)

# Legacy Agents location kept read-only for historical journals.
_LEGACY_AGENTS_ROOT = REPO_ROOT / "agents"


def strategy_runs_root(slug: str = MACDBB_SLUG) -> Path:
    return runs_dir(slug)


def create_session(
    *,
    slug: str,
    strategy_name: str,
    strategy_description: str,
    config: dict[str, Any],
    run_key: str,
) -> tuple[int, Path, JournalManager]:
    """Allocate next session under data/strategy_runs and open a journal."""
    root = strategy_runs_root(slug)
    root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(parents=True, exist_ok=True)

    session_num = next_session_number(root)
    # Also skip numbers already used under legacy agents tree so ids stay unique.
    legacy_root = _legacy_strategy_data_dir(slug)
    if legacy_root is not None:
        legacy_next = next_session_number(legacy_root)
        session_num = max(session_num, legacy_next)

    session_dir = sessions_dir(slug) / f"session_{session_num}"
    session_dir.mkdir(parents=True, exist_ok=True)

    from condor.agents.config import save_full_config

    save_full_config(session_dir, config)

    agent_id = f"{run_key}_{session_num}"
    journal = JournalManager(
        agent_id,
        strategy_name=strategy_name,
        strategy_description=strategy_description,
        session_dir=session_dir,
        agent_dir=root,
    )
    return session_num, session_dir, journal


def _legacy_strategy_data_dir(slug: str) -> Path | None:
    candidate = (
        _LEGACY_AGENTS_ROOT
        / slug
        / "strategies"
        / slug
    )
    if candidate.is_dir():
        return candidate
    alt = _LEGACY_AGENTS_ROOT / slug
    if (alt / "sessions").is_dir() or alt.is_dir():
        return alt
    return None


def list_session_dirs(slug: str = MACDBB_SLUG) -> list[Path]:
    """New runs first, then legacy agents sessions (read-only history)."""
    seen: set[int] = set()
    out: list[Path] = []
    for root in (sessions_dir(slug),):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), reverse=True):
            if not path.is_dir() or not path.name.startswith("session_"):
                continue
            try:
                num = int(path.name.split("_", 1)[1])
            except ValueError:
                continue
            if num in seen:
                continue
            seen.add(num)
            out.append(path)

    legacy = _legacy_strategy_data_dir(slug)
    if legacy is not None:
        legacy_sessions = legacy / "sessions"
        if legacy_sessions.is_dir():
            for path in sorted(legacy_sessions.iterdir(), reverse=True):
                if not path.is_dir() or not path.name.startswith("session_"):
                    continue
                try:
                    num = int(path.name.split("_", 1)[1])
                except ValueError:
                    continue
                if num in seen:
                    continue
                seen.add(num)
                out.append(path)
    out.sort(key=lambda p: int(p.name.split("_", 1)[1]), reverse=True)
    return out


def find_session_dir(slug: str, session_num: int) -> Path | None:
    for path in list_session_dirs(slug):
        if path.name == f"session_{session_num}":
            return path
    return None


def load_default_config(slug: str = MACDBB_SLUG) -> dict[str, Any]:
    """Load persisted Strategies defaults from private strategy.yaml (or agent.md)."""
    path = resolve_strategy_defaults_path(slug)
    if path.is_file() and path.name == "strategy.yaml":
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if isinstance(data, dict):
            cfg = data.get("default_config")
            return dict(cfg) if isinstance(cfg, dict) else dict(data)
        return {}

    # Fallback: private agent.md frontmatter during migration.
    agent_md = path.parent / "agent.md"
    if agent_md.is_file():
        text = agent_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                try:
                    meta = yaml.safe_load(text[3:end]) or {}
                except yaml.YAMLError:
                    return {}
                cfg = meta.get("default_config") if isinstance(meta, dict) else None
                return dict(cfg) if isinstance(cfg, dict) else {}
    return {}


def save_default_config(slug: str, default_config: dict[str, Any]) -> Path:
    """Persist defaults to private strategies/{slug}/strategy.yaml."""
    path = resolve_strategy_defaults_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"default_config": dict(default_config)}
    # Preserve unrelated top-level keys if file already exists.
    if path.is_file():
        try:
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(existing, dict):
                existing["default_config"] = dict(default_config)
                payload = existing
        except (OSError, yaml.YAMLError):
            pass
    path.write_text(
        yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return path
