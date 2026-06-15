"""容量规划 Skill — 资源容量分析与扩容建议."""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import PermissionLevel, ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class CapacityPlanningSkill(SkillInstance):
    """容量规划技能.

    能力:
    - forecast_capacity: 预测未来容量需求
    - analyze_utilization: 分析资源利用率
    - recommend_scaling: 推荐扩缩容方案
    """

    concurrency_safe = True
    permission_requirements = [PermissionLevel.READ_ONLY]
    description = "资源容量分析与扩容建议，预测未来容量需求并推荐扩缩容方案"
    render_format = "json"

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行容量规划任务."""
        action = input_data.get("action", "")

        if action == "forecast_capacity":
            return await self._forecast_capacity(input_data)
        elif action == "analyze_utilization":
            return await self._analyze_utilization(input_data)
        elif action == "recommend_scaling":
            return await self._recommend_scaling(input_data)
        else:
            return {"error": f"不支持的容量规划操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        """校验输入参数."""
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/capacity-planning-skill",
            agent_instance_id="capacity-planning-skill",
            identity_provider="ram",
            permissions=["cms:QueryMetricData", "ecs:DescribeInstances", "ess:DescribeScalingGroups"],
        )

    async def _forecast_capacity(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """预测未来容量需求."""
        resource_type = input_data.get("resource_type", "")
        instance_id = input_data.get("instance_id", "")
        forecast_days = input_data.get("forecast_days", 7)

        logger.info("容量预测: resource=%s, instance=%s, days=%d", resource_type, instance_id, forecast_days)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "forecast_capacity",
                "resource_type": resource_type,
                "instance_id": instance_id,
                "forecast_days": forecast_days,
                "forecast": [],
            }

        result = await self._tool_executor.execute(
            tool_name="query_metric_data",
            arguments={
                "namespace": "acs_ecs_dashboard",
                "metric_name": "CPUUtilization",
                "instance_id": instance_id,
                "period": 3600,
            },
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "forecast_capacity",
            "resource_type": resource_type,
            "instance_id": instance_id,
            "forecast_days": forecast_days,
            "forecast": result.output if result.success else [],
        }

    async def _analyze_utilization(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """分析资源利用率."""
        resource_type = input_data.get("resource_type", "")
        instance_id = input_data.get("instance_id", "")

        logger.info("利用率分析: resource=%s, instance=%s", resource_type, instance_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "analyze_utilization",
                "resource_type": resource_type,
                "instance_id": instance_id,
                "utilization": {},
            }

        result = await self._tool_executor.execute(
            tool_name="describe_instances",
            arguments={"instance_ids": instance_id},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "analyze_utilization",
            "resource_type": resource_type,
            "instance_id": instance_id,
            "utilization": result.output if result.success else {},
        }

    async def _recommend_scaling(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """推荐扩缩容方案."""
        resource_type = input_data.get("resource_type", "")
        instance_id = input_data.get("instance_id", "")

        logger.info("扩缩容建议: resource=%s, instance=%s", resource_type, instance_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "recommend_scaling",
                "resource_type": resource_type,
                "instance_id": instance_id,
                "recommendations": [],
            }

        result = await self._tool_executor.execute(
            tool_name="describe_scaling_groups",
            arguments={},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "recommend_scaling",
            "resource_type": resource_type,
            "instance_id": instance_id,
            "recommendations": result.output if result.success else [],
        }
