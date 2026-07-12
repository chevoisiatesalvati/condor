"""Dynamic position sizing and volatility-aware barriers for replay backtest."""

from __future__ import annotations

import datetime as dt

from condor.trading_agent.policies.macdbb_dynamic import (
    NATR_LOOKBACK_PERIODS,
    NATR_MIN_CANDLES,
    SCANNER_NATR_LOOKBACK_HOURS_DEFAULT,
    STATIC_TIER_VOL_PCT,
    EntryPolicyResult,
    _canonical_trading_pair,
    bb_width_pct,
    compute_conviction_multiplier,
    compute_dynamic_barriers,
    compute_vol_risk_multiplier,
    estimate_pair_volatility,
    natr_from_candles,
    resolve_entry_policy,
    resolve_fixed_entry_policy,
    scanner_natr_mean_from_candles,
    static_tier_volatility_pct,
)
from routines.macdbb_scanner_aggressive_hl_replay.models import (
    DynamicStrategyReplayConfig,
    JournalSignal1h,
    StrategyReplayConfig,
    TickMeta,
)
from routines.macdbb_scanner_aggressive_hl_replay.replay_data import is_report_driven_data_source

__all__ = [
    "NATR_LOOKBACK_PERIODS",
    "NATR_MIN_CANDLES",
    "SCANNER_NATR_LOOKBACK_HOURS_DEFAULT",
    "STATIC_TIER_VOL_PCT",
    "DynamicReplayPolicy",
    "EntryPolicyResult",
    "ReplayPolicy",
    "bb_width_pct",
    "compute_conviction_multiplier",
    "compute_dynamic_barriers",
    "compute_vol_risk_multiplier",
    "estimate_pair_volatility",
    "natr_from_candles",
    "resolve_entry_policy",
    "resolve_fixed_entry_policy",
    "scanner_natr_mean_from_candles",
    "static_tier_volatility_pct",
]


class DynamicReplayPolicy:
    """Replay policy for dynamic sizing and volatility-aware barriers."""

    def __init__(self, config: DynamicStrategyReplayConfig) -> None:
        self.config = config

    def resolve_entry(
        self,
        *,
        pair: str,
        side: str,
        entry_class: str,
        metrics: dict[str, float | bool],
        meta: TickMeta,
        entry_streak: int,
        journal_signal: JournalSignal1h | None = None,
        hl_candle_cache: HlCandleCache | SharedCandleStore | LazyCandleStore | None = None,
        hl_vol_candle_cache: HlCandleCache | SharedCandleStore | LazyCandleStore | None = None,
        entry_time: dt.datetime | None = None,
    ) -> EntryPolicyResult:
        if (
            not self.config.enable_dynamic_sizing
            and not self.config.enable_dynamic_barriers
        ):
            return resolve_fixed_entry_policy(
                entry_class=entry_class,
                config=self.config,
            )

        pair_vol_override: float | None = None
        if meta.natr_by_pair:
            pair_vol_override = meta.natr_by_pair.get(_canonical_trading_pair(pair))
            if pair_vol_override is None:
                pair_vol_override = meta.natr_by_pair.get(pair)

        return resolve_entry_policy(
            pair=pair,
            side=side,
            entry_class=entry_class,
            metrics=metrics,
            meta=meta,
            entry_streak=entry_streak,
            config=self.config,
            journal_signal=journal_signal,
            hl_candle_cache=hl_candle_cache,
            hl_vol_candle_cache=hl_vol_candle_cache,
            entry_time=entry_time,
            pair_vol_override=pair_vol_override,
        )

    def skip_journal_barriers(self) -> bool:
        if self.config.replay_mode == "session_parity":
            return False
        if is_report_driven_data_source(self.config.data_source):
            return True
        if not self.config.use_journal_barriers:
            return True
        return (
            self.config.enable_dynamic_barriers
            and self.config.ignore_journal_barriers_when_dynamic
        )


ReplayPolicy = DynamicReplayPolicy
