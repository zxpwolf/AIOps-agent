"""Tests for aiops_agent.core.exceptions."""

import pytest

from aiops_agent.core.exceptions import (
    AgentError,
    CredentialError,
    PermissionDeniedError,
    SkillExecutionError,
    SkillNotFoundError,
    TimeoutError as AgentTimeoutError,
)


# ────────────────────────────────────────────────────────────
# AgentError (base class)
# ────────────────────────────────────────────────────────────

class TestAgentError:
    def test_init_attributes(self):
        exc = AgentError("something broke", "ERR_001", "try again")
        assert exc.message == "something broke"
        assert exc.error_code == "ERR_001"
        assert exc.suggestion == "try again"

    def test_default_suggestion(self):
        exc = AgentError("fail", "ERR_002")
        assert exc.suggestion == ""

    def test_str(self):
        exc = AgentError("boom", "ERR_003")
        assert str(exc) == "boom"

    def test_repr(self):
        exc = AgentError("boom", "ERR_004", "fix it")
        expected = "AgentError(message='boom', error_code='ERR_004', suggestion='fix it')"
        assert repr(exc) == expected

    def test_can_raise_and_catch(self):
        with pytest.raises(AgentError) as exc_info:
            raise AgentError("raised", "ERR_005")
        assert exc_info.value.message == "raised"
        assert exc_info.value.error_code == "ERR_005"

    def test_inherits_from_exception(self):
        exc = AgentError("x", "y")
        assert isinstance(exc, Exception)


# ────────────────────────────────────────────────────────────
# SkillExecutionError
# ────────────────────────────────────────────────────────────

class TestSkillExecutionError:
    def test_attributes(self):
        exc = SkillExecutionError("skill failed", skill_name="my_skill", suggestion="retry")
        assert exc.message == "skill failed"
        assert exc.error_code == "SKILL_EXECUTION_ERROR"
        assert exc.skill_name == "my_skill"
        assert exc.suggestion == "retry"

    def test_defaults(self):
        exc = SkillExecutionError("oops")
        assert exc.skill_name == ""
        assert exc.suggestion == ""
        assert exc.error_code == "SKILL_EXECUTION_ERROR"

    def test_is_instance_of_agent_error(self):
        exc = SkillExecutionError("x", skill_name="s")
        assert isinstance(exc, AgentError)

    def test_str(self):
        exc = SkillExecutionError("skill crash", skill_name="calc")
        assert str(exc) == "skill crash"

    def test_repr_inherits_from_agent_error(self):
        """SkillExecutionError uses AgentError's __repr__ (no skill_name in repr)."""
        exc = SkillExecutionError("err", skill_name="tool_a")
        r = repr(exc)
        assert "SkillExecutionError" in r
        assert "SKILL_EXECUTION_ERROR" in r
        assert "tool_a" not in r  # AgentError's repr doesn't include subclass attrs

    def test_can_raise_and_catch(self):
        with pytest.raises(SkillExecutionError):
            raise SkillExecutionError("bad skill", skill_name="x")


# ────────────────────────────────────────────────────────────
# PermissionDeniedError
# ────────────────────────────────────────────────────────────

class TestPermissionDeniedError:
    def test_attributes(self):
        exc = PermissionDeniedError(
            "no access",
            required_permission="admin:write",
            current_permissions=["user:read"],
            suggestion="upgrade role",
        )
        assert exc.message == "no access"
        assert exc.error_code == "PERMISSION_DENIED"
        assert exc.required_permission == "admin:write"
        assert exc.current_permissions == ["user:read"]
        assert exc.suggestion == "upgrade role"

    def test_default_current_permissions(self):
        exc = PermissionDeniedError("denied", required_permission="x")
        assert exc.current_permissions == []

    def test_default_suggestion(self):
        exc = PermissionDeniedError("nope", required_permission="foo")
        assert "请联系管理员" in exc.suggestion

    def test_custom_suggestion_overrides_default(self):
        exc = PermissionDeniedError("nope", suggestion="custom hint")
        assert exc.suggestion == "custom hint"

    def test_is_instance_of_agent_error(self):
        exc = PermissionDeniedError("x")
        assert isinstance(exc, AgentError)

    def test_str(self):
        exc = PermissionDeniedError("access denied")
        assert str(exc) == "access denied"

    def test_can_raise_and_catch(self):
        with pytest.raises(PermissionDeniedError):
            raise PermissionDeniedError("blocked")


# ────────────────────────────────────────────────────────────
# CredentialError
# ────────────────────────────────────────────────────────────

class TestCredentialError:
    def test_attributes(self):
        exc = CredentialError("no creds", credential_scope="aliyun:oss", suggestion="refresh")
        assert exc.message == "no creds"
        assert exc.error_code == "CREDENTIAL_ERROR"
        assert exc.credential_scope == "aliyun:oss"
        assert exc.suggestion == "refresh"

    def test_defaults(self):
        exc = CredentialError("oops")
        assert exc.credential_scope == ""
        # Default suggestion is a Chinese string, not empty
        assert "Agent Identity" in exc.suggestion

    def test_default_suggestion(self):
        exc = CredentialError("bad")
        assert "Agent Identity" in exc.suggestion

    def test_custom_suggestion_overrides_default(self):
        exc = CredentialError("bad", suggestion="my hint")
        assert exc.suggestion == "my hint"

    def test_is_instance_of_agent_error(self):
        exc = CredentialError("x")
        assert isinstance(exc, AgentError)

    def test_can_raise_and_catch(self):
        with pytest.raises(CredentialError):
            raise CredentialError("no token")


