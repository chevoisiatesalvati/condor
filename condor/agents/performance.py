"""Shared aggregator for trading agent performance.

Single source of truth for PnL / volume / trade stats for a given ``agent_id``
(``controller_id`` tag on executors). Used both by the live ``ExecutorsProvider``
and the web API so they always agree.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)


def controller_ids_for_lookup(agent_id: str) -> list[str]:
    """HB API ``controller_id`` tags to try for a session agent_id.

    Exact match only. Legacy flat ``{strategy}_{N}`` aliases were removed —
    they collided with recycled session numbers after the agent/strategy path
    merge renumbered sessions on disk.
    """
    return [agent_id]


@dataclass
class AgentPerformance:
    agent_id: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    volume: float = 0.0
    fees: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    open_count: int = 0
    closed_count: int = 0
    executors: list[dict[str, Any]] = field(default_factory=list)
    # Controller-mode attribution: each bot the agent operates has its aggregate
    # PnL merged into the totals above, and its resolved instance name surfaced
    # here for transparency. A session can own several bots ([[FEAT-018]]), so this
    # is a list; the merge is plain addition over disjoint sets and needs no
    # de-duplication as long as the bases are disjoint (see ``resolve_bots``).
    bot_names: list[str] = field(default_factory=list)
    controllers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bot_name(self) -> str:
        """The first operated bot — wire compat for single-bot consumers."""
        return self.bot_names[0] if self.bot_names else ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "bot_name": self.bot_name}


def _extract_executors_list(result: Any) -> list[dict]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def _executor_notional_quote(cfg: dict, entry_price: float) -> float:
    quote = float(cfg.get("total_amount_quote") or 0)
    if quote > 0:
        return quote
    base_amount = float(cfg.get("amount") or 0)
    if base_amount > 0 and entry_price > 0:
        return base_amount * entry_price
    return base_amount


def is_running_status(status: str) -> bool:
    return (status or "").lower() in {"running", "active", "active_position"}


_is_running_status = is_running_status

# HB often strips custom_info on these terminations, so entry_price is absent by design.
_EXPECTED_ZERO_ENTRY_CLOSE_TYPES = frozenset({"recovery_failed", "stale_duplicate"})
# Duplicate / intentional junk rows must not move session or strategy PnL.
_PNL_EXCLUDED_CLOSE_TYPES = frozenset({"stale_duplicate", "mistake"})
# Avoid WARNING spam when the same dead executor is re-polled every few seconds.
_ZERO_ENTRY_WARNED_IDS: set[str] = set()


def is_pnl_excluded_close_type(close_type: str | None) -> bool:
    """Return True when this close_type must not contribute to Condor PnL totals."""
    return (close_type or "").lower().replace(" ", "_") in _PNL_EXCLUDED_CLOSE_TYPES


def _log_missing_entry_price(ex: dict) -> None:
    """Warn only for actionable cases; expected failed recoveries stay at debug."""
    ex_id = str(ex.get("id") or ex.get("executor_id") or "?")
    status = str(ex.get("status") or "").lower()
    close_type = str(ex.get("close_type") or "").lower()
    expected_missing = (
        not is_running_status(status)
        and (
            close_type in _EXPECTED_ZERO_ENTRY_CLOSE_TYPES
            or ex.get("custom_info") is None
        )
    )
    if expected_missing:
        log.debug(
            "entry_price unavailable for terminated position executor %s "
            "(status=%s close_type=%s) — skipping warning",
            ex_id,
            status,
            close_type or "?",
        )
        return
    if ex_id in _ZERO_ENTRY_WARNED_IDS:
        return
    _ZERO_ENTRY_WARNED_IDS.add(ex_id)
    log.warning(
        "entry_price fell back to 0.0 for position executor %s — PnL may be wrong",
        ex_id,
    )


def _executor_row(ex: dict) -> dict[str, Any]:
    from condor.fetchers.executors import (
        get_executor_close_timestamp,
        get_executor_config,
        get_executor_custom_info,
        get_executor_display_config,
        get_executor_entry_price,
        get_executor_fees,
        get_executor_pnl,
        get_executor_timestamp,
        get_executor_type,
        get_executor_volume,
        get_executor_side,
    )

    cfg = get_executor_config(ex)
    custom_info = get_executor_custom_info(ex)

    entry_price = get_executor_entry_price(ex)
    _ex_type = str(cfg.get("type") or ex.get("type") or "").lower()
    if entry_price == 0.0 and "position" in _ex_type:
        _log_missing_entry_price(ex)

    notional_quote = _executor_notional_quote(cfg, entry_price)

    # current_price / close_price: top-level > custom_info
    _top_cur = float(ex.get("current_price") or 0)
    _ci_cur = float(custom_info.get("current_price") or 0)
    _ci_close = float(custom_info.get("close_price") or 0)
    current_price = (
        _top_cur
        if _top_cur > 0
        else (_ci_cur if _ci_cur > 0 else (_ci_close if _ci_close > 0 else 0.0))
    )

    # Amount for UI must be quote notional — never raw base `amount` (e.g. 110152 kBONK).
    amount = float(cfg.get("total_amount_quote") or 0)
    if amount <= 0:
        amount = float(custom_info.get("total_value_quote") or 0)
    if amount <= 0:
        amount = notional_quote

    _ex_id = str(ex.get("id") or ex.get("executor_id") or "")
    close_type = str(ex.get("close_type") or "").lower()
    pnl = get_executor_pnl(ex)
    fees = get_executor_fees(ex)
    volume = get_executor_volume(ex)
    # Stale duplicates (and similar) keep showing in the table but contribute $0.
    if is_pnl_excluded_close_type(close_type):
        pnl = 0.0
        fees = 0.0

    return {
        "id": _ex_id,
        "type": get_executor_type(ex),
        "connector": cfg.get("connector_name")
        or ex.get("connector_name")
        or cfg.get("connector")
        or ex.get("connector")
        or "",
        "pair": cfg.get("trading_pair") or ex.get("trading_pair") or "",
        "side": get_executor_side(ex),
        "status": str(ex.get("status") or "").lower(),
        "close_type": close_type,
        "pnl": pnl,
        "net_pnl_pct": float(ex.get("net_pnl_pct") or 0)
        if not is_pnl_excluded_close_type(close_type)
        else 0.0,
        "volume": volume,
        "fees": fees,
        "notional_quote": notional_quote,
        "amount": amount,
        "entry_price": entry_price,
        "current_price": current_price,
        "timestamp": get_executor_timestamp(ex),
        "close_timestamp": get_executor_close_timestamp(ex),
        "created_at": str(ex.get("created_at") or ""),
        "closed_at": str(ex.get("closed_at") or ""),
        "controller_id": str(cfg.get("controller_id") or ex.get("controller_id") or ""),
        "custom_info": custom_info,
        "config": get_executor_display_config(ex),
    }


async def fetch_agent_performance(
    client: Any, agent_id: str, bot_names: list[str] | None = None
) -> AgentPerformance:
    """Fetch authoritative performance for a single ``agent_id``.

    When ``bot_names`` is given, the agent is in controller mode: each named bot's
    aggregate PnL is merged into the returned totals (see
    :func:`fetch_agent_performance_batch`).
    """
    names = [b for b in (bot_names or []) if b]
    batch = await fetch_agent_performance_batch(
        client, [agent_id], {agent_id: names} if names else None
    )
    return batch.get(
        agent_id, AgentPerformance(agent_id=agent_id, bot_names=list(names))
    )


def _merge_bot_perf(perf: AgentPerformance, bot: dict[str, Any]) -> None:
    """Fold a bot's aggregate into an executor-derived ``AgentPerformance`` in place.

    The two sources are disjoint (bot controllers tag executors with their own
    config ids, never the ``agent_id``), so the merge is plain addition — no
    de-duplication. The bot's open positions are surfaced as executor-like rows so
    bot-mode agents show live positions in both the executors tab and the agent's
    own core-data view, which otherwise only see the (empty) ``agent_id`` table.

    Additive in the bot dimension too: folding several owned bots in turn
    accumulates rather than overwrites, so a session operating two bots reports
    their sum and both controller breakdowns.
    """
    from condor.fetchers.bot_performance import bot_executor_rows

    perf.realized_pnl += float(bot.get("realized_pnl_quote", 0) or 0)
    perf.unrealized_pnl += float(bot.get("unrealized_pnl_quote", 0) or 0)
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl
    perf.volume += float(bot.get("volume_traded", 0) or 0)
    perf.fees += float(bot.get("cum_fees_quote", 0) or 0)
    perf.controllers = perf.controllers + list(bot.get("controllers", []))

    rows = bot_executor_rows(bot)
    perf.executors = perf.executors + rows
    open_rows = [r for r in rows if r["status"] == "RUNNING"]
    perf.open_count += len(open_rows)
    perf.trade_count += len(rows)


def _build_perf_from_rows(
    agent_id: str,
    rows: list[dict[str, Any]],
) -> AgentPerformance:
    # Compute everything directly from per-executor rows so realized/unrealized
    # stay consistent with what the UI renders per-row. The backend's
    # performance_report endpoint returns net_pnl_quote which already includes
    # open-position PnL; using it as "realized" and then adding unrealized on
    # top double-counts open positions.
    # Rows with excluded close_types (e.g. stale_duplicate) already have pnl/fees
    # zeroed in ``_executor_row``.
    running = [r for r in rows if is_running_status(r["status"])]
    closed = [r for r in rows if not is_running_status(r["status"])]
    scored_closed = [
        r for r in closed if not is_pnl_excluded_close_type(str(r.get("close_type") or ""))
    ]

    unrealized = sum(r["pnl"] for r in running)
    realized_pnl = sum(r["pnl"] for r in closed)
    volume = sum(r["volume"] for r in rows)
    fees = sum(r["fees"] for r in rows)

    win_rate = 0.0
    if scored_closed:
        wins = sum(1 for r in scored_closed if r["pnl"] > 0)
        win_rate = wins / len(scored_closed)

    return AgentPerformance(
        agent_id=agent_id,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized,
        total_pnl=realized_pnl + unrealized,
        volume=volume,
        fees=fees,
        trade_count=len(rows),
        win_rate=win_rate,
        open_count=len(running),
        closed_count=len(closed),
        executors=rows,
    )


async def fetch_agent_performance_batch(
    client: Any,
    agent_ids: list[str],
    bot_names: dict[str, list[str]] | None = None,
    failed_ids: set[str] | None = None,
) -> dict[str, AgentPerformance]:
    """Batched multi-agent fetch via a single cursor-paginated executor search.

    ``bot_names`` maps ``agent_id -> the bases it owns`` for agents running in
    controller mode; each such agent's bot aggregates (one shared snapshot fetch
    for the whole batch) are merged into its executor-derived totals.

    ``failed_ids``, when provided, is populated with the agent_ids whose executor
    search raised — their entries may be partial/empty. This lets callers avoid
    caching a failed fetch as a genuinely empty result.
    """
    out: dict[str, AgentPerformance] = {
        aid: AgentPerformance(agent_id=aid) for aid in agent_ids
    }
    if not client or not agent_ids:
        return out

    # Fetch per-agent in parallel. A single multi-id filter was unreliable:
    # the backend sometimes returned partial data for some controller_ids,
    # causing sessions with many executors to appear as zero in the rollup
    # while the per-session endpoint showed the correct numbers.
    PAGE_SIZE = 50
    MAX_PAGES = 200  # safety cap → 10,000 executors per agent

    async def _fetch_rows_once(aid: str) -> list[dict]:
        for cid in controller_ids_for_lookup(aid):
            rows: list[dict] = []
            cursor: str | None = None
            for _ in range(MAX_PAGES):
                kwargs: dict[str, Any] = {
                    "controller_ids": [cid],
                    "limit": PAGE_SIZE,
                }
                if cursor:
                    kwargs["cursor"] = cursor
                result = await client.executors.search_executors(**kwargs)
                page = _extract_executors_list(result)
                for ex in page:
                    if isinstance(ex, dict):
                        rows.append(_executor_row(ex))

                next_cursor = None
                if isinstance(result, dict):
                    next_cursor = result.get("next_cursor") or result.get("cursor")
                    pagination = result.get("pagination")
                    if not next_cursor and isinstance(pagination, dict):
                        next_cursor = pagination.get("next_cursor") or pagination.get(
                            "cursor"
                        )
                if not next_cursor or len(page) < PAGE_SIZE:
                    break
                cursor = next_cursor
            if rows:
                return rows
        return []

    async def _fetch_rows(aid: str) -> list[dict]:
        # search_executors can transiently return empty while in-memory executors
        # are still starting; retry briefly before surfacing an empty session list.
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                rows = await _fetch_rows_once(aid)
                if rows or attempt == 2:
                    return rows
            except Exception as e:
                last_error = e
                log.warning(
                    "search_executors(%s) failed (attempt %d): %s", aid, attempt + 1, e
                )
                if failed_ids is not None:
                    failed_ids.add(aid)
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
        if last_error:
            log.warning("search_executors(%s) exhausted retries: %s", aid, last_error)
            if failed_ids is not None:
                failed_ids.add(aid)
        return []

    rows_lists = await asyncio.gather(*[_fetch_rows(aid) for aid in agent_ids])
    for aid, rows in zip(agent_ids, rows_lists):
        out[aid] = _build_perf_from_rows(aid, rows)

    # Controller mode: merge each agent's bot aggregates. One snapshot fetch is
    # shared across the whole batch since the API returns all bots at once.
    wanted = {
        aid: [b for b in bases if b]
        for aid, bases in (bot_names or {}).items()
        if aid in out and any(bases)
    }
    if wanted:
        from condor.fetchers.bot_performance import (
            fetch_all_bot_performance,
            resolve_bots,
        )

        try:
            all_bot_perf = await fetch_all_bot_performance(client)
        except Exception as e:
            log.warning("fetch_all_bot_performance failed: %s", e)
            all_bot_perf = {}
        for aid, bases in wanted.items():
            # Resolved per agent over ALL its bases at once, so an owned parent
            # never resolves to a tagged sibling's instance and no bot is merged
            # into the same agent twice.
            live = resolve_bots(all_bot_perf, bases)
            for base in bases:
                bot = live.get(base)
                # An unresolved base (never deployed, or no snapshot yet) still
                # names the bot the agent operates, as the single-bot path did.
                out[aid].bot_names.append(bot.get("bot_name", base) if bot else base)
                if bot:
                    _merge_bot_perf(out[aid], bot)
    return out
