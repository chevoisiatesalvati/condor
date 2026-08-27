"""Random ~3k decay / flip / ATR-dynamics sweep for macdbb_pullback_hl.

Holds the latest sweep-lead entry gates. Case 0 is that lead; the rest are
unique random draws. The RNG seed and output directory advance with the
highest ``pullback_sweep_lead_NNN`` so each new lead starts a fresh search
instead of repeating the previous grid.
"""

from __future__ import annotations

import random
from typing import Any, Iterator

DYNAMICS_SWEEP_CONFIG_COUNT = 3000
# First dynamics sweep used seed 43 after the entry 2k run had already written
# leads 001–007. Each later highest lead number increments the draw.
_FIRST_DYNAMICS_SEED = 43
_LEADS_BEFORE_FIRST_DYNAMICS = 7

HELD_OVERRIDES: dict[str, Any] = {
    "bb_proximity_epsilon_pct": 0.22,
    "impulse_lookback_bars": 2,
    "atr_period": 14,
    "pullback_timeout_hours": 12.0,
    "sl_symbol_cooldown_hours": 5.0,
    "flip_confirm_ticks": 2,
    "flip_cooldown_hours": 1.5,
    "thesis_decay_negative_grace_minutes": 30.0,
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


def dynamics_sweep_preset(*, presets_path: Any | None = None) -> str:
    from condor.strategy_runners.macdbb_pullback.presets import DEFAULT_WINNER_PRESET
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        latest_sweep_lead_preset,
    )

    return latest_sweep_lead_preset(presets_path) or DEFAULT_WINNER_PRESET


def dynamics_sweep_seed(*, presets_path: Any | None = None) -> int:
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        latest_sweep_lead_number,
    )

    lead_n = latest_sweep_lead_number(presets_path) or 0
    return _FIRST_DYNAMICS_SEED + max(0, lead_n - _LEADS_BEFORE_FIRST_DYNAMICS)


def dynamics_sweep_out_dir(*, presets_path: Any | None = None) -> str:
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        latest_sweep_lead_preset,
    )

    lead = latest_sweep_lead_preset(presets_path)
    if not lead:
        return "data/backtests/pullback_dynamics_sweep"
    suffix = lead.removeprefix("pullback_sweep_")
    return f"data/backtests/pullback_dynamics_sweep_{suffix}"


def current_lead_sweep_values(*, presets_path: Any | None = None) -> dict[str, Any]:
    from condor.strategy_runners.macdbb_pullback.presets import (
        _preset_override_dict,
        strategy_params_from_preset,
    )
    from routines.macdbb_pullback_hl_replay.sweep_automation import STRATEGY_PRESET_KEYS

    preset = dynamics_sweep_preset(presets_path=presets_path)
    merged = {
        **_preset_override_dict(preset),
        **strategy_params_from_preset(preset),
    }
    values: dict[str, Any] = {}
    for key in STRATEGY_PRESET_KEYS:
        if key == "frequency_sec":
            continue
        if key in merged and merged[key] is not None:
            values[key] = merged[key]
    return values


def held_random_entry(*, presets_path: Any | None = None) -> dict[str, Any]:
    lead = current_lead_sweep_values(presets_path=presets_path)
    return {
        key: lead[key] for key in HELD_RANDOM_ENTRY if key in lead
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
    presets_path: Any | None = None,
) -> dict[str, Any]:
    merged = {**HELD_OVERRIDES}
    if include_random_entry:
        merged.update(held_random_entry(presets_path=presets_path))
    merged.update(combo)
    merged["name"] = name if name is not None else _case_name(merged)
    return merged


def current_lead_baseline_case(*, presets_path: Any | None = None) -> dict[str, Any]:
    return _finalize(
        current_lead_sweep_values(presets_path=presets_path),
        include_random_entry=False,
        presets_path=presets_path,
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


def gate_dry_run_cases(*, presets_path: Any | None = None) -> list[dict[str, Any]]:
    """Decay-off vs current lead vs dynamic-on, all on the latest-lead gates."""
    values = current_lead_sweep_values(presets_path=presets_path)
    lead = current_lead_baseline_case(presets_path=presets_path)
    decay_off = _finalize(
        {**values, **_apply_decay(0.0)},
        include_random_entry=False,
        presets_path=presets_path,
    )
    dynamic_on = _finalize(
        {
            **values,
            "enable_dynamic_barriers": True,
            "enable_dynamic_sizing": True,
            "sl_vol_exponent": 1.0,
            "tp_vol_exponent": 1.0,
            "min_vol_mult": 0.5,
            "max_vol_mult": 1.5,
            "ref_volatility_pct": 1.0,
        },
        include_random_entry=False,
        presets_path=presets_path,
    )
    return [decay_off, lead, dynamic_on]


def iter_pullback_dynamics_cases(
    *,
    min_configs: int = DYNAMICS_SWEEP_CONFIG_COUNT,
    seed: int | None = None,
    presets_path: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the current sweep lead, then unique random draws."""
    target = max(1, int(min_configs))
    rng_seed = dynamics_sweep_seed(presets_path=presets_path) if seed is None else seed
    current = current_lead_baseline_case(presets_path=presets_path)
    yield current
    emitted = 1
    seen = {current["name"]}
    if emitted >= target:
        return

    rng = random.Random(rng_seed)
    attempts = 0
    max_attempts = target * 80
    space = dynamics_sweep_space_size()
    while emitted < target and attempts < max_attempts:
        attempts += 1
        case = _finalize(_random_combo(rng), presets_path=presets_path)
        name = case["name"]
        if name in seen:
            continue
        seen.add(name)
        yield case
        emitted += 1
        if len(seen) >= space + 1:
            break


def pullback_dynamics_cases(
    *,
    min_configs: int = DYNAMICS_SWEEP_CONFIG_COUNT,
    seed: int | None = None,
    presets_path: Any | None = None,
) -> list[dict[str, Any]]:
    cases = list(
        iter_pullback_dynamics_cases(
            min_configs=min_configs,
            seed=seed,
            presets_path=presets_path,
        )
    )
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    assert cases[0]["name"] == current_lead_baseline_case(
        presets_path=presets_path
    )["name"]
    return cases


__all__ = [
    "DYNAMICS_SWEEP_CONFIG_COUNT",
    "HELD_OVERRIDES",
    "HELD_RANDOM_ENTRY",
    "current_lead_baseline_case",
    "current_lead_sweep_values",
    "dynamics_sweep_out_dir",
    "dynamics_sweep_preset",
    "dynamics_sweep_seed",
    "dynamics_sweep_space_size",
    "gate_dry_run_cases",
    "held_random_entry",
    "iter_pullback_dynamics_cases",
    "pullback_dynamics_cases",
]
