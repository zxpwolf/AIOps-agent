"""Tests for Skill — ToolExecutor integration (P1-1)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiops_agent.models.schemas import ToolResult, ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.monitoring import MonitoringSkill
from aiops_agent.skills.troubleshooting import TroubleshootingSkill
from aiops_agent.skills.change_management import ChangeManagementSkill


# ---------------------------------------------------------------------------
# Base Skill Tests
# ---------------------------------------------------------------------------

class TestSkillInstanceBase:
    def test_default_tool_executor_is_none(self):
        class TestSkill(SkillInstance):
            async def execute(self, input_data): return {}
            async def validate(self, input_data): return ValidationResult(valid=True)

        skill = TestSkill()
        assert skill.tool_executor is None

    def test_set_tool_executor(self):
        class TestSkill(SkillInstance):
            async def execute(self, input_data): return {}
            async def validate(self, input_data): return ValidationResult(valid=True)

        skill = TestSkill()
        mock_executor = MagicMock()
        skill.set_tool_executor(mock_executor)
        assert skill.tool_executor is mock_executor


# ---------------------------------------------------------------------------
# Monitoring Skill Tests
# ---------------------------------------------------------------------------

class TestMonitoringSkill:
    def test_init_calls_super(self):
        skill = MonitoringSkill()
        assert hasattr(skill, "_tool_executor")
        assert skill._tool_executor is None

    async def test_validate_missing_action(self):
        skill = MonitoringSkill()
        result = await skill.validate({})
        assert result.valid is False

    async def test_validate_with_action(self):
        skill = MonitoringSkill()
        result = await skill.validate({"action": "query_metrics"})
        assert result.valid is True

    async def test_execute_unsupported_action(self):
        skill = MonitoringSkill()
        result = await skill.execute({"action": "unknown"})
        assert "error" in result

    async def test_query_metrics_without_executor(self):
        skill = MonitoringSkill()
        result = await skill.execute({
            "action": "query_metrics",
            "namespace": "acs_ecs_dashboard",
            "metric_name": "CPUUtilization",
            "instance_id": "i-test",
        })
        assert result["status"] == "success"
        assert result["data"] == []

    async def test_query_logs_without_executor(self):
        skill = MonitoringSkill()
        result = await skill.execute({
            "action": "query_logs",
            "project": "test-project",
            "logstore": "test-logstore",
            "query": "error",
        })
        assert result["status"] == "success"
        assert result["logs"] == []

    async def test_analyze_metrics(self):
        skill = MonitoringSkill()
        result = await skill.execute({"action": "analyze_metrics"})
        assert result["status"] == "success"
        assert "analysis" in result

    async def test_query_metrics_with_executor_success(self):
        skill = MonitoringSkill()
        mock_executor = AsyncMock()
        mock_executor.execute.return_value = ToolResult(
            tool_name="query_metric_last",
            success=True,
            output={"data": [{"timestamp": 123, "value": 50}]},
        )
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "query_metrics",
            "namespace": "acs_ecs_dashboard",
            "metric_name": "CPUUtilization",
            "instance_id": "i-test",
        })
        assert result["status"] == "success"
        assert len(result["data"]) == 1

    async def test_query_logs_with_executor_success(self):
        skill = MonitoringSkill()
        mock_executor = AsyncMock()
        mock_executor.execute.return_value = ToolResult(
            tool_name="query_logs",
            success=True,
            output={"logs": [{"message": "error"}]},
        )
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "query_logs",
            "project": "test",
            "logstore": "test",
            "query": "error",
        })
        assert result["status"] == "success"
        assert len(result["logs"]) == 1

    def test_get_identity(self):
        skill = MonitoringSkill()
        identity = skill._get_identity()
        assert isinstance(identity, WorkloadIdentity)
        assert "monitoring-skill" in identity.workload_identity_arn


# ---------------------------------------------------------------------------
# Troubleshooting Skill Tests
# ---------------------------------------------------------------------------

class TestTroubleshootingSkill:
    def test_init_calls_super(self):
        skill = TroubleshootingSkill()
        assert skill._tool_executor is None

    async def test_execute_unsupported_action(self):
        skill = TroubleshootingSkill()
        result = await skill.execute({"action": "unknown"})
        assert "error" in result

    async def test_ecs_health_check_without_executor(self):
        skill = TroubleshootingSkill()
        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-test",
        })
        assert result["status"] == "success"
        assert result["checks"] == []

    async def test_network_diagnosis_without_executor(self):
        skill = TroubleshootingSkill()
        result = await skill.execute({
            "action": "network_diagnosis",
            "source": "vpc-1",
            "target": "vpc-2",
        })
        assert result["status"] == "success"

    async def test_rds_slow_query_without_executor(self):
        skill = TroubleshootingSkill()
        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-test",
        })
        assert result["status"] == "success"
        assert result["slow_queries"] == []

    async def test_ecs_health_check_with_executor(self):
        skill = TroubleshootingSkill()
        mock_executor = AsyncMock()
        mock_executor.execute.return_value = ToolResult(
            tool_name="describe_instance_status",
            success=True,
            output={"status": "Running"},
        )
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "ecs_health_check",
            "instance_id": "i-test",
        })
        assert result["status"] == "success"
        assert len(result["checks"]) >= 1

    async def test_rds_slow_query_with_executor(self):
        skill = TroubleshootingSkill()
        mock_executor = AsyncMock()
        mock_executor.execute.return_value = ToolResult(
            tool_name="describe_slowlog_records",
            success=True,
            output={"items": [{"sql": "SELECT *"}]},
        )
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "rds_slow_query",
            "instance_id": "rm-test",
        })
        assert result["status"] == "success"
        assert len(result["slow_queries"]) == 1

    def test_get_identity(self):
        skill = TroubleshootingSkill()
        identity = skill._get_identity()
        assert "troubleshooting-skill" in identity.workload_identity_arn


# ---------------------------------------------------------------------------
# Change Management Skill Tests
# ---------------------------------------------------------------------------

class TestChangeManagementSkill:
    def test_init_calls_super(self):
        skill = ChangeManagementSkill()
        assert skill._tool_executor is None

    async def test_execute_unsupported_action(self):
        skill = ChangeManagementSkill()
        result = await skill.execute({"action": "unknown"})
        assert "error" in result

    async def test_risk_assessment_without_executor(self):
        skill = ChangeManagementSkill()
        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "ecs_restart",
            "target_resources": [],
        })
        assert result["status"] == "success"
        assert result["risk_level"] == "medium"

    async def test_rollback_plan_without_executor(self):
        skill = ChangeManagementSkill()
        result = await skill.execute({
            "action": "rollback_plan",
            "change_id": "chg-001",
        })
        assert result["status"] == "success"
        assert result["steps"] == []

    async def test_risk_assessment_with_executor(self):
        skill = ChangeManagementSkill()
        mock_executor = AsyncMock()
        mock_executor.execute.return_value = ToolResult(
            tool_name="describe_instances",
            success=True,
            output={"instances": []},
        )
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "risk_assessment",
            "change_type": "ecs_restart",
            "target_resources": [{"type": "ecs", "id": "i-001"}],
        })
        assert result["status"] == "success"
        assert "recommendations" in result

    async def test_rollback_plan_with_executor(self):
        skill = ChangeManagementSkill()
        mock_executor = AsyncMock()
        skill.set_tool_executor(mock_executor)

        result = await skill.execute({
            "action": "rollback_plan",
            "change_id": "chg-001",
            "target_resources": [
                {"type": "ecs", "id": "i-001"},
                {"type": "rds", "id": "rm-001"},
            ],
        })
        assert result["status"] == "success"
        assert len(result["steps"]) >= 3

    def test_generate_recommendations_high_risk(self):
        skill = ChangeManagementSkill()
        recs = skill._generate_recommendations("ecs_restart", "high", [])
        assert len(recs) > 0
        assert any("低峰期" in r for r in recs)

    def test_generate_recommendations_low_risk(self):
        skill = ChangeManagementSkill()
        recs = skill._generate_recommendations("ecs_restart", "low", [])
        assert any("风险较低" in r for r in recs)

    def test_generate_recommendations_ecs(self):
        skill = ChangeManagementSkill()
        recs = skill._generate_recommendations("ecs_restart", "low", [])
        assert any("ECS" in r for r in recs)

    def test_generate_recommendations_rds(self):
        skill = ChangeManagementSkill()
        recs = skill._generate_recommendations("rds_upgrade", "low", [])
        assert any("RDS" in r for r in recs)

    def test_generate_recommendations_vpc(self):
        skill = ChangeManagementSkill()
        recs = skill._generate_recommendations("vpc_change", "low", [])
        assert any("VPC" in r for r in recs)

    def test_get_identity(self):
        skill = ChangeManagementSkill()
        identity = skill._get_identity()
        assert "change-management-skill" in identity.workload_identity_arn
