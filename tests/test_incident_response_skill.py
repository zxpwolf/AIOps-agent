"""Tests for IncidentResponseSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.incident_response import IncidentResponseSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_tool_result(success: bool = True, output: Any = None) -> ToolResult:
    return ToolResult(
        tool_name="mock",
        success=success,
        output=output,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def skill() -> IncidentResponseSkill:
    return IncidentResponseSkill()


@pytest.fixture
def skill_with_executor(skill: IncidentResponseSkill) -> IncidentResponseSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_acknowledge_incident_no_executor(self, skill: IncidentResponseSkill) -> None:
        result = await skill.execute({
            "action": "acknowledge_incident",
            "incident_id": "INC-001",
            "responder": "on-call",
        })
        assert result["status"] == "success"
        assert result["action"] == "acknowledge_incident"
        assert result["incident_id"] == "INC-001"
        assert result["responder"] == "on-call"
        assert result["acknowledged_at"] is None

    @pytest.mark.asyncio
    async def test_run_playbook_no_executor(self, skill: IncidentResponseSkill) -> None:
        result = await skill.execute({
            "action": "run_playbook",
            "incident_id": "INC-001",
            "playbook_name": "restart-service",
        })
        assert result["status"] == "success"
        assert result["action"] == "run_playbook"
        assert result["incident_id"] == "INC-001"
        assert result["playbook_name"] == "restart-service"
        assert result["steps"] == []

    @pytest.mark.asyncio
    async def test_escalate_no_executor(self, skill: IncidentResponseSkill) -> None:
        result = await skill.execute({
            "action": "escalate",
            "incident_id": "INC-001",
            "escalation_level": "L3",
            "reason": "needs expert",
        })
        assert result["status"] == "success"
        assert result["action"] == "escalate"
        assert result["incident_id"] == "INC-001"
        assert result["escalation_level"] == "L3"
        assert result["reason"] == "needs expert"
        assert result["escalated_at"] is None

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: IncidentResponseSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_acknowledge_incident_success(self, skill_with_executor: IncidentResponseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        ack_time = {"timestamp": "2026-06-15T10:00:00Z"}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=ack_time))

        result = await skill.execute({
            "action": "acknowledge_incident",
            "incident_id": "INC-001",
            "responder": "on-call",
        })
        assert result["status"] == "success"
        assert result["acknowledged_at"] == ack_time
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "publish_message"
        assert call_kwargs["arguments"]["topic"] == "incident-updates"

    @pytest.mark.asyncio
    async def test_run_playbook_success(self, skill_with_executor: IncidentResponseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        steps = {"steps": [{"step": 1, "action": "restart", "status": "done"}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=steps))

        result = await skill.execute({
            "action": "run_playbook",
            "incident_id": "INC-001",
            "playbook_name": "restart-service",
        })
        assert result["status"] == "success"
        assert result["steps"] == steps
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalate_success(self, skill_with_executor: IncidentResponseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        esc_time = {"timestamp": "2026-06-15T10:05:00Z"}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=esc_time))

        result = await skill.execute({
            "action": "escalate",
            "incident_id": "INC-001",
            "escalation_level": "L3",
            "reason": "needs expert",
        })
        assert result["status"] == "success"
        assert result["escalated_at"] == esc_time
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["arguments"]["topic"] == "escalations"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: IncidentResponseSkill) -> None:
        result = await skill.validate({"action": "acknowledge_incident"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: IncidentResponseSkill) -> None:
        result = await skill.validate({"incident_id": "INC-001"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: IncidentResponseSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: IncidentResponseSkill) -> None:
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: IncidentResponseSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "incident-response-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "incident-response-skill"
        assert identity.identity_provider == "ram"
        assert "sls:GetLogs" in identity.permissions
        assert "mns:PublishMessage" in identity.permissions
