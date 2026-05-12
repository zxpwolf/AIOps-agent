"""Context_Manager — 多轮对话与上下文管理.

整合 SessionStore、MemoryLayer、ResourceResolver，
实现对话上下文更新、资源引用自动解析、交互模式切换和任务进度跟踪。
"""

from __future__ import annotations

import logging
from typing import Optional

from aiops_agent.context.memory import MemoryLayer
from aiops_agent.context.resource_resolver import ResourceResolver
from aiops_agent.context.session import SessionStore
from aiops_agent.models.schemas import (
    InteractionMode,
    Message,
    SessionState,
    TaskProgress,
)

logger = logging.getLogger(__name__)


class ContextManager:
    """上下文管理器.

    职责:
    - 整合 SessionStore、MemoryLayer、ResourceResolver
    - 对话上下文更新和资源引用自动解析
    - Chat / Task / Watch 三种交互模式切换，保留上下文
    - Task 模式下的任务进度跟踪
    - 支持任务暂停和取消
    """

    def __init__(
        self,
        session_store: Optional[SessionStore] = None,
        memory_layer: Optional[MemoryLayer] = None,
        resource_resolver: Optional[ResourceResolver] = None,
    ) -> None:
        self._session_store = session_store or SessionStore()
        self._memory = memory_layer or MemoryLayer()
        self._resolver = resource_resolver or ResourceResolver()

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    async def get_session(self, session_id: str, user_id: str = "") -> SessionState:
        """获取或创建会话状态."""
        return await self._session_store.get_or_create(session_id, user_id)

    # ------------------------------------------------------------------
    # 上下文更新
    # ------------------------------------------------------------------

    async def update_context(self, session_id: str, message: Message) -> None:
        """更新对话上下文，自动解析资源引用.

        Args:
            session_id: 会话 ID。
            message: 新消息。
        """
        session = await self._session_store.get(session_id)
        if session is None:
            logger.warning("会话不存在: %s", session_id)
            return

        # 添加消息到历史
        session.messages.append(message)

        # 自动解析资源引用
        references = self._resolver.resolve(message.content)
        for ref in references:
            session.resources[ref.resource_id] = ref

        # 存储到短期记忆
        await self._memory.store_short_term(
            session_id,
            {"role": message.role, "content": message.content},
        )

        logger.debug(
            "上下文已更新: session=%s, resources=%d",
            session_id,
            len(references),
        )

    # ------------------------------------------------------------------
    # 交互模式
    # ------------------------------------------------------------------

    async def switch_mode(
        self,
        session_id: str,
        mode: InteractionMode,
    ) -> None:
        """切换交互模式，保留上下文.

        Args:
            session_id: 会话 ID。
            mode: 目标交互模式。
        """
        session = await self._session_store.get(session_id)
        if session is None:
            logger.warning("会话不存在: %s", session_id)
            return

        old_mode = session.mode
        session.mode = mode

        # 切换到 Task 模式时初始化进度
        if mode == InteractionMode.TASK and session.task_progress is None:
            session.task_progress = TaskProgress()

        # 离开 Task 模式时清理进度
        if old_mode == InteractionMode.TASK and mode != InteractionMode.TASK:
            session.task_progress = None

        logger.info("交互模式切换: %s → %s (session=%s)", old_mode.value, mode.value, session_id)

    # ------------------------------------------------------------------
    # 任务进度
    # ------------------------------------------------------------------

    async def update_task_progress(
        self,
        session_id: str,
        percentage: float,
        current_step: str,
        total_steps: int = 0,
        completed_steps: int = 0,
    ) -> None:
        """更新任务执行进度.

        Args:
            session_id: 会话 ID。
            percentage: 完成百分比 (0-100)。
            current_step: 当前步骤描述。
            total_steps: 总步骤数。
            completed_steps: 已完成步骤数。
        """
        session = await self._session_store.get(session_id)
        if session is None or session.mode != InteractionMode.TASK:
            return

        session.task_progress = TaskProgress(
            percentage=percentage,
            current_step=current_step,
            total_steps=total_steps,
            completed_steps=completed_steps,
        )

    async def pause_task(self, session_id: str) -> None:
        """暂停任务."""
        session = await self._session_store.get(session_id)
        if session is not None and session.task_progress is not None:
            session.task_progress.current_step = f"[已暂停] {session.task_progress.current_step}"
            logger.info("任务已暂停: session=%s", session_id)

    async def cancel_task(self, session_id: str) -> None:
        """取消任务."""
        session = await self._session_store.get(session_id)
        if session is not None:
            session.task_progress = None
            session.mode = InteractionMode.CHAT
            logger.info("任务已取消: session=%s", session_id)

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    async def persist_session(self, session_id: str) -> None:
        """持久化会话状态."""
        await self._session_store.persist(session_id)

    async def check_idle_sessions(self) -> list[str]:
        """检查并持久化空闲超时的会话."""
        return await self._session_store.check_idle_sessions()

    # ------------------------------------------------------------------
    # 记忆层代理
    # ------------------------------------------------------------------

    @property
    def memory(self) -> MemoryLayer:
        return self._memory

    @property
    def resolver(self) -> ResourceResolver:
        return self._resolver
