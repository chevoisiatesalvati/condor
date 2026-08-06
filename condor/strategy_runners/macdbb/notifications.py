"""Compact Telegram OPEN/CLOSE lines for deterministic Strategies runners."""

from __future__ import annotations

from typing import Any


def format_open_notification(
    *,
    side: str,
    pair: str,
    entry_class: str,
    notional_quote: float,
    sl_pct: float,
    tp_pct: float,
    session_num: int | None = None,
    leverage: float | int | None = None,
    price: float | None = None,
    bb_pos_pct: float | None = None,
    score: float | None = None,
    base_amount: float | None = None,
    flip_reverse: bool = False,
) -> str:
    parts = [
        f"⚡ OPEN {str(side).upper()} {pair}",
        str(entry_class or ""),
    ]
    if flip_reverse:
        parts.append("flip_reverse")
    parts.append(f"notional ${float(notional_quote):.2f}")
    if base_amount is not None and float(base_amount) > 0:
        parts.append(f"qty {float(base_amount):.6g}")
    if leverage is not None and str(leverage).strip() != "":
        try:
            parts.append(f"{float(leverage):.0f}x")
        except (TypeError, ValueError):
            parts.append(f"{leverage}x")
    if price is not None and float(price) > 0:
        parts.append(f"px {float(price):.6g}")
    if bb_pos_pct is not None:
        parts.append(f"bb {float(bb_pos_pct):.1f}%")
    if score is not None:
        parts.append(f"score {float(score):.3g}")
    parts.append(f"SL {float(sl_pct):.2f}% TP {float(tp_pct):.2f}%")
    if session_num is not None:
        parts.append(f"session_{session_num}")
    return " | ".join(p for p in parts if p)


def format_close_notification(
    *,
    pair: str,
    reason: str,
    close_type: str = "",
    side: str = "",
    pnl: float | None = None,
    net_pnl_pct: float | None = None,
    executor_id: str = "",
    session_num: int | None = None,
    volume: float | None = None,
) -> str:
    side_bit = f"{str(side).upper()} " if side else ""
    parts = [f"⚡ CLOSED {side_bit}{pair}".rstrip()]
    label = (close_type or reason or "").strip()
    if label:
        parts.append(label)
    if reason and close_type and reason != close_type and reason not in label:
        parts.append(reason)
    if pnl is not None:
        parts.append(f"PnL ${float(pnl):+.2f}")
    if net_pnl_pct is not None:
        parts.append(f"{float(net_pnl_pct):+.2f}%")
    if volume is not None and float(volume) > 0:
        parts.append(f"vol ${float(volume):.0f}")
    if executor_id:
        parts.append(f"id: {executor_id}")
    if session_num is not None:
        parts.append(f"session_{session_num}")
    return " | ".join(parts)


def format_barrier_close_notification(
    close: dict[str, Any], *, session_num: int | None = None
) -> str:
    return format_close_notification(
        pair=str(close.get("pair") or "?"),
        reason=str(close.get("close_type") or "BARRIER"),
        close_type=str(close.get("close_type") or ""),
        side=str(close.get("side") or ""),
        pnl=float(close.get("pnl") or 0),
        net_pnl_pct=(
            float(close["net_pnl_pct"])
            if close.get("net_pnl_pct") is not None
            else None
        ),
        executor_id=str(close.get("id") or ""),
        session_num=session_num,
        volume=(
            float(close["volume"])
            if close.get("volume") is not None
            else None
        ),
    )
