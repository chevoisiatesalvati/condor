"""Deterministic formal/adaptive signal metrics for MACDBB live entries."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.trading_agent.policies.macdbb_metrics import (
    LiveSignalInput,
    compute_live_signal_metrics,
    infer_signal_label,
)


class Config(BaseModel):
    """Compute formal triggers, adaptive gates, and strength scores for one symbol."""

    pair: str = Field(description="Trading pair (exact TRADEABLE PAIRS string)")
    price: float = Field(description="Analysis price from macd_bb_analysis")
    bb_pos_pct: float = Field(description="BB position percent 0-100 from macd_bb_analysis")
    bb_mid: float = Field(description="1h Bollinger mid from macd_bb_analysis")
    bb_upper: float = Field(description="1h Bollinger upper from macd_bb_analysis")
    macd: float = Field(description="MACD line value")
    signal_line: float = Field(description="MACD signal line value")
    histogram: float = Field(description="MACD histogram value")
    trend: Literal["bullish", "bearish"] = Field(description="Trend from macd_bb_analysis")
    momentum: Literal["increasing", "decreasing"] = Field(
        description="Momentum from macd_bb_analysis",
    )
    bullish_cross: bool = Field(description="Bullish MACD crossover this bar")
    bearish_cross: bool = Field(description="Bearish MACD crossover this bar")
    macd_gap_ratio: float | None = Field(
        default=None,
        description="Optional precomputed gap ratio (omit to derive from macd/signal)",
    )
    hist_ratio: float | None = Field(
        default=None,
        description="Optional precomputed hist ratio (omit to derive from macd/hist)",
    )
    strategy_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Adaptive scoring + bb_proximity keys from [STRATEGY CONFIG] "
            "(adaptive_* gates, score thresholds, hist/momentum bonuses, bb_proximity_epsilon_pct)"
        ),
    )


Config.model_rebuild()


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _format_metrics(pair: str, metrics: dict[str, float | bool]) -> str:
    signal = infer_signal_label(metrics)
    lines = [
        f"MACDBB signal metrics for {pair}",
        f"signal={signal}",
        f"macd_gap_ratio={float(metrics['macd_gap_ratio']):.4f}",
        f"hist_ratio={float(metrics['hist_ratio']):.4f}",
        f"formal_long={_format_bool(bool(metrics['formal_long']))}",
        f"formal_short={_format_bool(bool(metrics['formal_short']))}",
        f"has_formal={_format_bool(bool(metrics['has_formal']))}",
        f"adaptive_strength_long={float(metrics['adaptive_strength_long']):.4f}",
        f"adaptive_strength_short={float(metrics['adaptive_strength_short']):.4f}",
        f"long_open_threshold={float(metrics['long_open_threshold']):.4f}",
        f"short_open_threshold={float(metrics['short_open_threshold']):.4f}",
        f"extreme_long_candidate={_format_bool(bool(metrics['extreme_long_candidate']))}",
        f"extreme_short_candidate={_format_bool(bool(metrics['extreme_short_candidate']))}",
        f"adaptive_long_eligible={_format_bool(bool(metrics['adaptive_long_eligible']))}",
        f"adaptive_short_eligible={_format_bool(bool(metrics['adaptive_short_eligible']))}",
        f"strength_gate={_format_bool(bool(metrics['strength_gate']))}",
        f"adaptive_long_open={_format_bool(bool(metrics['adaptive_long_open']))}",
        f"adaptive_short_open={_format_bool(bool(metrics['adaptive_short_open']))}",
        "Use these values verbatim for Step 4 decisions, signals_1h journal, and macdbb_entry_policy.",
    ]
    return "\n".join(lines)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    _ = context
    signal_input = LiveSignalInput(
        pair=config.pair,
        price=config.price,
        bb_pos_pct=config.bb_pos_pct,
        bb_mid=config.bb_mid,
        bb_upper=config.bb_upper,
        macd=config.macd,
        signal_line=config.signal_line,
        histogram=config.histogram,
        trend=config.trend,
        momentum=config.momentum,
        bullish_cross=config.bullish_cross,
        bearish_cross=config.bearish_cross,
        macd_gap_ratio=config.macd_gap_ratio,
        hist_ratio=config.hist_ratio,
    )
    metrics = compute_live_signal_metrics(signal_input, config.strategy_params)
    return _format_metrics(config.pair, metrics)
