"""MCP Server 注册管理 — 动态注册和注销 MCP Server.

管理 MCP Server 的生命周期，维护工具名称到 MCP Server 的映射关系，
支持从配置文件自动加载。
"""

from __future__ import annotations

import logging
from typing import Optional

import yaml

from aiops_agent.models.schemas import MCPServerConfig, MCPTool
from aiops_agent.tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)


class MCPRegistry:
    """MCP Server 注册中心.

    职责:
    - 管理 MCP Server 的动态注册和注销
    - 加载 mcp_servers.yaml 配置，自动连接已配置的 MCP Server
    - 维护工具名称到 MCP Server 的映射关系
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tool_map: dict[str, str] = {}  # tool_name → server_name
        self._tools: dict[str, MCPTool] = {}  # tool_name → MCPTool

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    async def register(self, config: MCPServerConfig) -> list[MCPTool]:
        """注册 MCP Server 并获取工具清单.

        Args:
            config: MCP Server 配置。

        Returns:
            该 Server 提供的工具列表。

        Raises:
            RuntimeError: 连接或工具发现失败时抛出。
        """
        if config.server_name in self._clients:
            logger.warning("MCP Server '%s' 已注册，先注销再重新注册", config.server_name)
            await self.unregister(config.server_name)

        client = MCPClient()
        await client.connect(config)

        tools = await client.list_tools()

        self._clients[config.server_name] = client
        for tool in tools:
            self._tool_map[tool.name] = config.server_name
            self._tools[tool.name] = tool

        logger.info(
            "MCP Server '%s' 注册成功，提供 %d 个工具",
            config.server_name,
            len(tools),
        )
        return tools

    async def unregister(self, server_name: str) -> None:
        """注销 MCP Server.

        Args:
            server_name: 要注销的 Server 名称。
        """
        client = self._clients.pop(server_name, None)
        if client is not None:
            await client.disconnect()

        # 清理工具映射
        tools_to_remove = [
            name for name, sn in self._tool_map.items() if sn == server_name
        ]
        for name in tools_to_remove:
            del self._tool_map[name]
            self._tools.pop(name, None)

        logger.info("MCP Server '%s' 已注销", server_name)

    # ------------------------------------------------------------------
    # 工具查找
    # ------------------------------------------------------------------

    def find_tool(self, tool_name: str) -> Optional[MCPTool]:
        """根据工具名称查找 MCPTool 定义."""
        return self._tools.get(tool_name)

    def get_client(self, server_name: str) -> Optional[MCPClient]:
        """获取指定 Server 的 MCP Client."""
        return self._clients.get(server_name)

    def get_client_for_tool(self, tool_name: str) -> Optional[MCPClient]:
        """获取提供指定工具的 MCP Client."""
        server_name = self._tool_map.get(tool_name)
        if server_name is None:
            return None
        return self._clients.get(server_name)

    def list_all_tools(self) -> list[MCPTool]:
        """列出所有已注册的 MCP 工具."""
        return list(self._tools.values())

    def list_servers(self) -> list[str]:
        """列出所有已注册的 MCP Server 名称."""
        return list(self._clients.keys())

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    async def load_from_config(self, config_path: str) -> None:
        """从 mcp_servers.yaml 加载并自动连接 MCP Server.

        Args:
            config_path: 配置文件路径。
        """
        try:
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error("MCP Server 配置加载失败: %s", exc)
            return

        servers = config.get("servers", {})
        for name, server_config in servers.items():
            if not server_config.get("enabled", True):
                logger.info("MCP Server '%s' 已禁用，跳过", name)
                continue

            try:
                mcp_config = MCPServerConfig(
                    server_name=server_config.get("server_name", name),
                    transport=server_config["transport"],
                    command=server_config.get("command"),
                    args=server_config.get("args", []),
                    url=server_config.get("url"),
                    env=server_config.get("env", {}),
                )
                await self.register(mcp_config)
            except Exception:
                logger.exception("MCP Server '%s' 注册失败", name)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭所有 MCP Client 连接."""
        for name in list(self._clients.keys()):
            await self.unregister(name)
