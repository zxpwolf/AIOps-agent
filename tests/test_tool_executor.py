"""ToolExecutor 单元测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.core.exceptions import PermissionDeniedError
from aiops_agent.models.schemas import (
    CredentialScope,
    ToolResult,
    WorkloadIdentity,
)
from aiops_agent.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_credential_manager() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_permission_gate() -> AsyncMock:
    gate = AsyncMock()
    gate.check_permission.return_value = MagicMock(
        allowed=True,
        permission_level=MagicMock(value="read_only"),
        required_permission="ecs:DescribeInstances",
        current_permissions=["ecs:Describe*"],
    )
    return gate


@pytest.fixture
def mock_audit_logger() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_mcp_registry() -> MagicMock:
    return MagicMock()


@pytest.fixture
def executor(
    mock_credential_manager, mock_permission_gate, mock_audit_logger, mock_mcp_registry
) -> ToolExecutor:
    return ToolExecutor(
        credential_manager=mock_credential_manager,
        permission_gate=mock_permission_gate,
        audit_logger=mock_audit_logger,
        mcp_registry=mock_mcp_registry,
    )


@pytest.fixture
def workload_identity() -> WorkloadIdentity:
    return WorkloadIdentity(
        workload_identity_arn="acs:agent-identity::123456:workload-identity/test",
        agent_instance_id="test-001",
        identity_provider="ram",
        permissions=["ecs:Describe*"],
    )


# ---------------------------------------------------------------------------
# Test: execute — permission denied
# ---------------------------------------------------------------------------


class TestExecutePermissionDenied:
    @pytest.mark.asyncio
    async def test_permission_denied(self, executor, mock_permission_gate, workload_identity) -> None:
        mock_permission_gate.check_permission.return_value = MagicMock(
            allowed=False,
            permission_level=MagicMock(value="read_only"),
            required_permission="ecs:DeleteInstance",
            current_permissions=["ecs:Describe*"],
            denial_reason="权限不足",
        )

        result = await executor.execute(
            tool_name="delete_instance",
            arguments={"instance_id": "i-xxx"},
            skill_identity=workload_identity,
        )

        assert result.success is False
        assert "权限" in (result.error or "")


# ---------------------------------------------------------------------------
# Test: execute — MCP tool call
# ---------------------------------------------------------------------------


class TestExecuteMcpTool:
    @pytest.mark.asyncio
    async def test_mcp_tool_success(
        self, executor, mock_mcp_registry, mock_permission_gate, workload_identity
    ) -> None:
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"result": "ok"}
        mock_mcp_registry.get_client_for_tool.return_value = mock_client

        result = await executor.execute(
            tool_name="describe_instances",
            arguments={},
            skill_identity=workload_identity,
        )

        assert result.success is True
        assert result.tool_name == "describe_instances"
        mock_client.call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_mcp_tool_failure(
        self, executor, mock_mcp_registry, mock_permission_gate, workload_identity
    ) -> None:
        mock_client = AsyncMock()
        mock_client.call_tool.side_effect = RuntimeError("API error")
        mock_mcp_registry.get_client_for_tool.return_value = mock_client

        result = await executor.execute(
            tool_name="broken_tool",
            arguments={},
            skill_identity=workload_identity,
        )

        assert result.success is False
        assert "API error" in (result.error or "")


# ---------------------------------------------------------------------------
# Test: execute — local tool call
# ---------------------------------------------------------------------------


class TestExecuteLocalTool:
    @pytest.mark.asyncio
    async def test_local_tool_success(self, executor, mock_permission_gate, workload_identity) -> None:
        from aiops_agent.tools.local_tools import LocalToolRegistry, LocalToolRegistry

        local_tools = LocalToolRegistry()
        local_tools.register(
            "greet",
            "Greet someone",
            lambda name: f"Hello, {name}!",
        )
        executor._local_tools = local_tools
        executor._mcp_registry = MagicMock()
        executor._mcp_registry.get_client_for_tool.return_value = None

        result = await executor.execute(
            tool_name="greet",
            arguments={"name": "World"},
            skill_identity=workload_identity,
        )

        assert result.success is True
        assert "Hello" in str(result.output)

    @pytest.mark.asyncio
    async def test_local_tool_not_found(
        self, executor, mock_permission_gate, workload_identity
    ) -> None:
        executor._local_tools = MagicMock()
        executor._local_tools.has_tool.return_value = False
        executor._mcp_registry.get_client_for_tool.return_value = None

        result = await executor.execute(
            tool_name="unknown_tool",
            arguments={},
            skill_identity=workload_identity,
        )

        assert result.success is False
        assert "未注册" in (result.error or "")


# ---------------------------------------------------------------------------
# Test: execute — audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_audit_logged_on_success(
        self, executor, mock_audit_logger, mock_mcp_registry, mock_permission_gate, workload_identity
    ) -> None:
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {"ok": True}
        mock_mcp_registry.get_client_for_tool.return_value = mock_client

        await executor.execute(
            tool_name="test_tool",
            arguments={"key": "value"},
            skill_identity=workload_identity,
            resource_arn="acs:ecs:cn-hangzhou:*:instance/i-xxx",
        )

        mock_audit_logger.log.assert_called_once()
        event = mock_audit_logger.log.call_args[0][0]
        assert event.action == "tool:test_tool"
        assert "key" in event.parameters


# ---------------------------------------------------------------------------
# Test: execute — credential injection
# ---------------------------------------------------------------------------


class TestCredentialInjection:
    @pytest.mark.asyncio
    async def test_aliyun_credential_injected(
        self, executor, mock_credential_manager, mock_mcp_registry, mock_permission_gate, workload_identity
    ) -> None:
        """验证凭证被获取但不在传给工具时暴露（工具层看不到 _credential）."""
        from aiops_agent.models.schemas import AliyunCredential, CredentialScope
        from datetime import datetime, timezone, timedelta

        mock_credential = AliyunCredential(
            access_key_id="AK",
            access_key_secret="SK",
            security_token="ST",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        mock_credential_manager.get_aliyun_credential.return_value = mock_credential

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {}
        mock_mcp_registry.get_client_for_tool.return_value = mock_client

        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="test",
            ram_role_arn="acs:ram::123:role/test",
        )

        await executor.execute(
            tool_name="test",
            arguments={"foo": "bar"},
            skill_identity=workload_identity,
            credential_scope=scope,
        )

        # 1. 凭证管理器被调用（含 workload_identity_manager 参数）
        mock_credential_manager.get_aliyun_credential.assert_called_once()
        call = mock_credential_manager.get_aliyun_credential.call_args
        assert call[0][0] == scope  # first positional arg is scope
        assert "workload_identity_manager" in call[1]

        # 2. MCP 工具被调用，但 _credential 已被清理（不传给工具）
        call_kwargs = mock_client.call_tool.call_args
        assert call_kwargs is not None
        call_args = call_kwargs[0][1]
        assert "_credential" not in call_args  # 工具不应看到凭证
        assert "foo" in call_args  # 正常参数应该保留


# ---------------------------------------------------------------------------
# Test: retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_on_connection_error(
        self, executor, mock_mcp_registry, mock_permission_gate, workload_identity
    ) -> None:
        mock_client = AsyncMock()
        mock_client.call_tool.side_effect = [
            ConnectionError("network error"),
            ConnectionError("network error"),
            {"success": True},
        ]
        mock_mcp_registry.get_client_for_tool.return_value = mock_client

        result = await executor.execute(
            tool_name="flaky_tool",
            arguments={},
            skill_identity=workload_identity,
            timeout_seconds=1.0,
        )

        assert result.success is True
        assert mock_client.call_tool.call_count == 3
