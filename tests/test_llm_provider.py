"""Tests for LLMProviderFactory — registration, fallback, and lifecycle."""

from __future__ import annotations

import pytest

from aiops_agent.llm.provider import ChatResponse, LLMProviderFactory
from aiops_agent.models.schemas import Message

from .conftest import MockLLMProvider


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestLLMProviderFactoryRegister:
    """Tests for register and get_provider."""

    def test_register_adds_provider(self):
        factory = LLMProviderFactory()
        provider = MockLLMProvider()
        factory.register("mock", provider)
        assert factory.get_provider("mock") is provider

    def test_register_duplicate_overwrites(self):
        """Re-registering the same name overwrites the previous provider."""
        factory = LLMProviderFactory()
        p1 = MockLLMProvider()
        p2 = MockLLMProvider()
        factory.register("mock", p1)
        factory.register("mock", p2)
        assert factory.get_provider("mock") is p2

    def test_get_provider_unregistered_raises(self):
        factory = LLMProviderFactory()
        with pytest.raises(ValueError, match="未注册"):
            factory.get_provider("nonexistent")


# ---------------------------------------------------------------------------
# Primary / Fallback
# ---------------------------------------------------------------------------


class TestLLMProviderFactoryPrimaryFallback:
    """Tests for set_primary, set_fallback, and primary property."""

    def test_set_primary_sets_correctly(self):
        factory = LLMProviderFactory()
        factory.register("mock", MockLLMProvider())
        factory.set_primary("mock")
        assert factory.primary is factory.get_provider("mock")

    def test_set_primary_unregistered_raises(self):
        factory = LLMProviderFactory()
        with pytest.raises(ValueError, match="未注册"):
            factory.set_primary("nonexistent")

    def test_set_fallback_sets_correctly(self):
        factory = LLMProviderFactory()
        factory.register("primary", MockLLMProvider())
        factory.register("fallback", MockLLMProvider())
        factory.set_fallback("fallback")
        assert factory.get_provider("fallback") is not None

    def test_set_fallback_unregistered_raises(self):
        factory = LLMProviderFactory()
        factory.register("mock", MockLLMProvider())
        with pytest.raises(ValueError, match="未注册"):
            factory.set_fallback("nonexistent")

    def test_primary_not_set_raises(self):
        factory = LLMProviderFactory()
        with pytest.raises(ValueError, match="未设置主 Provider"):
            _ = factory.primary


# ---------------------------------------------------------------------------
# Chat auto-fallback
# ---------------------------------------------------------------------------


class TestLLMProviderFactoryChatFallback:
    """Tests for chat() with automatic fallback logic."""

    @pytest.mark.asyncio
    async def test_chat_primary_succeeds(self):
        factory = LLMProviderFactory()
        factory.register("mock", MockLLMProvider(["primary result"]))
        factory.set_primary("mock")

        response = await factory.chat([Message(role="user", content="hello")])
        assert response.content == "primary result"

    @pytest.mark.asyncio
    async def test_chat_primary_fails_fallback_succeeds(self):
        factory = LLMProviderFactory()
        primary = MockLLMProvider()
        fallback = MockLLMProvider(["fallback result"])

        # Make primary raise on every call
        async def failing_chat(*args, **kwargs):
            raise ConnectionError("primary down")

        primary.chat = failing_chat

        factory.register("primary", primary)
        factory.register("fallback", fallback)
        factory.set_primary("primary")
        factory.set_fallback("fallback")

        response = await factory.chat([Message(role="user", content="hello")])
        assert response.content == "fallback result"

    @pytest.mark.asyncio
    async def test_chat_both_fail_raises_runtime_error(self):
        factory = LLMProviderFactory()
        primary = MockLLMProvider()
        fallback = MockLLMProvider()

        async def failing_chat(*args, **kwargs):
            raise ConnectionError("down")

        primary.chat = failing_chat
        fallback.chat = failing_chat

        factory.register("primary", primary)
        factory.register("fallback", fallback)
        factory.set_primary("primary")
        factory.set_fallback("fallback")

        with pytest.raises(RuntimeError, match="所有 LLM Provider 均不可用"):
            await factory.chat([Message(role="user", content="hello")])

    @pytest.mark.asyncio
    async def test_chat_no_primary_no_fallback_raises(self):
        """If neither primary nor fallback is set, chat raises RuntimeError."""
        factory = LLMProviderFactory()
        factory.register("mock", MockLLMProvider(["result"]))
        # Neither set_primary nor set_fallback called
        with pytest.raises(RuntimeError, match="所有 LLM Provider 均不可用"):
            await factory.chat([Message(role="user", content="hello")])


# ---------------------------------------------------------------------------
# Complete auto-fallback
# ---------------------------------------------------------------------------


class TestLLMProviderFactoryCompleteFallback:
    """Tests for complete() with automatic fallback logic."""

    @pytest.mark.asyncio
    async def test_complete_primary_succeeds(self):
        factory = LLMProviderFactory()
        factory.register("mock", MockLLMProvider(["completed"]))
        factory.set_primary("mock")

        result = await factory.complete("finish this: ")
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_complete_primary_fails_fallback_succeeds(self):
        factory = LLMProviderFactory()
        primary = MockLLMProvider()
        fallback = MockLLMProvider(["fallback completion"])

        async def failing_complete(*args, **kwargs):
            raise TimeoutError("primary timeout")

        primary.complete = failing_complete

        factory.register("primary", primary)
        factory.register("fallback", fallback)
        factory.set_primary("primary")
        factory.set_fallback("fallback")

        result = await factory.complete("hello")
        assert result == "fallback completion"

    @pytest.mark.asyncio
    async def test_complete_both_fail_raises_runtime_error(self):
        factory = LLMProviderFactory()
        primary = MockLLMProvider()
        fallback = MockLLMProvider()

        async def failing_complete(*args, **kwargs):
            raise RuntimeError("dead")

        primary.complete = failing_complete
        fallback.complete = failing_complete

        factory.register("primary", primary)
        factory.register("fallback", fallback)
        factory.set_primary("primary")
        factory.set_fallback("fallback")

        with pytest.raises(RuntimeError, match="所有 LLM Provider 均不可用"):
            await factory.complete("hello")


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestLLMProviderFactoryClose:
    """Tests for close() lifecycle."""

    @pytest.mark.asyncio
    async def test_close_calls_close_on_all_providers(self):
        factory = LLMProviderFactory()
        p1 = MockLLMProvider()
        p2 = MockLLMProvider()
        p1.close = pytest.mock_coro_func = True  # type: ignore[attr-defined]

        close_calls = []

        async def tracked_close_1():
            close_calls.append("p1")

        async def tracked_close_2():
            close_calls.append("p2")

        p1.close = tracked_close_1
        p2.close = tracked_close_2

        factory.register("p1", p1)
        factory.register("p2", p2)
        await factory.close()

        assert "p1" in close_calls
        assert "p2" in close_calls

    @pytest.mark.asyncio
    async def test_close_exceptions_suppressed(self):
        """close() should not raise even if a provider's close fails."""
        factory = LLMProviderFactory()
        good = MockLLMProvider()
        bad = MockLLMProvider()

        async def failing_close():
            raise RuntimeError("cannot close")

        bad.close = failing_close

        factory.register("good", good)
        factory.register("bad", bad)

        # Should not raise
        await factory.close()
