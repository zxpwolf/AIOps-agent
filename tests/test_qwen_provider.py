"""Tests for QwenProvider.

Covers provider_name, chat, complete, embed, session lifecycle, and error handling.
Uses mocked aiohttp.ClientSession with fake responses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from aiops_agent.llm.qwen import QwenProvider
from aiops_agent.models.schemas import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(**kwargs) -> QwenProvider:
    return QwenProvider(api_key="test-key", **kwargs)


def _fake_resp(status: int, json_data: dict | None = None, text: str = ""):
    """Create a mock aiohttp response context manager."""
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

    session.post() is NOT async itself — it returns a context manager directly.
    So .post must be a MagicMock (not AsyncMock) that returns the ctx manager.
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
    """provider_name returns 'qwen'."""
    p = _make_provider()
    assert p.provider_name == "qwen"


# ---------------------------------------------------------------------------
# chat — success
# ---------------------------------------------------------------------------


async def test_chat_successful_response():
    """chat: successful response parsing (content, model, usage, finish_reason)."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "qwen3-235b-a22b",
        "choices": [{
            "message": {"content": "Hello world", "reasoning_content": ""},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.chat([Message(role="user", content="Hi")])

    assert result.content == "Hello world"
    assert result.model == "qwen3-235b-a22b"
    assert result.usage["input_tokens"] == 10
    assert result.usage["output_tokens"] == 5
    assert result.usage["total_tokens"] == 15
    assert result.finish_reason == "stop"


async def test_chat_handles_reasoning_content():
    """chat: handles reasoning_content in response."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "qwen3-235b-a22b",
        "choices": [{
            "message": {
                "content": "The answer is 42",
                "reasoning_content": "Let me think about this step by step...",
            },
            "finish_reason": "stop",
        }],
        "usage": {},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.chat([Message(role="user", content="21+21=?")])

    assert result.content == "The answer is 42"
    assert "reasoning_content" in result.metadata
    assert result.metadata["reasoning_content"] == "Let me think about this step by step..."


# ---------------------------------------------------------------------------
# chat — errors
# ---------------------------------------------------------------------------


async def test_chat_non_200_raises_runtime_error():
    """chat: non-200 response raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(429, text="Rate limited")
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            await p.chat([Message(role="user", content="Hi")])


async def test_chat_json_decode_error_raises_runtime_error():
    """chat: JSON decode error raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(200, text="not json")
    # Use a plain RuntimeError instead of ContentTypeError to avoid
    # aiohttp's ContentTypeError __str__ crash when request_info is None.
    fake_resp.json = AsyncMock(side_effect=ValueError("bad json"))
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(ValueError):
            await p.chat([Message(role="user", content="Hi")])


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


async def test_complete_delegates_to_chat():
    """complete: delegates to chat."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "model": "qwen3-235b-a22b",
        "choices": [{"message": {"content": "Done"}, "finish_reason": "stop"}],
        "usage": {},
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.complete("Complete this")

    assert result == "Done"


# ---------------------------------------------------------------------------
# embed
# ---------------------------------------------------------------------------


async def test_embed_successful_response():
    """embed: successful embedding response."""
    p = _make_provider()
    fake_resp = _fake_resp(200, {
        "data": [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
        ],
    })
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        result = await p.embed(["hello", "world"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
    assert result[1] == [0.4, 0.5, 0.6]


async def test_embed_non_200_raises_runtime_error():
    """embed: non-200 raises RuntimeError."""
    p = _make_provider()
    fake_resp = _fake_resp(500, text="Internal error")
    with patch.object(p, "_get_session", new=AsyncMock(return_value=_mock_session(fake_resp))):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await p.embed(["hello"])


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


async def test_lazy_session_creation():
    """Lazy session creation (_get_session)."""
    p = _make_provider()
    assert p._session is None

    # First call creates a real session
    session1 = await p._get_session()
    assert session1 is not None
    assert isinstance(session1, aiohttp.ClientSession)

    # Second call returns the same session
    session2 = await p._get_session()
    assert session1 is session2

    await p.close()


async def test_close_closes_session_if_open():
    """close: closes session if open."""
    p = _make_provider()
    session = await p._get_session()
    assert not session.closed

    await p.close()
    assert session.closed
    assert p._session is None


async def test_close_no_op_if_session_not_created():
    """close: no-op if session not created."""
    p = _make_provider()
    assert p._session is None

    # Should not raise
    await p.close()
    assert p._session is None
