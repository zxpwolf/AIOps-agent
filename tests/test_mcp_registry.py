"""Tests for MCPRegistry."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.models.schemas import MCPServerConfig, MCPTool
from aiops_agent.tools.mcp_registry import MCPRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(
    server_name: str = "test-server",
    tools: list[MCPTool] | None = None,
) -> MagicMock:
    """Create a fully mocked MCPClient instance."""
    client = MagicMock()
    client.server_name = server_name
    client.connected = True
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.list_tools = AsyncMock(return_value=tools or [])
    client.call_tool = AsyncMock(return_value={"result": "ok"})
    return client


def _make_config(server_name: str = "test-server", transport: str = "stdio") -> MCPServerConfig:
    return MCPServerConfig(
        server_name=server_name,
        transport=transport,
        command="npx" if transport == "stdio" else None,
        args=["-y", "@modelcontextprotocol/server-everything"] if transport == "stdio" else [],
        url="http://localhost:3001" if transport != "stdio" else None,
    )


def _make_tool(name: str, server_name: str = "test-server") -> MCPTool:
    return MCPTool(
        name=name,
        description=f"Tool {name}",
        input_schema={"type": "object", "properties": {}},
        server_name=server_name,
    )


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

class TestRegister:
    """Test MCPRegistry.register()."""

    @pytest.mark.asyncio
    async def test_register_connects_and_discovers_tools(self) -> None:
        """register: connects client, discovers tools, populates mappings."""
        tools = [_make_tool("tool_a"), _make_tool("tool_b")]
        mock_client = _make_mock_client(tools=tools)

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            config = _make_config("my-server")
            result = await registry.register(config)

            # Returns the tool list
            assert result == tools

            # Client connected
            mock_client.connect.assert_called_once_with(config)
            mock_client.list_tools.assert_called_once()

            # Mappings populated
            assert "my-server" in registry._clients
            assert registry._clients["my-server"] is mock_client
            assert "tool_a" in registry._tool_map
            assert "tool_b" in registry._tool_map
            assert registry._tool_map["tool_a"] == "my-server"
            assert registry._tool_map["tool_b"] == "my-server"
            assert "tool_a" in registry._tools
            assert "tool_b" in registry._tools

    @pytest.mark.asyncio
    async def test_register_empty_tools(self) -> None:
        """register: server with no tools still succeeds."""
        mock_client = _make_mock_client(tools=[])

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            result = await registry.register(_make_config("empty-server"))

            assert result == []
            assert "empty-server" in registry._clients
            assert registry.list_servers() == ["empty-server"]

    @pytest.mark.asyncio
    async def test_register_reregistration_unregisters_old_first(self) -> None:
        """register: re-registration unregisters old client first."""
        tools = [_make_tool("tool_x")]
        old_client = _make_mock_client(tools=tools)
        new_client = _make_mock_client(tools=[_make_tool("tool_y")])

        with patch("aiops_agent.tools.mcp_registry.MCPClient", side_effect=[old_client, new_client]):
            registry = MCPRegistry()
            config = _make_config("re-register-server")

            # First registration
            await registry.register(config)
            assert registry._clients["re-register-server"] is old_client
            assert "tool_x" in registry._tools

            # Re-registration
            result = await registry.register(config)

            # Old client disconnected
            old_client.disconnect.assert_called_once()
            # New client is now active
            assert registry._clients["re-register-server"] is new_client
            # Old tool gone, new tool present
            assert "tool_x" not in registry._tools
            assert "tool_y" in registry._tools
            assert result == [_make_tool("tool_y")]


# ---------------------------------------------------------------------------
# unregister
# ---------------------------------------------------------------------------

class TestUnregister:
    """Test MCPRegistry.unregister()."""

    @pytest.mark.asyncio
    async def test_unregister_disconnects_and_cleans(self) -> None:
        """unregister: disconnects, cleans tool mappings."""
        tools = [_make_tool("tool_a", "srv"), _make_tool("tool_b", "srv")]
        mock_client = _make_mock_client(tools=tools)

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            await registry.register(_make_config("srv"))

            assert "srv" in registry._clients
            assert "tool_a" in registry._tool_map

            await registry.unregister("srv")

            mock_client.disconnect.assert_called_once()
            assert "srv" not in registry._clients
            assert "tool_a" not in registry._tool_map
            assert "tool_b" not in registry._tool_map
            assert "tool_a" not in registry._tools
            assert "tool_b" not in registry._tools

    @pytest.mark.asyncio
    async def test_unregister_non_existent_is_noop(self) -> None:
        """unregister: non-existent server is no-op."""
        registry = MCPRegistry()
        # Should not raise
        await registry.unregister("does-not-exist")
        assert registry.list_servers() == []


# ---------------------------------------------------------------------------
# find_tool / get_client / get_client_for_tool
# ---------------------------------------------------------------------------

class TestToolLookup:
    """Test find_tool, get_client, get_client_for_tool."""

    @pytest.mark.asyncio
    async def test_find_tool_found(self) -> None:
        tool = _make_tool("find_me")
        mock_client = _make_mock_client(tools=[tool])

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            await registry.register(_make_config("srv"))

            found = registry.find_tool("find_me")
            assert found is not None
            assert found.name == "find_me"

    @pytest.mark.asyncio
    async def test_find_tool_not_found(self) -> None:
        registry = MCPRegistry()
        assert registry.find_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_client_found(self) -> None:
        mock_client = _make_mock_client()

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            await registry.register(_make_config("srv"))

            assert registry.get_client("srv") is mock_client

    @pytest.mark.asyncio
    async def test_get_client_not_found(self) -> None:
        registry = MCPRegistry()
        assert registry.get_client("unknown") is None

    @pytest.mark.asyncio
    async def test_get_client_for_tool_found(self) -> None:
        tools = [_make_tool("my_tool", "srv")]
        mock_client = _make_mock_client(tools=tools)

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            await registry.register(_make_config("srv"))

            client = registry.get_client_for_tool("my_tool")
            assert client is mock_client

    @pytest.mark.asyncio
    async def test_get_client_for_tool_not_found(self) -> None:
        registry = MCPRegistry()
        assert registry.get_client_for_tool("no_such_tool") is None


# ---------------------------------------------------------------------------
# list_all_tools / list_servers
# ---------------------------------------------------------------------------

class TestListing:
    """Test list_all_tools and list_servers."""

    @pytest.mark.asyncio
    async def test_list_all_tools(self) -> None:
        tools = [_make_tool("a"), _make_tool("b")]
        mock_client = _make_mock_client(tools=tools)

        with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
            registry = MCPRegistry()
            await registry.register(_make_config("srv"))

            all_tools = registry.list_all_tools()
            assert len(all_tools) == 2
            names = {t.name for t in all_tools}
            assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_servers(self) -> None:
        mock_client_a = _make_mock_client(tools=[_make_tool("ta", "a")])
        mock_client_b = _make_mock_client(tools=[_make_tool("tb", "b")])

        with patch("aiops_agent.tools.mcp_registry.MCPClient", side_effect=[mock_client_a, mock_client_b]):
            registry = MCPRegistry()
            await registry.register(_make_config("a"))
            await registry.register(_make_config("b"))

            servers = registry.list_servers()
            assert set(servers) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        registry = MCPRegistry()
        assert registry.list_all_tools() == []
        assert registry.list_servers() == []


# ---------------------------------------------------------------------------
# load_from_config
# ---------------------------------------------------------------------------

class TestLoadFromConfig:
    """Test MCPRegistry.load_from_config()."""

    @pytest.mark.asyncio
    async def test_load_enabled_servers(self) -> None:
        """load_from_config: YAML parsing, enabled servers."""
        yaml_content = """
