"""pytest 共享 fixtures — mock 服务和测试基础设施."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import settings as hypothesis_settings

from aiops_agent.context.manager import ContextManager
from aiops_agent.context.memory import MemoryLayer
from aiops_agent.context.resource_resolver import ResourceResolver
from aiops_agent.context.session import SessionStore
from aiops_agent.core.orchestrator import AgentOrchestrator
from aiops_agent.llm.provider import ChatResponse, LLMProvider, LLMProviderFactory
from aiops_agent.models.schemas import (
    Message,
    PermissionCheckResult,
    PermissionLevel,
    SkillDefinition,
    WorkloadIdentity,
)
from aiops_agent.security.audit_logger import AuditLogger
from aiops_agent.security.credential_manager import CredentialManager
from aiops_agent.security.identity import WorkloadIdentityManager
from aiops_agent.security.permission_gate import PermissionGate
from aiops_agent.security.security_guard import SecurityGuard
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.registry import SkillRegistry
from aiops_agent.tools.executor import ToolExecutor
from aiops_agent.tools.mcp_registry import MCPRegistry

# ---------------------------------------------------------------------------
# Hypothesis 配置
# ---------------------------------------------------------------------------

hypothesis_settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
)
hypothesis_settings.register_profile(
    "dev",
    max_examples=50,
    deadline=None,
)
hypothesis_settings.load_profile("dev")


# ---------------------------------------------------------------------------
# Mock LLM Provider
# ---------------------------------------------------------------------------


class MockLLMProvider(LLMProvider):
    """Mock LLM Provider for testing."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or ["Mock LLM response"]
        self._call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return ChatResponse(
            content=self._responses[idx],
            model="mock-model",
            usage={"input_tokens": 10, "output_tokens": 20},
        )

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        response = await self.chat([Message(role="user", content=prompt)])
        return response.content

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


# ---------------------------------------------------------------------------
# Mock Skill
# ---------------------------------------------------------------------------


class MockSkillInstance(SkillInstance):
    """Mock Skill for testing."""

    def __init__(self, result: dict | None = None, should_fail: bool = False) -> None:
        super().__init__()
        self._result = result or {"status": "success"}
        self._should_fail = should_fail

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        if self._should_fail:
            raise RuntimeError("Mock skill execution failed")
        return self._result

    async def validate(self, input_data: dict[str, Any]) -> Any:
        from aiops_agent.models.schemas import ValidationResult
        return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_provider() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
def mock_llm_factory(mock_llm_provider: MockLLMProvider) -> LLMProviderFactory:
    factory = LLMProviderFactory()
    factory.register("mock", mock_llm_provider)
    factory.set_primary("mock")
    return factory


@pytest.fixture
def mock_workload_identity() -> WorkloadIdentity:
    return WorkloadIdentity(
        workload_identity_arn="acs:ram::123456:role/aiops-agent-role",
        agent_instance_id="test-agent-001",
        identity_provider="ram",
        permissions=["ecs:Describe*", "cms:Query*", "sls:GetLogs"],
    )


@pytest.fixture
def permission_gate(tmp_path) -> PermissionGate:
    return PermissionGate(ram_policies_dir=str(tmp_path))


@pytest.fixture
def audit_logger(tmp_path) -> AuditLogger:
    return AuditLogger(
        local_log_dir=str(tmp_path / "audit"),
        backup_log_dir=str(tmp_path / "backup"),
    )


@pytest.fixture
def credential_manager() -> CredentialManager:
    """CredentialManager 不再需要 endpoint/ARN，简化初始化."""
    return CredentialManager()


@pytest.fixture
def workload_identity_manager() -> WorkloadIdentityManager:
    """WorkloadIdentityManager fixture（assume_role 需在测试中 mock）."""
    return WorkloadIdentityManager(
        role_arn="acs:ram::123456:role/aiops-agent-role",
        oidc_provider_arn="acs:ram::123456:oidc-provider/aiops-provider",
        region="cn-hangzhou",
    )


@pytest.fixture
def security_guard() -> SecurityGuard:
    return SecurityGuard()


@pytest.fixture
def skill_registry() -> SkillRegistry:
    return SkillRegistry()


@pytest.fixture
def mcp_registry() -> MCPRegistry:
    return MCPRegistry()


@pytest.fixture
def tool_executor(
    credential_manager: CredentialManager,
    permission_gate: PermissionGate,
    audit_logger: AuditLogger,
    mcp_registry: MCPRegistry,
    workload_identity_manager: WorkloadIdentityManager,
) -> ToolExecutor:
    return ToolExecutor(
        credential_manager=credential_manager,
        permission_gate=permission_gate,
        audit_logger=audit_logger,
        mcp_registry=mcp_registry,
        workload_identity_manager=workload_identity_manager,
    )


@pytest.fixture
def context_manager(tmp_path) -> ContextManager:
    return ContextManager(
        session_store=SessionStore(persist_dir=str(tmp_path / "sessions")),
        memory_layer=MemoryLayer(long_term_dir=str(tmp_path / "memory")),
        resource_resolver=ResourceResolver(),
    )


@pytest.fixture
def mock_skill() -> MockSkillInstance:
    return MockSkillInstance()


@pytest.fixture
def mock_failing_skill() -> MockSkillInstance:
    return MockSkillInstance(should_fail=True)
