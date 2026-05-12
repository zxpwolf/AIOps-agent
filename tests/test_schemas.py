"""单元测试 — models/schemas.py 数据模型验证."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from aiops_agent.models.schemas import (
    AgentResponse,
    AliyunCredential,
    AuditEvent,
    CachedCredential,
    CredentialScope,
    InteractionMode,
    MCPServerConfig,
    MCPTool,
    Message,
    PermissionCheckResult,
    PermissionLevel,
    ResourceReference,
    SecurityCheckResult,
    SecurityRule,
    SessionState,
    SkillDefinition,
    SubTask,
    TaskPlan,
    TaskProgress,
    TaskStatus,
    ThirdPartyCredential,
    ToolResult,
    ValidationResult,
    WorkloadIdentity,
)


# ---------------------------------------------------------------------------
# 1. TaskStatus enum
# ---------------------------------------------------------------------------


class TestTaskStatus:
    def test_all_values_present(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.PENDING, str)


# ---------------------------------------------------------------------------
# 2. SubTask
# ---------------------------------------------------------------------------


class TestSubTask:
    def test_minimal_creation(self):
        st = SubTask(task_id="t1", skill_name="monitoring", action="query_metrics")
        assert st.task_id == "t1"
        assert st.parameters == {}
        assert st.dependencies == []
        assert st.status == TaskStatus.PENDING
        assert st.result is None
        assert st.error is None
        assert isinstance(st.created_at, datetime)

    def test_full_creation(self):
        now = datetime.utcnow()
        st = SubTask(
            task_id="t2",
            skill_name="troubleshooting",
            action="diagnose",
            parameters={"instance_id": "i-abc123"},
            dependencies=["t1"],
            status=TaskStatus.RUNNING,
            result={"healthy": True},
            error=None,
            created_at=now,
        )
        assert st.dependencies == ["t1"]
        assert st.status == TaskStatus.RUNNING
        assert st.result == {"healthy": True}

    def test_serialization_roundtrip(self):
        st = SubTask(task_id="t1", skill_name="mon", action="check")
        data = st.model_dump()
        restored = SubTask.model_validate(data)
        assert restored == st

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            SubTask(skill_name="mon", action="check")  # missing task_id


# ---------------------------------------------------------------------------
# 3. TaskPlan
# ---------------------------------------------------------------------------


class TestTaskPlan:
    def test_minimal_creation(self):
        tp = TaskPlan(plan_id="p1", user_request="check ECS health")
        assert tp.sub_tasks == []
        assert tp.context == {}
        assert tp.status == TaskStatus.PENDING

    def test_with_subtasks(self):
        st = SubTask(task_id="t1", skill_name="mon", action="query")
        tp = TaskPlan(plan_id="p1", user_request="check", sub_tasks=[st])
        assert len(tp.sub_tasks) == 1

    def test_serialization_roundtrip(self):
        st = SubTask(task_id="t1", skill_name="mon", action="query")
        tp = TaskPlan(plan_id="p1", user_request="check", sub_tasks=[st])
        data = tp.model_dump()
        restored = TaskPlan.model_validate(data)
        assert restored == tp


# ---------------------------------------------------------------------------
# 4. AgentResponse
# ---------------------------------------------------------------------------


class TestAgentResponse:
    def test_success_response(self):
        resp = AgentResponse(success=True, message="OK", data={"key": "val"})
        assert resp.success is True
        assert resp.data == {"key": "val"}

    def test_error_response(self):
        resp = AgentResponse(
            success=False,
            message="Failed",
            error_code="PERM_DENIED",
            suggestion="Request admin access",
        )
        assert resp.error_code == "PERM_DENIED"
        assert resp.suggestion == "Request admin access"

    def test_optional_fields_default_none(self):
        resp = AgentResponse(success=True, message="OK")
        assert resp.data is None
        assert resp.error_code is None
        assert resp.trace_id is None


# ---------------------------------------------------------------------------
# 5. Message
# ---------------------------------------------------------------------------


class TestMessage:
    def test_creation(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert isinstance(msg.timestamp, datetime)
        assert msg.metadata == {}

    def test_serialization_roundtrip(self):
        msg = Message(role="assistant", content="Hi there", metadata={"source": "llm"})
        data = msg.model_dump()
        restored = Message.model_validate(data)
        assert restored == msg


# ---------------------------------------------------------------------------
# 6. ToolResult
# ---------------------------------------------------------------------------


class TestToolResult:
    def test_success_result(self):
        tr = ToolResult(tool_name="ecs_describe", success=True, output={"status": "running"})
        assert tr.execution_time_ms == 0.0
        assert tr.sanitized is False

    def test_failure_result(self):
        tr = ToolResult(tool_name="ecs_describe", success=False, error="timeout")
        assert tr.output is None
        assert tr.error == "timeout"


# ---------------------------------------------------------------------------
# 7. WorkloadIdentity
# ---------------------------------------------------------------------------


class TestWorkloadIdentity:
    def test_creation(self):
        wi = WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::1234:workload/my-agent",
            agent_instance_id="agent-001",
            identity_provider="ram",
        )
        assert wi.permissions == []
        assert wi.metadata == {}

    def test_with_permissions(self):
        wi = WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::1234:workload/my-agent",
            agent_instance_id="agent-001",
            identity_provider="okta",
            permissions=["ecs:DescribeInstances", "rds:DescribeDBInstances"],
        )
        assert len(wi.permissions) == 2


# ---------------------------------------------------------------------------
# 8. CredentialScope
# ---------------------------------------------------------------------------


class TestCredentialScope:
    def test_aliyun_scope(self):
        cs = CredentialScope(
            target_service="aliyun",
            credential_provider_name="ecs-provider",
            ram_role_arn="acs:ram::1234:role/ecs-readonly",
        )
        assert cs.scopes == []

    def test_third_party_scope(self):
        cs = CredentialScope(
            target_service="third_party",
            credential_provider_name="dingtalk-provider",
            scopes=["read", "write"],
        )
        assert cs.ram_role_arn is None


# ---------------------------------------------------------------------------
# 9. CachedCredential
# ---------------------------------------------------------------------------


class TestCachedCredential:
    def test_aliyun_cached(self):
        now = datetime.utcnow()
        scope = CredentialScope(
            target_service="aliyun", credential_provider_name="ecs-provider"
        )
        cc = CachedCredential(
            credential_scope=scope,
            access_key_id="LTAI...",
            access_key_secret="secret",
            security_token="token",
            expires_at=now + timedelta(hours=1),
            refresh_before=now + timedelta(minutes=55),
        )
        assert cc.oauth_token is None
        assert cc.api_key is None

    def test_serialization_roundtrip(self):
        now = datetime.utcnow()
        scope = CredentialScope(
            target_service="aliyun", credential_provider_name="p"
        )
        cc = CachedCredential(
            credential_scope=scope,
            access_key_id="ak",
            expires_at=now,
            refresh_before=now,
        )
        data = cc.model_dump()
        restored = CachedCredential.model_validate(data)
        assert restored == cc


# ---------------------------------------------------------------------------
# 10. AliyunCredential
# ---------------------------------------------------------------------------


class TestAliyunCredential:
    def test_creation(self):
        now = datetime.utcnow()
        ac = AliyunCredential(
            access_key_id="LTAI...",
            access_key_secret="secret",
            security_token="sts-token",
            expires_at=now,
        )
        assert ac.access_key_id == "LTAI..."

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            AliyunCredential(access_key_id="ak", access_key_secret="sk")  # missing security_token, expires_at


# ---------------------------------------------------------------------------
# 11. ThirdPartyCredential
# ---------------------------------------------------------------------------


class TestThirdPartyCredential:
    def test_oauth_credential(self):
        tp = ThirdPartyCredential(oauth_token="oauth-xyz", scopes=["read"])
        assert tp.api_key is None
        assert tp.expires_at is None

    def test_api_key_credential(self):
        tp = ThirdPartyCredential(api_key="key-123")
        assert tp.oauth_token is None
        assert tp.scopes == []

    def test_empty_credential(self):
        tp = ThirdPartyCredential()
        assert tp.oauth_token is None
        assert tp.api_key is None


# ---------------------------------------------------------------------------
# 12. MCPServerConfig
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    def test_stdio_config(self):
        cfg = MCPServerConfig(
            server_name="cloudmonitor",
            transport="stdio",
            command="python -m cloudmonitor_mcp",
            args=["--region", "cn-hangzhou"],
        )
        assert cfg.url is None
        assert cfg.env == {}

    def test_sse_config(self):
        cfg = MCPServerConfig(
            server_name="sls",
            transport="sse",
            url="https://sls-mcp.example.com/sse",
        )
        assert cfg.command is None
        assert cfg.args == []


# ---------------------------------------------------------------------------
# 13. MCPTool
# ---------------------------------------------------------------------------


class TestMCPTool:
    def test_creation(self):
        tool = MCPTool(
            name="describe_instances",
            description="List ECS instances",
            input_schema={"type": "object", "properties": {"region": {"type": "string"}}},
            server_name="ecs-mcp",
        )
        assert tool.name == "describe_instances"

    def test_default_schema(self):
        tool = MCPTool(name="ping", description="Ping", server_name="local")
        assert tool.input_schema == {}


# ---------------------------------------------------------------------------
# 14. AuditEvent
# ---------------------------------------------------------------------------


class TestAuditEvent:
    def test_full_creation(self):
        now = datetime.utcnow()
        event = AuditEvent(
            event_id="evt-001",
            timestamp=now,
            workload_identity_arn="acs:agent-identity::1234:workload/agent",
            user_identity="user@example.com",
            action="ecs:DescribeInstances",
            resource_arn="acs:ecs:cn-hangzhou:1234:instance/i-abc",
            parameters={"region": "cn-hangzhou"},
            result="success",
            permission_level="read_only",
            trace_id="trace-abc",
            span_id="span-123",
        )
        assert event.error_message is None

    def test_serialization_roundtrip(self):
        now = datetime.utcnow()
        event = AuditEvent(
            event_id="evt-002",
            timestamp=now,
            workload_identity_arn="arn",
            action="act",
            resource_arn="res",
            result="failure",
            error_message="boom",
            permission_level="admin",
            trace_id="t",
            span_id="s",
        )
        data = event.model_dump()
        restored = AuditEvent.model_validate(data)
        assert restored == event

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            AuditEvent(event_id="e1", timestamp=datetime.utcnow())  # missing many required fields


# ---------------------------------------------------------------------------
# 15. PermissionLevel
# ---------------------------------------------------------------------------


class TestPermissionLevel:
    def test_all_levels(self):
        assert PermissionLevel.READ_ONLY == "read_only"
        assert PermissionLevel.LIMITED_WRITE == "limited_write"
        assert PermissionLevel.ADMIN == "admin"

    def test_is_str_enum(self):
        assert isinstance(PermissionLevel.READ_ONLY, str)


# ---------------------------------------------------------------------------
# 16. PermissionCheckResult
# ---------------------------------------------------------------------------


class TestPermissionCheckResult:
    def test_allowed(self):
        pcr = PermissionCheckResult(
            allowed=True,
            required_permission="ecs:DescribeInstances",
            current_permissions=["ecs:*"],
            permission_level=PermissionLevel.READ_ONLY,
            requires_approval=False,
        )
        assert pcr.denial_reason is None

    def test_denied(self):
        pcr = PermissionCheckResult(
            allowed=False,
            required_permission="ecs:DeleteInstance",
            permission_level=PermissionLevel.ADMIN,
            requires_approval=True,
            denial_reason="Admin approval required",
        )
        assert pcr.allowed is False


# ---------------------------------------------------------------------------
# 17. SecurityRule
# ---------------------------------------------------------------------------


class TestSecurityRule:
    def test_blacklist_rule(self):
        rule = SecurityRule(
            rule_id="r1",
            rule_type="blacklist",
            description="Block production deletes",
            config={"actions": ["ecs:DeleteInstance"]},
        )
        assert rule.config["actions"] == ["ecs:DeleteInstance"]

    def test_default_config(self):
        rule = SecurityRule(rule_id="r2", rule_type="rate_limit", description="Rate limit")
        assert rule.config == {}


# ---------------------------------------------------------------------------
# 18. SecurityCheckResult
# ---------------------------------------------------------------------------


class TestSecurityCheckResult:
    def test_allowed(self):
        scr = SecurityCheckResult(allowed=True)
        assert scr.rule_id is None

    def test_denied(self):
        scr = SecurityCheckResult(
            allowed=False,
            rule_id="r1",
            denial_reason="Blacklisted operation",
            suggestion="Use read-only alternative",
        )
        assert scr.suggestion == "Use read-only alternative"


# ---------------------------------------------------------------------------
# 19. InteractionMode
# ---------------------------------------------------------------------------


class TestInteractionMode:
    def test_all_modes(self):
        assert InteractionMode.CHAT == "chat"
        assert InteractionMode.TASK == "task"
        assert InteractionMode.WATCH == "watch"


# ---------------------------------------------------------------------------
# 20. ResourceReference
# ---------------------------------------------------------------------------


class TestResourceReference:
    def test_creation(self):
        rr = ResourceReference(
            resource_type="ecs",
            resource_id="i-abc123",
            region="cn-hangzhou",
            display_name="My ECS",
        )
        assert rr.resource_type == "ecs"

    def test_optional_display_name(self):
        rr = ResourceReference(resource_type="rds", resource_id="rm-xyz", region="cn-shanghai")
        assert rr.display_name is None


# ---------------------------------------------------------------------------
# 21. TaskProgress
# ---------------------------------------------------------------------------


class TestTaskProgress:
    def test_defaults(self):
        tp = TaskProgress()
        assert tp.percentage == 0.0
        assert tp.current_step == ""
        assert tp.total_steps == 0
        assert tp.completed_steps == 0

    def test_custom_values(self):
        tp = TaskProgress(percentage=50.0, current_step="Diagnosing", total_steps=4, completed_steps=2)
        assert tp.percentage == 50.0


# ---------------------------------------------------------------------------
# 22. SessionState
# ---------------------------------------------------------------------------


class TestSessionState:
    def test_creation(self):
        now = datetime.utcnow()
        ss = SessionState(
            session_id="sess-001",
            user_id="user-001",
            created_at=now,
            last_active_at=now,
        )
        assert ss.mode == InteractionMode.CHAT
        assert ss.messages == []
        assert ss.resources == {}
        assert ss.task_progress is None
        assert ss.ttl_minutes == 30

    def test_with_messages_and_resources(self):
        now = datetime.utcnow()
        msg = Message(role="user", content="Check ECS")
        rr = ResourceReference(resource_type="ecs", resource_id="i-abc", region="cn-hangzhou")
        ss = SessionState(
            session_id="sess-002",
            user_id="user-002",
            mode=InteractionMode.TASK,
            messages=[msg],
            resources={"i-abc": rr},
            task_progress=TaskProgress(percentage=25.0),
            created_at=now,
            last_active_at=now,
        )
        assert len(ss.messages) == 1
        assert "i-abc" in ss.resources

    def test_serialization_roundtrip(self):
        now = datetime.utcnow()
        ss = SessionState(
            session_id="s1",
            user_id="u1",
            created_at=now,
            last_active_at=now,
        )
        data = ss.model_dump()
        restored = SessionState.model_validate(data)
        assert restored == ss


# ---------------------------------------------------------------------------
# 23. SkillDefinition
# ---------------------------------------------------------------------------


class TestSkillDefinition:
    def test_creation(self):
        sd = SkillDefinition(
            skill_name="monitoring",
            description="Cloud monitoring skill",
            version="1.0.0",
            capabilities=["query_metrics", "check_alerts"],
            required_permissions=["cms:DescribeMetricList"],
        )
        assert sd.status == "healthy"

    def test_defaults(self):
        sd = SkillDefinition(skill_name="test", description="Test", version="0.1.0")
        assert sd.capabilities == []
        assert sd.required_permissions == []
        assert sd.status == "healthy"


# ---------------------------------------------------------------------------
# 24. ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_valid(self):
        vr = ValidationResult(valid=True)
        assert vr.errors == []

    def test_invalid(self):
        vr = ValidationResult(valid=False, errors=["Missing field: instance_id"])
        assert len(vr.errors) == 1
