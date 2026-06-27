"""Pydantic-based configuration for trading agents.

Mirrors the routines pattern: typed config with defaults, stored as config.yml
in the agent directory, editable via key=value messages or web UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# Session-only keys: defaults live on Strategy frontmatter (agent_key, trading_context).
SESSION_OVERRIDE_KEYS = ("agent_key", "trading_context")


def strip_session_overrides(config: dict[str, Any] | None) -> dict[str, Any]:
    """Remove session override keys from a defaults/config dict."""
    if not config:
        return {}
    result = dict(config)
    for key in SESSION_OVERRIDE_KEYS:
        result.pop(key, None)
    return result


def resolve_session_overrides(
    config: dict[str, Any],
    *,
    strategy_agent_key: str = "",
    strategy_trading_context: str = "",
    trading_context_override: str = "",
    agent_key_override: str = "",
) -> dict[str, Any]:
    """Fill session agent_key and trading_context from strategy defaults when unset."""
    result = dict(config)
    explicit_key = (agent_key_override or result.get("agent_key") or "").strip()
    result["agent_key"] = explicit_key or strategy_agent_key or "claude-code"
    if trading_context_override.strip():
        result["trading_context"] = trading_context_override.strip()
    elif not (result.get("trading_context") or "").strip():
        result["trading_context"] = strategy_trading_context or ""
    return result


class RiskLimitsConfig(BaseModel):
    max_open_executors: int = Field(default=5, description="Max simultaneous executors")
    max_drawdown_pct: float = Field(default=-1.0, description="Max drawdown percentage; -1 = disabled")


def strip_legacy_risk_limits(risk_limits: dict[str, Any] | None) -> dict[str, Any]:
    """Remove deprecated persisted fields from risk_limits dict."""
    if not risk_limits:
        return {}
    cleaned = dict(risk_limits)
    cleaned.pop("max_position_size_quote", None)
    return cleaned


def sanitize_config_dict(config: dict[str, Any]) -> dict[str, Any]:
    """Strip deprecated keys before persisting agent/session config."""
    result = dict(config)
    if "risk_limits" in result and isinstance(result["risk_limits"], dict):
        result["risk_limits"] = strip_legacy_risk_limits(result["risk_limits"])
    return result


class AgentConfig(BaseModel):
    server_name: str = Field(default="local", description="Hummingbot API server name")
    agent_key: str = Field(default="", description="LLM model to use (e.g. 'claude-code', 'ollama:llama3.1'). Empty = use strategy default.")
    model_base_url: str = Field(default="", description="Custom base URL for OpenAI-compatible endpoints (LM Studio, vLLM). Leave empty for standard providers.")
    total_amount_quote: float = Field(default=100.0, description="Per-position notional budget in quote currency (USDT)")
    frequency_sec: int = Field(default=60, description="Tick frequency in seconds")
    trading_context: str = Field(default="", description="Natural language session context that guides the agent's trading decisions")
    execution_mode: Literal["dry_run", "run_once", "loop"] = Field(default="loop", description="Execution mode: dry_run (simulate), run_once (single live tick), loop (continuous)")
    max_ticks: int = Field(default=0, description="Max ticks before auto-stop; 0 = unlimited")
    digest_interval_ticks: int = Field(
        default=0,
        description="Telegram digest every N hold-only ticks; 0 = disabled",
    )
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)

    def to_engine_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by TickEngine."""
        d = self.model_dump()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        """Create from a raw dict (e.g. strategy.default_config)."""
        cleaned = {k: v for k, v in d.items() if k in cls.model_fields}
        if isinstance(cleaned.get("risk_limits"), dict):
            cleaned["risk_limits"] = strip_legacy_risk_limits(cleaned["risk_limits"])
        # Translate dry_run shorthand → execution_mode
        if d.get("dry_run") and "execution_mode" not in d:
            cleaned["execution_mode"] = "dry_run"
        return cls(**cleaned)


def load_agent_config(agent_dir: Path, defaults: dict[str, Any] | None = None) -> AgentConfig:
    """Load config from config.yml in the agent directory, falling back to defaults."""
    config_path = agent_dir / "config.yml"
    if config_path.exists():
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
            return AgentConfig(**data)
        except Exception:
            pass
    if defaults:
        return AgentConfig.from_dict(defaults)
    return AgentConfig()


def save_agent_config(agent_dir: Path, config: AgentConfig) -> None:
    """Save config to config.yml in the agent directory."""
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config.model_dump(), default_flow_style=False, sort_keys=False))


def load_full_config(agent_dir: Path, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load config preserving both AgentConfig fields and strategy-specific keys.

    Starts from strategy defaults, overlays saved config.yml, then validates
    core fields via AgentConfig and merges defaults for any missing core fields.

    Session overrides (agent_key, trading_context) are excluded — those belong
    on Strategy frontmatter or per-session config at start time.
    """
    result = strip_session_overrides(defaults)

    config_path = agent_dir / "config.yml"
    if config_path.exists():
        try:
            saved = strip_session_overrides(yaml.safe_load(config_path.read_text()) or {})
            result.update(saved)
        except Exception:
            pass

    # Validate core fields and fill in any missing AgentConfig defaults
    core = AgentConfig.from_dict(result)
    core_defaults = core.model_dump()
    for k, v in core_defaults.items():
        if k in SESSION_OVERRIDE_KEYS:
            continue
        result.setdefault(k, v)

    return strip_session_overrides(result)


def save_full_config(agent_dir: Path, config: dict[str, Any]) -> None:
    """Save a raw config dict as YAML (no filtering through AgentConfig)."""
    config_path = agent_dir / "config.yml"
    agent_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
