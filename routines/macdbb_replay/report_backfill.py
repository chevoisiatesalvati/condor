"""Backfill archived scanner + MACD BB HTML reports for historical replay ticks."""

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

from condor.reports import ReportBuilder
from routines.lib.hl_candles import (
    HL_INFO_URL,
    configure_hl_rate_limit,
    fetch_hl_candles_between,
    trading_pair_to_hl_coin,
)
from routines.macdbb_replay.paths import TRADING_AGENTS_DIR, REPORTS_DIR, REPORTS_INDEX_PATH
from routines.macdbb_replay.models import DynamicStrategyReplayConfig
from routines.macdbb_replay.reports import (
    ParsedScannerReport,
    ScannerPairRow,
    load_scanner_reports_index,
    nearest_scanner_report,
)
from routines.macdbb_replay.scanner_queue import build_scanner_queue
from routines.macdbb_replay.session_builder import _default_strategy_params
from routines.macdbb_replay.tick_schedule import parse_tick_schedule_file
from routines.market_scanner import analyze_pair, classify_markets, format_volume

logger = logging.getLogger(__name__)

# HL 1m candleSnapshot retention is short; use 5m for older backfill ticks.
HL_1M_MAX_AGE_DAYS = 4
CANDLE_MINUTES = {"1m": 1, "5m": 5}

_INDEX_FILE = REPORTS_DIR / "reports_index.json"
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


