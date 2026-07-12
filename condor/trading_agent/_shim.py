"""Register ``condor.trading_agent.*`` as aliases of ``condor.agents.*``."""

from __future__ import annotations

import importlib
import sys

_ALIASES = (
    "agent_presets",
    "config",
    "engine",
    "journal",
    "performance",
    "performance_digest",
    "prompts",
    "risk",
    "session_status",
    "strategy_paths",
    "strategy",
    "policies",
    "policies.macdbb_dynamic",
    "policies.macdbb_metrics",
    "providers",
    "providers.base",
    "providers.executors",
    "providers.positions",
    "strategy_configs",
    "strategy_configs.macdbb_hl",
    "strategy_configs.registry",
)


def _ensure_parent(pkg_name: str, parts: list[str]) -> None:
    """Ensure intermediate packages exist on ``condor.trading_agent``."""
    root = sys.modules[pkg_name]
    for depth in range(1, len(parts)):
        subpath = ".".join(parts[:depth])
        alias = f"{pkg_name}.{subpath}"
        if alias in sys.modules:
            parent = sys.modules[alias]
        else:
            target = f"condor.agents.{subpath}"
            parent = importlib.import_module(target)
            sys.modules[alias] = parent
        if depth == 1:
            setattr(root, parts[0], parent)
        else:
            grandparent = sys.modules[f"{pkg_name}.{'.'.join(parts[: depth - 1])}"]
            setattr(grandparent, parts[depth - 1], parent)


def install() -> None:
    pkg_name = "condor.trading_agent"
    if pkg_name not in sys.modules:
        importlib.import_module(pkg_name)
    for sub in _ALIASES:
        target = f"condor.agents.{sub}"
        alias = f"{pkg_name}.{sub}"
        parts = sub.split(".")
        if len(parts) > 1:
            _ensure_parent(pkg_name, parts)
        mod = importlib.import_module(target)
        sys.modules[alias] = mod
        if len(parts) == 1:
            setattr(sys.modules[pkg_name], sub, mod)
        else:
            parent = sys.modules[f"{pkg_name}.{'.'.join(parts[:-1])}"]
            setattr(parent, parts[-1], mod)
