"""PermissionGate 单元测试."""

from __future__ import annotations

import pytest

from aiops_agent.core.exceptions import PermissionDeniedError
from aiops_agent.models.schemas import (
    PermissionCheckResult,
    PermissionLevel,
    WorkloadIdentity,
)
from aiops_agent.security.permission_gate import PermissionGate, _classify_permission_level


# ---------------------------------------------------------------------------
# Test: Permission level classification
# ---------------------------------------------------------------------------


class TestPermissionLevelClassification:
    def test_read_only_action(self) -> None:
        level = _classify_permission_level("ecs:DescribeInstances")
        assert level == PermissionLevel.READ_ONLY

    def test_write_action(self) -> None:
        level = _classify_permission_level("ecs:CreateInstance")
        assert level == PermissionLevel.LIMITED_WRITE

    def test_admin_action(self) -> None:
        level = _classify_permission_level("ecs:DeleteInstance")
        assert level == PermissionLevel.ADMIN

    def test_modify_action(self) -> None:
        level = _classify_permission_level("rds:ModifyDBInstanceAttribute")
        assert level == PermissionLevel.LIMITED_WRITE

    def test_start_action(self) -> None:
        level = _classify_permission_level("ecs:StartInstance")
        assert level == PermissionLevel.LIMITED_WRITE

    def test_stop_action(self) -> None:
        level = _classify_permission_level("ecs:StopInstance")
        assert level == PermissionLevel.LIMITED_WRITE


# ---------------------------------------------------------------------------
# Test: Permission matching
# ---------------------------------------------------------------------------


class TestPermissionMatching:
    def test_exact_match(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._is_action_allowed(
            "ecs:DescribeInstances",
            ["ecs:DescribeInstances"],
            "*",
        ) is True

    def test_wildcard_match(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._is_action_allowed(
            "ecs:DescribeInstances",
            ["ecs:Describe*"],
            "*",
        ) is True

    def test_full_wildcard(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._is_action_allowed(
            "ecs:DeleteInstance",
            ["*"],
            "*",
        ) is True

    def test_no_match(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._is_action_allowed(
            "ecs:DeleteInstance",
            ["ecs:Describe*"],
            "*",
        ) is False

    def test_resource_arn_match(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._match_resource_arn(
            "acs:ecs:cn-hangzhou:*:instance/*",
            "acs:ecs:cn-hangzhou:123:instance/i-abc123",
        ) is True

    def test_resource_arn_no_match(self, permission_gate: PermissionGate) -> None:
        assert permission_gate._match_resource_arn(
            "acs:ecs:cn-beijing:*:instance/*",
            "acs:ecs:cn-hangzhou:123:instance/i-abc123",
        ) is False


# ---------------------------------------------------------------------------
# Test: check_permission
# ---------------------------------------------------------------------------


class TestCheckPermission:
    @pytest.mark.asyncio
    async def test_readonly_allowed(self, permission_gate: PermissionGate) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Describe*"],
        )

        result = await permission_gate.check_permission(
            identity, "ecs:DescribeInstances", "acs:ecs:cn-hangzhou:*:instance/*"
        )

        assert result.allowed is True
        assert result.permission_level == PermissionLevel.READ_ONLY

    @pytest.mark.asyncio
    async def test_write_requires_approval(self, permission_gate: PermissionGate) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Create*"],
        )

        result = await permission_gate.check_permission(
            identity, "ecs:CreateInstance", "acs:ecs:cn-hangzhou:*:instance/*"
        )

        # 没有审批回调 → 默认拒绝
        assert result.allowed is False
        assert "审批" in (result.denial_reason or "")

    @pytest.mark.asyncio
    async def test_permission_denied(self, permission_gate: PermissionGate) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Describe*"],
        )

        result = await permission_gate.check_permission(
            identity, "ecs:DeleteInstance", "acs:ecs:cn-hangzhou:*:instance/*"
        )

        assert result.allowed is False
        assert result.denial_reason is not None

    @pytest.mark.asyncio
    async def test_on_behalf_of_intersection(self, permission_gate: PermissionGate) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Describe*", "ecs:Create*", "ecs:Delete*"],
        )

        user_perms = ["ecs:Describe*", "ecs:Create*"]

        result = await permission_gate.check_permission(
            identity,
            "ecs:DescribeInstances",
            "acs:ecs:cn-hangzhou:*:instance/*",
            user_permissions=user_perms,
        )

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_on_behalf_of_denied_when_user_lacks_perm(self, permission_gate: PermissionGate) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Describe*", "ecs:Delete*"],
        )

        user_perms = ["ecs:Describe*"]  # 用户没有 Delete

        result = await permission_gate.check_permission(
            identity,
            "ecs:DeleteInstance",
            "acs:ecs:cn-hangzhou:*:instance/*",
            user_permissions=user_perms,
        )

        assert result.allowed is False


# ---------------------------------------------------------------------------
# Test: Approval callback
# ---------------------------------------------------------------------------


class TestApprovalCallback:
    @pytest.mark.asyncio
    async def test_approval_granted(self, permission_gate: PermissionGate) -> None:
        async def approve_cb(identity, action, resource, level):
            return True

        permission_gate.set_approval_callback(approve_cb)

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Create*"],
        )

        result = await permission_gate.check_permission(
            identity, "ecs:CreateInstance", "acs:ecs:cn-hangzhou:*:instance/*"
        )

        assert result.allowed is True
        assert result.requires_approval is True

    @pytest.mark.asyncio
    async def test_approval_denied(self, permission_gate: PermissionGate) -> None:
        async def deny_cb(identity, action, resource, level):
            return False

        permission_gate.set_approval_callback(deny_cb)

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
            permissions=["ecs:Create*"],
        )

        result = await permission_gate.check_permission(
            identity, "ecs:CreateInstance", "acs:ecs:cn-hangzhou:*:instance/*"
        )

        assert result.allowed is False
