"""Shared Binance / Hyperliquid pair universe helpers for aligned backtests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Literal

import aiohttp

from routines.lib.hl_candles import trading_pair_to_hl_coin
from routines.lib.pair_format import binance_pair_from_any, hl_pair_from_any
from routines.macdbb_replay.report_backfill import fetch_binance_universe, fetch_hl_universe

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_INTERSECTION_MANIFEST = ROOT_DIR / "data" / "shared_universe" / "intersection.json"

# HL coin name -> canonical base used for cross-venue matching.
_HL_CANONICAL_BASE: dict[str, str] = {
    "kPEPE": "1000PEPE",
}


def base_asset_from_pair(trading_pair: str) -> str:
    text = trading_pair.upper()
    for suffix in ("-USDT", "-USD"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text.split("-", 1)[0]


def canonical_base(asset_or_pair: str) -> str:
    base = base_asset_from_pair(asset_or_pair) if "-" in asset_or_pair else asset_or_pair.upper()
    return _HL_CANONICAL_BASE.get(base, base)


def hl_canonical_base(trading_pair: str) -> str:
    coin = trading_pair_to_hl_coin(trading_pair)
    return _HL_CANONICAL_BASE.get(coin, coin)


async def fetch_intersection_universe(
    session: aiohttp.ClientSession,
    *,
    top_n_per_exchange: int = 100,
    max_pairs: int = 60,
    min_volume_usd: float = 2_000_000.0,
    exclude_hip3: bool = True,
    rank_by: Literal["binance", "hl", "min"] = "min",
) -> list[dict[str, Any]]:
    """Return pairs listed on both venues, ranked by cross-venue liquidity."""
    binance_rows = await fetch_binance_universe(
        session,
        top_n=top_n_per_exchange,
        min_volume_usd=min_volume_usd,
    )
    hl_rows = await fetch_hl_universe(session, exclude_hip3=exclude_hip3)
    hl_rows = [row for row in hl_rows if float(row.get("volume_24h_usd", 0)) >= min_volume_usd]
    hl_rows = hl_rows[:top_n_per_exchange]

    binance_by_base: dict[str, dict[str, Any]] = {}
    for row in binance_rows:
        base = canonical_base(row["trading_pair"])
        binance_by_base[base] = row

    hl_by_base: dict[str, dict[str, Any]] = {}
    for row in hl_rows:
        base = hl_canonical_base(row["trading_pair"])
        hl_by_base[base] = row

    shared_bases = sorted(set(binance_by_base) & set(hl_by_base))
    intersection: list[dict[str, Any]] = []
    for base in shared_bases:
        b_row = binance_by_base[base]
        h_row = hl_by_base[base]
        b_vol = float(b_row["volume_24h_usd"])
        h_vol = float(h_row["volume_24h_usd"])
        if rank_by == "binance":
            rank_volume = b_vol
        elif rank_by == "hl":
            rank_volume = h_vol
        else:
            rank_volume = min(b_vol, h_vol)
        intersection.append(
            {
                "canonical_base": base,
                "binance_pair": binance_pair_from_any(b_row["trading_pair"]),
                "hl_pair": hl_pair_from_any(h_row["trading_pair"]),
                "binance_volume_24h_usd": b_vol,
                "hl_volume_24h_usd": h_vol,
                "binance_price": float(b_row.get("price", 0)),
                "hl_price": float(h_row.get("price", 0)),
                "rank_volume_usd": rank_volume,
            }
        )

    intersection.sort(key=lambda row: row["rank_volume_usd"], reverse=True)
    if max_pairs > 0:
        intersection = intersection[:max_pairs]
    return intersection


def universe_rows_for_exchange(
    intersection: list[dict[str, Any]],
    exchange: Literal["binance_perpetual", "hyperliquid"],
) -> list[dict[str, Any]]:
    """Map intersection rows to the scanner universe shape for one venue."""
    rows: list[dict[str, Any]] = []
    for item in intersection:
        if exchange == "binance_perpetual":
            rows.append(
                {
                    "trading_pair": item["binance_pair"],
                    "volume_24h_usd": float(item["binance_volume_24h_usd"]),
                    "price": float(item.get("binance_price", 0)),
                    "canonical_base": item["canonical_base"],
                }
            )
        else:
            rows.append(
                {
                    "trading_pair": item["hl_pair"],
                    "volume_24h_usd": float(item["hl_volume_24h_usd"]),
                    "price": float(item.get("hl_price", 0)),
                    "canonical_base": item["canonical_base"],
                }
            )
    return rows


def trading_pairs_for_exchange(
    intersection: list[dict[str, Any]],
    exchange: Literal["binance_perpetual", "hyperliquid"],
) -> list[str]:
    key = "binance_pair" if exchange == "binance_perpetual" else "hl_pair"
    return [str(item[key]) for item in intersection]


def write_intersection_manifest(
    intersection: list[dict[str, Any]],
    *,
    path: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    out_path = Path(path) if path is not None else DEFAULT_INTERSECTION_MANIFEST
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pair_count": len(intersection),
        "pairs": intersection,
    }
    if metadata:
        payload.update(metadata)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def load_intersection_manifest(path: Path | str | None = None) -> list[dict[str, Any]]:
    manifest_path = Path(path) if path is not None else DEFAULT_INTERSECTION_MANIFEST
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    pairs = payload.get("pairs", [])
    if not isinstance(pairs, list):
        raise ValueError(f"Invalid intersection manifest: {manifest_path}")
    return pairs
