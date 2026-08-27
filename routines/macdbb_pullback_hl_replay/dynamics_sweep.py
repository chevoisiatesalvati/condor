"""Random ~3k decay / ATR-dynamics sweep for macdbb_pullback_hl (v2).

Case 0 is the latest sweep lead. Remaining cases are unique random draws.
Impulse, flip, and sizing stay on the lead / off. The RNG seed and output
directory advance with the highest ``pullback_sweep_lead_NNN``.
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
    "enable_flip_exit": False,
    "enable_dynamic_sizing": False,
    "thesis_bb_drift_pts": 20.0,
}

HELD_RANDOM_ENTRY: dict[str, Any] = {
    "impulse_atr_mult": 0.75,
    "sl_pct": 3.8,
}

DECAY_HOURS_VALUES: tuple[float, ...] = (0.0, 1.0, 2.0, 8.0)
EPSILON_VALUES: tuple[float, ...] = (1.25, 1.5, 2.0)
TP_VALUES: tuple[float, ...] = (9.0, 11.0, 13.0)
REF_VOL_VALUES: tuple[float, ...] = (0.5, 0.6, 0.75, 1.0)
GRACE_VALUES: tuple[float, ...] = (0.0, 30.0, 60.0)
CHASE_PAIRS: tuple[tuple[float, float], ...] = (
    (70.0, 30.0),
    (70.0, 40.0),
    (80.0, 30.0),
    (80.0, 40.0),
)
EXPONENT_PAIRS: tuple[tuple[float, float], ...] = (
    (0.5, 0.5),
    (0.5, 1.0),
    (1.0, 0.5),
)
CLAMP_PAIRS: tuple[tuple[float, float, float, float], ...] = (
    (2.0, 6.0, 4.0, 16.0),
    (2.5, 6.0, 6.0, 16.0),
)
PROBE_LOOKBACK_BARS: tuple[int, ...] = (1, 2, 4)
PROBE_ATR_PERIODS: tuple[int, ...] = (7, 14, 21)
DEFAULT_CLAMP = CLAMP_PAIRS[0]
DEFAULT_SIZING_BAND = (0.5, 1.5)
DEFAULT_REF_VOL = 1.0
DEFAULT_DRIFT = 20.0
DEFAULT_GRACE = 30.0
DEFAULT_PROBE_LOOKBACK = 2
DEFAULT_PROBE_ATR_PERIOD = 14


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


def _lead_dir_suffix(*, presets_path: Any | None = None) -> str | None:
    from routines.macdbb_pullback_hl_replay.sweep_automation import (
        latest_sweep_lead_preset,
    )

    lead = latest_sweep_lead_preset(presets_path)
    if not lead:
        return None
    return lead.removeprefix("pullback_sweep_")


def dynamics_sweep_out_dir(*, presets_path: Any | None = None) -> str:
    suffix = _lead_dir_suffix(presets_path=presets_path)
    if not suffix:
        return "data/backtests/pullback_dynamics_sweep"
    return f"data/backtests/pullback_dynamics_sweep_{suffix}"


def lookback_atr_probe_out_dir(*, presets_path: Any | None = None) -> str:
    suffix = _lead_dir_suffix(presets_path=presets_path)
    if not suffix:
        return "data/backtests/pullback_lookback_atr_probe"
    return f"data/backtests/pullback_lookback_atr_probe_{suffix}"


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


def _iter_barrier_specs() -> Iterator[dict[str, Any]]:
    yield _barrier_off()
    for sl_exp, tp_exp in EXPONENT_PAIRS:
        for clamp in CLAMP_PAIRS:
            yield _barrier_on(sl_exp, tp_exp, clamp)


def _case_name(overrides: dict[str, Any]) -> str:
    parts = [
        f"imp{overrides['impulse_atr_mult']:g}",
        f"pb{overrides['pullback_epsilon_pct']:g}",
        f"tp{overrides['tp_pct']:g}",
        f"cl{int(overrides['chase_long_bb_pos_max'])}",
        f"cs{int(overrides['chase_short_bb_pos_min'])}",
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
        parts.append(f"g{float(overrides.get('thesis_decay_negative_grace_minutes') or 0):g}")
    return "_".join(parts)


def _apply_decay(hours: float) -> dict[str, Any]:
    hours_value = float(hours)
    return {
        "enable_thesis_decay_exit": hours_value > 0,
        "thesis_decay_exit_hours": hours_value,
    }


def _apply_grace(hours: float, grace: float) -> dict[str, Any]:
    hours_value = float(hours)
    return {
        "thesis_decay_negative_grace_minutes": (
            float(grace) if hours_value > 0 else DEFAULT_GRACE
        )
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
    if "thesis_decay_negative_grace_minutes" not in merged:
        merged["thesis_decay_negative_grace_minutes"] = DEFAULT_GRACE
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
    chase_long, chase_short = rng.choice(CHASE_PAIRS)
    combo: dict[str, Any] = {
        "pullback_epsilon_pct": rng.choice(EPSILON_VALUES),
        "tp_pct": rng.choice(TP_VALUES),
        "chase_long_bb_pos_max": chase_long,
        "chase_short_bb_pos_min": chase_short,
        "enable_flip_exit": False,
        **_apply_decay(decay_hours),
        "thesis_bb_drift_pts": DEFAULT_DRIFT,
        **_apply_grace(
            decay_hours,
            rng.choice(GRACE_VALUES) if decay_hours > 0 else DEFAULT_GRACE,
        ),
        **_sizing_off(),
    }
    if barriers_on:
        sl_exp, tp_exp = rng.choice(EXPONENT_PAIRS)
        combo.update(_barrier_on(sl_exp, tp_exp, rng.choice(CLAMP_PAIRS)))
    else:
        combo.update(_barrier_off())
    combo["ref_volatility_pct"] = (
        rng.choice(REF_VOL_VALUES) if barriers_on else DEFAULT_REF_VOL
    )
    return combo


def iter_dynamics_space() -> Iterator[dict[str, Any]]:
    """Enumerate the nested unique-behavior space (no unused-knob duplicates)."""
    for decay_hours in DECAY_HOURS_VALUES:
        graces = GRACE_VALUES if decay_hours > 0 else (DEFAULT_GRACE,)
        for grace in graces:
            for epsilon in EPSILON_VALUES:
                for tp_pct in TP_VALUES:
                    for chase_long, chase_short in CHASE_PAIRS:
                        for barrier in _iter_barrier_specs():
                            refs = (
                                REF_VOL_VALUES
                                if barrier["enable_dynamic_barriers"]
                                else (DEFAULT_REF_VOL,)
                            )
                            for ref_vol in refs:
                                yield {
                                    "pullback_epsilon_pct": epsilon,
                                    "tp_pct": tp_pct,
                                    "chase_long_bb_pos_max": chase_long,
                                    "chase_short_bb_pos_min": chase_short,
                                    "enable_flip_exit": False,
                                    **_apply_decay(decay_hours),
                                    "thesis_bb_drift_pts": DEFAULT_DRIFT,
                                    **_apply_grace(decay_hours, grace),
                                    **barrier,
                                    **_sizing_off(),
                                    "ref_volatility_pct": ref_vol,
                                }


def dynamics_sweep_space_size() -> int:
    return sum(1 for _ in iter_dynamics_space())


def gate_dry_run_cases(*, presets_path: Any | None = None) -> list[dict[str, Any]]:
    """Decay-off vs current lead vs barriers-on (sizing off), latest-lead gates."""
    values = current_lead_sweep_values(presets_path=presets_path)
    lead = current_lead_baseline_case(presets_path=presets_path)
    decay_off = _finalize(
        {**values, **_apply_decay(0.0), **_apply_grace(0.0, DEFAULT_GRACE)},
        include_random_entry=False,
        presets_path=presets_path,
    )
    dynamic_on = _finalize(
        {
            **values,
            "enable_dynamic_barriers": True,
            "enable_dynamic_sizing": False,
            "sl_vol_exponent": 1.0,
            "tp_vol_exponent": 1.0,
            "min_vol_mult": DEFAULT_SIZING_BAND[0],
            "max_vol_mult": DEFAULT_SIZING_BAND[1],
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


def _probe_case_name(lookback: int, atr_period: int) -> str:
    return f"lb{lookback}_atr{atr_period}"


def iter_lookback_atr_probe_cases(
    *,
    presets_path: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the current lead, then the other lookback × ATR cells."""
    lead = current_lead_baseline_case(presets_path=presets_path)
    yield lead
    lead_lookback = int(lead.get("impulse_lookback_bars") or DEFAULT_PROBE_LOOKBACK)
    lead_atr = int(lead.get("atr_period") or DEFAULT_PROBE_ATR_PERIOD)
    values = current_lead_sweep_values(presets_path=presets_path)
    for lookback in PROBE_LOOKBACK_BARS:
        for atr_period in PROBE_ATR_PERIODS:
            if lookback == lead_lookback and atr_period == lead_atr:
                continue
            yield _finalize(
                {
                    **values,
                    "impulse_lookback_bars": lookback,
                    "atr_period": atr_period,
                },
                name=_probe_case_name(lookback, atr_period),
                include_random_entry=False,
                presets_path=presets_path,
            )


