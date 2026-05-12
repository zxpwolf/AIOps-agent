"""Tests for ECS/VPC/RDS MCP Server."""

import pytest

from mcp_servers.ecs_vpc_rds import create_server


class TestEcsVpcRdsServer:
    def test_create_server(self):
        server = create_server()
        assert server._name == "ecs_vpc_rds"
        assert len(server._tools) == 9

    def test_ecs_tools(self):
        server = create_server()
        assert "describe_instances" in server._tools
        assert "describe_instance_status" in server._tools
        assert "describe_disks" in server._tools
        assert "describe_security_groups" in server._tools

    def test_vpc_tools(self):
        server = create_server()
        assert "describe_vpcs" in server._tools
        assert "describe_vswitches" in server._tools

    def test_rds_tools(self):
        server = create_server()
        assert "describe_dbinstances" in server._tools
        assert "describe_slowlog_records" in server._tools
        assert "describe_dbinstance_status" in server._tools

    @pytest.mark.asyncio
    async def test_initialize(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize", "params": {},
        })
        assert response["result"]["serverInfo"]["name"] == "ecs_vpc_rds"

    @pytest.mark.asyncio
    async def test_tools_list(self):
        server = create_server()
        response = await server._handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/list", "params": {},
        })
        assert len(response["result"]["tools"]) == 9

    def test_describe_slowlog_requires_db_instance_id(self):
        server = create_server()
        tool = server._tools["describe_slowlog_records"]
        required = tool["inputSchema"].get("required", [])
        assert "db_instance_id" in required
