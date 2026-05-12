"""Skill 基类和标准接口.

定义 SkillInstance 抽象基类，包含 execute、validate 方法
和技能生命周期钩子。支持依赖注入 ToolExecutor。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from aiops_agent.models.schemas import ValidationResult

if TYPE_CHECKING:
    from aiops_agent.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class SkillInstance(ABC):
    """技能实例抽象基类.

    所有技能模块必须继承此基类并实现 execute 和 validate 方法。
    可选覆盖生命周期钩子。

    支持通过 set_tool_executor() 注入 ToolExecutor，
    使 Skill 能够调用 MCP Server 或本地工具。
    """

    def __init__(self) -> None:
        self._tool_executor: Optional["ToolExecutor"] = None

    def set_tool_executor(self, executor: "ToolExecutor") -> None:
        """注入 ToolExecutor 实例.

        由 Orchestrator 或 SkillRegistry 在注册时调用，
        使 Skill 能够通过统一执行器调用工具。
        """
        self._tool_executor = executor

    @property
    def tool_executor(self) -> Optional["ToolExecutor"]:
        """获取 ToolExecutor 实例."""
        return self._tool_executor

    @abstractmethod
    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行技能.

        Args:
            input_data: 输入参数字典。

        Returns:
            执行结果字典。

        Raises:
            SkillExecutionError: 执行失败时抛出。
        """
        ...

    @abstractmethod
    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        """校验输入参数.

        Args:
            input_data: 待校验的输入参数。

        Returns:
            ValidationResult 包含校验结果。
        """
        ...

    async def on_register(self) -> None:
        """注册时的生命周期钩子.

        在技能注册到 SkillRegistry 后调用，可用于初始化资源。
        """

    async def on_unregister(self) -> None:
        """注销时的生命周期钩子.

        在技能从 SkillRegistry 注销前调用，可用于清理资源。
        """

    async def health_check(self) -> bool:
        """健康检查.

        Returns:
            True 表示健康，False 表示不健康。
        """
        return True
