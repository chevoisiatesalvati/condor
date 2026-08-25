"""Random ~3k decay / flip / ATR-dynamics sweep for macdbb_pullback_hl.

Holds the 2k-leader entry gates (pb 1.25, chase 80/30). Case 0 is the live
winner; case 1 is the 2k leader. Remaining cases are unique random draws
(seed 43) from a nested space several times larger than 3000.
"""

from __future__ import annotations

import random
from typing import Any, Iterator

from routines.macdbb_pullback_hl_replay.entry_sltp_sweep import PULLBACK_WINNER_PRESET

DYNAMICS_SWEEP_PRESET = PULLBACK_WINNER_PRESET
DYNAMICS_SWEEP_CONFIG_COUNT = 3000
DYNAMICS_SWEEP_SEED = 43

LIVE_CASE_NAME = "imp1.25_pb0.35_sl3_tp6_cl70_cs30"
LEADER_CASE_NAME = "imp1_pb1.25_sl3.8_tp9_cl80_cs30"

HELD_OVERRIDES: dict[str, Any] = {
    "bb_proximity_epsilon_pct": 0.22,
    "impulse_lookback_bars": 2,
    "atr_period": 14,
    "pullback_timeout_hours": 12.0,
    "sl_symbol_cooldown_hours": 5.0,
    "flip_confirm_ticks": 2,
    "flip_cooldown_hours": 1.5,
}

HELD_RANDOM_ENTRY: dict[str, Any] = {
    "pullback_epsilon_pct": 1.25,
    "chase_long_bb_pos_max": 80.0,
    "chase_short_bb_pos_min": 30.0,
    "sl_pct": 3.8,
    "tp_pct": 9.0,
}

IMPULSE_VALUES: tuple[float, ...] = (0.75, 1.0, 1.25)
DECAY_HOURS_VALUES: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0, 8.0)
FLIP_VALUES: tuple[bool, ...] = (False, True)
REF_VOL_VALUES: tuple[float, ...] = (0.75, 1.0, 1.5)
DRIFT_VALUES: tuple[float, ...] = (15.0, 20.0, 30.0)
SL_VOL_EXPONENTS: tuple[float, ...] = (0.5, 1.0)
TP_VOL_EXPONENTS: tuple[float, ...] = (0.5, 1.0)
CLAMP_PAIRS: tuple[tuple[float, float, float, float], ...] = (
    (2.0, 6.0, 4.0, 12.0),
    (2.0, 5.0, 4.0, 10.0),
    (2.5, 6.0, 6.0, 12.0),
)
SIZING_BANDS: tuple[tuple[float, float], ...] = (
    (0.5, 1.5),
    (0.35, 2.0),
)
DEFAULT_CLAMP = CLAMP_PAIRS[0]
DEFAULT_SIZING_BAND = SIZING_BANDS[0]
DEFAULT_REF_VOL = 1.0
DEFAULT_DRIFT = 20.0

LIVE_SWEEP_VALUES: dict[str, Any] = {
    "impulse_atr_mult": 1.25,
    "pullback_epsilon_pct": 0.35,
    "sl_pct": 3.0,
    "tp_pct": 6.0,
    "chase_long_bb_pos_max": 70.0,
    "chase_short_bb_pos_min": 30.0,
    "enable_thesis_decay_exit": True,
    "thesis_decay_exit_hours": 2.0,
    "enable_flip_exit": False,
    "enable_dynamic_barriers": False,
    "enable_dynamic_sizing": False,
    "thesis_bb_drift_pts": DEFAULT_DRIFT,
    "ref_volatility_pct": DEFAULT_REF_VOL,
    "sl_vol_exponent": 1.0,
    "tp_vol_exponent": 1.0,
    "sl_min_pct": DEFAULT_CLAMP[0],
    "sl_max_pct": DEFAULT_CLAMP[1],
    "tp_min_pct": DEFAULT_CLAMP[2],
    "tp_max_pct": DEFAULT_CLAMP[3],
    "min_vol_mult": DEFAULT_SIZING_BAND[0],
    "max_vol_mult": DEFAULT_SIZING_BAND[1],
}

LEADER_SWEEP_VALUES: dict[str, Any] = {
    "impulse_atr_mult": 1.0,
    "pullback_epsilon_pct": 1.25,
    "sl_pct": 3.8,
    "tp_pct": 9.0,
    "chase_long_bb_pos_max": 80.0,
    "chase_short_bb_pos_min": 30.0,
    "enable_thesis_decay_exit": True,
    "thesis_decay_exit_hours": 2.0,
    "enable_flip_exit": False,
    "enable_dynamic_barriers": False,
    "enable_dynamic_sizing": False,
    "thesis_bb_drift_pts": DEFAULT_DRIFT,
    "ref_volatility_pct": DEFAULT_REF_VOL,
    "sl_vol_exponent": 1.0,
    "tp_vol_exponent": 1.0,
    "sl_min_pct": DEFAULT_CLAMP[0],
    "sl_max_pct": DEFAULT_CLAMP[1],
    "tp_min_pct": DEFAULT_CLAMP[2],
    "tp_max_pct": DEFAULT_CLAMP[3],
    "min_vol_mult": DEFAULT_SIZING_BAND[0],
    "max_vol_mult": DEFAULT_SIZING_BAND[1],
}


