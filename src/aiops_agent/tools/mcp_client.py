"""MCP Client — Model Context Protocol 客户端实现.

支持 stdio 和 SSE/HTTP 两种传输模式，实现 JSON-RPC 消息的
序列化和反序列化，提供 connect、list_tools、call_tool、disconnect 方法。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

import aiohttp

from aiops_agent.models.schemas import MCPServerConfig, MCPTool

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP 协议客户端.

    支持:
    - stdio 传输模式（本地子进程）
    - SSE/HTTP 传输模式（远程服务）
    - JSON-RPC 2.0 消息协议
    """

    def __init__(self) -> None:
        self._config: Optional[MCPServerConfig] = None
        self._connected = False

        # stdio 模式
        self._process: Optional[asyncio.subprocess.Process] = None

        # SSE/HTTP 模式
        self._session: Optional[aiohttp.ClientSession] = None

        # 工具缓存
        self._tools: list[MCPTool] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_name(self) -> str:
        return self._config.server_name if self._config else ""

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    async def connect(self, config: MCPServerConfig) -> None:
        """连接到 MCP Server.

        Args:
            config: MCP Server 配置。

        Raises:
            RuntimeError: 连接失败时抛出。
        """
        self._config = config

        if config.transport == "stdio":
            await self._connect_stdio(config)
        elif config.transport in ("sse", "streamable-http"):
            await self._connect_http(config)
        else:
            raise ValueError(f"不支持的传输模式: {config.transport}")

        self._connected = True
        logger.info("MCP Client 已连接: %s (%s)", config.server_name, config.transport)

    async def disconnect(self) -> None:
        """断开连接."""
        if self._process is not None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
            self._process = None

        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

        self._connected = False
        self._tools.clear()
        server = self._config.server_name if self._config else "unknown"
        logger.info("MCP Client 已断开: %s", server)

    # ------------------------------------------------------------------
    # 工具发现
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[MCPTool]:
        """获取 MCP Server 提供的工具清单.

        Returns:
            MCPTool 列表。

        Raises:
            RuntimeError: 未连接或请求失败时抛出。
        """
        self._ensure_connected()

        response = await self._send_request("tools/list", {})
        tools_data = response.get("tools", [])

        self._tools = [
            MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.server_name,
            )
            for t in tools_data
        ]

        logger.info(
            "MCP Server '%s' 提供 %d 个工具",
            self.server_name,
            len(self._tools),
        )
        return self._tools

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 MCP Server 工具.

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。

        Returns:
            工具执行结果。

        Raises:
            RuntimeError: 未连接或调用失败时抛出。
        """
        self._ensure_connected()

        response = await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

        return response

    # ------------------------------------------------------------------
    # JSON-RPC 通信
    # ------------------------------------------------------------------

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 2.0 请求并等待响应.

        Args:
            method: RPC 方法名。
            params: 方法参数。

        Returns:
            响应结果。

        Raises:
            RuntimeError: 通信失败或收到错误响应时抛出。
        """
        request_id = str(uuid.uuid4())
        message = serialize_jsonrpc_request(request_id, method, params)

        if self._config and self._config.transport == "stdio":
            return await self._send_stdio(message, request_id)
        else:
            return await self._send_http(message, request_id)

    async def _send_stdio(self, message: dict, request_id: str) -> dict:
        """通过 stdio 发送 JSON-RPC 请求."""
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("stdio 进程未就绪")

        raw = json.dumps(message) + "\n"
        self._process.stdin.write(raw.encode())
        await self._process.stdin.drain()

        # 读取响应
        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=30,
        )
        if not line:
            raise RuntimeError("MCP Server 未返回响应")

        response = json.loads(line.decode())
        return self._parse_response(response, request_id)

    async def _send_http(self, message: dict, request_id: str) -> dict:
        """通过 HTTP 发送 JSON-RPC 请求."""
        if self._session is None:
            raise RuntimeError("HTTP 会话未就绪")

        url = self._config.url if self._config else ""
        async with self._session.post(
            url,
            json=message,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"MCP HTTP 请求失败: HTTP {resp.status} - {body}")

            response = await resp.json()

        return self._parse_response(response, request_id)

    # ------------------------------------------------------------------
    # 连接实现
    # ------------------------------------------------------------------

    async def _connect_stdio(self, config: MCPServerConfig) -> None:
        """通过 stdio 连接到本地 MCP Server 进程."""
        if not config.command:
            raise ValueError("stdio 模式需要指定 command")

        cmd = [config.command] + config.args
        env = config.env if config.env else None

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # 发送 initialize 请求
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aiops-agent", "version": "0.1.0"},
        })

    async def _connect_http(self, config: MCPServerConfig) -> None:
        """通过 HTTP/SSE 连接到远程 MCP Server."""
        if not config.url:
            raise ValueError("SSE/HTTP 模式需要指定 url")

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("MCP Client 未连接，请先调用 connect()")

    @staticmethod
    def _parse_response(response: dict, expected_id: str) -> dict:
        """解析 JSON-RPC 响应."""
        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"MCP RPC 错误 [{error.get('code', -1)}]: {error.get('message', 'unknown')}"
            )
        return response.get("result", {})


# ---------------------------------------------------------------------------
# JSON-RPC 序列化/反序列化
# ---------------------------------------------------------------------------


def serialize_jsonrpc_request(
    request_id: str,
    method: str,
    params: dict,
) -> dict:
    """序列化 JSON-RPC 2.0 请求."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def serialize_jsonrpc_response(
    request_id: str,
    result: Any = None,
    error: dict | None = None,
) -> dict:
    """序列化 JSON-RPC 2.0 响应."""
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
    }
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def deserialize_jsonrpc(data: dict) -> dict:
    """反序列化 JSON-RPC 2.0 消息.

    Returns:
        解析后的消息字典，包含 jsonrpc、id、method/result/error 等字段。

    Raises:
        ValueError: 消息格式不合法时抛出。
    """
    if data.get("jsonrpc") != "2.0":
        raise ValueError(f"不支持的 JSON-RPC 版本: {data.get('jsonrpc')}")
    return data
