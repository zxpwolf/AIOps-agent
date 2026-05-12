"""监控诊断 Skill — 云监控指标查询与 SLS 日志分析.

实现 MonitoringSkill 类，继承 SkillInstance，
通过 Tool_Executor 调用 MCP Server 或本地工具。
"""

from __future__ import annotations

import logging
from typing import Any

from aiops_agent.models.schemas import ValidationResult, WorkloadIdentity
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class MonitoringSkill(SkillInstance):
    """监控诊断技能.

    能力:
    - cloud_monitor_query: 云监控指标查询
    - sls_log_query: SLS 日志查询
    - metric_analysis: 指标分析
    """

    def __init__(self) -> None:
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行监控诊断任务."""
        action = input_data.get("action", "")

        if action == "query_metrics":
            return await self._query_metrics(input_data)
        elif action == "query_logs":
            return await self._query_logs(input_data)
        elif action == "analyze_metrics":
            return await self._analyze_metrics(input_data)
        else:
            return {"error": f"不支持的监控操作: {action}"}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        """校验输入参数."""
        errors = []
        if "action" not in input_data:
            errors.append("缺少必填参数: action")
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _get_identity(self) -> WorkloadIdentity:
        """获取 Skill 的 Workload Identity."""
        return WorkloadIdentity(
            workload_identity_arn="acs:agent-identity::system:workload-identity/monitoring-skill",
            agent_instance_id="monitoring-skill",
            identity_provider="ram",
            permissions=["cms:QueryMetricData", "cms:QueryMetricLast", "sls:GetLogs"],
        )

    async def _query_metrics(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """查询云监控指标."""
        namespace = input_data.get("namespace", "")
        metric_name = input_data.get("metric_name", "")
        instance_id = input_data.get("instance_id", "")

        logger.info(
            "查询云监控指标: namespace=%s, metric=%s, instance=%s",
            namespace, metric_name, instance_id,
        )

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "query_metrics",
                "namespace": namespace,
                "metric_name": metric_name,
                "instance_id": instance_id,
                "data": [],
            }

        result = await self._tool_executor.execute(
            tool_name="query_metric_last",
            arguments={
                "namespace": namespace,
                "metric_name": metric_name,
                "instance_id": instance_id,
            },
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "query_metrics",
            "namespace": namespace,
            "metric_name": metric_name,
            "instance_id": instance_id,
            "data": result.output if result.success else [],
        }

    async def _query_logs(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """查询 SLS 日志."""
        project = input_data.get("project", "")
        logstore = input_data.get("logstore", "")
        query = input_data.get("query", "")

        logger.info("查询 SLS 日志: project=%s, logstore=%s", project, logstore)

        if self._tool_executor is None:
            return {
                "status": "success",
                "action": "query_logs",
                "project": project,
                "logstore": logstore,
                "query": query,
                "logs": [],
            }

        result = await self._tool_executor.execute(
            tool_name="query_logs",
            arguments={"project": project, "logstore": logstore, "query": query},
            skill_identity=self._get_identity(),
        )

        return {
            "status": "success",
            "action": "query_logs",
            "project": project,
            "logstore": logstore,
            "query": query,
            "logs": result.output if result.success else [],
        }

    async def _analyze_metrics(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """分析指标趋势."""
        logger.info("分析指标趋势")
        return {
            "status": "success",
            "action": "analyze_metrics",
            "analysis": {},
        }
