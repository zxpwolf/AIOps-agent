"""Tests for ChangeManagementSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.change_management import ChangeManagementSkill


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
def skill() -> ChangeManagementSkill:
    return ChangeManagementSkill()


@pytest.fixture
def skill_with_executor(skill: ChangeManagementSkill) -> ChangeManagementSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_risk_assessment_no_executor(self, skill: ChangeManagementSkill) -> None:
        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "ecs_scaling",
            "target_resources": [],
        })
        assert result["status"] == "success"
        assert result["action"] == "risk_assessment"
        assert result["change_type"] == "ecs_scaling"
        assert result["risk_level"] == "medium"
        assert result["recommendations"] == []

    @pytest.mark.asyncio
    async def test_rollback_plan_no_executor(self, skill: ChangeManagementSkill) -> None:
        result = await skill.execute({
            "action": "rollback_plan",
            "change_id": "CHG-001",
            "target_resources": [],
        })
        assert result["status"] == "success"
        assert result["action"] == "rollback_plan"
        assert result["change_id"] == "CHG-001"
        assert result["steps"] == []

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: ChangeManagementSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_risk_assessment_all_ok(self, skill_with_executor: ChangeManagementSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output={"status": "Running"}))

        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "ecs_restart",
            "target_resources": [
                {"type": "ecs", "id": "i-001"},
                {"type": "rds", "id": "rm-001"},
            ],
        })
        assert result["status"] == "success"
        assert result["risk_level"] == "low"
        assert len(result["resource_details"]) == 2
        assert all(d["status"] == "ok" for d in result["resource_details"])
        # Recommendations for low risk
        assert any("风险较低" in r for r in result["recommendations"])

    @pytest.mark.asyncio
    async def test_risk_assessment_partial_errors(self, skill_with_executor: ChangeManagementSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]

        call_count = 0

        async def mock_execute(**kwargs: Any) -> ToolResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_tool_result(success=False)
            return _mock_tool_result(success=True, output={"ok": True})

        executor.execute = AsyncMock(side_effect=mock_execute)

        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "vpc_change",
            "target_resources": [
                {"type": "vpc", "id": "vpc-001"},
                {"type": "vpc", "id": "vpc-002"},
                {"type": "ecs", "id": "i-001"},
            ],
        })
        # 1 error out of 3 → medium
        assert result["risk_level"] == "medium"

    @pytest.mark.asyncio
    async def test_risk_assessment_most_errors(self, skill_with_executor: ChangeManagementSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "ecs",
            "target_resources": [
                {"type": "ecs", "id": "i-001"},
                {"type": "ecs", "id": "i-002"},
            ],
        })
        assert result["risk_level"] == "high"
        # High risk recommendations
        recs = result["recommendations"]
        assert any("低峰期" in r for r in recs)
        assert any("回滚方案" in r for r in recs)

    @pytest.mark.asyncio
    async def test_risk_assessment_unknown_resource_type(self, skill_with_executor: ChangeManagementSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True))

        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "other",
            "target_resources": [{"type": "slb", "id": "lb-001"}],
        })
        # Unknown type → result is None → status error → high risk
        assert result["risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_rollback_plan_with_executor(self, skill_with_executor: ChangeManagementSkill) -> None:
        skill = skill_with_executor
        result = await skill.execute({
            "action": "rollback_plan",
            "change_id": "CHG-042",
            "target_resources": [
                {"type": "ecs", "id": "i-001"},
                {"type": "rds", "id": "rm-001"},
            ],
        })
        assert result["status"] == "success"
        assert result["action"] == "rollback_plan"
        assert result["change_id"] == "CHG-042"
        # 2 resource steps + 1 global verification
        assert len(result["steps"]) == 3
        assert result["steps"][0]["step"] == 1
        assert "ecs" in result["steps"][0]["action"]
        assert result["steps"][-1]["action"] == "全局验证"

    @pytest.mark.asyncio
    async def test_execute_tool_executor_calls_correct_tools(self, skill_with_executor: ChangeManagementSkill) -> None:
        """Verify the right tool names are called for each resource type."""
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True))

        await skill.execute({
            "action": "risk_assessment",
            "change_type": "mixed",
            "target_resources": [
                {"type": "ecs", "id": "i-001"},
                {"type": "rds", "id": "rm-001"},
                {"type": "vpc", "id": "vpc-001"},
            ],
        })
        calls = executor.execute.call_args_list
        assert len(calls) == 3
        assert calls[0].kwargs["tool_name"] == "describe_instances"
        assert calls[1].kwargs["tool_name"] == "describe_dbinstances"
        assert calls[2].kwargs["tool_name"] == "describe_vpcs"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: ChangeManagementSkill) -> None:
        result = await skill.validate({"action": "risk_assessment"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: ChangeManagementSkill) -> None:
        result = await skill.validate({"change_type": "ecs"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: ChangeManagementSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: ChangeManagementSkill) -> None:
        # Base class health_check always returns True
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: ChangeManagementSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "change-management-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "change-management-skill"
        assert identity.identity_provider == "ram"
        assert "ecs:DescribeInstances" in identity.permissions
