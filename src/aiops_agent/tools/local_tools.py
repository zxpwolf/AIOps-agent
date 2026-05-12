"""本地工具注册 — 将 Python 函数注册为本地工具.

支持将 Python 函数注册为本地工具，提供工具参数校验，
作为 MCP 工具的回退方案。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class LocalToolDefinition:
    """本地工具定义."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        parameters_schema: dict | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.parameters_schema = parameters_schema or {}

    def __repr__(self) -> str:
        return f"LocalToolDefinition(name={self.name!r})"


class LocalToolRegistry:
    """本地工具注册中心.

    支持将 Python 函数注册为本地工具，提供参数校验和调用接口。
    """

    def __init__(self) -> None:
        self._tools: dict[str, LocalToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        parameters_schema: dict | None = None,
    ) -> None:
        """注册本地工具.

        Args:
            name: 工具名称（唯一）。
            description: 工具描述。
            handler: 工具处理函数（同步或异步）。
            parameters_schema: 参数 JSON Schema（可选）。

        Raises:
            ValueError: 工具名称已存在时抛出。
        """
        if name in self._tools:
            raise ValueError(f"本地工具 '{name}' 已注册")

        self._tools[name] = LocalToolDefinition(
            name=name,
            description=description,
            handler=handler,
            parameters_schema=parameters_schema,
        )
        logger.info("本地工具已注册: %s", name)

    def unregister(self, name: str) -> None:
        """注销本地工具."""
        if name in self._tools:
            del self._tools[name]
            logger.info("本地工具已注销: %s", name)

    def get(self, name: str) -> Optional[LocalToolDefinition]:
        """获取本地工具定义."""
        return self._tools.get(name)

    def list_tools(self) -> list[LocalToolDefinition]:
        """列出所有已注册的本地工具."""
        return list(self._tools.values())

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册."""
        return name in self._tools

    async def call(self, name: str, arguments: dict) -> Any:
        """调用本地工具.

        Args:
            name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具执行结果。

        Raises:
            ValueError: 工具未注册时抛出。
            TypeError: 参数校验失败时抛出。
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"本地工具 '{name}' 未注册")

        # 参数校验
        self._validate_parameters(tool, arguments)

        # 调用处理函数（支持同步和异步）
        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(**arguments)
        else:
            return tool.handler(**arguments)

    @staticmethod
    def _validate_parameters(tool: LocalToolDefinition, arguments: dict) -> None:
        """校验工具参数.

        基于 parameters_schema 中的 required 字段进行基本校验。
        """
        schema = tool.parameters_schema
        if not schema:
            return

        required = schema.get("required", [])
        for field_name in required:
            if field_name not in arguments:
                raise TypeError(
                    f"工具 '{tool.name}' 缺少必填参数: {field_name}"
                )

        # 类型校验（基于 properties）
        properties = schema.get("properties", {})
        for key, value in arguments.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type and not _check_type(value, expected_type):
                    raise TypeError(
                        f"工具 '{tool.name}' 参数 '{key}' 类型错误: "
                        f"期望 {expected_type}，实际 {type(value).__name__}"
                    )


def _check_type(value: Any, expected_type: str) -> bool:
    """检查值是否匹配 JSON Schema 类型."""
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected = type_map.get(expected_type)
    if expected is None:
        return True  # 未知类型不校验
    return isinstance(value, expected)