def _barrier_off() -> dict[str, Any]:
    return {
        "enable_dynamic_barriers": False,
        "sl_vol_exponent": 1.0,
        "tp_vol_exponent": 1.0,
        "sl_min_pct": DEFAULT_CLAMP[0],
        "sl_max_pct": DEFAULT_CLAMP[1],
        "tp_min_pct": DEFAULT_CLAMP[2],
        "tp_max_pct": DEFAULT_CLAMP[3],
    }


def _barrier_on(
    sl_exp: float,
    tp_exp: float,
    clamp: tuple[float, float, float, float],
) -> dict[str, Any]:
    return {
        "enable_dynamic_barriers": True,
        "sl_vol_exponent": sl_exp,
        "tp_vol_exponent": tp_exp,
        "sl_min_pct": clamp[0],
        "sl_max_pct": clamp[1],
        "tp_min_pct": clamp[2],
        "tp_max_pct": clamp[3],
    }


def _sizing_off() -> dict[str, Any]:
    return {
        "enable_dynamic_sizing": False,
        "min_vol_mult": DEFAULT_SIZING_BAND[0],
        "max_vol_mult": DEFAULT_SIZING_BAND[1],
    }


def _sizing_on(band: tuple[float, float]) -> dict[str, Any]:
    return {
        "enable_dynamic_sizing": True,
        "min_vol_mult": band[0],
        "max_vol_mult": band[1],
    }


def _iter_barrier_specs() -> Iterator[dict[str, Any]]:
    yield _barrier_off()
    for sl_exp in SL_VOL_EXPONENTS:
        for tp_exp in TP_VOL_EXPONENTS:
            for clamp in CLAMP_PAIRS:
                yield _barrier_on(sl_exp, tp_exp, clamp)


def _iter_sizing_specs() -> Iterator[dict[str, Any]]:
    yield _sizing_off()
    for band in SIZING_BANDS:
        yield _sizing_on(band)


def _case_name(overrides: dict[str, Any]) -> str:
    parts = [
        f"imp{overrides['impulse_atr_mult']:g}",
        f"d{float(overrides['thesis_decay_exit_hours']):g}",
        f"f{int(bool(overrides['enable_flip_exit']))}",
    ]
    if overrides.get("enable_dynamic_barriers"):
        parts.append(
            f"b{overrides['sl_vol_exponent']:g}_{overrides['tp_vol_exponent']:g}"
            f"_{overrides['sl_min_pct']:g}-{overrides['sl_max_pct']:g}"
            f"_{overrides['tp_min_pct']:g}-{overrides['tp_max_pct']:g}"
        )
    else:
        parts.append("b0")
    if overrides.get("enable_dynamic_sizing"):
        parts.append(f"s{overrides['min_vol_mult']:g}-{overrides['max_vol_mult']:g}")
    else:
        parts.append("s0")
    if overrides.get("enable_dynamic_barriers") or overrides.get("enable_dynamic_sizing"):
        parts.append(f"r{overrides['ref_volatility_pct']:g}")
    if float(overrides.get("thesis_decay_exit_hours") or 0) > 0:
        parts.append(f"dr{overrides['thesis_bb_drift_pts']:g}")
    return "_".join(parts)


def _apply_decay(hours: float) -> dict[str, Any]:
    hours_value = float(hours)
    return {
        "enable_thesis_decay_exit": hours_value > 0,
        "thesis_decay_exit_hours": hours_value,
    }


def _finalize(
    combo: dict[str, Any],
    *,
    name: str | None = None,
    include_random_entry: bool = True,
) -> dict[str, Any]:
    merged = {**HELD_OVERRIDES}
    if include_random_entry:
        merged.update(HELD_RANDOM_ENTRY)
    merged.update(combo)
    merged["name"] = name if name is not None else _case_name(merged)
    return merged


def live_baseline_case() -> dict[str, Any]:
    return _finalize(dict(LIVE_SWEEP_VALUES), name=LIVE_CASE_NAME, include_random_entry=False)


def leader_baseline_case() -> dict[str, Any]:
    return _finalize(
        dict(LEADER_SWEEP_VALUES),
        name=LEADER_CASE_NAME,
        include_random_entry=False,
    )


