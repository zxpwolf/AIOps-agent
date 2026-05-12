"""任务分解与 DAG 构建.

调用 LLM 将自然语言请求分解为 TaskPlan，
构建子任务依赖关系的 DAG 并进行拓扑排序。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from aiops_agent.llm.provider import LLMProviderFactory
from aiops_agent.models.schemas import Message, SubTask, TaskPlan, TaskStatus
from aiops_agent.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_DECOMPOSE_SYSTEM_PROMPT = """你是一个 AIOps 任务分解助手。根据用户的运维请求，将其分解为一个或多个子任务。

每个子任务需要包含:
- task_id: 唯一标识（如 "t1", "t2"）
- skill_name: 对应的技能名称（monitoring, troubleshooting, change_management 等）
- action: 具体操作
- parameters: 操作参数
- dependencies: 依赖的其他子任务 ID 列表

请以 JSON 格式返回子任务列表。"""


class TaskPlanner:
    """任务分解与 DAG 构建.

    职责:
    - 调用 LLM 将自然语言请求分解为 TaskPlan
    - 构建子任务依赖关系的 DAG
    - 拓扑排序确定执行顺序
    - 无法映射到 Skill 时生成提示信息
    """

    def __init__(
        self,
        llm_factory: LLMProviderFactory,
        skill_registry: SkillRegistry,
    ) -> None:
        self._llm = llm_factory
        self._skill_registry = skill_registry

    async def decompose(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
    ) -> TaskPlan:
        """将自然语言请求分解为任务计划.

        Args:
            user_input: 用户的自然语言请求。
            context: 对话上下文。

        Returns:
            TaskPlan 包含分解后的子任务列表。
        """
        plan_id = str(uuid.uuid4())

        # 构建 LLM 消息
        messages = [
            Message(role="system", content=_DECOMPOSE_SYSTEM_PROMPT),
        ]

        if context:
            context_str = json.dumps(context, ensure_ascii=False, default=str)
            messages.append(
                Message(role="system", content=f"当前上下文: {context_str}")
            )

        # 添加可用技能信息
        available_skills = self._skill_registry.list_skills()
        if available_skills:
            skills_info = "\n".join(
                f"- {s.skill_name}: {s.description} (capabilities: {s.capabilities})"
                for s in available_skills
            )
            messages.append(
                Message(role="system", content=f"可用技能:\n{skills_info}")
            )

        messages.append(Message(role="user", content=user_input))

        # 调用 LLM
        try:
            response = await self._llm.chat(messages)
            sub_tasks = self._parse_subtasks(response.content, plan_id)
        except Exception as exc:
            logger.error("LLM 任务分解失败: %s", exc)
            sub_tasks = []

        # 验证技能映射
        validated_tasks = await self._validate_skill_mapping(sub_tasks)

        plan = TaskPlan(
            plan_id=plan_id,
            user_request=user_input,
            sub_tasks=validated_tasks,
            context=context or {},
        )

        logger.info(
            "任务分解完成: plan_id=%s, sub_tasks=%d",
            plan_id,
            len(validated_tasks),
        )
        return plan

    def topological_sort(self, plan: TaskPlan) -> list[list[SubTask]]:
        """对子任务进行拓扑排序，返回可并行执行的层级列表.

        Returns:
            每个元素是一组可并行执行的子任务。
        """
        # 构建邻接表和入度
        in_degree: dict[str, int] = {}
        dependents: dict[str, list[str]] = {}
        task_map: dict[str, SubTask] = {}

        for task in plan.sub_tasks:
            task_map[task.task_id] = task
            in_degree[task.task_id] = len(task.dependencies)
            for dep in task.dependencies:
                if dep not in dependents:
                    dependents[dep] = []
                dependents[dep].append(task.task_id)

        # BFS 拓扑排序
        levels: list[list[SubTask]] = []
        current_level = [
            tid for tid, deg in in_degree.items() if deg == 0
        ]

        while current_level:
            levels.append([task_map[tid] for tid in current_level])
            next_level = []
            for tid in current_level:
                for dep_tid in dependents.get(tid, []):
                    in_degree[dep_tid] -= 1
                    if in_degree[dep_tid] == 0:
                        next_level.append(dep_tid)
            current_level = next_level

        return levels

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _parse_subtasks(self, llm_output: str, plan_id: str) -> list[SubTask]:
        """解析 LLM 输出为子任务列表."""
        try:
            # 尝试提取 JSON
            content = llm_output.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content)
            if isinstance(data, dict):
                data = data.get("sub_tasks", data.get("tasks", [data]))
            if not isinstance(data, list):
                data = [data]

            sub_tasks = []
            for item in data:
                sub_tasks.append(
                    SubTask(
                        task_id=item.get("task_id", f"t{len(sub_tasks) + 1}"),
                        skill_name=item.get("skill_name", ""),
                        action=item.get("action", ""),
                        parameters=item.get("parameters", {}),
                        dependencies=item.get("dependencies", []),
                    )
                )
            return sub_tasks

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("LLM 输出解析失败: %s", exc)
            return []

    async def _validate_skill_mapping(
        self,
        sub_tasks: list[SubTask],
    ) -> list[SubTask]:
        """验证子任务的技能映射是否有效."""
        validated = []
        for task in sub_tasks:
            skill = await self._skill_registry.get_skill(task.skill_name)
            if skill is None:
                logger.warning(
                    "子任务 '%s' 的技能 '%s' 未注册",
                    task.task_id,
                    task.skill_name,
                )
                task.status = TaskStatus.FAILED
                task.error = f"技能 '{task.skill_name}' 未注册"
            validated.append(task)
        return validated
