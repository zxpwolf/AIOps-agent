"""MCP Server 基类 + JSON-RPC 协议测试."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mcp_servers.base import McpServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server() -> McpServer:
    return McpServer("test-server", "0.1.0")


@pytest.fixture
def server_with_tools(server: McpServer) -> McpServer:
    async def echo_handler(args: dict) -> dict:
        return {"echo": args.get("text", "")}

    async def add_handler(args: dict) -> dict:
        return {"result": args.get("a", 0) + args.get("b", 0)}

    async def fail_handler(args: dict) -> dict:
        raise ValueError("intentional failure")

    server.register_tool(
        name="echo",
        description="Echo back the input text",
        handler=echo_handler,
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    server.register_tool(
        name="add",
        description="Add two numbers",
        handler=add_handler,
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    )
    server.register_tool(
        name="fail_tool",
        description="A tool that always fails",
        handler=fail_handler,
    )
    return server


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_register_tool_basic(self, server: McpServer) -> None:
        async def handler(args: dict) -> dict:
            return {}

        server.register_tool("my_tool", "My description", handler)
        assert "my_tool" in server._tools
        assert "my_tool" in server._handlers
        assert server._tools["my_tool"]["name"] == "my_tool"
        assert server._tools["my_tool"]["description"] == "My description"

    def test_register_tool_default_schema(self, server: McpServer) -> None:
        async def handler(args: dict) -> dict:
            return {}

        server.register_tool("tool", "desc", handler)
        schema = server._tools["tool"]["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_register_tool_custom_schema(self, server: McpServer) -> None:
        async def handler(args: dict) -> dict:
            return {}

        custom_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        server.register_tool("tool", "desc", handler, input_schema=custom_schema)
        assert server._tools["tool"]["inputSchema"] == custom_schema

    def test_register_duplicate_tool(self, server: McpServer) -> None:
        async def handler(args: dict) -> dict:
            return {}

        server.register_tool("dup", "desc", handler)
        with pytest.raises(ValueError, match="已注册"):
            server.register_tool("dup", "desc2", handler)


# ---------------------------------------------------------------------------
# JSON-RPC Request Handling
# ---------------------------------------------------------------------------


class TestJsonRpcHandling:
    @pytest.mark.asyncio
    async def test_initialize(self, server: McpServer) -> None:
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        })
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        result = response["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "test-server"
        assert result["serverInfo"]["version"] == "0.1.0"
        assert "tools" in result["capabilities"]

    @pytest.mark.asyncio
    async def test_tools_list_empty(self, server: McpServer) -> None:
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert response["result"]["tools"] == []

    @pytest.mark.asyncio
    async def test_tools_list(self, server_with_tools: McpServer) -> None:
        response = await server_with_tools._handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        tools = response["result"]["tools"]
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"echo", "add", "fail_tool"}

    @pytest.mark.asyncio
    async def test_tool_call_success(self, server_with_tools: McpServer) -> None:
        response = await server_with_tools._handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hello"}},
        })
        assert response["id"] == 4
        content = json.loads(response["result"]["content"][0]["text"])
        assert content["echo"] == "hello"

    @pytest.mark.asyncio
    async def test_tool_call_add(self, server_with_tools: McpServer) -> None:
        response = await server_with_tools._handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 3, "b": 7}},
        })
        content = json.loads(response["result"]["content"][0]["text"])
        assert content["result"] == 10

    @pytest.mark.asyncio
    async def test_tool_call_not_found(self, server_with_tools: McpServer) -> None:
        response = await server_with_tools._handle_request({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_tool_call_execution_error(self, server_with_tools: McpServer) -> None:
        response = await server_with_tools._handle_request({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "fail_tool", "arguments": {}},
        })
        assert "error" in response
        assert "intentional failure" in response["error"]["message"]

    @pytest.mark.asyncio
    async def test_notifications_initialized(self, server: McpServer) -> None:
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        assert response == {}

    @pytest.mark.asyncio
    async def test_unknown_method(self, server: McpServer) -> None:
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "unknown/method",
            "params": {},
        })
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_make_response(self, server: McpServer) -> None:
        resp = server._make_response(42, {"key": "value"})
        assert resp == {"jsonrpc": "2.0", "id": 42, "result": {"key": "value"}}

    @pytest.mark.asyncio
    async def test_make_error(self, server: McpServer) -> None:
        resp = server._make_error(1, -32600, "Invalid Request")
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Invalid Request"
