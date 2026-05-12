"""会话状态管理 — 创建、获取、持久化和恢复会话.

管理会话的生命周期，支持空闲超时检测（30 分钟）和自动持久化。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from aiops_agent.models.schemas import InteractionMode, SessionState

logger = logging.getLogger(__name__)


class SessionStore:
    """会话状态存储.

    职责:
    - 会话创建、获取、持久化和恢复
    - 空闲超时检测（30 分钟）
    - 自动持久化到本地文件
    """

    def __init__(
        self,
        persist_dir: str | Path = "data/sessions",
        ttl_minutes: int = 30,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._ttl_minutes = ttl_minutes

    async def create(self, session_id: str, user_id: str) -> SessionState:
        """创建新会话."""
        now = datetime.now(timezone.utc)
        session = SessionState(
            session_id=session_id,
            user_id=user_id,
            mode=InteractionMode.CHAT,
            created_at=now,
            last_active_at=now,
            ttl_minutes=self._ttl_minutes,
        )
        self._sessions[session_id] = session
        logger.info("会话已创建: %s", session_id)
        return session

    async def get(self, session_id: str) -> Optional[SessionState]:
        """获取会话，如果内存中没有则尝试从持久化存储恢复."""
        session = self._sessions.get(session_id)

        if session is None:
            session = await self._restore(session_id)
            if session is not None:
                self._sessions[session_id] = session

        if session is not None:
            session.last_active_at = datetime.now(timezone.utc)

        return session

    async def get_or_create(self, session_id: str, user_id: str) -> SessionState:
        """获取或创建会话."""
        session = await self.get(session_id)
        if session is None:
            session = await self.create(session_id, user_id)
        return session

    async def persist(self, session_id: str) -> None:
        """持久化会话状态到本地文件."""
        session = self._sessions.get(session_id)
        if session is None:
            return

        file_path = self._persist_dir / f"{session_id}.json"
        try:
            data = session.model_dump(mode="json")
            file_path.write_text(
                json.dumps(data, ensure_ascii=False, default=str, indent=2),
                encoding="utf-8",
            )
            logger.debug("会话已持久化: %s", session_id)
        except OSError:
            logger.exception("会话持久化失败: %s", session_id)

    async def remove(self, session_id: str) -> None:
        """移除会话."""
        self._sessions.pop(session_id, None)
        file_path = self._persist_dir / f"{session_id}.json"
        if file_path.exists():
            file_path.unlink()

    async def check_idle_sessions(self) -> list[str]:
        """检查并持久化空闲超时的会话.

        Returns:
            已持久化的会话 ID 列表。
        """
        now = datetime.now(timezone.utc)
        idle_sessions = []

        for session_id, session in list(self._sessions.items()):
            idle_time = now - session.last_active_at
            if idle_time > timedelta(minutes=session.ttl_minutes):
                await self.persist(session_id)
                self._sessions.pop(session_id, None)
                idle_sessions.append(session_id)
                logger.info("会话空闲超时已持久化: %s", session_id)

        return idle_sessions

    async def _restore(self, session_id: str) -> Optional[SessionState]:
        """从持久化存储恢复会话."""
        file_path = self._persist_dir / f"{session_id}.json"
        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            session = SessionState.model_validate(data)
            logger.info("会话已恢复: %s", session_id)
            return session
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("会话恢复失败 %s: %s", session_id, exc)
            return None
