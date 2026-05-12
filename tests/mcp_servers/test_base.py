"""Tests for McpServer base class."""

import pytest

from mcp_servers.base import McpServer


@pytest.fixture
def server():
    return McpServer("test_server", "0.1.0")


class TestMcpServerRegistration:
    def test_register_tool(self, server):
        async def handler(args):
            return {"result": "ok"}

        server.register_tool(
            name="test_tool",
            description="A test tool",
            handler=handler,
            input_schema={"type": "object", "properties": {}},
        )
        assert "test_tool" in server._tools
        assert "test_tool" in server._handlers

    def test_register_multiple_tools(self, server):
        async def h1(a): return {}
        async def h2(a): return {}

        server.register_tool("tool1", "desc1", h1)
        server.register_tool("tool2", "desc2", h2)
        assert len(server._tools) == 2

    def test_tool_has_correct_structure(self, server):
        async def handler(a): return {}
        server.register_tool("my_tool", "My description", handler)
        tool = server._tools["my_tool"]
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["name"] == "my_tool"
        assert tool["description"] == "My description"

    def test_register_duplicate_raises(self, server):
        async def handler(a): return {}
        server.register_tool("dup", "desc", handler)
        with pytest.raises(ValueError, match="已注册"):
            server.register_tool("dup", "desc2", handler)


class TestMcpServerHandleRequest:
    @pytest.mark.asyncio
    async def test_initialize_returns_protocol_info(self, server):
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert response["id"] == 1
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert response["result"]["serverInfo"]["name"] == "test_server"

    @pytest.mark.asyncio
    async def test_tools_list_returns_registered_tools(self, server):
        async def handler(a): return {}
        server.register_tool("test_tool", "desc", handler)

        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert len(response["result"]["tools"]) == 1
        assert response["result"]["tools"][0]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_tools_call_invokes_handler(self, server):
        async def handler(args):
            return {"echo": args.get("value")}

        server.register_tool("echo", "Echo tool", handler)

        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"value": "hello"}},
        })
        import json
        content = json.loads(response["result"]["content"][0]["text"])
        assert content["echo"] == "hello"

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, server):
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "unknown_method",
            "params": {},
        })
        assert "error" in response

    @pytest.mark.asyncio
    async def test_initialized_notification_returns_empty_dict(self, server):
        response = await server._handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        assert response == {}

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self, server):
        async def failing_handler(args):
            raise RuntimeError("handler error")

        server.register_tool("fail", "fail", failing_handler)

        response = await server._handle_request({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "fail", "arguments": {}},
        })
        assert "error" in response
        assert "handler error" in response["error"]["message"]

    def test_make_response(self, server):
        resp = server._make_response(42, {"key": "value"})
        assert resp == {"jsonrpc": "2.0", "id": 42, "result": {"key": "value"}}

    def test_make_error(self, server):
        resp = server._make_error(1, -32600, "Invalid Request")
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Invalid Request"