def lookback_atr_probe_cases(
    *,
    presets_path: Any | None = None,
) -> list[dict[str, Any]]:
    cases = list(iter_lookback_atr_probe_cases(presets_path=presets_path))
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    lead = current_lead_baseline_case(presets_path=presets_path)
    assert cases[0]["name"] == lead["name"]
    expected = len(PROBE_LOOKBACK_BARS) * len(PROBE_ATR_PERIODS)
    assert len(cases) == expected
    return cases


def lookback_atr_probe_space_size() -> int:
    return len(PROBE_LOOKBACK_BARS) * len(PROBE_ATR_PERIODS)


__all__ = [
    "CHASE_PAIRS",
    "CLAMP_PAIRS",
    "DECAY_HOURS_VALUES",
    "DYNAMICS_SWEEP_CONFIG_COUNT",
    "EPSILON_VALUES",
    "GRACE_VALUES",
    "HELD_OVERRIDES",
    "HELD_RANDOM_ENTRY",
    "PROBE_ATR_PERIODS",
    "PROBE_LOOKBACK_BARS",
    "REF_VOL_VALUES",
    "TP_VALUES",
    "current_lead_baseline_case",
    "current_lead_sweep_values",
    "dynamics_sweep_out_dir",
    "dynamics_sweep_preset",
    "dynamics_sweep_seed",
    "dynamics_sweep_space_size",
    "gate_dry_run_cases",
    "held_random_entry",
    "iter_lookback_atr_probe_cases",
    "iter_pullback_dynamics_cases",
    "lookback_atr_probe_cases",
    "lookback_atr_probe_out_dir",
    "lookback_atr_probe_space_size",
    "pullback_dynamics_cases",
]
