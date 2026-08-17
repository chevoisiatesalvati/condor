"""Compact entry-gate + SL/TP sweep for macdbb_pullback_hl.

Pullback has no adaptive score / BB-band open filters. A trade opens only when
``decide()`` has a thesis and then either:

* immediate (no impulse and not chase-extended), or
* pullback (armed thesis reaches BB mid within timeout).

This grid therefore sweeps the knobs that change those gates, plus fixed SL/TP
pairs taken from the two strategy winners — not a full cartesian of scanner
adaptive params.

Held at ``pullback_decay_2h_60s``: 60s ticks, thesis-decay 2h, no flip exit,
chase 70/30, pullback timeout 12h, ``bb_proximity_epsilon_pct=0.22`` (both
winners). MACD-cross thesis ignores epsilon; keep it fixed so the 24-config
product stays small.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterator

# Current live / backtest winner.
PULLBACK_WINNER_PRESET = "pullback_decay_2h_60s"

# Scanner named winner (strategies/macdbb_scanner_aggressive_hl/presets.yaml).
SCANNER_WINNER_PRESET = "hl_dynamic_timeline_sweep_lead_013"
SCANNER_REFINE_PRESET = "hl_dynamic_timeline_refine_lead_013"

# Immediate vs arm: body-sum ≥ mult × ATR%. Lower → more impulse → more wait.
# Winner 1.25 first so case 0 is the live baseline.
IMPULSE_ATR_MULTS: tuple[float, ...] = (1.25, 1.0, 1.75)

# How close to BB mid an armed thesis must get before filling.
PULLBACK_EPSILON_PCTS: tuple[float, ...] = (0.35, 0.75)

# (sl_pct, tp_pct). Scanner uses dynamic barriers; these are the comparable
# fixed levels from pullback + lead_013 / refine_013.
BARRIER_PAIRS: tuple[tuple[float, float], ...] = (
    (3.0, 6.0),  # pullback_decay_2h_60s
    (3.8, 5.0),  # lead_013 sl_pct / tp_pct
    (3.8, 7.5),  # lead_013 sl_pct / tp_min_pct
    (4.4, 6.8),  # refine_lead_013 sl_pct / tp_min_pct
)

SWEPT_KEYS: tuple[str, ...] = (
    "impulse_atr_mult",
    "pullback_epsilon_pct",
    "sl_pct",
    "tp_pct",
)

ENTRY_SLTP_SWEEP_CONFIG_COUNT = (
    len(IMPULSE_ATR_MULTS) * len(PULLBACK_EPSILON_PCTS) * len(BARRIER_PAIRS)
)


def _case_name(
    *,
    impulse_atr_mult: float,
    pullback_epsilon_pct: float,
    sl_pct: float,
    tp_pct: float,
) -> str:
    return (
        f"imp{impulse_atr_mult:g}"
        f"_pb{pullback_epsilon_pct:g}"
        f"_sl{sl_pct:g}"
        f"_tp{tp_pct:g}"
    )


def iter_pullback_entry_sltp_cases() -> Iterator[dict[str, Any]]:
    """Yield 24 named override dicts. Winner values are first in each axis."""
    for impulse, pullback_eps, (sl_pct, tp_pct) in itertools.product(
        IMPULSE_ATR_MULTS,
        PULLBACK_EPSILON_PCTS,
        BARRIER_PAIRS,
    ):
        name = _case_name(
            impulse_atr_mult=impulse,
            pullback_epsilon_pct=pullback_eps,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
        )
        yield {
            "name": name,
            "impulse_atr_mult": float(impulse),
            "pullback_epsilon_pct": float(pullback_eps),
            "sl_pct": float(sl_pct),
            "tp_pct": float(tp_pct),
        }


def pullback_entry_sltp_cases() -> list[dict[str, Any]]:
    cases = list(iter_pullback_entry_sltp_cases())
    assert len(cases) == ENTRY_SLTP_SWEEP_CONFIG_COUNT
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    return cases
