"""Deterministic dynamic notional and SL/TP for MACDBB live entries."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.trading_agent.policies.macdbb_dynamic import (
    LivePolicyMeta,
    resolve_live_entry_policy,
)


class Config(BaseModel):
    """Resolve dynamic entry notional and triple-barrier percentages for one candidate."""

    pair: str = Field(description="Trading pair (exact TRADEABLE PAIRS string)")
    side: Literal["long", "short"] = Field(description="Entry side")
    entry_class: Literal["formal", "regime_adaptive_half_size"] = Field(
        description="Entry class from Step 4 or flip reverse create",
    )
    formal_notional_quote: float = Field(
        default=500.0,
        description="Full formal base notional from total_amount_quote / [CURRENT CONFIG]",
    )
    adaptive_activation_streak: int = Field(
        default=0,
        description="Current adaptive_activation_streak from agent state",
    )
    scanner_regime: Literal["mature", "degen"] | None = Field(
        default=None,
        description="Scanner tape regime from Step 2",
    )
    tradeable_count: int | None = Field(
        default=None,
        description="Tradeable pair count from scanner this tick",
    )
    natr_mean_pct: float | None = Field(
        default=None,
        description="Scanner NATR mean % for this pair",
    )
    bb_mid: float | None = Field(
        default=None,
        description="1h Bollinger mid from macd_bb_analysis (optional BB-width vol fallback)",
    )
    bb_upper: float | None = Field(
        default=None,
        description="1h Bollinger upper from macd_bb_analysis",
    )
    adaptive_strength_long: float = Field(
        description="From macdbb_signal_metrics adaptive_strength_long for this symbol",
    )
    adaptive_strength_short: float = Field(
        description="From macdbb_signal_metrics adaptive_strength_short for this symbol",
    )
    long_open_threshold: float = Field(
        description="From macdbb_signal_metrics long_open_threshold",
    )
    short_open_threshold: float = Field(
        description="From macdbb_signal_metrics short_open_threshold",
    )
    extreme_long_candidate: bool = Field(
        default=False,
        description="From macdbb_signal_metrics extreme_long_candidate",
    )
    extreme_short_candidate: bool = Field(
        default=False,
        description="From macdbb_signal_metrics extreme_short_candidate",
    )
    strategy_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dynamic sizing/barrier keys from [STRATEGY CONFIG] "
            "(enable_dynamic_*, min/max_notional_quote, conviction, vol, barrier clamps, sl_pct, tp_pct, etc.)"
        ),
    )


Config.model_rebuild()


def _format_result(result) -> str:
    return (
        f"MACDBB entry policy for {result.notional_quote:.2f} USD notional\n"
        f"notional_usd={result.notional_quote:.2f}\n"
        f"sl_pct={result.sl_pct:.4f}\n"
        f"tp_pct={result.tp_pct:.4f}\n"
        f"stop_loss={result.stop_loss_decimal:.6f}\n"
        f"take_profit={result.take_profit_decimal:.6f}\n"
        f"volatility_proxy_pct={result.volatility_proxy_pct:.4f}\n"
        f"sizing_multiplier={result.sizing_multiplier:.4f}\n"
        f"Use notional_usd and stop_loss/take_profit verbatim in manage_executors(create)."
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    _ = context
    metrics: dict[str, float | bool] = {
        "adaptive_strength_long": config.adaptive_strength_long,
        "adaptive_strength_short": config.adaptive_strength_short,
        "long_open_threshold": config.long_open_threshold,
        "short_open_threshold": config.short_open_threshold,
        "extreme_long_candidate": config.extreme_long_candidate,
        "extreme_short_candidate": config.extreme_short_candidate,
    }
    meta = LivePolicyMeta(
        tradeable_count=config.tradeable_count,
        scanner_regime=config.scanner_regime,
    )
    result = resolve_live_entry_policy(
        pair=config.pair,
        side=config.side,
        entry_class=config.entry_class,
        metrics=metrics,
        meta=meta,
        entry_streak=config.adaptive_activation_streak,
        strategy_params=config.strategy_params,
        formal_notional_quote=config.formal_notional_quote,
        natr_mean_pct=config.natr_mean_pct,
        bb_mid=config.bb_mid,
        bb_upper=config.bb_upper,
    )
    return _format_result(result)
