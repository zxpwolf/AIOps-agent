"""Tests for KnowledgeBaseSkill."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.knowledge_base import KnowledgeBaseSkill


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
def skill() -> KnowledgeBaseSkill:
    return KnowledgeBaseSkill()


@pytest.fixture
def skill_with_executor(skill: KnowledgeBaseSkill) -> KnowledgeBaseSkill:
    executor = AsyncMock()
    skill.set_tool_executor(executor)
    return skill


# ---------------------------------------------------------------------------
# execute — no tool executor
# ---------------------------------------------------------------------------

class TestExecuteNoExecutor:
    """Test execute() when _tool_executor is None."""

    @pytest.mark.asyncio
    async def test_search_knowledge_no_executor(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.execute({
            "action": "search_knowledge",
            "query": "ECS high CPU",
            "category": "troubleshooting",
        })
        assert result["status"] == "success"
        assert result["action"] == "search_knowledge"
        assert result["query"] == "ECS high CPU"
        assert result["category"] == "troubleshooting"
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_match_case_no_executor(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.execute({
            "action": "match_case",
            "symptoms": "slow response",
            "service": "payment",
        })
        assert result["status"] == "success"
        assert result["action"] == "match_case"
        assert result["symptoms"] == "slow response"
        assert result["service"] == "payment"
        assert result["matches"] == []

    @pytest.mark.asyncio
    async def test_suggest_solution_no_executor(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.execute({
            "action": "suggest_solution",
            "issue": "OOM kill",
            "context": "Java app",
        })
        assert result["status"] == "success"
        assert result["action"] == "suggest_solution"
        assert result["issue"] == "OOM kill"
        assert result["context"] == "Java app"
        assert result["suggestions"] == []

    @pytest.mark.asyncio
    async def test_unknown_action(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.execute({"action": "unknown"})
        assert "error" in result
        assert "unknown" in result["error"]


# ---------------------------------------------------------------------------
# execute — with tool executor
# ---------------------------------------------------------------------------

class TestExecuteWithExecutor:
    """Test execute() with a mocked ToolExecutor."""

    @pytest.mark.asyncio
    async def test_search_knowledge_success(self, skill_with_executor: KnowledgeBaseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        kb_results = {"results": [{"title": "CPU 高处理", "relevance": 0.95}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=kb_results))

        result = await skill.execute({
            "action": "search_knowledge",
            "query": "ECS high CPU",
            "category": "troubleshooting",
        })
        assert result["status"] == "success"
        assert result["results"] == kb_results
        executor.execute.assert_called_once()
        call_kwargs = executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "search_knowledge"

    @pytest.mark.asyncio
    async def test_match_case_success(self, skill_with_executor: KnowledgeBaseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        matches = {"matches": [{"case_id": "CASE-42", "similarity": 0.88}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=matches))

        result = await skill.execute({
            "action": "match_case",
            "symptoms": "slow response",
            "service": "payment",
        })
        assert result["status"] == "success"
        assert result["matches"] == matches
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_suggest_solution_success(self, skill_with_executor: KnowledgeBaseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        suggestions = {"suggestions": [{"solution": "increase heap size", "confidence": 0.9}]}
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=True, output=suggestions))

        result = await skill.execute({
            "action": "suggest_solution",
            "issue": "OOM kill",
            "context": "Java app",
        })
        assert result["status"] == "success"
        assert result["suggestions"] == suggestions
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_failure_returns_empty(self, skill_with_executor: KnowledgeBaseSkill) -> None:
        skill = skill_with_executor
        executor = skill._tool_executor  # type: ignore[attr-defined]
        executor.execute = AsyncMock(return_value=_mock_tool_result(success=False))

        result = await skill.execute({
            "action": "search_knowledge",
            "query": "test",
        })
        assert result["status"] == "success"
        assert result["results"] == []


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate:
    """Test validate() method."""

    @pytest.mark.asyncio
    async def test_valid_input(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.validate({"action": "search_knowledge"})
        assert result.valid is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_missing_action(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.validate({"query": "test"})
        assert result.valid is False
        assert len(result.errors) == 1
        assert "action" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_input(self, skill: KnowledgeBaseSkill) -> None:
        result = await skill.validate({})
        assert result.valid is False


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """Test health_check() method."""

    @pytest.mark.asyncio
    async def test_healthy(self, skill: KnowledgeBaseSkill) -> None:
        assert await skill.health_check() is True


# ---------------------------------------------------------------------------
# _get_identity
# ---------------------------------------------------------------------------

class TestGetIdentity:
    """Test _get_identity() returns correct WorkloadIdentity."""

    def test_identity_fields(self, skill: KnowledgeBaseSkill) -> None:
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "knowledge-base-skill" in identity.workload_identity_arn
        assert identity.agent_instance_id == "knowledge-base-skill"
        assert identity.identity_provider == "ram"
        assert "sls:GetLogs" in identity.permissions
        assert "kms:Decrypt" in identity.permissions
