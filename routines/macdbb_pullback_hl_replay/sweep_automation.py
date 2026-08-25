"""Auto-promote pullback sweep leaders: preset + Telegram (no refine)."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
import yaml

from condor.strategy_runners.macdbb.presets import PRESET_CAPITAL_KEYS
from condor.strategy_runners.macdbb_pullback.presets import (
    AGENT_SLUG,
    DEFAULT_WINNER_PRESET,
    invalidate_preset_cache,
)

logger = logging.getLogger(__name__)

PRESET_NAME_PREFIX = "pullback_sweep_lead_"

PRESET_STRIP_KEYS = frozenset(
    {
        "preset",
        "name",
        "session_nums",
        "sessions",
        "range_start_utc",
        "range_end_utc",
        "snapshot_dir",
        "candle_source",
        "price_source",
        "hl_cache_dir",
        "live_equivalent_queue",
        "write_csv",
        "auto_update_snapshots",
        *PRESET_CAPITAL_KEYS,
    }
)

STRATEGY_PRESET_KEYS: tuple[str, ...] = (
    "frequency_sec",
    "impulse_atr_mult",
    "pullback_epsilon_pct",
    "sl_pct",
    "tp_pct",
    "enable_dynamic_barriers",
    "ref_volatility_pct",
    "sl_vol_exponent",
    "tp_vol_exponent",
    "sl_min_pct",
    "sl_max_pct",
    "tp_min_pct",
    "tp_max_pct",
    "enable_dynamic_sizing",
    "min_vol_mult",
    "max_vol_mult",
    "chase_long_bb_pos_max",
    "chase_short_bb_pos_min",
    "bb_proximity_epsilon_pct",
    "impulse_lookback_bars",
    "atr_period",
    "pullback_timeout_hours",
    "sl_symbol_cooldown_hours",
    "enable_flip_exit",
    "enable_thesis_decay_exit",
    "thesis_decay_exit_hours",
    "thesis_bb_drift_pts",
    "flip_confirm_ticks",
    "flip_cooldown_hours",
)


@dataclass
class PullbackSweepResult:
    name: str
    pnl: float
    trades: int
    overrides: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class SweepLeaderState:
    anchor_established: bool = False
    best_pnl: float = float("-inf")
    best_name: str = ""
    promote_count: int = 0

    @classmethod
    def load(cls, path: Any) -> SweepLeaderState:
        from pathlib import Path

        state_path = Path(path)
        if not state_path.is_file():
            return cls()
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            return cls(
                anchor_established=bool(raw.get("anchor_established")),
                best_pnl=float(raw.get("best_pnl", float("-inf"))),
                best_name=str(raw.get("best_name") or ""),
                promote_count=int(raw.get("promote_count") or 0),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            logger.warning("Could not load pullback automation state from %s: %s", path, error)
            return cls()

    def save(self, path: Any) -> None:
        from pathlib import Path

        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


@dataclass(frozen=True)
class PromoteJob:
    result: PullbackSweepResult
    preset_name: str
    output_tag: str


def existing_sweep_lead_numbers(presets_path: Any | None = None) -> set[int]:
    """Lead indices already present in presets.yaml (``pullback_sweep_lead_NNN``)."""
    from pathlib import Path

    from condor.strategy_runners.macdbb_pullback.presets import _resolve_presets_yaml

    yaml_path = Path(presets_path) if presets_path is not None else _resolve_presets_yaml()
    if yaml_path is None or not yaml_path.is_file():
        return set()
    try:
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(loaded, dict):
        return set()
    names = set(loaded.get("agent_strategy_preset_names") or [])
    names.update((loaded.get("dynamic_preset_overrides") or {}).keys())
    names.update((loaded.get("labels") or {}).keys())
    found: set[int] = set()
    prefix = PRESET_NAME_PREFIX
    for name in names:
        if not str(name).startswith(prefix):
            continue
        suffix = str(name)[len(prefix) :]
        if suffix.isdigit():
            found.add(int(suffix))
    return found


def next_sweep_lead_number(
    presets_path: Any | None = None,
    *,
    at_least: int = 1,
) -> int:
    used = existing_sweep_lead_numbers(presets_path)
    lead_num = max(1, int(at_least))
    while lead_num in used:
        lead_num += 1
    return lead_num


class LeaderTracker:
    """Track net-PnL leader; emit promote jobs after first positive anchor."""

    def __init__(self, state_path: Any, *, presets_path: Any | None = None) -> None:
        from pathlib import Path

        self._state_path = Path(state_path)
        self._presets_path = Path(presets_path) if presets_path is not None else None
        self._state = SweepLeaderState.load(self._state_path)
        self._lock = threading.Lock()

    @property
    def state(self) -> SweepLeaderState:
        return self._state

    def _save(self) -> None:
        self._state.save(self._state_path)

    def consider(self, result: PullbackSweepResult) -> PromoteJob | None:
        pnl = float(result.pnl)
        with self._lock:
            if not self._state.anchor_established:
                if pnl <= 0:
                    if pnl > self._state.best_pnl:
                        self._state.best_pnl = pnl
                        self._state.best_name = result.name
                        self._save()
                    return None
                self._state.anchor_established = True
                self._state.best_pnl = pnl
                self._state.best_name = result.name
                self._save()
                logger.info(
                    "Pullback sweep anchor established: %s pnl=%+.2f (no promote yet)",
                    result.name,
                    pnl,
                )
                return None

            if pnl <= self._state.best_pnl:
                return None

            self._state.promote_count += 1
            self._state.best_pnl = pnl
            self._state.best_name = result.name
            lead_num = next_sweep_lead_number(
                self._presets_path,
                at_least=self._state.promote_count,
            )
            self._state.promote_count = lead_num
            self._save()

        preset_name = f"{PRESET_NAME_PREFIX}{lead_num:03d}"
        output_tag = f"lead_{lead_num:03d}"
        logger.info(
            "New pullback sweep leader #%d: %s pnl=%+.2f -> preset %s",
            lead_num,
            result.name,
            pnl,
            preset_name,
        )
        return PromoteJob(
            result=result,
            preset_name=preset_name,
            output_tag=output_tag,
        )


def strategy_overrides_from_result(result: PullbackSweepResult) -> dict[str, Any]:
    source = dict(result.overrides or {})
    filtered = {
        key: source[key]
        for key in STRATEGY_PRESET_KEYS
        if key in source and key not in PRESET_STRIP_KEYS
    }
    return filtered


def register_dynamic_preset(
    preset_name: str,
    preset_overrides: dict[str, Any],
    *,
    label: str,
    presets_path: Any | None = None,
    replace_existing: bool = False,
) -> None:
    """Append a sweep-lead preset without touching winner defaults."""
    from pathlib import Path

    from condor.strategy_runners.macdbb_pullback.presets import _resolve_presets_yaml

    yaml_path = Path(presets_path) if presets_path is not None else _resolve_presets_yaml()
    if yaml_path is None:
        raise FileNotFoundError("pullback presets.yaml not found")
    bundle: dict[str, Any] = {}
    if yaml_path.is_file():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            bundle = loaded

    winner_preset = str(bundle.get("current_winner_preset") or DEFAULT_WINNER_PRESET)
    default_preset = str(bundle.get("default_agent_strategy_preset") or winner_preset)

    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_name in dynamic_overrides and not replace_existing:
        raise ValueError(f"Preset {preset_name!r} already exists in {yaml_path}")

    filtered = {
        key: value
        for key, value in preset_overrides.items()
        if key not in PRESET_STRIP_KEYS
    }
    dynamic_overrides[preset_name] = filtered

    labels = bundle.setdefault("labels", {})
    labels[preset_name] = label

    names = list(bundle.get("agent_strategy_preset_names") or [])
    if preset_name not in names:
        names.append(preset_name)
    bundle["agent_strategy_preset_names"] = names
    bundle["current_winner_preset"] = winner_preset
    bundle["default_agent_strategy_preset"] = default_preset

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(bundle, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    from condor.agents.preset_store import invalidate_agent_preset_cache

    invalidate_preset_cache()
    invalidate_agent_preset_cache(AGENT_SLUG)


def register_sweep_lead_preset(
    preset_name: str,
    preset_overrides: dict[str, Any],
    *,
    presets_path: Any | None = None,
) -> None:
    register_dynamic_preset(
        preset_name,
        preset_overrides,
        label=f"Sweep lead {preset_name.removeprefix(PRESET_NAME_PREFIX)}",
        presets_path=presets_path,
        replace_existing=False,
    )


def promote_leader(
    job: PromoteJob,
    *,
    presets_path: Any | None = None,
) -> dict[str, Any]:
    overrides = strategy_overrides_from_result(job.result)
    register_sweep_lead_preset(
        job.preset_name,
        overrides,
        presets_path=presets_path,
    )
    return overrides


def promote_telegram_text(job: PromoteJob) -> str:
    result = job.result
    stats = result.stats or {}
    lines = [
        "New pullback sweep leader",
        f"Config: {result.name}",
        f"Net PnL: ${float(stats.get('net_pnl_quote', result.pnl)):+.2f}",
    ]
    cap_norm = stats.get("capital_normalized_pnl")
    if cap_norm is not None:
        lines.append(f"Cap-norm ($100): ${float(cap_norm):+.2f}")
    avg_notional = stats.get("avg_notional")
    if avg_notional is not None:
        lines.append(f"Avg notional: ${float(avg_notional):.2f}")
    lines.extend(
        [
            f"Trades: {result.trades}",
            f"Immediate: {stats.get('immediate', 0)}  Pullback: {stats.get('pullback', 0)}",
            (
                f"SL: {stats.get('sl_hits', stats.get('stop_loss_trades', 0))}  "
                f"TP: {stats.get('tp_hits', stats.get('take_profit_trades', 0))}  "
                f"Decay: {stats.get('thesis_decay', stats.get('thesis_decay_trades', 0))}"
            ),
            f"Preset: {job.preset_name}",
        ]
    )
    return "\n".join(lines)


async def send_promote_telegram(chat_id: str, job: PromoteJob) -> None:
    from condor.routine_store import _http_bot

    await _http_bot.send_message(chat_id=chat_id, text=promote_telegram_text(job))


def send_promote_telegram_sync(chat_id: str, job: PromoteJob) -> None:
    """Send immediately from a sync callback (event loop may be blocked by workers)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
    if not token:
        logger.warning("Telegram promote skipped: TELEGRAM_TOKEN not set")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=30) as client:
        response = client.post(
            url,
            json={"chat_id": chat_id, "text": promote_telegram_text(job)},
        )
    payload = response.json()
    if not payload.get("ok"):
        logger.warning("Telegram sendMessage failed: %s", response.text)


