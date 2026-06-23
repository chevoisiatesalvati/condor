"""Re-export macdbb_scanner_aggressive_hl preset definitions (owned by the agent)."""

from trading_agents.macdbb_scanner_aggressive_hl.presets import (  # noqa: F401
    DEFAULT_TIMELINE_SNAPSHOT_DIR,
    DYNAMIC_PRESET_OVERRIDES,
    FIXED_CAPITAL_BENCHMARK_AVG_NOTIONAL,
    PRESET_LABELS,
    PRESET_OVERRIDES,
    USER_WINS_AFTER_PRESET_KEYS,
    _DRIVER_SESSION,
    _DRIVER_TIMELINE,
    _DYNAMIC_PRESET_INFRA,
    _STRATEGY_SESSION_MEGA_BEST,
    _STRATEGY_TIMELINE_MEGA_BEST,
    _build_timeline_driver,
    _merge_preset_layers,
    capital_normalized_pnl,
    resolve_config_with_preset,
    resolve_timeline_range,
)
