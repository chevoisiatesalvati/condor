"""Debug instrumentation for stop-loss vs realized PnL analysis (session 4dcf4f)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_DEBUG_LOG = Path(__file__).resolve().parents[1] / ".cursor" / "debug-4dcf4f.log"
_SESSION_ID = "4dcf4f"


def _append_debug(payload: dict[str, Any]) -> None:
    try:
        payload.setdefault("sessionId", _SESSION_ID)
        payload.setdefault("timestamp", int(time.time() * 1000))
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _barrier_cfg(config: dict[str, Any]) -> dict[str, Any]:
    tb = config.get("triple_barrier_config")
    if isinstance(tb, dict):
        return tb
    return {}


def log_position_executor_create(
    *,
    executor_id: str | None,
    merged_config: dict[str, Any],
    controller_id: str,
) -> None:
    tb = _barrier_cfg(merged_config)
    sl = tb.get("stop_loss")
    tp = tb.get("take_profit")
    leverage = merged_config.get("leverage", 1)
    sl_f = float(sl) if sl is not None else None
    lev_f = float(leverage or 1)
    # #region agent log
    _append_debug(
        {
            "location": "executors.py:create",
            "message": "position_executor create config",
            "hypothesisId": "A-config",
            "runId": "live",
            "data": {
                "executor_id": executor_id,
                "controller_id": controller_id,
                "pair": merged_config.get("trading_pair"),
                "connector": merged_config.get("connector_name"),
                "leverage": lev_f,
                "stop_loss_decimal": sl_f,
                "take_profit_decimal": float(tp) if tp is not None else None,
                "stop_loss_pct_display": sl_f * 100 if sl_f is not None else None,
                "effective_price_sl_pct_if_divided_by_lev": (sl_f / lev_f * 100)
                if sl_f is not None and lev_f
                else None,
                "amount": merged_config.get("amount"),
            },
        }
    )
    # #endregion


def log_barrier_close_executor(ex: dict[str, Any]) -> None:
    from condor.fetchers.executors import (
        get_executor_config,
        get_executor_custom_info,
        get_executor_entry_price,
    )

    cfg = get_executor_config(ex)
    ci = get_executor_custom_info(ex)
    tb = _barrier_cfg(cfg)
    sl = tb.get("stop_loss")
    leverage = cfg.get("leverage", 1)
    entry = get_executor_entry_price(ex) or float(ci.get("current_position_average_price") or 0)
    close = float(ci.get("close_price") or 0)
    sl_f = float(sl) if sl is not None else None
    lev_f = float(leverage or 1)
    pnl = float(ex.get("pnl") or ex.get("net_pnl_quote") or 0)
    pnl_pct = float(ex.get("net_pnl_pct") or 0)
    vol = float(ex.get("volume") or ex.get("filled_amount_quote") or 0)
    notional = vol / 2 if vol else 0
    price_move_pct = ((close - entry) / entry * 100) if entry and close else None
    loss_on_notional_pct = (pnl / notional * 100) if notional else None
    roe_pct = (loss_on_notional_pct * lev_f) if loss_on_notional_pct is not None else None
    sl_price_notional = entry * (1 - sl_f) if entry and sl_f else None
    sl_price_lev_adj = entry * (1 - sl_f / lev_f) if entry and sl_f and lev_f else None
    configured_sl_pct = sl_f * 100 if sl_f is not None else None
    unexpected = (
        str(ex.get("close_type") or "").upper() == "STOP_LOSS"
        and sl_f is not None
        and loss_on_notional_pct is not None
        and abs(loss_on_notional_pct) + 0.05 < configured_sl_pct
    )
    # #region agent log
    _append_debug(
        {
            "location": "engine.py:barrier_close",
            "message": "barrier close SL diagnostic",
            "hypothesisId": "B-close-mismatch" if unexpected else "B-close-ok",
            "runId": "live",
            "data": {
                "executor_id": ex.get("id"),
                "pair": ex.get("pair") or cfg.get("trading_pair"),
                "close_type": ex.get("close_type"),
                "leverage": lev_f,
                "stop_loss_decimal": sl_f,
                "entry_price": entry,
                "close_price": close,
                "price_move_pct": price_move_pct,
                "net_pnl_quote": pnl,
                "net_pnl_pct_reported": pnl_pct * 100 if pnl_pct else None,
                "loss_on_notional_pct": loss_on_notional_pct,
                "roe_pct_approx": roe_pct,
                "sl_price_if_notional_pct": sl_price_notional,
                "sl_price_if_sl_div_leverage": sl_price_lev_adj,
                "unexpected_early_sl": unexpected,
            },
        }
    )
    # #endregion
