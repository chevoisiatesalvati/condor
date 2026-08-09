"""Tests for Telegram /strategies (deterministic catalog) helpers."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from handlers.executors.menu import _list_callback_for_prefix
from handlers.strategies._shared import (
    CALLBACK_PREFIX,
    deterministic_session_agent_id,
    format_pnl_plain,
    format_strategy_overview_lines,
    session_pnl_by_number,
)


def test_strategies_callback_prefix():
    assert CALLBACK_PREFIX == "strategies"


def test_deterministic_session_agent_id_matches_web_ui():
    assert deterministic_session_agent_id(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl", 3
    ) == "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_3"
    assert deterministic_session_agent_id("macdbb_pullback_hl.macdbb_pullback_hl", 1) == (
        "macdbb_pullback_hl.macdbb_pullback_hl_1"
    )


def test_format_pnl_plain():
    assert format_pnl_plain(12.3) == "+12.30"
    assert format_pnl_plain(-1.5) == "-1.50"


def test_format_strategy_overview_lines_positive_pnl():
    lines = format_strategy_overview_lines(
        slug="macdbb_pullback_hl",
        name="MACD+BB Pullback HL",
        status="running",
        total_pnl=100.5,
        last_session_pnl=12.25,
        realized_pnl=90.0,
        unrealized_pnl=10.5,
        volume=15000.0,
        open_positions=2,
        session_count=7,
    )
    text = "\n".join(lines)
    assert "Strategy" in text
    assert "running" in text
    assert "Total PnL" in text
    assert "Last:" in text
    assert "Realized:" in text
    assert "Open: `2`" in text
    assert "Sessions: `7`" in text
    assert "🟢" in text


def test_format_strategy_overview_lines_negative_pnl():
    lines = format_strategy_overview_lines(
        slug="foo",
        name="Foo",
        status="idle",
        total_pnl=-5.0,
        last_session_pnl=-2.0,
        realized_pnl=-5.0,
        unrealized_pnl=0.0,
        volume=0.0,
        open_positions=0,
        session_count=0,
    )
    text = "\n".join(lines)
    assert "🔴" in text
    assert "idle" in text


def test_session_pnl_by_number_accepts_deterministic_rows():
    sessions = [
        {"session_num": 3, "total_pnl": 10.0},
        {"session_num": 1, "total_pnl": -2.5},
        SimpleNamespace(kind="experiment", session_num=9, total_pnl=99.0),
        SimpleNamespace(kind="session", session_num=2, total_pnl=4.0),
    ]
    by_num = session_pnl_by_number(sessions)
    assert by_num == {3: 10.0, 1: -2.5, 2: 4.0}


def test_list_callback_for_strategies_uses_stored_view():
    context = SimpleNamespace(user_data={"sessions_slug": "foo", "sessions_num": 4})
    assert _list_callback_for_prefix("strategies", context) == "strategies:view:foo:4"
    assert _list_callback_for_prefix("agents", context) == "agents:view:foo:4"


def test_stop_confirm_callbacks_use_strategies_prefix():
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
        "sessions_slug": "macdbb_pullback_hl",
        "sessions_num": 2,
    }

    asyncio.run(
        handle_stop_executor(update, context, "abc123", callback_prefix="strategies")
    )

    _args, kwargs = query.message.edit_text.call_args
    markup = kwargs["reply_markup"]
    callback_datas = [
        btn.callback_data for row in markup.inline_keyboard for btn in row
    ]
    assert "strategies:confirm_stop:abc123" in callback_datas
    assert "strategies:detail:abc123" in callback_datas


def test_strategies_menu_uses_catalog_not_strategy_store():
    """Smoke check: strategies package sources deterministic catalog."""
    menu_path = Path(__file__).resolve().parents[1] / "handlers" / "strategies" / "menu.py"
    init_path = Path(__file__).resolve().parents[1] / "handlers" / "strategies" / "__init__.py"
    menu_src = menu_path.read_text(encoding="utf-8")
    init_src = init_path.read_text(encoding="utf-8")
    assert "strategy_runners.catalog" in menu_src or "from condor.strategy_runners.catalog" in menu_src
    assert "StrategyStore" not in menu_src
    assert "StrategyStore" not in init_src
    assert "get_strategy" in init_src

    # Parseable and imports catalog symbols.
    tree = ast.parse(menu_src)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert any("strategy_runners.catalog" in m for m in imports)
    assert not any(m.endswith("agents.strategy") for m in imports)


def test_agents_default_prefix_still_agents():
    from handlers.sessions.menu import CALLBACK_PREFIX as agents_menu_prefix
    from handlers.sessions._shared import DEFAULT_CALLBACK_PREFIX

    assert DEFAULT_CALLBACK_PREFIX == "agents"
    assert agents_menu_prefix == "agents"
