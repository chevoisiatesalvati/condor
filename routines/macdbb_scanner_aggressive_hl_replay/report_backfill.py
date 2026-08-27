"""Backfill scanner + MACD BB HTML reports for historical replay ticks.

Deprecated for bulk historical backfill: prefer ``scripts/prefetch_hl_candles.py``
and ``scripts/build_replay_snapshots.py`` for candle-first replay pipelines.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np

from condor.reports import ReportBuilder, source_dir_name
from routines.lib.hl_candle_cache import fetch_hl_candles_between_cached
from routines.lib.binance_candle_cache import (
    fetch_binance_candles_between_cached,
    load_candles as load_binance_candles,
)
from routines.lib.hl_candle_cache import load_candles as load_hl_candles
from routines.lib.pair_format import binance_pair_from_any
from routines.lib.hl_candles import (
    HL_INFO_URL,
    configure_hl_rate_limit,
)
from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import (
    HL_1M_MAX_AGE_DAYS,
    CANDLE_MINUTES,
    bars_for_hours,
    classified_to_parsed_scanner,
    compute_macdbb_from_closes,
    price_change_window,
    quote_volume_24h,
    quote_volume_window,
    scanner_interval_for_tick,
)
from routines.macdbb_scanner_aggressive_hl_replay.paths import REPORTS_DIR, strategy_sessions_dir
from routines.macdbb_scanner_aggressive_hl_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_scanner_aggressive_hl_replay.reports import (
    ParsedScannerReport,
    ScannerPairRow,
    load_scanner_reports_index,
    nearest_scanner_report,
)
from routines.macdbb_scanner_aggressive_hl_replay.scanner_queue import build_scanner_queue
from routines.macdbb_scanner_aggressive_hl_replay.session_builder import _default_strategy_params
from routines.macdbb_scanner_aggressive_hl_replay.tick_schedule import parse_tick_schedule_file
from routines.market_scanner import analyze_pair, classify_markets, format_volume

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = None  # loaded lazily from condor.reports


def _html_template() -> str:
    global _HTML_TEMPLATE
    if _HTML_TEMPLATE is None:
        from condor.reports import _HTML_TEMPLATE as template

        _HTML_TEMPLATE = template
    return _HTML_TEMPLATE


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s-]+", "_", s).strip("_")[:40]


def _read_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_index(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
    )
    try:
        json.dump(entries, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise


def save_report_at(
    builder: ReportBuilder,
    created_at: dt.datetime,
    *,
    suffix: str = "",
) -> str:
    """Persist a report HTML file + index row with a historical timestamp."""
    created_at = created_at.astimezone(dt.timezone.utc)
    REPORTS_DIR.mkdir(exist_ok=True)

    from condor.reports import _render_meta_badges

    sections_html = builder._render_sections()
    meta_badges = _render_meta_badges(builder)

    html_content = _html_template().format(
        title=builder._title,
        created_at=created_at.strftime("%Y-%m-%d %H:%M UTC"),
        meta_badges=meta_badges,
        sections_html=sections_html,
    )

    new_id = uuid.uuid4().hex[:6]
    ts_str = created_at.strftime("%Y%m%d_%H%M%S")
    slug = _slugify(builder._title)
    extra = f"_{suffix}" if suffix else ""
    source_folder = source_dir_name(builder._source_name)
    basename = f"{ts_str}_{slug}{extra}_{new_id}.html"
    filename = f"{source_folder}/{basename}"
    report_dir = REPORTS_DIR / source_folder
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / basename).write_text(html_content, encoding="utf-8")

    entry = {
        "id": new_id,
        "title": builder._title,
        "filename": filename,
        "created_at": created_at.isoformat(),
        "source_type": builder._source_type,
        "source_name": builder._source_name,
        "tags": list(builder._tags),
    }
    index_path = report_dir / "reports_index.json"
    entries = _read_index(index_path)
    entries.append(entry)
    _write_index(index_path, entries)
    return new_id


@dataclass(frozen=True)
class BackfillSettings:
    lookback_hours: int = 6
    top_n: int = 30
    min_volume_usd: float = 2_000_000.0
    mature_count: int = 8
    degen_count: int = 8
    candidate_pool: int = 45
    macd_review_count: int = 5
    time_window_min: int = 5
    request_interval_ms: int = 600
    max_retries: int = 8
    exclude_hip3: bool = True
    cache_dir: Path | None = None
    candle_source: str = "hyperliquid"


@dataclass
class CandleCache:
    _file_cache: dict[tuple[str, str], list[dict[str, float]]]
    _file_cache_order: list[tuple[str, str]]
    _window_cache: dict[tuple[str, str, int, int], list[dict[str, float]]]
    _access_counts: dict[tuple[str, str], int]
    _load_locks: dict[tuple[str, str], asyncio.Lock]
    _session: aiohttp.ClientSession | None
    cache_dir: Path | None
    candle_source: str
    max_file_cache_entries: int
    max_window_cache_entries: int = 256

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        cache_dir: Path | None = None,
        candle_source: str = "hyperliquid",
        max_file_cache_entries: int = 80,
        max_window_cache_entries: int = 256,
    ) -> None:
        self._file_cache = {}
        self._file_cache_order = []
        self._window_cache = {}
        self._window_cache_order: list[tuple[str, str, int, int]] = []
        self._access_counts = {}
        self._load_locks = {}
        self._session = session
        self.cache_dir = cache_dir
        self.candle_source = candle_source
        self.max_file_cache_entries = max(1, max_file_cache_entries)
        self.max_window_cache_entries = max(1, max_window_cache_entries)

    def _cache_pair(self, trading_pair: str) -> str:
        if self.candle_source == "binance_perpetual":
            return binance_pair_from_any(trading_pair)
        return trading_pair

    @staticmethod
    def _filter_window(
        candles: list[dict[str, float]],
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, float]]:
        return [
            candle
            for candle in candles
            if start_ms <= int(candle["timestamp_ms"]) <= end_ms
        ]

    def _touch_file_cache(self, key: tuple[str, str]) -> None:
        if key in self._file_cache_order:
            self._file_cache_order.remove(key)
        self._file_cache_order.append(key)

    def _store_file_cache(self, key: tuple[str, str], candles: list[dict[str, float]]) -> None:
        self._file_cache[key] = candles
        self._touch_file_cache(key)
        while len(self._file_cache_order) > self.max_file_cache_entries:
            evict_key = self._file_cache_order.pop(0)
            self._file_cache.pop(evict_key, None)

    def _store_window_cache(
        self,
        key: tuple[str, str, int, int],
        candles: list[dict[str, float]],
    ) -> None:
        self._window_cache[key] = candles
        if key in self._window_cache_order:
            self._window_cache_order.remove(key)
        self._window_cache_order.append(key)
        while len(self._window_cache_order) > self.max_window_cache_entries:
            evict_key = self._window_cache_order.pop(0)
            self._window_cache.pop(evict_key, None)

    def _load_disk_candles(self, cache_pair: str, interval: str) -> list[dict[str, float]]:
        if self.candle_source == "binance_perpetual":
            return load_binance_candles(cache_pair, interval, cache_dir=self.cache_dir)
        return load_hl_candles(cache_pair, interval, cache_dir=self.cache_dir)

    async def _fetch_between_cached(
        self,
        trading_pair: str,
        interval: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[dict[str, float]]:
        cache_pair = self._cache_pair(trading_pair)
        if self.candle_source == "binance_perpetual":
            return await fetch_binance_candles_between_cached(
                cache_pair,
                interval,
                start,
                end,
                session=self._session,
                cache_dir=self.cache_dir,
                use_cache=True,
                refresh_cache=False,
                fill_gaps=True,
            )
        return await fetch_hl_candles_between_cached(
            cache_pair,
            interval,
            start,
            end,
            session=self._session,
            cache_dir=self.cache_dir,
            use_cache=True,
            refresh_cache=False,
            fill_gaps=True,
        )

    async def _maybe_promote_file_cache(self, cache_pair: str, interval: str) -> None:
        key = (cache_pair, interval)
        count = self._access_counts.get(key, 0) + 1
        self._access_counts[key] = count
        if count < 2 or key in self._file_cache:
            return

        lock = self._load_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._file_cache:
                return
            candles = self._load_disk_candles(cache_pair, interval)
            if candles:
                self._store_file_cache(key, candles)

    async def get_interval_window(
        self,
        trading_pair: str,
        interval: str,
        end: dt.datetime,
        hours: int,
    ) -> list[dict[str, float]]:
        if self._session is None:
            raise RuntimeError("CandleCache requires an aiohttp session")
        cache_pair = self._cache_pair(trading_pair)
        key = (cache_pair, interval)
        start = end - dt.timedelta(hours=hours)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        window_key = (cache_pair, interval, start_ms, end_ms)

        window_candles = self._window_cache.get(window_key)
        if window_candles is not None:
            return window_candles

        file_candles = self._file_cache.get(key)
        if file_candles is not None:
            self._touch_file_cache(key)
            sliced = self._filter_window(file_candles, start_ms, end_ms)
            self._store_window_cache(window_key, sliced)
            return sliced

        candles = await self._fetch_between_cached(
            trading_pair,
            interval,
            start,
            end,
        )
        self._store_window_cache(window_key, candles)
        await self._maybe_promote_file_cache(cache_pair, interval)
        return candles


async def fetch_binance_universe(
    session: aiohttp.ClientSession,
    *,
    top_n: int = 100,
    min_volume_usd: float = 2_000_000.0,
) -> list[dict[str, Any]]:
    """Fetch Binance USDT-M pairs by 24h quote volume. Use top_n=0 for no limit."""
    from routines.market_scanner import fetch_top_pairs

    _ = session
    rows = await fetch_top_pairs(top_n, min_volume_usd)
    return [
        {
            "trading_pair": row["trading_pair"],
            "volume_24h_usd": float(row["volume_24h_usd"]),
            "price": float(row["price"]),
        }
        for row in rows
    ]


async def fetch_hl_universe(
    session: aiohttp.ClientSession,
    *,
    exclude_hip3: bool = True,
) -> list[dict[str, Any]]:
    from routines.lib.hl_candles import _await_hl_rate_limit

    await _await_hl_rate_limit()
    async with session.post(HL_INFO_URL, json={"type": "metaAndAssetCtxs"}) as resp:
        resp.raise_for_status()
        payload = await resp.json()

    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("Unexpected metaAndAssetCtxs response shape")

    meta, asset_ctxs = payload[0], payload[1]
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    candidates: list[dict[str, Any]] = []
    for index, asset in enumerate(universe):
        if index >= len(asset_ctxs) or not isinstance(asset, dict):
            continue
        asset_name = asset.get("name", "")
        if not asset_name:
            continue
        if exclude_hip3 and ":" in asset_name:
            continue
        ctx = asset_ctxs[index] if isinstance(asset_ctxs[index], dict) else {}
        try:
            volume_24h = float(ctx.get("dayNtlVlm", 0))
        except (TypeError, ValueError):
            volume_24h = 0.0
        try:
            price = float(ctx.get("markPx", 0))
        except (TypeError, ValueError):
            price = 0.0
        trading_pair = f"{asset_name}-USD"
        candidates.append(
            {
                "trading_pair": trading_pair,
                "asset_name": asset_name,
                "volume_24h_usd": volume_24h,
                "price": price,
            }
        )
    candidates.sort(key=lambda row: row["volume_24h_usd"], reverse=True)
    return candidates


def collect_session_tick_times(
    session_nums: list[int],
    *,
    strategy_slug: str = "macdbb_scanner_aggressive_hl",
    until: dt.datetime | None = None,
) -> list[dt.datetime]:
    sessions_dir = strategy_sessions_dir(strategy_slug)
    timestamps: list[dt.datetime] = []
    for session_num in session_nums:
        journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
        if not journal_path.is_file():
            continue
        schedule = parse_tick_schedule_file(journal_path)
        for meta in schedule.values():
            ts = meta.timestamp.astimezone(dt.timezone.utc)
            if until is not None and ts >= until:
                continue
            timestamps.append(ts)
    return sorted(set(timestamps))


def _has_scanner_report_at(
    tick_time: dt.datetime,
    *,
    time_window_min: int,
) -> bool:
    return (
        nearest_scanner_report(
            load_scanner_reports_index(),
            tick_time,
            time_window_min,
        )
        is not None
    )


def _analysis_rows_to_scanner_rows(items: list[dict[str, Any]]) -> list[ScannerPairRow]:
    from routines.macdbb_scanner_aggressive_hl_replay.tick_market_state import analysis_rows_to_scanner_rows

    return analysis_rows_to_scanner_rows(items)


def _classified_to_parsed_scanner(
    classified: dict[str, Any],
    *,
    lookback_hours: int,
) -> ParsedScannerReport:
    return classified_to_parsed_scanner(classified, lookback_hours=lookback_hours)


def _save_scanner_report(
    classified: dict[str, Any],
    *,
    lookback_hours: int,
    created_at: dt.datetime,
    candle_interval: str = "1m",
) -> str:
    def _to_table(items: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "Pair": item["trading_pair"],
                "Volume 24h": format_volume(item["volume_24h_usd"]),
                "24h Chg": f"{item['price_change_24h']:+.1f}%",
                "NATR": f"{item['natr_mean']:.3f}%",
                "NATR-CV": f"{item['natr_cv']:.2f}",
                "Vol-CV": f"{item['bucket_cv']:.2f}",
                "Range": f"{item['price_range_pct']:.1f}%",
            }
            for item in items
        ]

    builder = ReportBuilder(f"Hyperliquid Market Scanner ({lookback_hours}h)")
    builder.source("routine", "hyperliquid_market_scanner").tags(
        ["scanner", "volatility", "hyperliquid", "backfill"]
    )
    builder.markdown(
        f"Analyzed {classified['total_analyzed']} Hyperliquid pairs "
        f"with {lookback_hours}h lookback on {candle_interval} candles"
    )
    builder.markdown("### Mature Markets\nHigh volume, stable volatility")
    builder.table(_to_table(classified["mature"]))
    builder.markdown("### Degen Markets\nHigh volatility, spiky activity")
    builder.table(_to_table(classified["degen"]))
    return save_report_at(builder, created_at, suffix="backfill")


def _save_macdbb_report(
    trading_pair: str,
    interval: str,
    metrics: dict[str, Any],
    *,
    created_at: dt.datetime,
) -> str:
    signal = metrics["signal"]
    table_columns = [
        "Pair",
        "Interval",
        "Signal",
        "Price",
        "BB Pos %",
        "BB Mid",
        "BB Upper",
        "MACD",
        "Signal Line",
        "Histogram",
        "Trend",
        "Momentum",
    ]
    table_row = {
        "Pair": trading_pair,
        "Interval": interval,
        "Signal": signal,
        "Price": round(metrics["close"], 8),
        "BB Pos %": round(metrics["bb_pos"] * 100, 2),
        "BB Mid": round(metrics["bb_mid_val"], 8),
        "BB Upper": round(metrics["bb_up"], 8),
        "MACD": round(metrics["macd_curr"], 8),
        "Signal Line": round(metrics["sig_curr"], 8),
        "Histogram": round(metrics["hist_curr"], 8),
        "Trend": metrics["trend"],
        "Momentum": metrics["momentum"],
    }
    conditions_rows = [
        {
            "Rule": "LONG (2/2)",
            "Condition": "Bullish crossover",
            "Met": metrics["c_long_cross"],
        },
        {
            "Rule": "LONG (2/2)",
            "Condition": "Price <= midBB",
            "Met": metrics["c_long_bb"],
        },
        {
            "Rule": "SHORT (3/3)",
            "Condition": "Bearish crossover",
            "Met": metrics["c_short_cross"],
        },
        {
            "Rule": "SHORT (3/3)",
            "Condition": "Price >= upperBB",
            "Met": metrics["c_short_bb"],
        },
        {
            "Rule": "SHORT (3/3)",
            "Condition": "MACD < 0",
            "Met": metrics["c_short_macd_neg"],
        },
    ]

    builder = ReportBuilder(f"MACD+BB: {trading_pair} ({interval})")
    builder.source("routine", "macd_bb_analysis").tags(
        ["technical-analysis", "macd", "bollinger", "backfill"]
    )
    builder.manual_order()
    builder.kpi(
        "Signal",
        signal,
        trend=(
            "positive"
            if signal == "LONG"
            else "negative" if signal == "SHORT" else "neutral"
        ),
    )
    builder.kpi("BB Position", f"{metrics['bb_pos'] * 100:.1f}%")
    builder.kpi("Histogram", f"{metrics['hist_curr']:.6g}")
    builder.table([table_row], columns=table_columns)
    builder.table(conditions_rows, columns=["Rule", "Condition", "Met"])
    return save_report_at(builder, created_at, suffix="backfill")


async def backfill_tick(
    tick_time: dt.datetime,
    *,
    session: aiohttp.ClientSession,
    universe: list[dict[str, Any]],
    cache: CandleCache,
    settings: BackfillSettings,
) -> dict[str, int]:
    if _has_scanner_report_at(tick_time, time_window_min=settings.time_window_min):
        return {"scanner": 0, "macd": 0, "skipped": 1}

    scanner_interval = scanner_interval_for_tick(tick_time)
    candle_minutes = CANDLE_MINUTES[scanner_interval]
    fetch_hours = max(settings.lookback_hours + 24, 30)
    min_bars = bars_for_hours(settings.lookback_hours, scanner_interval)
    bucket_size = max(1, 15 // candle_minutes)

    ranked: list[tuple[float, dict[str, Any], list[dict[str, float]]]] = []
    for candidate in universe[: settings.candidate_pool]:
        pair = candidate["trading_pair"]
        try:
            candles = await cache.get_interval_window(
                pair,
                scanner_interval,
                tick_time,
                fetch_hours,
            )
        except Exception as error:
            logger.debug(
                "%s fetch failed for %s at %s: %s",
                scanner_interval,
                pair,
                tick_time,
                error,
            )
            continue
        if len(candles) < min_bars:
            continue
        volume_24h = quote_volume_window(candles, scanner_interval)
        if volume_24h < settings.min_volume_usd:
            continue
        ranked.append((volume_24h, candidate, candles))

    ranked.sort(key=lambda row: row[0], reverse=True)
    top = ranked[: settings.top_n]
    analyses: list[dict[str, Any]] = []
    lookback_bars = bars_for_hours(settings.lookback_hours, scanner_interval)
    for _volume, candidate, candles in top:
        analysis_candles = candles[-lookback_bars:]
        pair_info = {
            "trading_pair": candidate["trading_pair"],
            "price": float(analysis_candles[-1]["close"]),
            "price_change_pct": price_change_window(candles, scanner_interval),
            "volume_24h_usd": quote_volume_window(candles, scanner_interval),
        }
        result = analyze_pair(
            analysis_candles,
            pair_info,
            bucket_size=bucket_size,
        )
        if result:
            analyses.append(result)

    if not analyses:
        logger.warning(
            "No scanner analyses at %s (interval=%s)",
            tick_time,
            scanner_interval,
        )
        return {"scanner": 0, "macd": 0, "skipped": 0}

    classified = classify_markets(analyses, settings.mature_count, settings.degen_count)
    classified["all_analyzed"] = analyses
    _save_scanner_report(
        classified,
        lookback_hours=settings.lookback_hours,
        created_at=tick_time,
        candle_interval=scanner_interval,
    )

    parsed_scanner = _classified_to_parsed_scanner(
        classified,
        lookback_hours=settings.lookback_hours,
    )
    strategy_params = _default_strategy_params(DynamicStrategyReplayConfig())
    queue = build_scanner_queue(parsed_scanner, strategy_params)
    macd_pairs = queue.macd_pairs[: settings.macd_review_count]

    macd_saved = 0
    for pair in macd_pairs:
        for interval, hours in (("1h", 250), ("4h", 400)):
            try:
                candles = await cache.get_interval_window(
                    pair,
                    interval,
                    tick_time,
                    hours,
                )
            except Exception as error:
                logger.debug(
                    "MACD fetch failed %s %s at %s: %s",
                    pair,
                    interval,
                    tick_time,
                    error,
                )
                continue
            closes = np.array([float(c["close"]) for c in candles], dtype=float)
            metrics = compute_macdbb_from_closes(closes)
            if metrics is None:
                continue
            _save_macdbb_report(pair, interval, metrics, created_at=tick_time)
            macd_saved += 1

    return {"scanner": 1, "macd": macd_saved, "skipped": 0}


async def run_backfill(
    tick_times: list[dt.datetime],
    settings: BackfillSettings | None = None,
) -> dict[str, int]:
    settings = settings or BackfillSettings()
    configure_hl_rate_limit(
        request_interval_ms=settings.request_interval_ms,
        max_retries=settings.max_retries,
    )

    totals = {"scanner": 0, "macd": 0, "skipped": 0, "errors": 0}

    async with aiohttp.ClientSession() as session:
        cache = CandleCache(session=session, cache_dir=settings.cache_dir)
        universe = await fetch_hl_universe(session, exclude_hip3=settings.exclude_hip3)
        logger.info(
            "Backfill: %d tick times, %d universe candidates, interval=%dms",
            len(tick_times),
            len(universe),
            settings.request_interval_ms,
        )
        for index, tick_time in enumerate(tick_times, start=1):
            try:
                result = await backfill_tick(
                    tick_time,
                    session=session,
                    universe=universe,
                    cache=cache,
                    settings=settings,
                )
            except Exception:
                logger.exception("Backfill failed at %s", tick_time)
                totals["errors"] += 1
                continue
            for key in ("scanner", "macd", "skipped"):
                totals[key] += result.get(key, 0)
            if index % 10 == 0 or index == len(tick_times):
                logger.info(
                    "Progress %d/%d — scanner=%d macd=%d skipped=%d errors=%d",
                    index,
                    len(tick_times),
                    totals["scanner"],
                    totals["macd"],
                    totals["skipped"],
                    totals["errors"],
                )
    return totals


# Re-export shared helpers for backward-compatible imports.
_quote_volume_24h = quote_volume_24h

__all__ = [
    "BackfillSettings",
    "CandleCache",
    "_quote_volume_24h",
    "collect_session_tick_times",
    "compute_macdbb_from_closes",
    "fetch_hl_universe",
    "fetch_binance_universe",
    "run_backfill",
]
