"""事件响应 Skill — 告警处理与事件响应编排."""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import PermissionLevel, ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class IncidentResponseSkill(SkillInstance):
    """事件响应技能.

    能力:
    - acknowledge_incident: 确认/认领事件
    - run_playbook: 执行事件响应预案
    - escalate: 升级事件
    """

    concurrency_safe = False  # 事件响应涉及状态变更，不应并发
    permission_requirements = [PermissionLevel.LIMITED_WRITE]
    description = "告警处理与事件响应编排，确认事件、执行预案、升级处理"
    render_format = "markdown"

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行事件响应任务."""
        action = input_data.get("action", "")

        if action == "acknowledge_incident":
            return await self._acknowledge_incident(input_data)
        elif action == "run_playbook":
            return await self._run_playbook(input_data)
        elif action == "escalate":
            return await self._escalate(input_data)
        else:
            return {"error": f"不支持的事件响应操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        """校验输入参数."""
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/incident-response-skill",
            agent_instance_id="incident-response-skill",
            identity_provider="ram",
            permissions=["cms:QueryMetricData", "sls:GetLogs", "mns:PublishMessage"],
        )

    async def _acknowledge_incident(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """确认/认领事件."""
        incident_id = input_data.get("incident_id", "")
        responder = input_data.get("responder", "aiops-agent")

        logger.info("确认事件: %s by %s", incident_id, responder)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "acknowledge_incident",
                "incident_id": incident_id,
                "responder": responder,
                "acknowledged_at": None,
            }

        result = await self._tool_executor.execute(
            tool_name="publish_message",
            arguments={
                "topic": "incident-updates",
                "message": {"incident_id": incident_id, "status": "acknowledged", "responder": responder},
            },
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "acknowledge_incident",
            "incident_id": incident_id,
            "responder": responder,
            "acknowledged_at": result.output if result.success else None,
        }

    async def _run_playbook(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行事件响应预案."""
        incident_id = input_data.get("incident_id", "")
        playbook_name = input_data.get("playbook_name", "")

        logger.info("执行预案: %s for incident=%s", playbook_name, incident_id)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "run_playbook",
                "incident_id": incident_id,
                "playbook_name": playbook_name,
                "steps": [],
            }

        result = await self._tool_executor.execute(
            tool_name="run_playbook",
            arguments={"incident_id": incident_id, "playbook_name": playbook_name},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "run_playbook",
            "incident_id": incident_id,
            "playbook_name": playbook_name,
            "steps": result.output if result.success else [],
        }

    async def _escalate(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """升级事件."""
        incident_id = input_data.get("incident_id", "")
        escalation_level = input_data.get("escalation_level", "L2")
        reason = input_data.get("reason", "")

        logger.info("升级事件: %s to %s, reason=%s", incident_id, escalation_level, reason)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "escalate",
                "incident_id": incident_id,
                "escalation_level": escalation_level,
                "reason": reason,
                "escalated_at": None,
            }

        result = await self._tool_executor.execute(
            tool_name="publish_message",
            arguments={
                "topic": "escalations",
                "message": {
                    "incident_id": incident_id,
                    "escalation_level": escalation_level,
                    "reason": reason,
                },
            },
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "escalate",
            "incident_id": incident_id,
            "escalation_level": escalation_level,
            "reason": reason,
            "escalated_at": result.output if result.success else None,
        }
