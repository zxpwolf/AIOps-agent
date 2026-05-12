"""Memory_Layer — 短期记忆（内存）和长期记忆（持久化）.

实现短期记忆存储（当前会话）和长期记忆接口（历史故障模式），
支持基于语义相似度的历史案例检索。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiops_agent.models.schemas import Message

logger = logging.getLogger(__name__)


class MemoryLayer:
    """记忆层.

    短期记忆: 内存存储，当前会话的对话历史和上下文。
    长期记忆: 持久化存储，历史故障模式和运维知识。
    """

    def __init__(
        self,
        long_term_dir: str | Path = "data/long_term_memory",
    ) -> None:
        # 短期记忆: {session_id: list[dict]}
        self._short_term: dict[str, list[dict[str, Any]]] = {}

        # 长期记忆持久化目录
        self._long_term_dir = Path(long_term_dir)
        self._long_term_dir.mkdir(parents=True, exist_ok=True)

        # 长期记忆内存索引（简化实现）
        self._long_term_index: list[dict[str, Any]] = []
        self._load_long_term_index()

    # ------------------------------------------------------------------
    # 短期记忆
    # ------------------------------------------------------------------

    async def store_short_term(self, session_id: str, data: dict[str, Any]) -> None:
        """存储短期记忆（内存）.

        Args:
            session_id: 会话 ID。
            data: 要存储的数据。
        """
        if session_id not in self._short_term:
            self._short_term[session_id] = []
        self._short_term[session_id].append(data)

    async def get_short_term(self, session_id: str) -> list[dict[str, Any]]:
        """获取短期记忆."""
        return self._short_term.get(session_id, [])

    async def clear_short_term(self, session_id: str) -> None:
        """清除指定会话的短期记忆."""
        self._short_term.pop(session_id, None)

    # ------------------------------------------------------------------
    # 长期记忆
    # ------------------------------------------------------------------

    async def store_long_term(self, case: dict[str, Any]) -> None:
        """存储故障案例到长期记忆.

        Args:
            case: 故障案例数据，应包含 title、description、resolution、tags 等字段。
        """
        case["stored_at"] = datetime.now(timezone.utc).isoformat()

        # 写入文件
        case_id = case.get("case_id", f"case_{len(self._long_term_index)}")
        file_path = self._long_term_dir / f"{case_id}.json"
        try:
            file_path.write_text(
                json.dumps(case, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("长期记忆写入失败: %s", case_id)
            return

        # 更新内存索引
        self._long_term_index.append(case)
        logger.info("故障案例已存储: %s", case_id)

    async def search_long_term(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """基于语义相似度检索长期记忆.

        简化实现：基于关键词匹配。
        实际部署时应集成向量数据库进行语义检索。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 K 条记录。

        Returns:
            匹配的故障案例列表。
        """
        query_lower = query.lower()
        scored: list[tuple[float, dict]] = []

        for case in self._long_term_index:
            score = 0.0
            # 标题匹配
            title = case.get("title", "").lower()
            if query_lower in title:
                score += 2.0

            # 描述匹配
            desc = case.get("description", "").lower()
            if query_lower in desc:
                score += 1.0

            # 标签匹配
            tags = [t.lower() for t in case.get("tags", [])]
            for word in query_lower.split():
                if word in tags:
                    score += 1.5

            if score > 0:
                scored.append((score, case))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [case for _, case in scored[:top_k]]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_long_term_index(self) -> None:
        """从持久化存储加载长期记忆索引."""
        for file_path in self._long_term_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                self._long_term_index.append(data)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("加载长期记忆失败 %s: %s", file_path, exc)