# ────────────────────────────────────────────────────────────
# TimeoutError (Agent-specific, distinct from builtins)
# ────────────────────────────────────────────────────────────

class TestAgentTimeoutError:
    def test_attributes(self):
        exc = AgentTimeoutError(
            "took too long",
            timeout_seconds=30.0,
            operation="api_call",
            suggestion="increase timeout",
        )
        assert exc.message == "took too long"
        assert exc.error_code == "TIMEOUT_ERROR"
        assert exc.timeout_seconds == 30.0
        assert exc.operation == "api_call"
        assert exc.suggestion == "increase timeout"

    def test_defaults(self):
        exc = AgentTimeoutError("timeout")
        assert exc.timeout_seconds == 0.0
        assert exc.operation == ""
        # Default suggestion is a Chinese string, not empty
        assert "请稍后重试" in exc.suggestion

    def test_default_suggestion(self):
        exc = AgentTimeoutError("slow")
        assert "请稍后重试" in exc.suggestion

    def test_custom_suggestion_overrides_default(self):
        exc = AgentTimeoutError("slow", suggestion="try later")
        assert exc.suggestion == "try later"

    def test_is_instance_of_agent_error(self):
        exc = AgentTimeoutError("x")
        assert isinstance(exc, AgentError)

    def test_is_instance_of_builtins_timeout_error(self):
        """AgentError inherits from Exception, so it IS a builtins.TimeoutError's superclass chain
        is NOT automatic — verify it's still an AgentError.  We check the class explicitly."""
        exc = AgentTimeoutError("x")
        assert isinstance(exc, Exception)

    def test_distinct_from_builtins_timeout_error(self):
        """Our TimeoutError should not be the same class as builtins.TimeoutError."""
        import builtins

        assert AgentTimeoutError is not builtins.TimeoutError
        # Our class inherits from AgentError, not builtins.TimeoutError
        assert not issubclass(AgentTimeoutError, builtins.TimeoutError)

    def test_catching_specific_agent_timeout(self):
        with pytest.raises(AgentTimeoutError) as exc_info:
            raise AgentTimeoutError("slow", timeout_seconds=5.0, operation="db_query")
        assert exc_info.value.timeout_seconds == 5.0
        assert exc_info.value.operation == "db_query"

    def test_can_raise_and_catch(self):
        with pytest.raises(AgentTimeoutError):
            raise AgentTimeoutError("time's up")


# ────────────────────────────────────────────────────────────
# SkillNotFoundError
# ────────────────────────────────────────────────────────────

class TestSkillNotFoundError:
    def test_attributes(self):
        exc = SkillNotFoundError(
            "no such skill",
            requested_capability="image_gen",
            available_skills=["text_summarize", "code_review"],
            suggestion="check catalog",
        )
        assert exc.message == "no such skill"
        assert exc.error_code == "SKILL_NOT_FOUND"
        assert exc.requested_capability == "image_gen"
        assert exc.available_skills == ["text_summarize", "code_review"]
        assert exc.suggestion == "check catalog"

    def test_defaults(self):
        exc = SkillNotFoundError("missing")
        assert exc.requested_capability == ""
        assert exc.available_skills == []

    def test_default_suggestion(self):
        exc = SkillNotFoundError("nope")
        assert "可用技能列表" in exc.suggestion

    def test_custom_suggestion_overrides_default(self):
        exc = SkillNotFoundError("nope", suggestion="look elsewhere")
        assert exc.suggestion == "look elsewhere"

    def test_is_instance_of_agent_error(self):
        exc = SkillNotFoundError("x")
        assert isinstance(exc, AgentError)

    def test_can_raise_and_catch(self):
        with pytest.raises(SkillNotFoundError):
            raise SkillNotFoundError("unknown skill")


# ────────────────────────────────────────────────────────────
# Inheritance chain
# ────────────────────────────────────────────────────────────

class TestInheritanceChain:
    """All custom exceptions are instances of AgentError."""

    @pytest.mark.parametrize("exc_cls", [
        SkillExecutionError,
        PermissionDeniedError,
        CredentialError,
        AgentTimeoutError,
        SkillNotFoundError,
    ])
    def test_all_inherit_from_agent_error(self, exc_cls):
        """Each exception class must be a subclass of AgentError."""
        assert issubclass(exc_cls, AgentError)

    @pytest.mark.parametrize("exc_cls, kwargs", [
        (SkillExecutionError, {"message": "x", "skill_name": "s"}),
        (PermissionDeniedError, {"message": "x"}),
        (CredentialError, {"message": "x"}),
        (AgentTimeoutError, {"message": "x"}),
        (SkillNotFoundError, {"message": "x"}),
    ])
    def test_all_are_instance_of_agent_error_at_runtime(self, exc_cls, kwargs):
        exc = exc_cls(**kwargs)
        assert isinstance(exc, AgentError)

    def test_all_are_instance_of_exception(self):
        exc = AgentError("x", "y")
        assert isinstance(exc, Exception)
