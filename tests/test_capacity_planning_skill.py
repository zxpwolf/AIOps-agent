"""Tests for CapacityPlanningSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.capacity_planning import CapacityPlanningSkill


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
def skill() -> CapacityPlanningSkill:
    return CapacityPlanningSkill()


@pytest.fixture
def skill_with_executor(skill: CapacityPlanningSkill) -> CapacityPlanningSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_forecast_capacity_no_executor(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.execute({
            "action": "forecast_capacity",
            "resource_type": "ecs",
            "instance_id": "i-001",
            "forecast_days": 14,
        })
        assert result["status"] == "success"
        assert result["action"] == "forecast_capacity"
        assert result["resource_type"] == "ecs"
        assert result["instance_id"] == "i-001"
        assert result["forecast_days"] == 14
        assert result["forecast"] == []

    @pytest.mark.asyncio
    async def test_analyze_utilization_no_executor(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.execute({
            "action": "analyze_utilization",
            "resource_type": "ecs",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["action"] == "analyze_utilization"
        assert result["resource_type"] == "ecs"
        assert result["instance_id"] == "i-001"
        assert result["utilization"] == {}

    @pytest.mark.asyncio
    async def test_recommend_scaling_no_executor(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.execute({
            "action": "recommend_scaling",
            "resource_type": "ecs",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["action"] == "recommend_scaling"
        assert result["resource_type"] == "ecs"
        assert result["instance_id"] == "i-001"
        assert result["recommendations"] == []

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_forecast_capacity_success(self, skill_with_executor: CapacityPlanningSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        forecast_data = {"trend": "up", "predicted_peak": 85.5}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=forecast_data))

        result = await skill.execute({
            "action": "forecast_capacity",
            "resource_type": "ecs",
            "instance_id": "i-001",
            "forecast_days": 14,
        })
        assert result["status"] == "success"
        assert result["forecast"] == forecast_data
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "query_metric_data"
        assert call_kwargs["arguments"]["instance_id"] == "i-001"

    @pytest.mark.asyncio
    async def test_forecast_capacity_failure(self, skill_with_executor: CapacityPlanningSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "forecast_capacity",
            "resource_type": "ecs",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["forecast"] == []

    @pytest.mark.asyncio
    async def test_analyze_utilization_success(self, skill_with_executor: CapacityPlanningSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        util_data = {"cpu_avg": 45.2, "memory_avg": 62.1}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=util_data))

        result = await skill.execute({
            "action": "analyze_utilization",
            "resource_type": "ecs",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["utilization"] == util_data
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_recommend_scaling_success(self, skill_with_executor: CapacityPlanningSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        recs = {"recommendations": [{"action": "scale_out", "count": 2}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=recs))

        result = await skill.execute({
            "action": "recommend_scaling",
            "resource_type": "ecs",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["recommendations"] == recs
        executor.execute.assert_called_once()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.validate({"action": "forecast_capacity"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.validate({"resource_type": "ecs"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: CapacityPlanningSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: CapacityPlanningSkill) -> None:
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: CapacityPlanningSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "capacity-planning-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "capacity-planning-skill"
        assert identity.identity_provider == "ram"
        assert "cms:QueryMetricData" in identity.permissions
        assert "ess:DescribeScalingGroups" in identity.permissions