def _random_combo(rng: random.Random) -> dict[str, Any]:
    decay_hours = rng.choice(DECAY_HOURS_VALUES)
    barriers_on = rng.choice((False, True))
    sizing_on = rng.choice((False, True))
    combo: dict[str, Any] = {
        "impulse_atr_mult": rng.choice(IMPULSE_VALUES),
        "enable_flip_exit": rng.choice(FLIP_VALUES),
        **_apply_decay(decay_hours),
        "thesis_bb_drift_pts": (
            rng.choice(DRIFT_VALUES) if decay_hours > 0 else DEFAULT_DRIFT
        ),
    }
    if barriers_on:
        combo.update(
            _barrier_on(
                rng.choice(SL_VOL_EXPONENTS),
                rng.choice(TP_VOL_EXPONENTS),
                rng.choice(CLAMP_PAIRS),
            )
        )
    else:
        combo.update(_barrier_off())
    if sizing_on:
        combo.update(_sizing_on(rng.choice(SIZING_BANDS)))
    else:
        combo.update(_sizing_off())
    combo["ref_volatility_pct"] = (
        rng.choice(REF_VOL_VALUES) if (barriers_on or sizing_on) else DEFAULT_REF_VOL
    )
    return combo


def iter_dynamics_space() -> Iterator[dict[str, Any]]:
    """Enumerate the nested unique-behavior space (no unused-knob duplicates)."""
    for impulse in IMPULSE_VALUES:
        for decay_hours in DECAY_HOURS_VALUES:
            drifts = DRIFT_VALUES if decay_hours > 0 else (DEFAULT_DRIFT,)
            for drift in drifts:
                for flip in FLIP_VALUES:
                    for barrier in _iter_barrier_specs():
                        for sizing in _iter_sizing_specs():
                            dyn_on = bool(
                                barrier["enable_dynamic_barriers"]
                                or sizing["enable_dynamic_sizing"]
                            )
                            refs = REF_VOL_VALUES if dyn_on else (DEFAULT_REF_VOL,)
                            for ref_vol in refs:
                                yield {
                                    "impulse_atr_mult": impulse,
                                    "enable_flip_exit": flip,
                                    **_apply_decay(decay_hours),
                                    "thesis_bb_drift_pts": drift,
                                    **barrier,
                                    **sizing,
                                    "ref_volatility_pct": ref_vol,
                                }


def dynamics_sweep_space_size() -> int:
    return sum(1 for _ in iter_dynamics_space())


def gate_dry_run_cases() -> list[dict[str, Any]]:
    """Decay-off vs decay-2h vs dynamic-on, all on the 2k-leader entry gates."""
    leader = leader_baseline_case()
    decay_off = _finalize(
        {
            **LEADER_SWEEP_VALUES,
            **_apply_decay(0.0),
        },
        name=f"{LEADER_CASE_NAME}_d0",
        include_random_entry=False,
    )
    dynamic_on = _finalize(
        {
            **LEADER_SWEEP_VALUES,
            "enable_dynamic_barriers": True,
            "enable_dynamic_sizing": True,
            "sl_vol_exponent": 1.0,
            "tp_vol_exponent": 1.0,
            "min_vol_mult": 0.5,
            "max_vol_mult": 1.5,
            "ref_volatility_pct": 1.0,
        },
        name=f"{LEADER_CASE_NAME}_dyn",
        include_random_entry=False,
    )
    return [decay_off, leader, dynamic_on]


def iter_pullback_dynamics_cases(
    *,
    min_configs: int = DYNAMICS_SWEEP_CONFIG_COUNT,
    seed: int = DYNAMICS_SWEEP_SEED,
) -> Iterator[dict[str, Any]]:
    """Yield live baseline, 2k leader, then unique random draws."""
    target = max(2, int(min_configs))
    live = live_baseline_case()
    leader = leader_baseline_case()
    yield live
    yield leader
    emitted = 2
    seen = {live["name"], leader["name"]}
    if emitted >= target:
        return

    rng = random.Random(seed)
    attempts = 0
    max_attempts = target * 80
    space = dynamics_sweep_space_size()
    while emitted < target and attempts < max_attempts:
        attempts += 1
        case = _finalize(_random_combo(rng))
        name = case["name"]
        if name in seen:
            continue
        seen.add(name)
        yield case
        emitted += 1
        if len(seen) >= space + 2:
            break


def pullback_dynamics_cases(
    *,
    min_configs: int = DYNAMICS_SWEEP_CONFIG_COUNT,
    seed: int = DYNAMICS_SWEEP_SEED,
) -> list[dict[str, Any]]:
    cases = list(iter_pullback_dynamics_cases(min_configs=min_configs, seed=seed))
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    assert cases[0]["name"] == LIVE_CASE_NAME
    assert cases[1]["name"] == LEADER_CASE_NAME
    return cases


__all__ = [
    "DYNAMICS_SWEEP_CONFIG_COUNT",
    "DYNAMICS_SWEEP_PRESET",
    "DYNAMICS_SWEEP_SEED",
    "HELD_OVERRIDES",
    "HELD_RANDOM_ENTRY",
    "LEADER_CASE_NAME",
    "LIVE_CASE_NAME",
    "dynamics_sweep_space_size",
    "gate_dry_run_cases",
    "iter_pullback_dynamics_cases",
    "leader_baseline_case",
    "live_baseline_case",
    "pullback_dynamics_cases",
]
