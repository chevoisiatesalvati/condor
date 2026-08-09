"""Registry of Condor-native deterministic strategies.

Discovery is explicit (not via AGENT.md) so Strategies never depends on the
Agents “every agent is chat/loopable” assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeterministicStrategy:
    """One entry in the Strategies catalog."""

    slug: str
    name: str
    description: str
    # Storage key under data/strategy_runs/{data_slug}/ (and private strategies/).
    data_slug: str
    # Canonical strategy identity slug (matches data_slug for MACDBB).
    strategy_slug: str
    connector: str = "hyperliquid_perpetual"
    require_promoted: bool = True
    default_config: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.data_slug}.{self.strategy_slug}"


_MACDBB = DeterministicStrategy(
    slug="macdbb_scanner_aggressive_hl",
    name="MACD+BB Scanner Aggressive HL",
    description=(
        "Hyperliquid-native scanner + MACD/BB momentum with dynamic sizing "
        "and adaptive entry filters. Deterministic runner — no LLM."
    ),
    data_slug="macdbb_scanner_aggressive_hl",
    strategy_slug="macdbb_scanner_aggressive_hl",
    connector="hyperliquid_perpetual",
    require_promoted=True,
    default_config={
        "server_name": "local",
        "total_amount_quote": 500,
        "frequency_sec": 1800,
        "execution_mode": "loop",
        "bot_name": "",
        "risk_limits": {
            "max_open_executors": 10,
            "max_drawdown_pct": -2,
        },
        "strategy_preset": "hl_dynamic_timeline_refine_lead_013",
        "tick_log_enabled": True,
        "tick_log_retention_days": 7,
    },
)

_MACDBB_PULLBACK = DeterministicStrategy(
    slug="macdbb_pullback_hl",
    name="MACD+BB Pullback HL",
    description=(
        "Hyperliquid scanner + MACD/BB thesis with impulse filter and "
        "staged BB-mid pullback entries. Deterministic runner — no LLM."
    ),
    data_slug="macdbb_pullback_hl",
    strategy_slug="macdbb_pullback_hl",
    connector="hyperliquid_perpetual",
    require_promoted=False,
    default_config={
        "server_name": "local",
        "total_amount_quote": 500,
        "frequency_sec": 60,
        "execution_mode": "loop",
        "bot_name": "",
        "risk_limits": {
            "max_open_executors": 10,
            "max_drawdown_pct": -2,
        },
        "strategy_preset": "pullback_decay_2h_60s",
        "tick_log_enabled": True,
        "tick_log_retention_days": 7,
    },
)

_CATALOG: dict[str, DeterministicStrategy] = {
    _MACDBB.slug: _MACDBB,
    _MACDBB_PULLBACK.slug: _MACDBB_PULLBACK,
}


def list_strategies() -> list[DeterministicStrategy]:
    return sorted(_CATALOG.values(), key=lambda s: s.slug)


def get_strategy(slug: str) -> DeterministicStrategy | None:
    return _CATALOG.get(slug)


def is_deterministic_strategy_slug(slug: str | None) -> bool:
    """True when ``slug`` names a Strategies-catalog entry (not a chat agent)."""
    return bool(slug) and slug in _CATALOG