def _read_index() -> list[dict[str, Any]]:
    if not _INDEX_FILE.exists():
        return []
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_index(entries: list[dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(REPORTS_DIR),
        suffix=".tmp",
        delete=False,
    )
    try:
        json.dump(entries, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(_INDEX_FILE))
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

    sections_html = builder._render_sections()
    meta_badges = ""
    if builder._source_type:
        meta_badges += f"<span>{builder._source_type}: {builder._source_name}</span>"
    for tag in builder._tags:
        meta_badges += f"<span>#{tag}</span>"

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
    filename = f"{ts_str}_{slug}{extra}_{new_id}.html"
    (REPORTS_DIR / filename).write_text(html_content, encoding="utf-8")

    entry = {
        "id": new_id,
        "title": builder._title,
        "filename": filename,
        "created_at": created_at.isoformat(),
        "source_type": builder._source_type,
        "source_name": builder._source_name,
        "tags": list(builder._tags),
    }
    entries = _read_index()
    entries.append(entry)
    _write_index(entries)
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


@dataclass
class CandleCache:
    _store: dict[tuple[str, str, int], list[dict[str, float]]]

    def __init__(self) -> None:
        self._store = {}

    def _hour_key(self, end: dt.datetime) -> int:
        end_utc = end.astimezone(dt.timezone.utc)
        return int(end_utc.timestamp() // 3600)

    async def get_1m_window(
        self,
        session: aiohttp.ClientSession,
        trading_pair: str,
        end: dt.datetime,
        hours: int,
    ) -> list[dict[str, float]]:
        key = (trading_pair, "1m", self._hour_key(end))
        cached = self._store.get(key)
        if cached is not None:
            return cached
        start = end - dt.timedelta(hours=hours)
        candles = await fetch_hl_candles_between(
            trading_pair,
            "1m",
            start,
            end,
            session=session,
        )
        self._store[key] = candles
        return candles

    async def get_interval_window(
        self,
        session: aiohttp.ClientSession,
        trading_pair: str,
        interval: str,
        end: dt.datetime,
        hours: int,
    ) -> list[dict[str, float]]:
        key = (trading_pair, interval, self._hour_key(end))
        cached = self._store.get(key)
        if cached is not None:
            return cached
        start = end - dt.timedelta(hours=hours)
        candles = await fetch_hl_candles_between(
            trading_pair,
            interval,
            start,
            end,
            session=session,
        )
        self._store[key] = candles
        return candles


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for index in range(1, len(values)):
        result[index] = alpha * values[index] + (1 - alpha) * result[index - 1]
    return result


def compute_macdbb_from_closes(
    closes: np.ndarray,
    *,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal_period: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> dict[str, Any] | None:
    min_required = macd_slow + macd_signal_period + bb_period
    if len(closes) < min_required:
        return None

    ema_fast = _ema(closes, macd_fast)
    ema_slow = _ema(closes, macd_slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, macd_signal_period)
    histogram = macd_line - signal_line

    n = len(closes)
    bb_mid = np.array([np.mean(closes[max(0, i - bb_period + 1) : i + 1]) for i in range(n)])
    bb_std_arr = np.array([np.std(closes[max(0, i - bb_period + 1) : i + 1]) for i in range(n)])
    bb_upper = bb_mid + bb_std * bb_std_arr
    bb_lower = bb_mid - bb_std * bb_std_arr

    close = float(closes[-1])
    macd_curr, macd_prev = float(macd_line[-1]), float(macd_line[-2])
    sig_curr, sig_prev = float(signal_line[-1]), float(signal_line[-2])
    hist_curr, hist_prev = float(histogram[-1]), float(histogram[-2])
    bb_up, bb_mid_val, bb_lo = float(bb_upper[-1]), float(bb_mid[-1]), float(bb_lower[-1])

    bb_range = bb_up - bb_lo
    bb_pos = (close - bb_lo) / bb_range if bb_range > 0 else 0.5

    bullish_cross = macd_prev < sig_prev and macd_curr >= sig_curr
    bearish_cross = macd_prev > sig_prev and macd_curr <= sig_curr
    c_long_cross = bullish_cross
    c_long_bb = close <= bb_mid_val
    c_short_cross = bearish_cross
    c_short_bb = close >= bb_up
    c_short_macd_neg = macd_curr < 0
    long_signal = c_long_cross and c_long_bb
    short_signal = c_short_cross and c_short_bb and c_short_macd_neg
    if long_signal:
        signal = "LONG"
    elif short_signal:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    trend = "bullish" if macd_curr > 0 else "bearish"
    momentum = "increasing" if abs(hist_curr) > abs(hist_prev) else "decreasing"

    return {
        "signal": signal,
        "close": close,
        "bb_up": bb_up,
        "bb_mid_val": bb_mid_val,
        "bb_lo": bb_lo,
        "bb_pos": bb_pos,
        "macd_curr": macd_curr,
        "sig_curr": sig_curr,
        "hist_curr": hist_curr,
        "trend": trend,
        "momentum": momentum,
        "c_long_cross": c_long_cross,
        "c_long_bb": c_long_bb,
        "c_short_cross": c_short_cross,
        "c_short_bb": c_short_bb,
        "c_short_macd_neg": c_short_macd_neg,
    }


def _scanner_interval_for_tick(tick_time: dt.datetime) -> str:
    age_days = (dt.datetime.now(dt.timezone.utc) - tick_time.astimezone(dt.timezone.utc)).days
    return "1m" if age_days <= HL_1M_MAX_AGE_DAYS else "5m"


def _bars_for_hours(hours: int, interval: str) -> int:
    minutes = CANDLE_MINUTES[interval]
    return max(1, (hours * 60) // minutes)


def _volume_window_bars(interval: str) -> int:
    return _bars_for_hours(24, interval)


def _quote_volume_window(candles: list[dict[str, float]], interval: str) -> float:
    window = candles[-_volume_window_bars(interval) :]
    return _quote_volume_24h(window)


def _price_change_window(candles: list[dict[str, float]], interval: str) -> float:
    window = candles[-_volume_window_bars(interval) :]
    return _price_change_pct(window)


def _quote_volume_24h(candles: list[dict[str, float]]) -> float:
    if not candles:
        return 0.0
    total = 0.0
    for candle in candles:
        try:
            total += float(candle["close"]) * float(candle["volume"])
        except (KeyError, TypeError, ValueError):
            continue
    return total


def _price_change_pct(candles: list[dict[str, float]]) -> float:
    if len(candles) < 2:
        return 0.0
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first <= 0:
        return 0.0
    return ((last - first) / first) * 100.0


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
    sessions_dir = TRADING_AGENTS_DIR / strategy_slug / "sessions"
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
    return [
        ScannerPairRow(
            pair=item["trading_pair"],
            volume_24h_usd=float(item["volume_24h_usd"]),
            price_change_24h=float(item["price_change_24h"]),
            natr_mean=float(item["natr_mean"]),
            natr_cv=float(item["natr_cv"]),
            bucket_cv=float(item["bucket_cv"]),
            price_range_pct=float(item["price_range_pct"]),
        )
        for item in items
    ]


def _classified_to_parsed_scanner(
    classified: dict[str, Any],
    *,
    lookback_hours: int,
) -> ParsedScannerReport:
    return ParsedScannerReport(
        total_analyzed=int(classified["total_analyzed"]),
        mature=_analysis_rows_to_scanner_rows(classified["mature"]),
        degen=_analysis_rows_to_scanner_rows(classified["degen"]),
        lookback_hours=lookback_hours,
    )


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

    scanner_interval = _scanner_interval_for_tick(tick_time)
    candle_minutes = CANDLE_MINUTES[scanner_interval]
    fetch_hours = max(settings.lookback_hours + 24, 30)
    min_bars = _bars_for_hours(settings.lookback_hours, scanner_interval)
    bucket_size = max(1, 15 // candle_minutes)

    ranked: list[tuple[float, dict[str, Any], list[dict[str, float]]]] = []
    for candidate in universe[: settings.candidate_pool]:
        pair = candidate["trading_pair"]
        try:
            candles = await cache.get_interval_window(
                session,
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
        volume_24h = _quote_volume_window(candles, scanner_interval)
        if volume_24h < settings.min_volume_usd:
            continue
        ranked.append((volume_24h, candidate, candles))

    ranked.sort(key=lambda row: row[0], reverse=True)
    top = ranked[: settings.top_n]
    analyses: list[dict[str, Any]] = []
    lookback_bars = _bars_for_hours(settings.lookback_hours, scanner_interval)
    for _volume, candidate, candles in top:
        analysis_candles = candles[-lookback_bars:]
        pair_info = {
            "trading_pair": candidate["trading_pair"],
            "price": float(analysis_candles[-1]["close"]),
            "price_change_pct": _price_change_window(candles, scanner_interval),
            "volume_24h_usd": _quote_volume_window(candles, scanner_interval),
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
                    session,
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
    cache = CandleCache()

    async with aiohttp.ClientSession() as session:
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
