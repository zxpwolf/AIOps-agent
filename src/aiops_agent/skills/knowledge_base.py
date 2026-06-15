"""知识库 Skill — 运维知识检索与故障案例匹配."""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class KnowledgeBaseSkill(SkillInstance):
    """知识库技能.

    能力:
    - search_knowledge: 检索运维知识库
    - match_case: 匹配历史故障案例
    - suggest_solution: 基于知识库推荐解决方案
    """

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行知识库任务."""
        action = input_data.get("action", "")

        if action == "search_knowledge":
            return await self._search_knowledge(input_data)
        elif action == "match_case":
            return await self._match_case(input_data)
        elif action == "suggest_solution":
            return await self._suggest_solution(input_data)
        else:
            return {"error": f"不支持的知识库操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        """校验输入参数."""
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/knowledge-base-skill",
            agent_instance_id="knowledge-base-skill",
            identity_provider="ram",
            permissions=["sls:GetLogs", "kms:Decrypt"],
        )

    async def _search_knowledge(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """检索运维知识库."""
        query = input_data.get("query", "")
        category = input_data.get("category", "")

        logger.info("知识检索: query=%s, category=%s", query, category)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "search_knowledge",
                "query": query,
                "category": category,
                "results": [],
            }

        result = await self._tool_executor.execute(
            tool_name="search_knowledge",
            arguments={"query": query, "category": category},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "search_knowledge",
            "query": query,
            "category": category,
            "results": result.output if result.success else [],
        }

    async def _match_case(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """匹配历史故障案例."""
        symptoms = input_data.get("symptoms", "")
        service = input_data.get("service", "")

        logger.info("案例匹配: symptoms=%s, service=%s", symptoms, service)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "match_case",
                "symptoms": symptoms,
                "service": service,
                "matches": [],
            }

        result = await self._tool_executor.execute(
            tool_name="match_historical_cases",
            arguments={"symptoms": symptoms, "service": service},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "match_case",
            "symptoms": symptoms,
            "service": service,
            "matches": result.output if result.success else [],
        }

    async def _suggest_solution(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """基于知识库推荐解决方案."""
        issue = input_data.get("issue", "")
        context = input_data.get("context", "")

        logger.info("方案推荐: issue=%s", issue)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "suggest_solution",
                "issue": issue,
                "context": context,
                "suggestions": [],
            }

        result = await self._tool_executor.execute(
            tool_name="suggest_solution",
            arguments={"issue": issue, "context": context},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "suggest_solution",
            "issue": issue,
            "context": context,
            "suggestions": result.output if result.success else [],
        }
