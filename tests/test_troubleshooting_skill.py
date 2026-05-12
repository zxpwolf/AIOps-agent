"""Tests for TroubleshootingSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.troubleshooting import TroubleshootingSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_tool_result(success: bool = True, output: dict | None = None) -> ToolResult:
    return ToolResult(
        tool_name="mock",
        success=success,
        output=output or {},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def skill() -> TroubleshootingSkill:
    return TroubleshootingSkill()


@pytest.fixture
def skill_with_executor(skill: TroubleshootingSkill) -> TroubleshootingSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_ecs_health_check_no_executor(self, skill: TroubleshootingSkill) -> None:
        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["action"] == "ecs_health_check"
        assert result["instance_id"] == "i-001"
        assert result["checks"] == []

    @pytest.mark.asyncio
    async def test_network_diagnosis_no_executor(self, skill: TroubleshootingSkill) -> None:
        result = await skill.execute({
            "action": "network_diagnosis",
            "source": "vpc-001",
            "target": "vpc-002",
        })
        assert result["status"] == "success"
        assert result["action"] == "network_diagnosis"
        assert result["source"] == "vpc-001"
        assert result["target"] == "vpc-002"
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_rds_slow_query_no_executor(self, skill: TroubleshootingSkill) -> None:
        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-001",
        })
        assert result["status"] == "success"
        assert result["action"] == "rds_slow_query"
        assert result["instance_id"] == "rm-001"
        assert result["slow_queries"] == []

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: TroubleshootingSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_ecs_health_check_success(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output={"status": "Running"}))

        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["instance_id"] == "i-001"
        # Two tool calls: describe_instance_status + describe_instances
        assert len(executor.execute.call_args_list) == 2
        # Both checks should be present
        assert len(result["checks"]) == 2
        check_names = [c["check"] for c in result["checks"]]
        assert "instance_status" in check_names
        assert "instance_detail" in check_names
        assert all(c["status"] == "ok" for c in result["checks"])

    @pytest.mark.asyncio
    async def test_ecs_health_check_partial_failure(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]

        call_count = 0

        async def mock_execute(**kwargs: Any) -> ToolResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_tool_result(success=True, output={"Running": True})
            return _mock_tool_result(success=False)

        executor.execute = AsyncMock(side_effect=mock_execute)

        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-001",
        })
        # Only the status check should pass
        assert len(result["checks"]) == 1
        assert result["checks"][0]["check"] == "instance_status"

    @pytest.mark.asyncio
    async def test_ecs_health_check_all_failure(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-001",
        })
        assert result["checks"] == []

    @pytest.mark.asyncio
    async def test_ecs_health_check_uses_correct_tools(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True))

        await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-001",
        })
        calls = executor.execute.call_args_list
        assert calls[0].kwargs["tool_name"] == "describe_instance_status"
        assert calls[1].kwargs["tool_name"] == "describe_instances"

    @pytest.mark.asyncio
    async def test_network_diagnosis_success(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output={"vpc": "vpc-001"}))

        result = await skill.execute({
            "action": "network_diagnosis",
            "source": "vpc-001",
            "target": "vpc-002",
        })
        assert result["status"] == "success"
        assert result["source"] == "vpc-001"
        assert result["target"] == "vpc-002"
        assert len(result["results"]) == 1
        assert result["results"][0]["check"] == "vpc_config"
        assert result["results"][0]["status"] == "ok"
        executor.execute.assert_called_once()
        assert executor.execute.call_args.kwargs["tool_name"] == "describe_vpcs"

    @pytest.mark.asyncio
    async def test_network_diagnosis_failure(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "network_diagnosis",
            "source": "vpc-001",
            "target": "vpc-002",
        })
        assert result["status"] == "success"
        # VPC check failed → no results
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_rds_slow_query_success(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        slow_log_data = {"items": [{"query": "SELECT * FROM large_table", "duration": 5000}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=slow_log_data))

        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-001",
        })
        assert result["status"] == "success"
        assert result["instance_id"] == "rm-001"
        assert len(result["slow_queries"]) == 1
        assert result["slow_queries"][0]["query"] == "SELECT * FROM large_table"
        executor.execute.assert_called_once()
        assert executor.execute.call_args.kwargs["tool_name"] == "describe_slowlog_records"

    @pytest.mark.asyncio
    async def test_rds_slow_query_failure(self, skill_with_executor: TroubleshootingSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-001",
        })
        assert result["status"] == "success"
        assert result["slow_queries"] == []

    @pytest.mark.asyncio
    async def test_rds_slow_query_missing_items_key(self, skill_with_executor: TroubleshootingSkill) -> None:
        """If tool executor succeeds but output lacks 'items' key, handle gracefully."""
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output={"other_key": []}))

        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-001",
        })
        assert result["slow_queries"] == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: TroubleshootingSkill) -> None:
        result = await skill.validate({"action": "ecs_health_check"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: TroubleshootingSkill) -> None:
        result = await skill.validate({"instance_id": "i-001"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: TroubleshootingSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: TroubleshootingSkill) -> None:
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: TroubleshootingSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "troubleshooting-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "troubleshooting-skill"
        assert identity.identity_provider == "ram"
        assert "ecs:DescribeInstances" in identity.permissions
        assert "rds:DescribeSlowLogs" in identity.permissions
