"""Tests for CloudMonitor MCP Server."""

import pytest

from mcp_servers.cloud_monitor import create_server


class TestCloudMonitorServer:
    def test_create_server(self):
        server = create_server()
        assert server._name == "cloud_monitor"
        assert len(server._tools) == 3

    def test_has_query_metric_last(self):
        server = create_server()
        assert "query_metric_last" in server._tools

    def test_has_query_metric_list(self):
        server = create_server()
        assert "query_metric_list" in server._tools

    def test_has_query_alarm_history(self):
        server = create_server()
        assert "query_alarm_history" in server._tools

    @pytest.mark.asyncio
    async def test_initialize(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize", "params": {},
        })
        assert response["result"]["serverInfo"]["name"] == "cloud_monitor"

    @pytest.mark.asyncio
    async def test_tools_list(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/list", "params": {},
        })
        assert len(response["result"]["tools"]) == 3

    def test_query_metric_last_has_required_params(self):
        server = create_server()
        tool = server._tools["query_metric_last"]
        required = tool["inputSchema"].get("required", [])
        assert "namespace" in required
        assert "metric_name" in required
        assert "instance_id" in required
