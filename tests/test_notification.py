"""Tests for MCP send_notification → Telegram API."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_servers.condor.tools import notification


def _mock_send(result: dict | None = None):
    mock_response = MagicMock()
    mock_response.json.return_value = result or {"ok": True}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _run_send(text: str, mock_client: AsyncMock, *, parse_mode: str = "Markdown") -> dict:
    with patch.object(notification.settings, "bot_token", "test-token"), patch.object(
        notification.settings, "chat_id", 12345
    ), patch("mcp_servers.condor.tools.notification.httpx.AsyncClient", return_value=mock_client):
        return asyncio.run(notification.send_notification(text, parse_mode))


def test_send_notification_uses_markdown_by_default():
    mock_client = _mock_send()
    result = _run_send("Hello world", mock_client)

    assert result == {"sent": True}
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["chat_id"] == 12345
    assert payload["text"] == "Hello world"
    assert payload["parse_mode"] == "Markdown"


def test_mcp_server_send_notification_delegates_parse_mode():
    mock_client = _mock_send()
    with patch.object(notification.settings, "bot_token", "test-token"), patch.object(
        notification.settings, "chat_id", 12345
    ), patch("mcp_servers.condor.tools.notification.httpx.AsyncClient", return_value=mock_client):
        from mcp_servers.condor import server

        result = asyncio.run(server.send_notification("Tick update", parse_mode="HTML"))

    assert result == {"sent": True}
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"


def test_send_notification_retries_without_parse_mode_on_format_error():
    bad_response = MagicMock()
    bad_response.json.return_value = {
        "ok": False,
        "description": "Bad Request: can't parse entities",
    }
    ok_response = MagicMock()
    ok_response.json.return_value = {"ok": True}

    seen_payloads: list[dict] = []

    async def capture_post(url, json=None, **kwargs):
        seen_payloads.append(dict(json or {}))
        if len(seen_payloads) == 1:
            return bad_response
        return ok_response

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=capture_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    result = _run_send("bad _markdown", mock_client)

    assert result == {"sent": True}
    assert len(seen_payloads) == 2
    assert seen_payloads[0]["parse_mode"] == "Markdown"
    assert "parse_mode" not in seen_payloads[1]


def test_send_notification_missing_token():
    with patch.object(notification.settings, "bot_token", ""), patch.object(
        notification.settings, "chat_id", 12345
    ):
        result = asyncio.run(notification.send_notification("Hello"))

    assert result == {"error": "TELEGRAM_BOT_TOKEN not configured"}


def test_send_notification_telegram_error():
    mock_client = _mock_send(
        {"ok": False, "description": "Bad Request: chat not found"}
    )
    result = _run_send("Hello", mock_client)

    assert result == {"error": "Bad Request: chat not found"}


def _load_integration_credentials() -> tuple[str, int] | None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_raw = (os.environ.get("CONDOR_CHAT_ID") or os.environ.get("ADMIN_USER_ID") or "").strip()
    if not token or not chat_raw:
        return None
    try:
        chat_id = int(chat_raw)
    except ValueError:
        return None
    return token, chat_id


@pytest.mark.integration
def test_live_send_notification_markdown():
    """Send a real Telegram message. Run with: RUN_TELEGRAM_INTEGRATION=1 pytest -m integration."""
    if not os.environ.get("RUN_TELEGRAM_INTEGRATION"):
        pytest.skip("Set RUN_TELEGRAM_INTEGRATION=1 to hit the Telegram API")

    creds = _load_integration_credentials()
    if creds is None:
        pytest.skip("Need TELEGRAM_TOKEN (or TELEGRAM_BOT_TOKEN) and CONDOR_CHAT_ID (or ADMIN_USER_ID)")

    token, chat_id = creds
    with patch.object(notification.settings, "bot_token", token), patch.object(
        notification.settings, "chat_id", chat_id
    ):
        result = asyncio.run(notification.send_notification("[test] upstream-style notification"))

    assert result == {"sent": True}, result
