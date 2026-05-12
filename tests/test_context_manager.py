"""ContextManager 单元测试."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aiops_agent.context.manager import ContextManager
from aiops_agent.context.memory import MemoryLayer
from aiops_agent.context.resource_resolver import ResourceResolver
from aiops_agent.context.session import SessionStore
from aiops_agent.models.schemas import (
    InteractionMode,
    Message,
    ResourceReference,
    SessionState,
)


# ---------------------------------------------------------------------------
# Test: Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_get_or_create_session(self, context_manager: ContextManager) -> None:
        session = await context_manager.get_session("session-1", "user-1")
        assert session is not None
        assert session.session_id == "session-1"
        assert session.user_id == "user-1"
        assert session.mode == InteractionMode.CHAT

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, context_manager: ContextManager) -> None:
        session = await context_manager.get_session("nonexistent", "user-1")
        assert session is not None  # 会自动创建


# ---------------------------------------------------------------------------
# Test: Context update
# ---------------------------------------------------------------------------


class TestContextUpdate:
    @pytest.mark.asyncio
    async def test_update_context_adds_message(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        msg = Message(role="user", content="hello")
        await context_manager.update_context("s1", msg)

        session = await context_manager.get_session("s1", "u1")
        assert len(session.messages) >= 1

    @pytest.mark.asyncio
    async def test_update_context_resolves_resources(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        msg = Message(role="user", content="检查实例 i-abc1234567890ab 的状态")
        await context_manager.update_context("s1", msg)

        session = await context_manager.get_session("s1", "u1")
        assert "i-abc1234567890ab" in session.resources

    @pytest.mark.asyncio
    async def test_update_context_nonexistent_session(self, context_manager: ContextManager, caplog) -> None:
        msg = Message(role="user", content="hello")
        await context_manager.update_context("nonexistent", msg)
        assert "会话不存在" in caplog.text


# ---------------------------------------------------------------------------
# Test: Mode switching
# ---------------------------------------------------------------------------


class TestModeSwitching:
    @pytest.mark.asyncio
    async def test_switch_to_task_mode(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        await context_manager.switch_mode("s1", InteractionMode.TASK)

        session = await context_manager.get_session("s1", "u1")
        assert session.mode == InteractionMode.TASK
        assert session.task_progress is not None

    @pytest.mark.asyncio
    async def test_switch_back_to_chat_clears_progress(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        await context_manager.switch_mode("s1", InteractionMode.TASK)
        await context_manager.switch_mode("s1", InteractionMode.CHAT)

        session = await context_manager.get_session("s1", "u1")
        assert session.mode == InteractionMode.CHAT
        assert session.task_progress is None

    @pytest.mark.asyncio
    async def test_mode_switch_preserves_messages(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        msg = Message(role="user", content="before switch")
        await context_manager.update_context("s1", msg)
        await context_manager.switch_mode("s1", InteractionMode.TASK)

        session = await context_manager.get_session("s1", "u1")
        assert any("before switch" in m.content for m in session.messages)


# ---------------------------------------------------------------------------
# Test: Task progress
# ---------------------------------------------------------------------------


class TestTaskProgress:
    @pytest.mark.asyncio
    async def test_update_task_progress(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        await context_manager.switch_mode("s1", InteractionMode.TASK)
        await context_manager.update_task_progress(
            "s1", percentage=50.0, current_step="Step 2", total_steps=4, completed_steps=2
        )

        session = await context_manager.get_session("s1", "u1")
        assert session.task_progress is not None
        assert session.task_progress.percentage == 50.0
        assert session.task_progress.completed_steps == 2

    @pytest.mark.asyncio
    async def test_pause_task(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        await context_manager.switch_mode("s1", InteractionMode.TASK)
        await context_manager.update_task_progress(
            "s1", percentage=25.0, current_step="Step 1", total_steps=4, completed_steps=1
        )
        await context_manager.pause_task("s1")

        session = await context_manager.get_session("s1", "u1")
        assert session.task_progress is not None
        assert "已暂停" in session.task_progress.current_step

    @pytest.mark.asyncio
    async def test_cancel_task(self, context_manager: ContextManager) -> None:
        await context_manager.get_session("s1", "u1")
        await context_manager.switch_mode("s1", InteractionMode.TASK)
        await context_manager.cancel_task("s1")

        session = await context_manager.get_session("s1", "u1")
        assert session.mode == InteractionMode.CHAT
        assert session.task_progress is None


# ---------------------------------------------------------------------------
# Test: Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    @pytest.mark.asyncio
    async def test_persist_and_restore(self, context_manager: ContextManager) -> None:
        session = await context_manager.get_session("persist-1", "user-1")
        session.messages.append(Message(role="user", content="persisted message"))
        await context_manager.persist_session("persist-1")

        # Clear memory cache
        context_manager._session_store._sessions.clear()

        restored = await context_manager.get_session("persist-1", "user-1")
        assert len(restored.messages) >= 1

    @pytest.mark.asyncio
    async def test_check_idle_sessions(self, context_manager: ContextManager) -> None:
        session = await context_manager.get_session("idle-1", "user-1")
        # Simulate idle session
        session.last_active_at = datetime.now(timezone.utc) - timedelta(hours=1)

        idle = await context_manager.check_idle_sessions()
        assert "idle-1" in idle
