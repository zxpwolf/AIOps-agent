"""Demo LLM Provider — 内置演示用 Provider，无需 API Key.

返回模拟的任务分解结果，用于本地开发和功能演示。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from aiops_agent.llm.provider import ChatResponse, LLMProvider
from aiops_agent.models.schemas import Message


# 关键词到技能的映射
_SKILL_MAP = {
    "监控": "monitoring",
    "指标": "monitoring",
    "cpu": "monitoring",
    "内存": "monitoring",
    "日志": "monitoring",
    "sls": "monitoring",
    "告警": "monitoring",
    "排查": "troubleshooting",
    "故障": "troubleshooting",
    "健康": "troubleshooting",
    "网络": "troubleshooting",
    "慢查询": "troubleshooting",
    "诊断": "troubleshooting",
    "变更": "change_management",
    "回滚": "change_management",
    "风险": "change_management",
    "扩容": "change_management",
}


class DemoProvider(LLMProvider):
    """演示用 LLM Provider，基于关键词匹配生成任务分解."""

    @property
    def provider_name(self) -> str:
        return "demo"

    async def chat(
        self, messages: list[Message], **kwargs: Any
    ) -> ChatResponse:
        user_msg = ""
        for m in reversed(messages):
            if m.role == "user":
                user_msg = m.content
                break

        tasks = self._decompose(user_msg)
        return ChatResponse(
            content=json.dumps(tasks, ensure_ascii=False),
            model="demo",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    async def chat_stream(
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[str]:
        """模拟流式响应 — 逐句返回分析结果，基于实际消息内容生成上下文感知的响应."""
        # Extract user message to provide context-aware mock response
        user_msg = ""
        for m in reversed(messages):
            if m.role == "user":
                user_msg = m.content
                break

        # Build context-aware analysis parts
        analysis_parts = self._build_analysis_parts(user_msg)

        for part in analysis_parts:
            yield part
            await asyncio.sleep(0.05)  # 模拟流式延迟

    def _build_analysis_parts(self, user_msg: str) -> list[str]:
        """根据用户消息内容构建分析响应片段."""
        # Check if this is a synthesis prompt with task results
        if "## 任务执行结果" not in user_msg:
            # Plain user query — reference the actual question
            return [
                "## 分析结果\n\n",
                f"您的问题是：\"{user_msg.strip()}\"\n\n",
                "当前 Demo 模式无法提供深度分析，",
                "但在真实环境中，我会基于您的请求进行智能处理。",
            ]

        # Parse the synthesis prompt structure
        original_request = ""
        req_match = re.search(r"用户原始请求: (.+?)(?:\n\n## 任务执行结果|\Z)", user_msg, re.DOTALL)
        if req_match:
            original_request = req_match.group(1).strip()

        # Parse individual task blocks
        tasks = self._parse_task_results(user_msg)

        # Build structured response referencing actual data
        parts: list[str] = []
        parts.append("## 分析结果\n\n")
        parts.append(f"针对您的请求 \"{original_request}\"，")
        parts.append(f"共执行了 {len(tasks)} 个子任务，")
        parts.append("以下是详细分析：\n\n")

        # Summarize each task with actual data
        for idx, task in enumerate(tasks, 1):
            parts.append(f"### {idx}. {task['skill']} · {task['action']}\n\n")
            parts.append(f"- **执行状态**: {task['status']}\n")

            # Reference actual result data if available
            if task["result"]:
                result_summary = self._summarize_result(task["result"])
                parts.append(f"- **数据发现**: {result_summary}\n")
            else:
                parts.append("- **数据发现**: 无返回数据\n")

            parts.append("\n")

        # Generate recommendations based on task statuses
        recommendations = self._generate_recommendations(tasks)
        parts.append("### 综合建议\n\n")
        parts.append(recommendations)
        parts.append("\n\n")
        parts.append("---\n")
        parts.append("*以上为基于实际任务执行结果的分析，由 DemoProvider 生成。*")

        return parts

    def _parse_task_results(self, user_msg: str) -> list[dict]:
        """从合成提示中解析任务结果."""
        tasks: list[dict] = []
        # Split by task markers and parse each block
        task_blocks = re.split(r"\n### 任务: ", user_msg)
        for block in task_blocks[1:]:  # Skip content before first task
            task: dict[str, Any] = {"skill": "", "action": "", "status": "", "result": None}

            # Extract skill and action
            header_match = re.match(r"(.+?) · (.+?)\n", block)
            if header_match:
                task["skill"] = header_match.group(1).strip()
                task["action"] = header_match.group(2).strip()

            # Extract status
            status_match = re.search(r"状态: (\w+)", block)
            if status_match:
                task["status"] = status_match.group(1).strip()

            # Extract JSON result
            json_match = re.search(r"结果:\n```json\n(.+?)\n```", block, re.DOTALL)
            if json_match:
                try:
                    task["result"] = json.loads(json_match.group(1))
                except (json.JSONDecodeError, TypeError):
                    task["result"] = json_match.group(1)

            tasks.append(task)
        return tasks

    def _summarize_result(self, result: Any) -> str:
        """对任务结果数据进行简要概括."""
        if isinstance(result, dict):
            # Look for common metric keys
            metric_keys = [k for k in result.keys() if any(
                kw in k.lower() for kw in ["cpu", "memory", "mem", "disk", "load", "metric", "count", "usage", "rate", "qps", "latency"]
            )]
            if metric_keys:
                snippets = []
                for k in metric_keys[:3]:
                    val = result[k]
                    if isinstance(val, (int, float)):
                        snippets.append(f"{k}={val}")
                    elif isinstance(val, str) and len(val) < 50:
                        snippets.append(f"{k}={val}")
                    else:
                        snippets.append(f"{k}=...")
                return f"检测到指标数据: {', '.join(snippets)}"

            # Check for status/health info
            if "status" in result or "healthy" in result or "state" in result:
                state = result.get("status") or result.get("healthy") or result.get("state")
                return f"系统状态: {state}"

            # Check for list data
            if any(isinstance(v, list) for v in result.values()):
                list_keys = [k for k, v in result.items() if isinstance(v, list)]
                return f"包含 {len(list_keys)} 组列表数据（如 {list_keys[0]}）"

            # Generic dict summary
            return f"包含字段: {', '.join(list(result.keys())[:5])}"

        if isinstance(result, list):
            return f"返回 {len(result)} 条记录"

        result_str = str(result)
        if len(result_str) > 100:
            result_str = result_str[:100] + "..."
        return result_str

    def _generate_recommendations(self, tasks: list[dict]) -> str:
        """基于任务状态和数据生成建议."""
        completed = sum(1 for t in tasks if t["status"] == "completed")
        failed = sum(1 for t in tasks if t["status"] == "failed")
        pending = sum(1 for t in tasks if t["status"] == "pending")

        lines: list[str] = []
        if failed > 0:
            lines.append(f"- 有 {failed} 个任务执行失败，建议检查相关服务状态并重试。")
        if pending > 0:
            lines.append(f"- 有 {pending} 个任务仍在等待执行，请关注后续结果。")
        if completed == len(tasks) and len(tasks) > 0:
            lines.append("- 所有任务均已成功完成，系统当前运行正常。")

        # Look for specific data patterns in results
        has_metrics = False
        has_issues = False
        for task in tasks:
            if task["result"] and isinstance(task["result"], dict):
                # Check for high resource usage indicators
                for k, v in task["result"].items():
                    if isinstance(v, (int, float)):
                        if any(kw in k.lower() for kw in ["cpu", "memory", "mem", "disk", "load"]):
                            has_metrics = True
                            if v > 80:  # noqa: PLR2004
                                has_issues = True
                                lines.append(f"- 注意：任务 {task['skill']} 检测到 {k} 使用率较高（{v}%），建议排查。")

        if has_metrics and not has_issues:
            lines.append("- 各项资源指标处于正常范围，建议持续监控趋势变化。")
        elif not has_metrics and completed > 0:
            lines.append("- 任务执行完毕，可根据返回数据进一步分析。")

        if not lines:
            lines.append("- 请根据上述任务结果进行进一步判断和操作。")

        return "\n".join(lines)

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        resp = await self.chat([Message(role="user", content=prompt)])
        return resp.content

    async def embed(
        self, texts: list[str], **kwargs: Any
    ) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def _decompose(self, text: str) -> list[dict]:
        lower = text.lower()
        matched_skills: list[str] = []

        for keyword, skill in _SKILL_MAP.items():
            if keyword in lower and skill not in matched_skills:
                matched_skills.append(skill)

        if not matched_skills:
            matched_skills = ["monitoring"]

        tasks = []
        for i, skill in enumerate(matched_skills, 1):
            action = self._infer_action(skill, text)
            params = self._extract_params(text)
            params["action"] = action
            tasks.append({
                "task_id": f"t{i}",
                "skill_name": skill,
                "action": action,
                "parameters": params,
                "dependencies": [f"t{i-1}"] if i > 1 else [],
            })
        return tasks

    @staticmethod
    def _infer_action(skill: str, text: str) -> str:
        actions = {
            "monitoring": "query_metrics",
            "troubleshooting": "ecs_health_check",
            "change_management": "risk_assessment",
        }
        return actions.get(skill, "execute")

    @staticmethod
    def _extract_params(text: str) -> dict:
        params: dict[str, str] = {}
        # 提取 ECS 实例 ID
        m = re.search(r"i-[a-z0-9]{8,17}", text)
        if m:
            params["instance_id"] = m.group()
        # 提取 RDS 实例 ID
        m = re.search(r"rm-[a-z0-9]{8,17}", text)
        if m:
            params["instance_id"] = m.group()
        return params