servers:
  server_alpha:
    server_name: alpha
    transport: stdio
    command: npx
    args: ["-y", "server-alpha"]
  server_beta:
    server_name: beta
    transport: sse
    url: http://localhost:4000
"""
        mock_client = _make_mock_client()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
                registry = MCPRegistry()
                await registry.load_from_config(f.name)

                assert "alpha" in registry._clients
                assert "beta" in registry._clients

    @pytest.mark.asyncio
    async def test_load_skips_disabled_servers(self) -> None:
        """load_from_config: disabled servers are skipped."""
        yaml_content = """
servers:
  enabled_server:
    server_name: enabled
    transport: stdio
    command: echo
  disabled_server:
    server_name: disabled
    transport: stdio
    command: echo
    enabled: false
"""
        mock_client = _make_mock_client()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
                registry = MCPRegistry()
                await registry.load_from_config(f.name)

                assert "enabled" in registry._clients
                assert "disabled" not in registry._clients

    @pytest.mark.asyncio
    async def test_load_uses_default_enabled_true(self) -> None:
        """load_from_config: servers without 'enabled' key are enabled by default."""
        yaml_content = """
servers:
  implicit:
    server_name: implicit
    transport: stdio
    command: echo
"""
        mock_client = _make_mock_client()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
                registry = MCPRegistry()
                await registry.load_from_config(f.name)
                assert "implicit" in registry._clients

    @pytest.mark.asyncio
    async def test_load_yaml_parse_error(self) -> None:
        """load_from_config: YAML errors are handled gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            f.flush()

            registry = MCPRegistry()
            # Should not raise, just logs error
            await registry.load_from_config(f.name)
            assert registry.list_servers() == []

    @pytest.mark.asyncio
    async def test_load_file_not_found(self) -> None:
        """load_from_config: missing file is handled gracefully."""
        registry = MCPRegistry()
        await registry.load_from_config("/nonexistent/path/config.yaml")
        assert registry.list_servers() == []

    @pytest.mark.asyncio
    async def test_load_register_failure_doesnt_crash(self) -> None:
        """load_from_config: individual server register failure is caught and logged."""
        yaml_content = """
servers:
  bad_server:
    server_name: bad
    transport: stdio
    command: echo
"""
        mock_client = _make_mock_client()
        mock_client.connect = AsyncMock(side_effect=RuntimeError("Connection refused"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with patch("aiops_agent.tools.mcp_registry.MCPClient", return_value=mock_client):
                registry = MCPRegistry()
                await registry.load_from_config(f.name)
                # Server not registered due to connect failure
                assert "bad" not in registry._clients

    @pytest.mark.asyncio
    async def test_load_empty_config(self) -> None:
        """load_from_config: empty YAML file is handled."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()

            registry = MCPRegistry()
            await registry.load_from_config(f.name)
            assert registry.list_servers() == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

class TestClose:
    """Test MCPRegistry.close()."""

    @pytest.mark.asyncio
    async def test_close_unregisters_all(self) -> None:
        """close: unregisters all servers."""
        tools_a = [_make_tool("ta", "a")]
        tools_b = [_make_tool("tb", "b")]
        mock_client_a = _make_mock_client(tools=tools_a)
        mock_client_b = _make_mock_client(tools=tools_b)

        with patch(
            "aiops_agent.tools.mcp_registry.MCPClient",
            side_effect=[mock_client_a, mock_client_b],
        ):
            registry = MCPRegistry()
            await registry.register(_make_config("a"))
            await registry.register(_make_config("b"))

            assert len(registry.list_servers()) == 2

            await registry.close()

            assert registry.list_servers() == []
            mock_client_a.disconnect.assert_called_once()
            mock_client_b.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_empty_registry(self) -> None:
        """close: empty registry is no-op."""
        registry = MCPRegistry()
        await registry.close()  # Should not raise
        assert registry.list_servers() == []
