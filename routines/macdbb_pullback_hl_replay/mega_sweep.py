"""Random 2k entry-gate + SL/TP + chase sweep for macdbb_pullback_hl.

Held at ``pullback_decay_2h_60s``. Case 0 is always the live winner so the
comparison has a baseline. Remaining cases are unique random draws (seed 42)
from a ~17k discrete space.
"""

from __future__ import annotations

import math
import random
from typing import Any, Iterator

from routines.macdbb_pullback_hl_replay.entry_sltp_sweep import PULLBACK_WINNER_PRESET

MEGA_SWEEP_PRESET = PULLBACK_WINNER_PRESET
MEGA_SWEEP_CONFIG_COUNT = 2000
MEGA_SWEEP_SEED = 42

HELD_OVERRIDES: dict[str, Any] = {
    "enable_thesis_decay_exit": True,
    "thesis_decay_exit_hours": 2.0,
    "enable_flip_exit": False,
    "bb_proximity_epsilon_pct": 0.22,
    "impulse_lookback_bars": 2,
    "atr_period": 14,
    "pullback_timeout_hours": 12.0,
    "sl_symbol_cooldown_hours": 5.0,
}

MEGA_SWEEP_GRID: dict[str, tuple[Any, ...]] = {
    "impulse_atr_mult": (0.75, 1.0, 1.25, 1.5, 1.75, 2.0),
    "pullback_epsilon_pct": (0.20, 0.35, 0.50, 0.75, 1.00, 1.25),
    "sl_pct": (2.0, 2.5, 3.0, 3.8, 4.4, 5.0),
    "tp_pct": (4.0, 5.0, 6.0, 6.8, 7.5, 9.0),
    "chase_long_bb_pos_max": (60.0, 70.0, 80.0),
    "chase_short_bb_pos_min": (20.0, 30.0, 40.0),
}

WINNER_SWEEP_VALUES: dict[str, Any] = {
    "impulse_atr_mult": 1.25,
    "pullback_epsilon_pct": 0.35,
    "sl_pct": 3.0,
    "tp_pct": 6.0,
    "chase_long_bb_pos_max": 70.0,
    "chase_short_bb_pos_min": 30.0,
}

SWEPT_KEYS: tuple[str, ...] = tuple(MEGA_SWEEP_GRID.keys())


def mega_sweep_space_size() -> int:
    return int(math.prod(len(values) for values in MEGA_SWEEP_GRID.values()))


def _case_name(overrides: dict[str, Any]) -> str:
    return (
        f"imp{overrides['impulse_atr_mult']:g}"
        f"_pb{overrides['pullback_epsilon_pct']:g}"
        f"_sl{overrides['sl_pct']:g}"
        f"_tp{overrides['tp_pct']:g}"
        f"_cl{int(overrides['chase_long_bb_pos_max'])}"
        f"_cs{int(overrides['chase_short_bb_pos_min'])}"
    )


def _finalize(combo: dict[str, Any]) -> dict[str, Any]:
    merged = {**HELD_OVERRIDES, **combo}
    merged["name"] = _case_name(merged)
    return merged


def winner_baseline_case() -> dict[str, Any]:
    return _finalize(dict(WINNER_SWEEP_VALUES))


def _random_combo(rng: random.Random) -> dict[str, Any]:
    return {key: rng.choice(values) for key, values in MEGA_SWEEP_GRID.items()}


def iter_pullback_mega_cases(
    *,
    min_configs: int = MEGA_SWEEP_CONFIG_COUNT,
    seed: int = MEGA_SWEEP_SEED,
) -> Iterator[dict[str, Any]]:
    """Yield the live winner first, then unique random draws."""
    target = max(1, int(min_configs))
    winner = winner_baseline_case()
    yield winner
    emitted = 1
    seen = {winner["name"]}
    if emitted >= target:
        return

    rng = random.Random(seed)
    attempts = 0
    max_attempts = target * 50
    space = mega_sweep_space_size()
    while emitted < target and attempts < max_attempts:
        attempts += 1
        case = _finalize(_random_combo(rng))
        name = case["name"]
        if name in seen:
            continue
        seen.add(name)
        yield case
        emitted += 1
        if len(seen) >= space:
            break


def pullback_mega_cases(
    *,
    min_configs: int = MEGA_SWEEP_CONFIG_COUNT,
    seed: int = MEGA_SWEEP_SEED,
) -> list[dict[str, Any]]:
    cases = list(iter_pullback_mega_cases(min_configs=min_configs, seed=seed))
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    assert cases[0]["name"] == winner_baseline_case()["name"]
    return cases


__all__ = [
    "HELD_OVERRIDES",
    "MEGA_SWEEP_CONFIG_COUNT",
    "MEGA_SWEEP_GRID",
    "MEGA_SWEEP_PRESET",
    "MEGA_SWEEP_SEED",
    "SWEPT_KEYS",
    "WINNER_SWEEP_VALUES",
    "iter_pullback_mega_cases",
    "mega_sweep_space_size",
    "pullback_mega_cases",
    "winner_baseline_case",
]