def default_telegram_chat_id() -> str | None:
    raw = os.environ.get("ADMIN_USER_ID") or os.environ.get("SWEEP_TELEGRAM_CHAT_ID")
    if not (raw and str(raw).strip()):
        from dotenv import load_dotenv

        load_dotenv()
        raw = os.environ.get("ADMIN_USER_ID") or os.environ.get("SWEEP_TELEGRAM_CHAT_ID")
    return str(raw).strip() if raw else None


def consider_and_promote(
    tracker: LeaderTracker,
    result: PullbackSweepResult,
    *,
    presets_path: Any | None = None,
    telegram_chat_id: str | None = None,
) -> PromoteJob | None:
    if presets_path is not None and tracker._presets_path is None:
        from pathlib import Path

        tracker._presets_path = Path(presets_path)
    job = tracker.consider(result)
    if job is None:
        return None
    promote_leader(job, presets_path=presets_path)
    return job


__all__ = [
    "LeaderTracker",
    "PRESET_NAME_PREFIX",
    "PRESET_STRIP_KEYS",
    "PromoteJob",
    "PullbackSweepResult",
    "SweepLeaderState",
    "consider_and_promote",
    "default_telegram_chat_id",
    "existing_sweep_lead_numbers",
    "next_sweep_lead_number",
    "promote_leader",
    "promote_telegram_text",
    "register_sweep_lead_preset",
    "send_promote_telegram",
    "send_promote_telegram_sync",
    "strategy_overrides_from_result",
]
