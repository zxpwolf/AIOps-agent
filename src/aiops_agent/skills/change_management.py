"""变更管理 Skill — 变更风险评估与回滚方案推荐.

实现 ChangeManagementSkill 类，继承 SkillInstance，
通过 Tool_Executor 调用 MCP Server 或本地工具。
"""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class ChangeManagementSkill(SkillInstance):
    """变更管理技能.

    能力:
    - change_risk_assessment: 变更风险评估
    - rollback_recommendation: 回滚方案推荐
    """

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行变更管理任务."""
        action = input_data.get("action", "")

        if action == "risk_assessment":
            return await self._risk_assessment(input_data)
        elif action == "rollback_plan":
            return await self._rollback_plan(input_data)
        else:
            return {"error": f"不支持的变更管理操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/change-management-skill",
            agent_instance_id="change-management-skill",
            identity_provider="ram",
            permissions=["ecs:DescribeInstances", "rds:DescribeDBInstances", "vpc:DescribeVpcs"],
        )

    async def _risk_assessment(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """变更风险评估."""
        change_type = input_data.get("change_type", "")
        target_resources = input_data.get("target_resources", [])
        logger.info("变更风险评估: type=%s, resources=%s", change_type, target_resources)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "risk_assessment",
                "change_type": change_type,
                "risk_level": "medium",
                "recommendations": [],
            }

        resource_details = []
        for resource in target_resources:
            resource_type = resource.get("type", "")
            resource_id = resource.get("id", "")

            if resource_type == "ecs":
                result = await self._tool_executor.execute(
                    tool_name="describe_instances",
                    arguments={"instance_ids": resource_id},
                    skill_identity=self._get_identity(),
                )
            elif resource_type == "rds":
                result = await self._tool_executor.execute(
                    tool_name="describe_dbinstances",
                    arguments={"db_instance_id": resource_id},
                    skill_identity=self._get_identity(),
                )
            elif resource_type == "vpc":
                result = await self._tool_executor.execute(
                    tool_name="describe_vpcs",
                    arguments={"vpc_id": resource_id},
                    skill_identity=self._get_identity(),
                )
            else:
                result = None

            if result and result.success:
                resource_details.append({"resource": resource, "status": "ok", "detail": result.output})
            else:
                resource_details.append({"resource": resource, "status": "error"})

        error_count = sum(1 for d in resource_details if d.get("status") == "error")
        if error_count == 0:
            risk_level = "low"
        elif error_count < len(resource_details) / 2:
            risk_level = "medium"
        else:
            risk_level = "high"

        return {
            "status": "success",
            "action": "risk_assessment",
            "change_type": change_type,
            "risk_level": risk_level,
            "resource_details": resource_details,
            "recommendations": self._generate_recommendations(change_type, risk_level, resource_details),
        }

    def _generate_recommendations(self, change_type: str, risk_level: str, resource_details: list) -> list[str]:
        """生成变更建议."""
        recommendations = []

        if risk_level == "high":
            recommendations.append("建议在低峰期执行变更")
            recommendations.append("变更前请确认回滚方案")
            recommendations.append("建议安排专人值守")
        elif risk_level == "medium":
            recommendations.append("建议在业务低峰期执行")
            recommendations.append("请准备回滚方案")
        else:
            recommendations.append("变更风险较低，可正常执行")

        if "ecs" in change_type.lower():
            recommendations.append("ECS 变更请先检查实例状态")
        if "rds" in change_type.lower():
            recommendations.append("RDS 变更请先备份数据库")
        if "vpc" in change_type.lower():
            recommendations.append("VPC 变更请先确认路由配置")

        return recommendations

    async def _rollback_plan(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """回滚方案推荐."""
        change_id = input_data.get("change_id", "")
        target_resources = input_data.get("target_resources", [])
        logger.info("生成回滚方案: change_id=%s", change_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "rollback_plan",
                "change_id": change_id,
                "steps": [],
            }

        steps = []
        for i, resource in enumerate(target_resources, 1):
            resource_type = resource.get("type", "")
            resource_id = resource.get("id", "")
            steps.append({
                "step": i,
                "action": f"回滚 {resource_type} 资源 {resource_id}",
                "description": f"将 {resource_type}:{resource_id} 恢复到变更前状态",
                "verification": f"验证 {resource_type}:{resource_id} 状态正常",
            })

        steps.append({
            "step": len(steps) + 1,
            "action": "全局验证",
            "description": "验证所有关联服务和监控指标正常",
            "verification": "检查 CloudMonitor 告警和 SLS 错误日志",
        })

        return {
            "status": "success",
            "action": "rollback_plan",
            "change_id": change_id,
            "steps": steps,
        }
