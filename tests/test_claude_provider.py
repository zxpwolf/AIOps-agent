"""Tests for ClaudeProvider.

Covers provider_name, chat (system messages, content blocks), complete, embed,
Anthropic-specific headers, close, and error handling.
Uses mocked aiohttp.ClientSession with fake responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from aiops_agent.llm.claude import ClaudeProvider
from aiops_agent.models.schemas import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(**kwargs) -> ClaudeProvider:
    return ClaudeProvider(api_key="test-anthropic-key", **kwargs)


def _fake_resp(status: int, json_data: dict | None = None, text: str = ""):
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    else:
        resp.json = AsyncMock(side_effect=aiohttp.ContentTypeError(None, None))
    resp.text = AsyncMock(return_value=text)
    return resp


def _mock_session(fake_resp):
    """Create a mock ClientSession whose .post returns an async context manager.

    session.post() returns a context manager directly (not a coroutine),
    so .post must be MagicMock, not AsyncMock.
    """
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=fake_resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    session.closed = False
    return session


# ---------------------------------------------------------------------------
# provider_name
# ---------------------------------------------------------------------------


def test_provider_name():
    """provider_name returns 'claude'."""
    p = _make_provider()
    assert p.provider_name == "claude"


# ---------------------------------------------------------------------------
# chat — system message extraction
# ---------------------------------------------------------------------------


async def test_chat_extracts_system_messages():
    """chat: extracts system messages separately."""
    p = _make_provider()

    captured_payload = {}

    def _capture_post(url, json=None, headers=None):
        captured_payload["url"] = url
        captured_payload["json"] = json
        captured_payload["headers"] = headers

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=_fake_resp(200, {
            "model": "claude-3-sonnet-20240229",
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }))
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    mock_sess = MagicMock()
    mock_sess.post = _capture_post
    mock_sess.closed = False

    with patch.object(p, "_get_session", new=AsyncMock(return_value=mock_sess)):
        await p.chat([
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hello"),
        ])

    assert captured_payload["json"]["system"] == "You are a helpful assistant."
    # System message should NOT be in the messages list
    assert len(captured_payload["json"]["messages"]) == 1
    assert captured_payload["json"]["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# chat — content block parsing
# ---------------------------------------------------------------------------


async def test_chat_parses_content_blocks():
    """chat: parses content blocks (text, other types)."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "claude-3-sonnet-20240229",
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world!"},
            {"type": "tool_use", "id": "tool-1", "name": "search"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.chat([Message(role="user", content="Search for cats")])

    # Only text blocks should be concatenated
    assert result.content == "Hello world!"
    assert result.finish_reason == "end_turn"


# ---------------------------------------------------------------------------
# chat — error
# ---------------------------------------------------------------------------


async def test_chat_non_200_raises_runtime_error():
    """chat: non-200 raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(400, text="Invalid request")
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await p.chat([Message(role="user", content="Hi")])


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


async def test_complete_delegates_to_chat():
    """complete: delegates to chat."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "claude-3-sonnet-20240229",
        "content": [{"type": "text", "text": "Completed."}],
        "usage": {"input_tokens": 5, "output_tokens": 3},
        "stop_reason": "end_turn",
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.complete("Finish this")

    assert result == "Completed."


# ---------------------------------------------------------------------------
# embed — not supported
# ---------------------------------------------------------------------------


async def test_embed_raises_not_implemented_error():
    """embed: raises NotImplementedError."""
    p = _make_provider()
    with pytest.raises(NotImplementedError, match="不提供原生 Embedding"):
        await p.embed(["hello"])


# ---------------------------------------------------------------------------
# Anthropic-specific headers
# ---------------------------------------------------------------------------


async def test_anthropic_specific_headers():
    """Anthropic-specific headers: x-api-key, anthropic-version."""
    p = _make_provider()

    captured_headers = {}

    def _capture_post(url, json=None, headers=None):
        captured_headers.update(headers or {})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=_fake_resp(200, {
            "model": "claude-3-sonnet-20240229",
            "content": [{"type": "text", "text": "OK"}],
            "usage": {},
            "stop_reason": "end_turn",
        }))
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    mock_sess = MagicMock()
    mock_sess.post = _capture_post
    mock_sess.closed = False

    with patch.object(p, "_get_session", new=AsyncMock(return_value=mock_sess)):
        await p.chat([Message(role="user", content="Hi")])

    assert "x-api-key" in captured_headers
    assert captured_headers["x-api-key"] == "test-anthropic-key"
    assert "anthropic-version" in captured_headers
    assert captured_headers["anthropic-version"] == "2023-06-01"


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


async def test_close_closes_session():
    """close: closes session."""
    p = _make_provider()
    session = await p._get_session()
    assert not session.closed

    await p.close()
    assert session.closed
    assert p._session is None
