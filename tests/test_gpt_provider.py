"""Tests for GPTProvider.

Covers provider_name, chat (response parsing, usage keys), complete, embed,
close, and error handling.
Uses mocked aiohttp.ClientSession with fake responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from aiops_agent.llm.gpt import GPTProvider
from aiops_agent.models.schemas import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(**kwargs) -> GPTProvider:
    return GPTProvider(api_key="test-openai-key", **kwargs)


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
    """provider_name returns 'gpt'."""
    p = _make_provider()
    assert p.provider_name == "gpt"


# ---------------------------------------------------------------------------
# chat — success
# ---------------------------------------------------------------------------


async def test_chat_successful_response():
    """chat: successful response parsing."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "gpt-4",
        "choices": [
            {"message": {"content": "Hello from GPT"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.chat([Message(role="user", content="Hi")])

    assert result.content == "Hello from GPT"
    assert result.model == "gpt-4"
    assert result.finish_reason == "stop"


async def test_chat_usage_keys():
    """chat: usage keys (prompt_tokens, completion_tokens)."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "gpt-4",
        "choices": [
            {"message": {"content": "OK"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.chat([Message(role="user", content="Test usage")])

    assert "prompt_tokens" in result.usage
    assert "completion_tokens" in result.usage
    assert "total_tokens" in result.usage
    assert result.usage["prompt_tokens"] == 100
    assert result.usage["completion_tokens"] == 50
    assert result.usage["total_tokens"] == 150


# ---------------------------------------------------------------------------
# chat — error
# ---------------------------------------------------------------------------


async def test_chat_non_200_raises_runtime_error():
    """chat: non-200 raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(503, text="Service unavailable")
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await p.chat([Message(role="user", content="Hi")])


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


async def test_complete_delegates_to_chat():
    """complete: delegates to chat."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "gpt-4",
        "choices": [
            {"message": {"content": "Done."}, "finish_reason": "stop"}
        ],
        "usage": {},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.complete("Complete this sentence")

    assert result == "Done."


# ---------------------------------------------------------------------------
# embed — success
# ---------------------------------------------------------------------------


async def test_embed_successful_response():
    """embed: successful embedding with text-embedding-3-small model."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "data": [
            {"embedding": [0.1, 0.2]},
            {"embedding": [0.3, 0.4]},
        ],
    })

    captured_payload = {}

    def _capture_post(url, json=None, headers=None):
        captured_payload["url"] = url
        captured_payload["json"] = json

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_resp)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    mock_sess = MagicMock()
    mock_sess.post = _capture_post
    mock_sess.closed = False

    with patch.object(p, "_get_session", new=AsyncMock(return_value=mock_sess)):
        result = await p.embed(["hello", "world"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2]
    assert result[1] == [0.3, 0.4]
    # Default model should be text-embedding-3-small
    assert captured_payload["json"]["model"] == "text-embedding-3-small"


# ---------------------------------------------------------------------------
# embed — error
# ---------------------------------------------------------------------------


async def test_embed_non_200_raises_runtime_error():
    """embed: non-200 raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(401, text="Unauthorized")
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            await p.embed(["hello"])


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
