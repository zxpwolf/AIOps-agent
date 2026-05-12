"""属性测试 — 模型序列化/反序列化."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import hypothesis.strategies as st
from hypothesis import given, settings

from aiops_agent.models.schemas import (
    AgentResponse,
    AuditEvent,
    CachedCredential,
    CredentialScope,
    Message,
    ResourceReference,
    SessionState,
    SkillDefinition,
    SubTask,
    TaskPlan,
    TaskProgress,
    TaskStatus,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def utc_datetimes(draw: st.DrawFn) -> datetime:
    """生成 UTC datetime."""
    offset = draw(st.integers(min_value=-86400 * 365, max_value=86400 * 365))
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset)


# ---------------------------------------------------------------------------
# Property: Round-trip serialization
# ---------------------------------------------------------------------------


class TestRoundTripSerialization:
    @given(
        success=st.booleans(),
        message=st.text(min_size=1, max_size=100),
        error_code=st.text(min_size=1, max_size=50) | st.none(),
        suggestion=st.text(min_size=1, max_size=100) | st.none(),
    )
    @settings(max_examples=30)
    def test_agent_response_roundtrip(
        self, success: bool, message: str, error_code: str | None, suggestion: str | None
    ) -> None:
        original = AgentResponse(
            success=success,
            message=message,
            error_code=error_code,
            suggestion=suggestion,
        )
        data = original.model_dump(mode="json")
        restored = AgentResponse.model_validate(data)
        assert restored.success == original.success
        assert restored.message == original.message
        assert restored.error_code == original.error_code

    @given(
        skill_name=st.text(min_size=1, max_size=50),
        description=st.text(min_size=1, max_size=200),
        version=st.text(min_size=1, max_size=20),
    )
    @settings(max_examples=30)
    def test_skill_definition_roundtrip(
        self, skill_name: str, description: str, version: str
    ) -> None:
        original = SkillDefinition(
            skill_name=skill_name,
            description=description,
            version=version,
        )
        data = original.model_dump(mode="json")
        restored = SkillDefinition.model_validate(data)
        assert restored.skill_name == original.skill_name
        assert restored.description == original.description
        assert restored.version == original.version

    @given(
        role=st.sampled_from(["user", "assistant", "system"]),
        content=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=30)
    def test_message_roundtrip(self, role: str, content: str) -> None:
        original = Message(role=role, content=content)
        data = original.model_dump(mode="json")
        restored = Message.model_validate(data)
        assert restored.role == original.role
        assert restored.content == original.content

    @given(
        tool_name=st.text(min_size=1, max_size=50),
        success=st.booleans(),
        execution_time_ms=st.floats(min_value=0, max_value=60000),
    )
    @settings(max_examples=30)
    def test_tool_result_roundtrip(
        self, tool_name: str, success: bool, execution_time_ms: float
    ) -> None:
        original = ToolResult(
            tool_name=tool_name,
            success=success,
            execution_time_ms=execution_time_ms,
        )
        data = original.model_dump(mode="json")
        restored = ToolResult.model_validate(data)
        assert restored.tool_name == original.tool_name
        assert restored.success == original.success

    @given(
        task_id=st.text(min_size=1, max_size=50),
        skill_name=st.text(min_size=1, max_size=50),
        action=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=30)
    def test_subtask_roundtrip(
        self, task_id: str, skill_name: str, action: str
    ) -> None:
        original = SubTask(
            task_id=task_id,
            skill_name=skill_name,
            action=action,
            parameters={"key": "value"},
        )
        data = original.model_dump(mode="json")
        restored = SubTask.model_validate(data)
        assert restored.task_id == original.task_id
        assert restored.skill_name == original.skill_name
        assert restored.action == original.action


# ---------------------------------------------------------------------------
# Property: JSON serializability
# ---------------------------------------------------------------------------


class TestJsonSerializability:
    @given(
        session_id=st.text(min_size=1, max_size=50),
        user_id=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=20)
    def test_session_state_is_json_serializable(
        self, session_id: str, user_id: str
    ) -> None:
        now = datetime.now(timezone.utc)
        session = SessionState(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            last_active_at=now,
        )
        data = session.model_dump(mode="json")
        # Should not raise
        json.dumps(data)

    @given(
        event_id=st.text(min_size=1, max_size=50),
        action=st.text(min_size=1, max_size=100),
        resource_arn=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=20)
    def test_audit_event_is_json_serializable(
        self, event_id: str, action: str, resource_arn: str
    ) -> None:
        event = AuditEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            workload_identity_arn="acs:ram::123:role/agent",
            action=action,
            resource_arn=resource_arn,
            parameters={"key": "value"},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )
        data = event.model_dump(mode="json")
        json.dumps(data)

    @given(
        resource_type=st.text(min_size=1, max_size=50),
        resource_id=st.text(min_size=1, max_size=100),
        region=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=20)
    def test_resource_reference_is_json_serializable(
        self, resource_type: str, resource_id: str, region: str
    ) -> None:
        ref = ResourceReference(
            resource_type=resource_type,
            resource_id=resource_id,
            region=region,
        )
        data = ref.model_dump(mode="json")
        json.dumps(data)


# ---------------------------------------------------------------------------
# Property: TaskStatus enum
# ---------------------------------------------------------------------------


class TestTaskStatus:
    def test_all_statuses_serialize(self) -> None:
        for status in TaskStatus:
            data = status.value
            assert isinstance(data, str)
            assert len(data) > 0

    @given(
        status=st.sampled_from(list(TaskStatus)),
    )
    @settings(max_examples=10)
    def test_task_status_roundtrip(self, status: TaskStatus) -> None:
        data = status.value
        restored = TaskStatus(data)
        assert restored == status
