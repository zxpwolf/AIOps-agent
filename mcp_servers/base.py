"""MCP Server 基类 — JSON-RPC 2.0 stdio 通信."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class McpServer:
    """MCP JSON-RPC 2.0 stdio 服务器基类."""

    def __init__(self, name: str, version: str) -> None:
        self._name = name
        self._version = version
        self._tools: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[..., Coroutine]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        handler: Callable[..., Coroutine],
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """注册工具（注意：这是方法调用，不是装饰器！）."""
        if name in self._tools:
            raise ValueError(f"工具 '{name}' 已注册")

        tool_def = {
            "name": name,
            "description": description,
            "inputSchema": input_schema or {"type": "object", "properties": {}},
        }
        self._tools[name] = tool_def
        self._handlers[name] = handler

    async def run(self) -> None:
        """启动 stdio JSON-RPC 循环."""
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode("utf-8"))
                response = await self._handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                continue
            except Exception as exc:
                logger.exception("处理请求失败")

    def _make_response(self, request_id: Any, result: Any) -> dict:
        """构建 JSON-RPC 成功响应."""
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _make_error(self, request_id: Any, code: int, message: str) -> dict:
        """构建 JSON-RPC 错误响应."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    async def _handle_request(self, request: dict) -> dict | None:
        """处理 JSON-RPC 请求."""
        method = request.get("method", "")
        request_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._make_response(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._name, "version": self._version},
            })
        elif method == "tools/list":
            return self._make_response(request_id, {
                "tools": list(self._tools.values()),
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            if tool_name not in self._handlers:
                return self._make_error(request_id, -32601, f"工具未找到: {tool_name}")
            try:
                result = await self._handlers[tool_name](arguments)
                return self._make_response(request_id, {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                })
            except Exception as exc:
                return self._make_error(request_id, -32603, str(exc))
        elif method == "notifications/initialized":
            return {}
        else:
            return self._make_error(request_id, -32601, f"未知方法: {method}")
