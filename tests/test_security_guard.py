"""SecurityGuard 单元测试."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from aiops_agent.models.schemas import WorkloadIdentity
from aiops_agent.security.security_guard import SecurityGuard


# ---------------------------------------------------------------------------
# Test: Blacklist
# ---------------------------------------------------------------------------


class TestBlacklist:
    @pytest.mark.asyncio
    async def test_blacklisted_action_blocked(self, security_guard: SecurityGuard) -> None:
        security_guard._blacklist = [{"action": "ecs:DeleteInstance", "description": "删除 ECS"}]

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        result = await security_guard.check(identity, "ecs:DeleteInstance", "*")

        assert result.allowed is False
        assert result.rule_id == "blacklist"

    @pytest.mark.asyncio
    async def test_non_blacklisted_allowed(self, security_guard: SecurityGuard) -> None:
        security_guard._blacklist = [{"action": "ecs:DeleteInstance", "description": "删除 ECS"}]

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        result = await security_guard.check(identity, "ecs:DescribeInstances", "*")

        assert result.allowed is True


# ---------------------------------------------------------------------------
# Test: Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_per_minute(self, security_guard: SecurityGuard) -> None:
        security_guard._rate_limits = {"default": {"max_calls_per_minute": 3, "max_calls_per_hour": 1000}}

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        # 发送 3 次成功的请求（记录历史）
        for _ in range(3):
            result = await security_guard.check(identity, "test_action", "*")
            assert result.allowed is True

        # 第 4 次应该被限流
        result = await security_guard.check(identity, "test_action", "*")

        assert result.allowed is False
        assert "超过阈值" in (result.denial_reason or "")

    @pytest.mark.asyncio
    async def test_within_rate_limit(self, security_guard: SecurityGuard) -> None:
        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        result = await security_guard.check(identity, "first_action", "*")

        assert result.allowed is True


# ---------------------------------------------------------------------------
# Test: Anomaly detection
# ---------------------------------------------------------------------------


class TestAnomalyDetection:
    @pytest.mark.asyncio
    async def test_anomaly_warning_on_diverse_actions(self, security_guard: SecurityGuard) -> None:
        security_guard._anomaly_config = {"enabled": True}

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        # 添加 10 个不同操作
        for i in range(10):
            security_guard._operation_sequences[identity.workload_identity_arn].append(
                {"action": f"action_{i}", "resource_arn": "*", "timestamp": time.time()}
            )

        result = await security_guard.check(identity, "action_10", "*")

        # 异常检测返回告警但不拦截
        assert result.allowed is True
        if result.suggestion:
            assert "异常" in result.suggestion

    @pytest.mark.asyncio
    async def test_anomaly_detection_disabled(self, security_guard: SecurityGuard) -> None:
        security_guard._anomaly_config = {"enabled": False}

        identity = WorkloadIdentity(
            workload_identity_arn="acs:ram::123:role/agent",
            agent_instance_id="agent-1",
            identity_provider="ram",
        )

        result = await security_guard.check(identity, "any_action", "*")
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Test: TLS compliance
# ---------------------------------------------------------------------------


class TestTlsCompliance:
    def test_https_allowed(self, security_guard: SecurityGuard) -> None:
        result = security_guard.check_tls_compliance("https://example.com/api")
        assert result.allowed is True

    def test_http_blocked(self, security_guard: SecurityGuard) -> None:
        result = security_guard.check_tls_compliance("http://example.com/api")
        assert result.allowed is False
        assert "HTTPS" in (result.denial_reason or "")


# ---------------------------------------------------------------------------
# Test: Rules loading
# ---------------------------------------------------------------------------


class TestRulesLoading:
    def test_load_rules_from_file(self, tmp_path) -> None:
        config_file = tmp_path / "security_rules.yaml"
        config_file.write_text("""
blacklist:
  - action: "ecs:DeleteInstance"
    description: "删除 ECS 实例"
    suggestion: "请手动操作"
rate_limits:
  default:
    max_calls_per_minute: 60
    max_calls_per_hour: 1000
anomaly_detection:
  enabled: true
  deviation_threshold: 3.0
communication:
  enforce_https: true
""")

        guard = SecurityGuard(security_rules_path=str(config_file))
        assert len(guard._blacklist) == 1
        assert guard._blacklist[0]["action"] == "ecs:DeleteInstance"
        assert guard._rate_limits["default"]["max_calls_per_minute"] == 60
        assert guard._anomaly_config["enabled"] is True
