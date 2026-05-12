"""Tests for MonitoringSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.monitoring import MonitoringSkill


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
def skill() -> MonitoringSkill:
    return MonitoringSkill()


@pytest.fixture
def skill_with_executor(skill: MonitoringSkill) -> MonitoringSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_query_metrics_no_executor(self, skill: MonitoringSkill) -> None:
        result = await skill.execute({
            "action": "query_metrics",
            "namespace": "acs_ecs_dashboard",
            "metric_name": "CPUUtilization",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["action"] == "query_metrics"
        assert result["namespace"] == "acs_ecs_dashboard"
        assert result["metric_name"] == "CPUUtilization"
        assert result["instance_id"] == "i-001"
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_query_logs_no_executor(self, skill: MonitoringSkill) -> None:
        result = await skill.execute({
            "action": "query_logs",
            "project": "my-project",
            "logstore": "app-log",
            "query": "error",
        })
        assert result["status"] == "success"
        assert result["action"] == "query_logs"
        assert result["project"] == "my-project"
        assert result["logstore"] == "app-log"
        assert result["query"] == "error"
        assert result["logs"] == []

    @pytest.mark.asyncio
    async def test_analyze_metrics_no_executor(self, skill: MonitoringSkill) -> None:
        result = await skill.execute({"action": "analyze_metrics"})
        assert result["status"] == "success"
        assert result["action"] == "analyze_metrics"
        assert result["analysis"] == {}

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: MonitoringSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_query_metrics_success(self, skill_with_executor: MonitoringSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        metric_data = {"datapoints": [{"timestamp": "2024-01-01", "value": 45.2}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=metric_data))

        result = await skill.execute({
            "action": "query_metrics",
            "namespace": "acs_ecs_dashboard",
            "metric_name": "CPUUtilization",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["data"] == metric_data
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "query_metric_last"
        assert call_kwargs["arguments"]["namespace"] == "acs_ecs_dashboard"
        assert call_kwargs["arguments"]["metric_name"] == "CPUUtilization"

    @pytest.mark.asyncio
    async def test_query_metrics_failure(self, skill_with_executor: MonitoringSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "query_metrics",
            "namespace": "acs_ecs_dashboard",
            "metric_name": "CPUUtilization",
            "instance_id": "i-001",
        })
        assert result["status"] == "success"
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_query_logs_success(self, skill_with_executor: MonitoringSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        log_entries = {"logs": [{"message": "error in handler", "level": "ERROR"}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=log_entries))

        result = await skill.execute({
            "action": "query_logs",
            "project": "my-project",
            "logstore": "app-log",
            "query": "error",
        })
        assert result["status"] == "success"
        assert result["logs"] == log_entries
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "query_logs"

    @pytest.mark.asyncio
    async def test_query_logs_failure(self, skill_with_executor: MonitoringSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "query_logs",
            "project": "my-project",
            "logstore": "app-log",
            "query": "error",
        })
        assert result["status"] == "success"
        assert result["logs"] == []

    @pytest.mark.asyncio
    async def test_analyze_metrics_with_executor(self, skill_with_executor: MonitoringSkill) -> None:
        """analyze_metrics does not use tool executor."""
        skill = skill_with_executor
        result = await skill.execute({"action": "analyze_metrics"})
        assert result["status"] == "success"
        assert result["action"] == "analyze_metrics"
        assert result["analysis"] == {}


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: MonitoringSkill) -> None:
        result = await skill.validate({"action": "query_metrics"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: MonitoringSkill) -> None:
        result = await skill.validate({"namespace": "acs_ecs_dashboard"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: MonitoringSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: MonitoringSkill) -> None:
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: MonitoringSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "monitoring-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "monitoring-skill"
        assert identity.identity_provider == "ram"
        assert "cms:QueryMetricData" in identity.permissions
        assert "sls:GetLogs" in identity.permissions
