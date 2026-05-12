"""有限状态机 — 任务生命周期状态管理.

实现任务状态转换校验和事件触发:
PENDING → RUNNING → COMPLETED / FAILED / CANCELLED
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from aiops_agent.models.schemas import TaskStatus

logger = logging.getLogger(__name__)

# 合法的状态转换
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.PENDING},  # 允许重试
    TaskStatus.CANCELLED: set(),
}


class TaskStateMachine:
    """任务生命周期状态机.

    管理单个任务的状态转换，校验转换合法性，
    并在状态变更时触发回调。
    """

    def __init__(
        self,
        task_id: str,
        initial_status: TaskStatus = TaskStatus.PENDING,
        on_transition: Optional[Callable[[str, TaskStatus, TaskStatus], None]] = None,
    ) -> None:
        self._task_id = task_id
        self._status = initial_status
        self._on_transition = on_transition

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def status(self) -> TaskStatus:
        return self._status

    def transition(self, new_status: TaskStatus) -> None:
        """执行状态转换.

        Args:
            new_status: 目标状态。

        Raises:
            ValueError: 非法状态转换时抛出。
        """
        valid_targets = _VALID_TRANSITIONS.get(self._status, set())
        if new_status not in valid_targets:
            raise ValueError(
                f"非法状态转换: {self._status.value} → {new_status.value} "
                f"(task_id={self._task_id})"
            )

        old_status = self._status
        self._status = new_status

        logger.debug(
            "任务状态转换: %s → %s (task_id=%s)",
            old_status.value,
            new_status.value,
            self._task_id,
        )

        if self._on_transition is not None:
            self._on_transition(self._task_id, old_status, new_status)

    def can_transition(self, new_status: TaskStatus) -> bool:
        """检查是否可以转换到目标状态."""
        return new_status in _VALID_TRANSITIONS.get(self._status, set())

    @property
    def is_terminal(self) -> bool:
        """是否处于终态."""
        return self._status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )
