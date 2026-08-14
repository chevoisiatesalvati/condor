"""Tests for Telegram /agents helpers and callback prefix wiring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from condor.agents.performance import (
    AgentPerformance,
    _build_perf_from_rows,
    is_pnl_excluded_close_type,
)
from handlers.executors.menu import _list_callback_for_prefix
from handlers.sessions._shared import (
    DEFAULT_CALLBACK_PREFIX,
    executor_row_button_text,
    format_session_executors_table,
    format_vol_col,
    session_agent_id,
    session_page_nav_callbacks,
    session_view_callback,
    sort_session_executors,
)


def test_default_callback_prefix_is_agents():
    assert DEFAULT_CALLBACK_PREFIX == "agents"


def test_session_agent_id_matches_web_ui_controller_id():
    assert session_agent_id("agent.strategy", 7) == "agent.strategy_7"
    assert session_agent_id("macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl", 3) == (
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_3"
    )


def test_sort_session_executors_running_first():
    rows = [
        {"id": "a", "status": "completed", "timestamp": 100},
        {"id": "b", "status": "running", "timestamp": 50},
        {"id": "c", "status": "active_position", "timestamp": 200},
        {"id": "d", "status": "stopped", "timestamp": 300},
    ]
    sorted_rows = sort_session_executors(rows)
    assert [r["id"] for r in sorted_rows[:2]] == ["c", "b"]
    assert sorted_rows[2]["id"] == "d"
    assert sorted_rows[3]["id"] == "a"


def test_format_vol_col():
    assert format_vol_col(500).strip() == "500"
    assert format_vol_col(1500).strip() == "1.5k"


def test_list_callback_for_agents_uses_stored_view():
    context = SimpleNamespace(user_data={"sessions_slug": "foo", "sessions_num": 4})
    assert _list_callback_for_prefix("agents", context) == "agents:view:foo:4"
    assert _list_callback_for_prefix("strategies", context) == "strategies:view:foo:4"
    assert _list_callback_for_prefix("sessions", context) == "sessions:view:foo:4"
    assert _list_callback_for_prefix("executors", context) == "executors:menu"


def test_list_callback_for_agents_appends_page_when_set():
    context = SimpleNamespace(
        user_data={"sessions_slug": "foo", "sessions_num": 4, "sessions_page": 2}
    )
    assert _list_callback_for_prefix("agents", context) == "agents:view:foo:4:2"
    assert _list_callback_for_prefix("strategies", context) == "strategies:view:foo:4:2"


def test_session_view_callback_omits_page_zero():
    assert session_view_callback("agents", "foo", 4) == "agents:view:foo:4"
    assert session_view_callback("agents", "foo", 4, 0) == "agents:view:foo:4"
    assert session_view_callback("strategies", "foo", 4, 2) == "strategies:view:foo:4:2"


def test_session_page_nav_callbacks():
    nav = session_page_nav_callbacks(
        "agents", "foo", 4, page=1, total=20, per_page=8
    )
    assert nav == [
        ("◀️ Prev", "agents:view:foo:4"),
        ("Next ▶️", "agents:view:foo:4:2"),
    ]
    assert session_page_nav_callbacks(
        "agents", "foo", 4, page=0, total=8, per_page=8
    ) == []


def test_format_session_executors_table_includes_status():
    rows = [
        {
            "id": "1",
            "pair": "BTC-USDT",
            "type": "grid",
            "side": 1,
            "config": {"side": 1, "leverage": 5},
            "pnl": 12.34,
            "volume": 1500,
            "status": "running",
        },
        {
            "id": "2",
            "pair": "ETH-USDT",
            "type": "position",
            "side": 2,
            "config": {"side": 2, "leverage": 10},
            "pnl": -1.5,
            "volume": 500,
            "status": "stopped",
        },
    ]
    lines, displayed = format_session_executors_table(
        rows, page=0, total_pnl=10.84, total_volume=2000.0
    )
    header = next(line for line in lines if line.startswith("Pair"))
    for column in ("Pair", "Type", "Side", "PnL", "Vol", "Status"):
        assert column in header
    body = "\n".join(lines)
    assert "runnin" in body
    assert "stoppe" in body
    assert "TOTAL" in body
    assert len(displayed) == 2


def test_format_session_executors_table_paginates():
    rows = [
        {
            "id": str(i),
            "pair": f"P{i}",
            "type": "ord",
            "side": 1,
            "pnl": 0.0,
            "volume": 0.0,
            "status": "completed",
        }
        for i in range(10)
    ]
    lines, displayed = format_session_executors_table(
        rows, page=1, per_page=8, total_pnl=0.0, total_volume=0.0
    )
    assert [row["id"] for row in displayed] == ["8", "9"]
    assert any("Page 2/2" in line for line in lines)


def test_executor_row_button_text_pnl_sign_and_decimals():
    assert (
        executor_row_button_text({"pair": "BTC-USDT", "side": 1, "pnl": 12.3})
        == "🟢 BTC-USDT L $+12.30"
    )
    assert (
        executor_row_button_text({"pair": "ETH-USDT", "side": 2, "pnl": -1.5})
        == "🔴 ETH-USDT S $-1.50"
    )


def test_session_totals_exclude_stale_duplicate_pnl():
    rows = [
        {
            "status": "completed",
            "close_type": "take_profit",
            "pnl": 10.0,
            "volume": 100.0,
            "fees": 1.0,
        },
        {
            "status": "completed",
            "close_type": "stale_duplicate",
            "pnl": 0.0,  # zeroed in _executor_row
            "volume": 50.0,
            "fees": 0.0,
        },
        {
            "status": "running",
            "close_type": "",
            "pnl": 2.5,
            "volume": 20.0,
            "fees": 0.5,
        },
    ]
    assert is_pnl_excluded_close_type("stale_duplicate")
    perf = _build_perf_from_rows("agent.strategy_1", rows)
    assert isinstance(perf, AgentPerformance)
    assert perf.total_pnl == 12.5
    assert perf.realized_pnl == 10.0
    assert perf.unrealized_pnl == 2.5
    assert perf.volume == 170.0
    assert perf.open_count == 1
    assert perf.closed_count == 2


def test_stop_confirm_callbacks_use_agents_prefix():
    """handle_stop_executor builds agents: confirm/cancel callbacks."""
    import asyncio
    from unittest.mock import AsyncMock

    from handlers.executors.menu import handle_stop_executor

    query = MagicMock()
    query.message.edit_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {
        "current_executor": {"config": {"trading_pair": "SOL-USDT", "side": 1}},
    }

    asyncio.run(
        handle_stop_executor(update, context, "abc123", callback_prefix="agents")
    )

    _args, kwargs = query.message.edit_text.call_args
    markup = kwargs["reply_markup"]
    callback_datas = [
        btn.callback_data for row in markup.inline_keyboard for btn in row
    ]
    assert "agents:confirm_stop:abc123" in callback_datas
    assert "agents:detail:abc123" in callback_datas


def test_sessions_menu_has_no_history_screen():
    from pathlib import Path

    menu_path = Path(__file__).resolve().parents[1] / "handlers" / "sessions" / "menu.py"
    init_path = Path(__file__).resolve().parents[1] / "handlers" / "sessions" / "__init__.py"
    menu_src = menu_path.read_text(encoding="utf-8")
    init_src = init_path.read_text(encoding="utf-8")
    assert "show_session_history" not in menu_src
    assert "show_session_history_detail" not in menu_src
    assert "📜 History" not in menu_src
    assert "show_session_history" not in init_src
