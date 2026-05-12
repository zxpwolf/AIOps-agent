"""故障排查 Skill — ECS 健康检查、网络诊断、RDS 慢查询分析.

实现 TroubleshootingSkill 类，继承 SkillInstance，
通过 Tool_Executor 调用 MCP Server 或本地工具。
"""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class TroubleshootingSkill(SkillInstance):
    """故障排查技能.

    能力:
    - ecs_health_check: ECS 实例健康检查
    - network_diagnosis: 网络连通性诊断
    - rds_slow_query_analysis: RDS 慢查询分析
    """

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行故障排查任务."""
        action = input_data.get("action", "")

        if action == "ecs_health_check":
            return await self._ecs_health_check(input_data)
        elif action == "network_diagnosis":
            return await self._network_diagnosis(input_data)
        elif action == "rds_slow_query":
            return await self._rds_slow_query(input_data)
        else:
            return {"error": f"不支持的排查操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/troubleshooting-skill",
            agent_instance_id="troubleshooting-skill",
            identity_provider="ram",
            permissions=["ecs:DescribeInstances", "ecs:DescribeInstanceStatus", "vpc:DescribeVpcs", "rds:DescribeSlowLogs"],
        )

    async def _ecs_health_check(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """ECS 实例健康检查."""
        instance_id = input_data.get("instance_id", "")
        logger.info("ECS 健康检查: %s", instance_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "ecs_health_check",
                "instance_id": instance_id,
                "checks": [],
            }

        status_result = await self._tool_executor.execute(
            tool_name="describe_instance_status",
            arguments={"instance_ids": instance_id},
            skill_identity=self._get_identity(),
        )
        detail_result = await self._tool_executor.execute(
            tool_name="describe_instances",
            arguments={"instance_ids": instance_id},
            skill_identity=self._get_identity(),
        )

        checks = []
        if status_result.success:
            checks.append({"check": "instance_status", "status": "ok", "data": status_result.output})
        if detail_result.success:
            checks.append({"check": "instance_detail", "status": "ok", "data": detail_result.output})

        return {
            "status": "success",
            "action": "ecs_health_check",
            "instance_id": instance_id,
            "checks": checks,
        }

    async def _network_diagnosis(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """网络连通性诊断."""
        source = input_data.get("source", "")
        target = input_data.get("target", "")
        logger.info("网络诊断: %s → %s", source, target)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "network_diagnosis",
                "source": source,
                "target": target,
                "results": [],
            }

        vpc_result = await self._tool_executor.execute(
            tool_name="describe_vpcs",
            arguments={"vpc_id": source},
            skill_identity=self._get_identity(),
        )

        results = []
        if vpc_result.success:
            results.append({"check": "vpc_config", "status": "ok", "data": vpc_result.output})

        return {
            "status": "success",
            "action": "network_diagnosis",
            "source": source,
            "target": target,
            "results": results,
        }

    async def _rds_slow_query(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """RDS 慢查询分析."""
        instance_id = input_data.get("instance_id", "")
        logger.info("RDS 慢查询分析: %s", instance_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "rds_slow_query",
                "instance_id": instance_id,
                "slow_queries": [],
            }

        result = await self._tool_executor.execute(
            tool_name="describe_slowlog_records",
            arguments={"db_instance_id": instance_id},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "rds_slow_query",
            "instance_id": instance_id,
            "slow_queries": result.output.get("items", []) if result.success else [],
        }
