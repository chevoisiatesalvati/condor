"""Auto-promote pullback sweep leaders: preset, backtest report, Telegram."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
VERIFY_DIR_NAME = "verify"


def lead_preset_names() -> list[str]:
    from condor.strategy_runners.macdbb_pullback.presets import known_preset_names

    return sorted(
        name
        for name in known_preset_names()
        if str(name).startswith(PRESET_NAME_PREFIX)
    )


def preset_has_backtest_report(preset_name: str) -> bool:
    from condor.reports import list_reports

    entries, _total = list_reports(
        source_type="routine",
        source_names=["macdbb_pullback_hl_backtest"],
        tag=preset_name,
        limit=1,
    )
    return bool(entries)


def lead_presets_missing_reports() -> list[str]:
    return [name for name in lead_preset_names() if not preset_has_backtest_report(name)]


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
    "thesis_decay_negative_grace_minutes",
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
    annualized_cap_norm: float | None = None


@dataclass
class SweepLeaderState:
    anchor_established: bool = False
    best_pnl: float = float("-inf")
    best_name: str = ""
    promote_count: int = 0
    verify_anchor_established: bool = False
    best_annual_cap_norm: float = float("-inf")
    best_verified_name: str = ""
    pending_verify_name: str = ""

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
                verify_anchor_established=bool(raw.get("verify_anchor_established")),
                best_annual_cap_norm=float(
                    raw.get("best_annual_cap_norm", float("-inf"))
                ),
                best_verified_name=str(raw.get("best_verified_name") or ""),
                pending_verify_name=str(raw.get("pending_verify_name") or ""),
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


def latest_sweep_lead_number(presets_path: Any | None = None) -> int | None:
    used = existing_sweep_lead_numbers(presets_path)
    if not used:
        return None
    return max(used)


def latest_sweep_lead_preset(presets_path: Any | None = None) -> str | None:
    lead_num = latest_sweep_lead_number(presets_path)
    if lead_num is None:
        return None
    return f"{PRESET_NAME_PREFIX}{lead_num:03d}"


def _annualized_from_result(result: PullbackSweepResult) -> float | None:
    if result.annualized_cap_norm is not None:
        return float(result.annualized_cap_norm)
    stats = result.stats or {}
    raw = stats.get("annualized_cap_norm")
    if raw is None:
        return None
    return float(raw)


def verify_results_dir(out_dir: Any) -> Path:
    return Path(out_dir) / VERIFY_DIR_NAME


def verify_result_path(out_dir: Any, name: str) -> Path:
    return verify_results_dir(out_dir) / f"{name}.json"


def load_verify_result(out_dir: Any, name: str) -> dict[str, Any] | None:
    path = verify_result_path(out_dir, name)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable verify result %s", path)
        return None
    if not isinstance(loaded, dict) or "stats" not in loaded:
        return None
    return loaded


def save_verify_result(out_dir: Any, result: dict[str, Any]) -> Path:
    directory = verify_results_dir(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result['name']}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def default_verify_range_start(range_end_utc: str) -> str:
    """One calendar year before ``range_end_utc`` (Feb 29 → Feb 28)."""
    from routines.macdbb_scanner_aggressive_hl_replay.replay_range import iso_utc
    from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_iso_utc

    end = parse_iso_utc(range_end_utc)
    try:
        start = end.replace(year=end.year - 1)
    except ValueError:
        start = end - dt.timedelta(days=365)
    return iso_utc(start)


def verify_range_coverage_gap(
    snapshot_dir: str,
    range_start_utc: str,
    range_end_utc: str,
) -> Any:
    """Return a coverage gap if the verify window is not fully in the snapshot store."""
    from routines.macdbb_scanner_aggressive_hl_replay.models import (
        DynamicStrategyReplayConfig,
    )
    from routines.macdbb_scanner_aggressive_hl_replay.replay_range import (
        requested_range_exceeds_coverage,
    )

    config = DynamicStrategyReplayConfig(
        replay_mode="timeline_backtest",
        data_source="snapshots",
        snapshot_dir=snapshot_dir,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
    )
    return requested_range_exceeds_coverage(config)


class LeaderTracker:
    """Track screen (30d) and verify (1y annualized) bars independently."""

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

    def consider_screen(self, result: PullbackSweepResult) -> bool:
        """Update the 30d screen bar. Return True if this config should be 1y-verified."""
        pnl = float(result.pnl)
        with self._lock:
            if not self._state.anchor_established:
                if pnl <= 0:
                    if pnl > self._state.best_pnl:
                        self._state.best_pnl = pnl
                        self._state.best_name = result.name
                        self._save()
                    return False
                self._state.anchor_established = True
                self._state.best_pnl = pnl
                self._state.best_name = result.name
                self._state.pending_verify_name = result.name
                self._save()
                logger.info(
                    "Pullback sweep screen anchor: %s cap_norm=%+.2f (verify, no promote yet)",
                    result.name,
                    pnl,
                )
                return True

            if pnl <= self._state.best_pnl:
                return False

            self._state.best_pnl = pnl
            self._state.best_name = result.name
            self._state.pending_verify_name = result.name
            self._save()
            logger.info(
                "Pullback sweep screen leader: %s cap_norm=%+.2f (queue 1y verify)",
                result.name,
                pnl,
            )
            return True

    def consider_verified(self, result: PullbackSweepResult) -> PromoteJob | None:
        """Rank a 1y verify run by annualized cap-norm. Promote only after the verify bar."""
        annualized = _annualized_from_result(result)
        with self._lock:
            self._state.pending_verify_name = ""
            if annualized is None:
                logger.warning(
                    "Verify consider skipped for %s: missing annualized_cap_norm",
                    result.name,
                )
                self._save()
                return None
            if not self._state.verify_anchor_established:
                self._state.verify_anchor_established = True
                self._state.best_annual_cap_norm = annualized
                self._state.best_verified_name = result.name
                self._save()
                logger.info(
                    "Pullback verify anchor established: %s annualized=%+.2f (no promote yet)",
                    result.name,
                    annualized,
                )
                return None

            if annualized <= self._state.best_annual_cap_norm or annualized <= 0:
                logger.info(
                    "Pullback verify did not promote %s annualized=%+.2f (bar=%+.2f)",
                    result.name,
                    annualized,
                    self._state.best_annual_cap_norm,
                )
                self._save()
                return None

            self._state.promote_count += 1
            self._state.best_annual_cap_norm = annualized
            self._state.best_verified_name = result.name
            lead_num = next_sweep_lead_number(
                self._presets_path,
                at_least=self._state.promote_count,
            )
            self._state.promote_count = lead_num
            self._save()

        preset_name = f"{PRESET_NAME_PREFIX}{lead_num:03d}"
        output_tag = f"lead_{lead_num:03d}"
        logger.info(
            "New pullback verify leader #%d: %s annualized=%+.2f -> preset %s",
            lead_num,
            result.name,
            annualized,
            preset_name,
        )
        return PromoteJob(
            result=result,
            preset_name=preset_name,
            output_tag=output_tag,
        )

    def consider(self, result: PullbackSweepResult) -> PromoteJob | None:
        """Track net-PnL leader; emit promote jobs after first positive screen anchor.

        Used by ``--no-verify`` and existing tests. Verify-enabled sweeps must call
        ``consider_screen`` then ``consider_verified`` instead, so 30d luck cannot
        write a preset.
        """
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
    annualized = result.annualized_cap_norm
    if annualized is None:
        annualized = stats.get("annualized_cap_norm")
    if annualized is not None:
        window = stats.get("window_days")
        window_note = f" ({float(window):.1f}d window)" if window else ""
        lines.append(f"Annualized cap-norm: ${float(annualized):+.2f}{window_note}")
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


async def run_backtest_for_preset(
    preset_name: str,
    *,
    range_start_utc: str = "",
    range_end_utc: str = "",
    snapshot_dir: str = "data/replay_snapshots_binance_60s",
    candle_source: str = "binance_perpetual",
    total_amount_quote: float = 100.0,
) -> tuple[str | None, str]:
    """Run macdbb_pullback_hl_backtest for a named preset and persist the UI report."""
    from condor.reports import get_last_report_id, reset_last_report_id
    from routines.macdbb_pullback_hl_backtest import Config, run as run_pullback_backtest

    reset_last_report_id()
    config = Config(
        preset=preset_name,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        snapshot_dir=snapshot_dir,
        candle_source=candle_source,  # type: ignore[arg-type]
        total_amount_quote=float(total_amount_quote),
        live_equivalent_queue=True,
    )
    result = await run_pullback_backtest(config, None)
    text = result.text if hasattr(result, "text") else str(result)
    return get_last_report_id(), text


async def send_report_html_telegram(
    chat_id: str, preset_name: str, report_id: str | None
) -> None:
    from condor.routine_hooks import _resolve_report_html
    from condor.routine_store import _http_bot

    await _http_bot.send_message(
        chat_id=chat_id,
        text=f"Pullback backtest report saved\nPreset: {preset_name}",
    )
    if not report_id:
        return
    html, filename = _resolve_report_html(report_id, None)
    await _http_bot.send_document(
        chat_id=chat_id,
        document=html.encode("utf-8"),
        caption=f"Backtest report: {preset_name}",
        filename=filename or f"{preset_name}.html",
    )


async def save_lead_reports_from_shared(
    shared: dict[str, Any],
    preset_names: list[str],
    *,
    total_amount_quote: float = 100.0,
) -> list[tuple[str, str | None]]:
    """Simulate each lead on an already-hydrated tape and save Condor UI reports.

    Intended to run after sweep workers exit so it does not allocate a second tape.
    """
    from routines.macdbb_pullback_hl_backtest import (
        Config,
        pullback_session_table_row,
        save_pullback_backtest_report,
    )
    from routines.macdbb_pullback_hl_replay.presets import resolve_pullback_config
    from routines.macdbb_pullback_hl_replay.simulator import simulate_pullback_session

    unique_names = list(dict.fromkeys(preset_names))
    tapes = shared.get("signal_tapes") or {}
    loader = shared["loader"]
    saved: list[tuple[str, str | None]] = []
    for preset_name in unique_names:
        logger.info("Saving pullback backtest report for %s on shared tape", preset_name)
        kwargs = {
            **shared["base_kwargs"],
            "preset": preset_name,
            "total_amount_quote": float(total_amount_quote),
        }
        config = resolve_pullback_config(Config(**kwargs))
        all_trades: list[Any] = []
        session_rows: list[dict[str, Any]] = []
        for session_num, tick_meta_map in shared["parsed_sessions"].items():
            _pairs, _ticks, trades, summary = simulate_pullback_session(
                session_num=session_num,
                tick_meta_map=tick_meta_map,
                reports_by_pair=shared["reports_by_pair"],
                config=config,
                signal_config=loader,
                hl_price_cache=shared["hl_caches_by_session"].get(session_num),
                hl_candle_cache=shared["hl_candle_cache"],
                hl_barrier_candle_cache=shared["hl_barrier_candle_cache"],
                hl_vol_candle_cache=shared["hl_vol_candle_cache"],
                signal_tape=tapes.get(session_num),
                collect_debug_rows=False,
            )
            if summary.get("status") == "skipped_no_price_data":
                continue
            all_trades.extend(trades)
            session_rows.append(
                pullback_session_table_row(
                    session_num,
                    tick_count=len(tick_meta_map),
                    trades=trades,
                )
            )
        _text, report_id = await save_pullback_backtest_report(
            config,
            all_trades=all_trades,
            session_rows=session_rows,
        )
        logger.info(
            "Saved report id=%s for %s trades=%d",
            report_id,
            preset_name,
            len(all_trades),
        )
        saved.append((preset_name, report_id))
    return saved


def start_lead_report_process(
    *,
    preset_name: str,
    range_start_utc: str,
    range_end_utc: str,
    snapshot_dir: str,
    candle_source: str = "binance_perpetual",
    total_amount_quote: float = 100.0,
    telegram_chat_id: str | None = None,
    log_dir: Any | None = None,
    repo_root: Any | None = None,
) -> int:
    """Spawn the pullback backtest routine so the Condor UI gets a report."""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    log_path_dir = (
        Path(log_dir)
        if log_dir is not None
        else root / "data" / "backtests" / "pullback_lead_reports"
    )
    log_path_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_path_dir / f"{preset_name}.log"
    command = [
        sys.executable,
        str(root / "scripts" / "run_macdbb_pullback_lead_report.py"),
        "--preset",
        preset_name,
        "--range-start",
        range_start_utc,
        "--range-end",
        range_end_utc,
        "--snapshot-dir",
        snapshot_dir,
        "--candle-source",
        candle_source,
        "--total-amount-quote",
        str(total_amount_quote),
    ]
    if telegram_chat_id:
        command.extend(["--telegram-chat-id", str(telegram_chat_id)])
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        f"{root}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(root)
    )
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    logger.info(
        "Started pullback lead report pid=%d preset=%s log=%s",
        process.pid,
        preset_name,
        log_path,
    )
    return int(process.pid)


__all__ = [
    "LeaderTracker",
    "PRESET_NAME_PREFIX",
    "PRESET_STRIP_KEYS",
    "PromoteJob",
    "PullbackSweepResult",
    "SweepLeaderState",
    "VERIFY_DIR_NAME",
    "consider_and_promote",
    "default_telegram_chat_id",
    "default_verify_range_start",
    "existing_sweep_lead_numbers",
    "latest_sweep_lead_number",
    "latest_sweep_lead_preset",
    "lead_preset_names",
    "lead_presets_missing_reports",
    "load_verify_result",
    "next_sweep_lead_number",
    "preset_has_backtest_report",
    "promote_leader",
    "promote_telegram_text",
    "register_sweep_lead_preset",
    "run_backtest_for_preset",
    "save_lead_reports_from_shared",
    "save_verify_result",
    "send_promote_telegram",
    "send_promote_telegram_sync",
    "send_report_html_telegram",
    "start_lead_report_process",
    "strategy_overrides_from_result",
    "verify_range_coverage_gap",
    "verify_result_path",
    "verify_results_dir",
]
