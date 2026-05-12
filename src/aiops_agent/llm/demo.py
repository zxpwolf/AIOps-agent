"""Demo LLM Provider — 内置演示用 Provider，无需 API Key.

返回模拟的任务分解结果，用于本地开发和功能演示。
"""

from __future__ import annotations

import json
import re
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
