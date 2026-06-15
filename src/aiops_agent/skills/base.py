"""Skill 基类和标准接口.

定义 SkillInstance 抽象基类，包含 execute、validate 方法
和技能生命周期钩子。支持依赖注入 ToolExecutor。

自描述接口: 每个 Skill 声明自己的并发安全性、权限需求、渲染行为。
遵循 Claude Code 的 self-describing tool pattern — 中心编排器无需了解每个 Skill 的细节。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from aiops_agent.models.schemas import ValidationResult, PermissionLevel

if TYPE_CHECKING:
    from aiops_agent.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class SkillInstance(ABC):
    """技能实例抽象基类.

    所有技能模块必须继承此基类并实现 execute 和 validate 方法。
    可选覆盖生命周期钩子和自描述声明。

    自描述声明 (Self-Describing Declarations):
    - concurrency_safe: 是否可以与其他 Skill 并发执行
    - permission_requirements: 该 Skill 需要的权限级别列表
    - description: 技能描述（供 LLM 选择技能时参考）
    - render_format: 结果渲染格式提示

    支持通过 set_tool_executor() 注入 ToolExecutor，
    使 Skill 能够通过统一执行器调用工具。
    """

    # ------------------------------------------------------------------
    # Self-describing declarations — 每个 Skill 自声明自己的特性
    # ------------------------------------------------------------------
    concurrency_safe: bool = True  # 是否可以并发执行（默认 True）
    permission_requirements: list[PermissionLevel] = [PermissionLevel.READ_ONLY]  # 权限需求
    description: str = ""  # 技能描述（供 LLM 路由参考）
    render_format: str = "json"  # 结果渲染格式: "json" | "markdown" | "text"

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

    def render_result(self, result: dict[str, Any]) -> str:
        """渲染执行结果为可展示的文本.

        根据 render_format 声明选择渲染方式:
        - json: JSON 格式化输出
        - markdown: Markdown 表格/列表
        - text: 纯文本摘要
        """
        import json as _json

        if self.render_format == "json":
            return _json.dumps(result, ensure_ascii=False, indent=2, default=str)
        elif self.render_format == "markdown":
            # 简化的 Markdown 渲染
            lines = []
            for key, value in result.items():
                if isinstance(value, list):
                    lines.append(f"- **{key}**: {len(value)} items")
                elif isinstance(value, dict):
                    lines.append(f"- **{key}**: {len(value)} fields")
                else:
                    lines.append(f"- **{key}**: {value}")
            return "\n".join(lines)
        else:
            # text 格式 — 简短摘要
            return str(result)[:200]
