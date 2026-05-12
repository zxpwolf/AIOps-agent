"""Tests for SLS MCP Server."""

import pytest

from mcp_servers.sls import create_server


class TestSLSServer:
    def test_create_server(self):
        server = create_server()
        assert server._name == "sls"
        assert len(server._tools) == 3

    def test_has_query_logs(self):
        server = create_server()
        assert "query_logs" in server._tools

    def test_has_list_logstores(self):
        server = create_server()
        assert "list_logstores" in server._tools

    def test_has_get_logstore_index(self):
        server = create_server()
        assert "get_logstore_index" in server._tools

    @pytest.mark.asyncio
    async def test_initialize(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize", "params": {},
        })
        assert response["result"]["serverInfo"]["name"] == "sls"

    @pytest.mark.asyncio
    async def test_tools_list(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/list", "params": {},
        })
        assert len(response["result"]["tools"]) == 3
