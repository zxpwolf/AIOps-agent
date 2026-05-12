"""Agent_Orchestrator — 核心编排器.

整合 TaskPlanner、SkillRegistry、ContextManager、ToolExecutor，
实现请求处理主入口、DAG 编排执行、失败处理、健康监控和 OpenTelemetry Trace。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from aiops_agent.context.manager import ContextManager
from aiops_agent.core.exceptions import (
    AgentError,
    SkillExecutionError,
    SkillNotFoundError,
)
from aiops_agent.core.state_machine import TaskStateMachine
from aiops_agent.core.task_planner import TaskPlanner
from aiops_agent.llm.provider import LLMProviderFactory
from aiops_agent.models.schemas import (
    AgentResponse,
    InteractionMode,
    Message,
    SubTask,
    TaskPlan,
    TaskStatus,
)
from aiops_agent.observability.metrics import AgentMetrics
from aiops_agent.observability.tracing import get_tracer, traced
from aiops_agent.security.security_guard import SecurityGuard
from aiops_agent.skills.registry import SkillRegistry
from aiops_agent.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# 健康监控配置
_HEALTH_CHECK_WINDOW_MINUTES = 10
_FAILURE_THRESHOLD = 5


class AgentOrchestrator:
    """核心编排器.

    职责:
    - 接收用户请求，调用 LLM 进行任务分解
    - 按 DAG 依赖关系编排执行，支持并行无依赖子任务
    - 子任务失败处理：记录失败原因、停止依赖任务、报告补救措施
    - 结构化错误响应
    - Skill 健康监控：10 分钟内连续失败 5 次标记为不健康
    - OpenTelemetry Trace 生成
    """

    def __init__(
        self,
        llm_factory: LLMProviderFactory,
        skill_registry: SkillRegistry,
        context_manager: ContextManager,
        tool_executor: ToolExecutor,
        security_guard: Optional[SecurityGuard] = None,
        metrics: Optional[AgentMetrics] = None,
    ) -> None:
        self._llm_factory = llm_factory
        self._skill_registry = skill_registry
        self._context_manager = context_manager
        self._tool_executor = tool_executor
        self._security_guard = security_guard
        self._metrics = metrics

        self._task_planner = TaskPlanner(llm_factory, skill_registry)

        # Skill 失败计数: {skill_name: [(timestamp, error)]}
        self._skill_failures: dict[str, list[tuple[float, str]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    @traced("orchestrator.process_request")
    async def process_request(
        self,
        user_input: str,
        session_id: str,
        user_id: str = "",
    ) -> AgentResponse:
        """处理用户请求的主入口.

        流程: 接收请求 → 输入校验 → 任务分解 → 技能路由 → 执行 → 响应

        Args:
            user_input: 用户的自然语言请求。
            session_id: 会话 ID。
            user_id: 用户 ID。

        Returns:
            AgentResponse 包含处理结果。
        """
        trace_id = _get_current_trace_id()
        start_time = time.monotonic()

        try:
            # 1. 输入安全校验
            sanitized_input = self._sanitize_input(user_input)

            # 2. 更新上下文
            session = await self._context_manager.get_session(session_id, user_id)
            await self._context_manager.update_context(
                session_id,
                Message(role="user", content=sanitized_input),
            )

            # 3. 切换到 Task 模式
            await self._context_manager.switch_mode(session_id, InteractionMode.TASK)

            # 4. 任务分解
            context = {
                "session_id": session_id,
                "resources": {k: v.model_dump() for k, v in session.resources.items()},
            }
            plan = await self._task_planner.decompose(sanitized_input, context)

            if not plan.sub_tasks:
                return AgentResponse(
                    success=False,
                    message="无法将请求分解为可执行的任务",
                    error_code="NO_TASKS",
                    suggestion="请尝试更具体地描述您的需求",
                    trace_id=trace_id,
                )

            # 5. 检查是否有无法映射的任务
            unmapped = [t for t in plan.sub_tasks if t.status == TaskStatus.FAILED]
            if unmapped and len(unmapped) == len(plan.sub_tasks):
                available = [s.skill_name for s in self._skill_registry.list_skills()]
                return AgentResponse(
                    success=False,
                    message="当前不支持该操作",
                    error_code="SKILL_NOT_FOUND",
                    suggestion=f"可用技能: {', '.join(available) if available else '无'}",
                    trace_id=trace_id,
                )

            # 6. 按 DAG 执行
            plan = await self._execute_plan(plan, session_id)

            # 7. 生成响应
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if self._metrics:
                self._metrics.record_task("completed", elapsed_ms)

            failed_tasks = [t for t in plan.sub_tasks if t.status == TaskStatus.FAILED]
            if failed_tasks:
                return AgentResponse(
                    success=False,
                    message=f"{len(failed_tasks)} 个子任务执行失败",
                    data={"plan": plan.model_dump(mode="json")},
                    error_code="PARTIAL_FAILURE",
                    suggestion="请检查失败任务的错误信息",
                    trace_id=trace_id,
                )

            return AgentResponse(
                success=True,
                message="任务执行完成",
                data={"plan": plan.model_dump(mode="json")},
                trace_id=trace_id,
            )

        except AgentError as exc:
            if self._metrics:
                self._metrics.record_task("failed")
            return AgentResponse(
                success=False,
                message=exc.message,
                error_code=exc.error_code,
                suggestion=exc.suggestion,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.exception("请求处理异常")
            if self._metrics:
                self._metrics.record_task("failed")
            return AgentResponse(
                success=False,
                message=str(exc),
                error_code="INTERNAL_ERROR",
                suggestion="请稍后重试",
                trace_id=trace_id,
            )
        finally:
            # 切回 Chat 模式
            await self._context_manager.switch_mode(session_id, InteractionMode.CHAT)

    # ------------------------------------------------------------------
    # 流式处理入口
    # ------------------------------------------------------------------

    async def process_request_stream(
        self,
        user_input: str,
        session_id: str,
        user_id: str = "",
    ):
        """流式处理用户请求，逐步 yield SSE 事件.

        事件类型:
        - planning: 任务分解开始/完成
        - task_start: 单个子任务开始执行
        - task_done: 单个子任务执行完成
        - error: 执行错误
        - done: 全部完成
        """
        trace_id = _get_current_trace_id()
        start_time = time.monotonic()

        try:
            # 1. 输入校验
            sanitized_input = self._sanitize_input(user_input)

            # 2. 更新上下文
            session = await self._context_manager.get_session(session_id, user_id)
            await self._context_manager.update_context(
                session_id,
                Message(role="user", content=sanitized_input),
            )
            await self._context_manager.switch_mode(session_id, InteractionMode.TASK)

            # 3. 任务分解
            yield {
                "type": "planning",
                "status": "started",
                "message": "正在分析任务...",
                "session_id": session_id,
                "trace_id": trace_id,
            }

            context = {
                "session_id": session_id,
                "resources": {k: v.model_dump() for k, v in session.resources.items()},
            }
            plan = await self._task_planner.decompose(sanitized_input, context)

            if not plan.sub_tasks:
                yield {
                    "type": "error",
                    "status": "failed",
                    "message": "无法将请求分解为可执行的任务",
                    "error_code": "NO_TASKS",
                    "suggestion": "请尝试更具体地描述您的需求",
                    "session_id": session_id,
                    "trace_id": trace_id,
                }
                return

            yield {
                "type": "planning",
                "status": "completed",
                "message": f"已生成 {len(plan.sub_tasks)} 个子任务",
                "total_tasks": len(plan.sub_tasks),
                "tasks": [
                    {"task_id": t.task_id, "skill_name": t.skill_name, "action": t.action}
                    for t in plan.sub_tasks
                ],
                "session_id": session_id,
                "trace_id": trace_id,
            }

            # 4. 按 DAG 执行（流式）
            levels = self._task_planner.topological_sort(plan)
            completed_count = 0
            total_tasks = len(plan.sub_tasks)
            failed_task_ids: set[str] = set()

            for level_idx, level_tasks in enumerate(levels):
                executable = []
                for task in level_tasks:
                    if task.status == TaskStatus.FAILED:
                        failed_task_ids.add(task.task_id)
                        continue
                    if any(dep in failed_task_ids for dep in task.dependencies):
                        task.status = TaskStatus.CANCELLED
                        task.error = "依赖的前置任务已失败"
                        failed_task_ids.add(task.task_id)
                        yield {
                            "type": "task_done",
                            "task_id": task.task_id,
                            "skill_name": task.skill_name,
                            "action": task.action,
                            "status": "cancelled",
                            "error": task.error,
                            "session_id": session_id,
                        }
                        continue
                    executable.append(task)

                if not executable:
                    continue

                # 顺序执行同层任务（流式需要逐个 yield）
                for task in executable:
                    yield {
                        "type": "task_start",
                        "task_id": task.task_id,
                        "skill_name": task.skill_name,
                        "action": task.action,
                        "level": f"{level_idx + 1}/{len(levels)}",
                        "session_id": session_id,
                    }

                    task.status = TaskStatus.RUNNING
                    try:
                        skill = await self._skill_registry.get_skill(task.skill_name)
                        if skill is None:
                            raise SkillNotFoundError(task.skill_name)

                        validation = await skill.validate(task.parameters)
                        if not validation.valid:
                            raise SkillExecutionError(
                                message=f"输入参数校验失败: {validation.errors}",
                                skill_name=task.skill_name,
                            )

                        result = await skill.execute(task.parameters)
                        task.result = result
                        task.status = TaskStatus.COMPLETED
                        completed_count += 1
                    except Exception as exc:
                        task.status = TaskStatus.FAILED
                        task.error = str(exc)
                        self._record_skill_failure(task.skill_name, str(exc))
                        failed_task_ids.add(task.task_id)

                    yield {
                        "type": "task_done",
                        "task_id": task.task_id,
                        "skill_name": task.skill_name,
                        "action": task.action,
                        "status": task.status.value,
                        "result": task.result,
                        "error": task.error,
                        "progress": f"{completed_count}/{total_tasks}",
                        "session_id": session_id,
                    }

                await self._context_manager.update_task_progress(
                    session_id,
                    percentage=(completed_count / total_tasks) * 100,
                    current_step=f"第 {level_idx + 1}/{len(levels)} 层完成",
                    total_steps=total_tasks,
                    completed_steps=completed_count,
                )

            # 5. 最终结果
            all_completed = all(t.status == TaskStatus.COMPLETED for t in plan.sub_tasks)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if self._metrics:
                self._metrics.record_task("completed" if all_completed else "failed", elapsed_ms)

            yield {
                "type": "done",
                "status": "completed" if all_completed else "partial_failure",
                "message": "任务执行完成" if all_completed else f"{len(failed_task_ids)} 个子任务执行失败",
                "success": all_completed,
                "elapsed_ms": round(elapsed_ms, 1),
                "data": {"plan": plan.model_dump(mode="json")},
                "session_id": session_id,
                "trace_id": trace_id,
            }

        except AgentError as exc:
            if self._metrics:
                self._metrics.record_task("failed")
            yield {
                "type": "error",
                "status": "failed",
                "message": exc.message,
                "error_code": exc.error_code,
                "suggestion": exc.suggestion,
                "session_id": session_id,
                "trace_id": trace_id,
            }
        except Exception as exc:
            logger.exception("流式请求处理异常")
            if self._metrics:
                self._metrics.record_task("failed")
            yield {
                "type": "error",
                "status": "failed",
                "message": str(exc),
                "error_code": "INTERNAL_ERROR",
                "suggestion": "请稍后重试",
                "session_id": session_id,
                "trace_id": trace_id,
            }
        finally:
            await self._context_manager.switch_mode(session_id, InteractionMode.CHAT)

    # ------------------------------------------------------------------
    # DAG 执行
    # ------------------------------------------------------------------

    async def _execute_plan(self, plan: TaskPlan, session_id: str) -> TaskPlan:
        """按 DAG 依赖关系执行任务计划，支持并行执行无依赖子任务."""
        levels = self._task_planner.topological_sort(plan)
        total_tasks = len(plan.sub_tasks)
        completed_count = 0

        # 跟踪失败的任务 ID
        failed_task_ids: set[str] = set()

        for level_idx, level_tasks in enumerate(levels):
            # 过滤掉依赖已失败任务的子任务
            executable = []
            for task in level_tasks:
                if task.status == TaskStatus.FAILED:
                    failed_task_ids.add(task.task_id)
                    continue
                if any(dep in failed_task_ids for dep in task.dependencies):
                    task.status = TaskStatus.CANCELLED
                    task.error = "依赖的前置任务已失败"
                    failed_task_ids.add(task.task_id)
                    continue
                executable.append(task)

            if not executable:
                continue

            # 并行执行同层无依赖任务（最多 10 个并发）
            semaphore = asyncio.Semaphore(10)

            async def _run_with_semaphore(task: SubTask) -> None:
                async with semaphore:
                    await self._route_to_skill(task)

            await asyncio.gather(
                *[_run_with_semaphore(t) for t in executable],
                return_exceptions=True,
            )

            # 更新进度
            for task in executable:
                if task.status == TaskStatus.FAILED:
                    failed_task_ids.add(task.task_id)
                elif task.status == TaskStatus.COMPLETED:
                    completed_count += 1

            await self._context_manager.update_task_progress(
                session_id,
                percentage=(completed_count / total_tasks) * 100,
                current_step=f"第 {level_idx + 1}/{len(levels)} 层完成",
                total_steps=total_tasks,
                completed_steps=completed_count,
            )

        # 更新 plan 状态
        all_completed = all(
            t.status == TaskStatus.COMPLETED for t in plan.sub_tasks
        )
        plan.status = TaskStatus.COMPLETED if all_completed else TaskStatus.FAILED

        return plan

    async def _route_to_skill(self, sub_task: SubTask) -> None:
        """将子任务路由到对应的 Skill 执行."""
        sm = TaskStateMachine(sub_task.task_id)

        try:
            sm.transition(TaskStatus.RUNNING)
            sub_task.status = TaskStatus.RUNNING

            # 获取技能实例
            skill = await self._skill_registry.get_skill(sub_task.skill_name)
            if skill is None:
                raise SkillNotFoundError(
                    message=f"技能 '{sub_task.skill_name}' 未注册",
                    requested_capability=sub_task.skill_name,
                    available_skills=[
                        s.skill_name for s in self._skill_registry.list_skills()
                    ],
                )

            # 校验输入
            validation = await skill.validate(sub_task.parameters)
            if not validation.valid:
                raise SkillExecutionError(
                    message=f"输入参数校验失败: {validation.errors}",
                    skill_name=sub_task.skill_name,
                )

            # 执行
            result = await skill.execute(sub_task.parameters)

            sub_task.result = result
            sub_task.status = TaskStatus.COMPLETED
            sm.transition(TaskStatus.COMPLETED)

        except Exception as exc:
            sub_task.status = TaskStatus.FAILED
            sub_task.error = str(exc)

            # 记录失败
            self._record_skill_failure(sub_task.skill_name, str(exc))

            logger.error(
                "子任务执行失败: task_id=%s, skill=%s, error=%s",
                sub_task.task_id,
                sub_task.skill_name,
                exc,
            )

    # ------------------------------------------------------------------
    # 健康监控
    # ------------------------------------------------------------------

    def _record_skill_failure(self, skill_name: str, error: str) -> None:
        """记录 Skill 失败，检查是否需要标记为不健康."""
        now = time.time()
        self._skill_failures[skill_name].append((now, error))

        # 清理过期记录
        cutoff = now - (_HEALTH_CHECK_WINDOW_MINUTES * 60)
        self._skill_failures[skill_name] = [
            (ts, err) for ts, err in self._skill_failures[skill_name]
            if ts > cutoff
        ]

        # 检查阈值
        if len(self._skill_failures[skill_name]) >= _FAILURE_THRESHOLD:
            logger.warning(
                "技能 '%s' 在 %d 分钟内连续失败 %d 次，标记为不健康",
                skill_name,
                _HEALTH_CHECK_WINDOW_MINUTES,
                len(self._skill_failures[skill_name]),
            )
            # 异步标记（不阻塞当前执行）
            asyncio.create_task(self._skill_registry.mark_unhealthy(skill_name))

            if self._metrics:
                self._metrics.record_security_event("skill_unhealthy")

    # ------------------------------------------------------------------
    # 输入安全防护
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_input(user_input: str) -> str:
        """用户输入参数校验和注入防护.

        检测并防护:
        - Prompt 注入攻击
        - 命令注入攻击
        """
        if not user_input or not user_input.strip():
            raise AgentError(
                message="输入不能为空",
                error_code="EMPTY_INPUT",
            )

        # 长度限制
        if len(user_input) > 10000:
            raise AgentError(
                message="输入长度超过限制（最大 10000 字符）",
                error_code="INPUT_TOO_LONG",
            )

        # 基本注入检测模式
        injection_patterns = [
            "ignore previous instructions",
            "ignore all instructions",
            "disregard previous",
            "system prompt",
            "you are now",
            "act as",
        ]

        lower_input = user_input.lower()
        for pattern in injection_patterns:
            if pattern in lower_input:
                logger.warning("检测到可疑 prompt 注入: %s", pattern)
                # 不拒绝，但记录告警
                break

        # 命令注入检测
        command_patterns = [";", "&&", "||", "`", "$(", "${"]
        for pattern in command_patterns:
            if pattern in user_input:
                logger.warning("检测到可疑命令注入字符: %s", pattern)
                break

        return user_input.strip()


def _get_current_trace_id() -> str:
    """获取当前 OpenTelemetry trace ID."""
    from opentelemetry import trace as otel_trace

    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return ""
