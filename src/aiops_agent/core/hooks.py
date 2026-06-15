"""Hook 系统 — 生命周期拦截器.

遵循 Claude Code 的 Hook pattern，允许外部代码在关键执行节点
注入自定义行为，而无需修改核心编排器代码。

Hook 类型:
- pre_decompose: 任务分解前
- post_decompose: 任务分解后
- pre_skill_execute: 单个技能执行前
- post_skill_execute: 单个技能执行后
- pre_loop_turn: Agent loop 每轮执行前
- post_loop_turn: Agent loop 每轮执行后
- on_error: 任何错误发生时
- on_terminal: Agent loop 终止时
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    """Hook 事件类型 — 定义可拦截的生命周期节点."""

    PRE_DECOMPOSE = "pre_decompose"
    POST_DECOMPOSE = "post_decompose"
    PRE_SKILL_EXECUTE = "pre_skill_execute"
    POST_SKILL_EXECUTE = "post_skill_execute"
    PRE_LOOP_TURN = "pre_loop_turn"
    POST_LOOP_TURN = "post_loop_turn"
    ON_ERROR = "on_error"
    ON_TERMINAL = "on_terminal"


@dataclass
class HookContext:
    """Hook 执行上下文 — 传递给 hook handler 的信息."""

    event: HookEvent
    session_id: str = ""
    user_input: str = ""
    plan: Any = None  # TaskPlan
    task: Any = None  # SubTask
    skill_name: str = ""
    action: str = ""
    result: Any = None
    error: str = ""
    turn_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# Hook handler 类型: 接收 HookContext，可选返回修改后的 HookContext
HookHandler = Callable[[HookContext], Coroutine[Any, Any, HookContext | None]]


class HookRegistry:
    """Hook 注册表 — 管理生命周期拦截器的注册和执行.

    用法:
        registry = HookRegistry()
        registry.register(HookEvent.PRE_DECOMPOSE, my_pre_decompose_handler)
        registry.register(HookEvent.ON_ERROR, my_error_handler)

        # 在编排器中触发:
        ctx = await registry.trigger(HookEvent.PRE_DECOMPOSE, HookContext(...))
    """

    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = {}

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        """注册一个 hook handler 到指定事件."""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)
        logger.info("Hook registered: %s → %s", event.value, handler.__name__)

    def unregister(self, event: HookEvent, handler: HookHandler) -> None:
        """从指定事件中移除一个 hook handler."""
        if event in self._handlers:
            self._handlers[event] = [
                h for h in self._handlers[event] if h != handler
            ]
            logger.info("Hook unregistered: %s → %s", event.value, handler.__name__)

    async def trigger(self, event: HookEvent, context: HookContext) -> HookContext:
        """触发指定事件的全部 hook handler.

        Handler 按注册顺序依次执行。如果 handler 返回了修改后的 HookContext，
        则后续 handler 接收修改后的版本（链式传递）。

        Args:
            event: 触发的 Hook 事件类型。
            context: Hook 执行上下文。

        Returns:
            经过所有 handler 处理后的 HookContext（可能已被修改）。
        """
        handlers = self._handlers.get(event, [])
        if not handlers:
            return context

        current_ctx = context
        for handler in handlers:
            try:
                result = await handler(current_ctx)
                if result is not None:
                    current_ctx = result
            except Exception as exc:
                logger.warning(
                    "Hook handler %s failed for event %s: %s",
                    handler.__name__,
                    event.value,
                    exc,
                )
                # Hook 失败不阻断主流程

        return current_ctx

    def list_hooks(self) -> dict[str, list[str]]:
        """列出所有已注册的 hook handler."""
        return {
            event.value: [h.__name__ for h in handlers]
            for event, handlers in self._handlers.items()
        }
