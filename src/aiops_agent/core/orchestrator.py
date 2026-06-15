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
from aiops_agent.core.hooks import HookContext, HookEvent, HookRegistry
from aiops_agent.core.state_machine import TaskStateMachine
from aiops_agent.core.task_planner import TaskPlanner
from aiops_agent.llm.provider import LLMProviderFactory
from aiops_agent.models.schemas import (
    AgentResponse,
    InteractionMode,
    LoopConfig,
    LoopTerminal,
    LoopTerminalReason,
    Message,
    SubTask,
    TaskPlan,
    TaskStatus,
)
from aiops_agent.observability.metrics import AgentMetrics
from aiops_agent.observability.metrics_store import MetricsStore, RequestEvent, SkillCallEvent
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
    - Re-entrant agent loop：LLM 决定是否继续执行
    - 子任务失败处理：记录失败原因、停止依赖任务、报告补救措施
    - 结构化错误响应
    - Skill 健康监控：10 分钟内连续失败 5 次标记为不健康
    - Hook 系统：生命周期拦截器
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
        metrics_store: MetricsStore | None = None,
        hook_registry: Optional[HookRegistry] = None,
        loop_config: Optional[LoopConfig] = None,
    ) -> None:
        self._llm_factory = llm_factory
        self._skill_registry = skill_registry
        self._context_manager = context_manager
        self._tool_executor = tool_executor
        self._security_guard = security_guard
        self._metrics = metrics
        self._metrics_store = metrics_store
        self._hook_registry = hook_registry or HookRegistry()
        self._loop_config = loop_config or LoopConfig()

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
        """处理用户请求的主入口 — Re-entrant agent loop.

        流程: 接收请求 → 输入校验 → [loop: 任务分解 → 技能路由 → 执行 → LLM 判断继续?] → 响应

        Re-entrant loop 遵循 Claude Code 的 generator loop pattern:
        每轮执行完成后，LLM 审查结果并决定是否需要更多操作。
        循环终止条件: LLM 判断完成 / 达到 max_turns / Token 预算耗尽 / 不可恢复错误。

        Args:
            user_input: 用户的自然语言请求。
            session_id: 会话 ID。
            user_id: 用户 ID。

        Returns:
            AgentResponse 包含处理结果和 LoopTerminal 终止状态。
        """
        trace_id = _get_current_trace_id()
        start_time = time.monotonic()
        self._llm_factory.clear_pending_llm_calls()

        logger.info(
            "开始处理请求: session_id=%s, user_id=%s, input=%s",
            session_id,
            user_id,
            user_input[:100],
        )

        request_status = "failed"
        sanitized_input = ""
        loop_terminal: LoopTerminal | None = None
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

            # 4. 触发 pre_decompose hook
            hook_ctx = HookContext(
                event=HookEvent.PRE_DECOMPOSE,
                session_id=session_id,
                user_input=sanitized_input,
            )
            await self._hook_registry.trigger(HookEvent.PRE_DECOMPOSE, hook_ctx)

            # 5. 任务分解
            context = {
                "session_id": session_id,
                "resources": {k: v.model_dump() for k, v in session.resources.items()},
            }
            logger.info("开始任务分解: input=%s", sanitized_input[:100])
            plan = await self._task_planner.decompose(sanitized_input, context)
            logger.info(
                "任务分解完成: plan_id=%s, sub_tasks=%d, tasks=%s",
                plan.plan_id,
                len(plan.sub_tasks),
                [(t.task_id, t.skill_name, t.action) for t in plan.sub_tasks],
            )

            # 6. 触发 post_decompose hook
            hook_ctx = HookContext(
                event=HookEvent.POST_DECOMPOSE,
                session_id=session_id,
                user_input=sanitized_input,
                plan=plan,
            )
            await self._hook_registry.trigger(HookEvent.POST_DECOMPOSE, hook_ctx)

            if not plan.sub_tasks:
                request_status = "failed"
                loop_terminal = LoopTerminal(
                    reason=LoopTerminalReason.UNRECOVERABLE_ERROR,
                    message="无法将请求分解为可执行的任务",
                    turn_count=0,
                )
                return AgentResponse(
                    success=False,
                    message="无法将请求分解为可执行的任务",
                    error_code="NO_TASKS",
                    suggestion="请尝试更具体地描述您的需求",
                    trace_id=trace_id,
                    data={"terminal": loop_terminal.model_dump(mode="json")} if loop_terminal else None,
                )

            # 7. 检查是否有无法映射的任务
            unmapped = [t for t in plan.sub_tasks if t.status == TaskStatus.FAILED]
            if unmapped and len(unmapped) == len(plan.sub_tasks):
                available = [s.skill_name for s in self._skill_registry.list_skills()]
                request_status = "failed"
                loop_terminal = LoopTerminal(
                    reason=LoopTerminalReason.UNRECOVERABLE_ERROR,
                    message="当前不支持该操作",
                    turn_count=0,
                )
                return AgentResponse(
                    success=False,
                    message="当前不支持该操作",
                    error_code="SKILL_NOT_FOUND",
                    suggestion=f"可用技能: {', '.join(available) if available else '无'}",
                    trace_id=trace_id,
                    data={"terminal": loop_terminal.model_dump(mode="json")} if loop_terminal else None,
                )

            # 8. Re-entrant agent loop
            turn_count = 0
            all_plans: list[TaskPlan] = []
            while turn_count < self._loop_config.max_turns:
                turn_count += 1
                logger.info("Agent loop turn %d/%d", turn_count, self._loop_config.max_turns)

                # 触发 pre_loop_turn hook
                hook_ctx = HookContext(
                    event=HookEvent.PRE_LOOP_TURN,
                    session_id=session_id,
                    user_input=sanitized_input,
                    plan=plan,
                    turn_count=turn_count,
                )
                await self._hook_registry.trigger(HookEvent.PRE_LOOP_TURN, hook_ctx)

                # 按 DAG 执行
                plan = await self._execute_plan(plan, session_id)
                all_plans.append(plan)

                # 检查循环终止条件
                elapsed_ms = (time.monotonic() - start_time) * 1000
                terminal = self._check_loop_terminal(
                    plan, turn_count, elapsed_ms, start_time
                )
                if terminal is not None:
                    loop_terminal = terminal
                    break

                # LLM 判断是否需要继续
                should_continue = await self._should_continue_loop(
                    sanitized_input, plan, turn_count, session_id
                )
                if not should_continue:
                    loop_terminal = LoopTerminal(
                        reason=LoopTerminalReason.COMPLETED,
                        message="LLM 判断任务已完成",
                        turn_count=turn_count,
                        elapsed_ms=elapsed_ms,
                    )
                    break

                # 继续循环：LLM 生成新的补充任务
                logger.info("LLM 决定继续执行, 生成补充任务")
                supplementary_plan = await self._task_planner.decompose(
                    f"基于前一轮执行结果，继续处理: {sanitized_input}",
                    {"session_id": session_id, "previous_results": self._summarize_plan_results(plan)},
                )
                if supplementary_plan.sub_tasks:
                    # 将补充任务追加到现有计划
                    plan = TaskPlan(
                        plan_id=plan.plan_id,
                        user_request=plan.user_request,
                        sub_tasks=plan.sub_tasks + supplementary_plan.sub_tasks,
                        context=plan.context,
                        status=TaskStatus.PENDING,
                    )

                # 触发 post_loop_turn hook
                hook_ctx = HookContext(
                    event=HookEvent.POST_LOOP_TURN,
                    session_id=session_id,
                    user_input=sanitized_input,
                    plan=plan,
                    turn_count=turn_count,
                )
                await self._hook_registry.trigger(HookEvent.POST_LOOP_TURN, hook_ctx)

            # 如果循环因 max_turns 终止，记录终端状态
            if loop_terminal is None:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                loop_terminal = LoopTerminal(
                    reason=LoopTerminalReason.MAX_TURNS,
                    message=f"达到最大循环轮数 {self._loop_config.max_turns}",
                    turn_count=turn_count,
                    elapsed_ms=elapsed_ms,
                )

            # 触发 on_terminal hook
            hook_ctx = HookContext(
                event=HookEvent.ON_TERMINAL,
                session_id=session_id,
                user_input=sanitized_input,
                plan=plan,
                turn_count=turn_count,
                extra={"terminal": loop_terminal.model_dump(mode="json")},
            )
            await self._hook_registry.trigger(HookEvent.ON_TERMINAL, hook_ctx)

            # 9. 生成响应
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if self._metrics:
                self._metrics.record_task("completed", elapsed_ms)

            failed_tasks = [t for t in plan.sub_tasks if t.status == TaskStatus.FAILED]
            if failed_tasks:
                request_status = "failed"
                return AgentResponse(
                    success=False,
                    message=f"{len(failed_tasks)} 个子任务执行失败",
                    data={"plan": plan.model_dump(mode="json"), "terminal": loop_terminal.model_dump(mode="json")},
                    error_code="PARTIAL_FAILURE",
                    suggestion="请检查失败任务的错误信息",
                    trace_id=trace_id,
                )

            request_status = "completed"
            return AgentResponse(
                success=True,
                message="任务执行完成",
                data={"plan": plan.model_dump(mode="json"), "terminal": loop_terminal.model_dump(mode="json")},
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
            if self._metrics_store:
                try:
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    llm_calls = self._llm_factory.get_pending_llm_calls()
                    self._llm_factory.clear_pending_llm_calls()
                    event = RequestEvent(
                        timestamp=datetime.now(timezone.utc),
                        session_id=session_id,
                        trace_id=trace_id,
                        status=request_status,
                        duration_ms=elapsed_ms,
                        llm_calls=llm_calls,
                        skill_calls=[],
                        user_input=sanitized_input[:100] if sanitized_input else user_input[:100],
                    )
                    await self._metrics_store.record_request(event)
                except Exception:
                    logger.exception("记录请求指标失败")
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
        self._llm_factory.clear_pending_llm_calls()
        sanitized_input = ""
        skill_call_events: list[SkillCallEvent] = []

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

            # 4. 按 DAG 执行（流式）— 并行执行 concurrency_safe 的同层任务
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

                # 分离并发安全任务和不安全任务
                parallel_tasks: list[SubTask] = []
                sequential_tasks: list[SubTask] = []
                for task in executable:
                    skill = await self._skill_registry.get_skill(task.skill_name)
                    if skill is not None and skill.concurrency_safe:
                        parallel_tasks.append(task)
                    else:
                        sequential_tasks.append(task)

                # 并发安全任务：并行执行，结果通过 asyncio.Queue 收集后逐个 yield
                if parallel_tasks:
                    event_queue: asyncio.Queue[dict] = asyncio.Queue()

                    async def _execute_parallel_task(task: SubTask) -> None:
                        """并行执行单个并发安全任务，将事件放入队列."""
                        skill_start = time.monotonic()
                        try:
                            skill = await self._skill_registry.get_skill(task.skill_name)
                            if skill is None:
                                raise SkillNotFoundError(task.skill_name)
                            input_data = {"action": task.action, **task.parameters}
                            validation = await skill.validate(input_data)
                            if not validation.valid:
                                raise SkillExecutionError(
                                    message=f"输入参数校验失败: {validation.errors}",
                                    skill_name=task.skill_name,
                                )
                            result = await skill.execute(input_data)
                            task.result = result
                            task.status = TaskStatus.COMPLETED
                            await self._skill_registry.mark_healthy(task.skill_name)
                            skill_duration = (time.monotonic() - skill_start) * 1000
                            skill_call_events.append(SkillCallEvent(
                                skill_name=task.skill_name,
                                action=task.action,
                                duration_ms=skill_duration,
                                success=True,
                            ))
                            event_queue.put_nowait({"task": task, "success": True, "duration": skill_duration})
                        except Exception as exc:
                            task.status = TaskStatus.FAILED
                            task.error = str(exc)
                            skill_duration = (time.monotonic() - skill_start) * 1000
                            skill_call_events.append(SkillCallEvent(
                                skill_name=task.skill_name,
                                action=task.action,
                                duration_ms=skill_duration,
                                success=False,
                                error=str(exc),
                            ))
                            self._record_skill_failure(task.skill_name, str(exc))
                            failed_task_ids.add(task.task_id)
                            event_queue.put_nowait({"task": task, "success": False, "error": str(exc)})

                    # Emit start events for all parallel tasks
                    for task in parallel_tasks:
                        yield {
                            "type": "task_start",
                            "task_id": task.task_id,
                            "skill_name": task.skill_name,
                            "action": task.action,
                            "level": f"{level_idx + 1}/{len(levels)}",
                            "mode": "parallel",
                            "session_id": session_id,
                        }
                        task.status = TaskStatus.RUNNING

                    # Launch all parallel tasks concurrently
                    parallel_done_count = len(parallel_tasks)
                    await asyncio.gather(
                        *[_execute_parallel_task(t) for t in parallel_tasks],
                        return_exceptions=True,
                    )

                    # Yield results in original order
                    for task in parallel_tasks:
                        if task.status == TaskStatus.COMPLETED:
                            completed_count += 1
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

                # 不安全任务：顺序执行，逐个 yield
                for task in sequential_tasks:
                    yield {
                        "type": "task_start",
                        "task_id": task.task_id,
                        "skill_name": task.skill_name,
                        "action": task.action,
                        "level": f"{level_idx + 1}/{len(levels)}",
                        "mode": "sequential",
                        "session_id": session_id,
                    }

                    task.status = TaskStatus.RUNNING
                    skill_start = time.monotonic()
                    try:
                        skill = await self._skill_registry.get_skill(task.skill_name)
                        if skill is None:
                            raise SkillNotFoundError(task.skill_name)

                        logger.info(
                            "开始校验任务: task_id=%s, skill=%s, action=%s, parameters=%s",
                            task.task_id,
                            task.skill_name,
                            task.action,
                            task.parameters,
                        )
                        input_data = {"action": task.action, **task.parameters}
                        validation = await skill.validate(input_data)
                        if not validation.valid:
                            logger.error(
                                "任务校验失败: task_id=%s, skill=%s, errors=%s, parameters=%s",
                                task.task_id,
                                task.skill_name,
                                validation.errors,
                                input_data,
                            )
                            raise SkillExecutionError(
                                message=f"输入参数校验失败: {validation.errors}",
                                skill_name=task.skill_name,
                            )

                        logger.info(
                            "开始执行任务: task_id=%s, skill=%s, action=%s",
                            task.task_id,
                            task.skill_name,
                            task.action,
                        )
                        result = await skill.execute(input_data)
                        logger.info(
                            "任务执行成功: task_id=%s, skill=%s, result_keys=%s",
                            task.task_id,
                            task.skill_name,
                            list(result.keys()) if isinstance(result, dict) else type(result),
                        )
                        task.result = result
                        task.status = TaskStatus.COMPLETED
                        completed_count += 1
                        await self._skill_registry.mark_healthy(task.skill_name)
                        skill_duration = (time.monotonic() - skill_start) * 1000
                        skill_call_events.append(SkillCallEvent(
                            skill_name=task.skill_name,
                            action=task.action,
                            duration_ms=skill_duration,
                            success=True,
                        ))
                    except Exception as exc:
                        task.status = TaskStatus.FAILED
                        task.error = str(exc)
                        skill_duration = (time.monotonic() - skill_start) * 1000
                        skill_call_events.append(SkillCallEvent(
                            skill_name=task.skill_name,
                            action=task.action,
                            duration_ms=skill_duration,
                            success=False,
                            error=str(exc),
                        ))
                        logger.error(
                            "任务执行失败: task_id=%s, skill=%s, action=%s, error=%s, parameters=%s",
                            task.task_id,
                            task.skill_name,
                            task.action,
                            str(exc),
                            task.parameters,
                            exc_info=True,
                        )
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

            # 6. LLM 流式总结分析
            if all_completed and plan.sub_tasks:
                try:
                    synthesis_messages = self._build_synthesis_prompt(
                        sanitized_input, plan
                    )
                    async for token in self._llm_factory.chat_stream(
                        synthesis_messages
                    ):
                        yield {
                            "type": "token",
                            "content": token,
                            "session_id": session_id,
                        }
                except Exception as exc:
                    logger.warning("LLM 总结分析失败: %s", exc)

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

            if self._metrics_store:
                try:
                    llm_calls = self._llm_factory.get_pending_llm_calls()
                    self._llm_factory.clear_pending_llm_calls()
                    event = RequestEvent(
                        timestamp=datetime.now(timezone.utc),
                        session_id=session_id,
                        trace_id=trace_id,
                        status="completed" if all_completed else "failed",
                        duration_ms=elapsed_ms,
                        llm_calls=llm_calls,
                        skill_calls=skill_call_events,
                        user_input=sanitized_input[:100],
                    )
                    await self._metrics_store.record_request(event)
                except Exception:
                    logger.exception("记录请求指标失败")

        except AgentError as exc:
            if self._metrics:
                self._metrics.record_task("failed")
            if self._metrics_store:
                try:
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    llm_calls = self._llm_factory.get_pending_llm_calls()
                    self._llm_factory.clear_pending_llm_calls()
                    event = RequestEvent(
                        timestamp=datetime.now(timezone.utc),
                        session_id=session_id,
                        trace_id=trace_id,
                        status="failed",
                        duration_ms=elapsed_ms,
                        llm_calls=llm_calls,
                        skill_calls=skill_call_events,
                        user_input=sanitized_input[:100] if sanitized_input else user_input[:100],
                    )
                    await self._metrics_store.record_request(event)
                except Exception:
                    logger.exception("记录请求指标失败")
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
            if self._metrics_store:
                try:
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    llm_calls = self._llm_factory.get_pending_llm_calls()
                    self._llm_factory.clear_pending_llm_calls()
                    event = RequestEvent(
                        timestamp=datetime.now(timezone.utc),
                        session_id=session_id,
                        trace_id=trace_id,
                        status="failed",
                        duration_ms=elapsed_ms,
                        llm_calls=llm_calls,
                        skill_calls=skill_call_events,
                        user_input=sanitized_input[:100] if sanitized_input else user_input[:100],
                    )
                    await self._metrics_store.record_request(event)
                except Exception:
                    logger.exception("记录请求指标失败")
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

            # 校验输入 — 将 action 注入到 input_data 中
            input_data = {"action": sub_task.action, **sub_task.parameters}
            validation = await skill.validate(input_data)
            if not validation.valid:
                raise SkillExecutionError(
                    message=f"输入参数校验失败: {validation.errors}",
                    skill_name=sub_task.skill_name,
                )

            # 执行
            result = await skill.execute(input_data)

            sub_task.result = result
            sub_task.status = TaskStatus.COMPLETED
            sm.transition(TaskStatus.COMPLETED)

            # 技能执行成功，恢复健康状态
            await self._skill_registry.mark_healthy(sub_task.skill_name)

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
    # Re-entrant loop 辅助方法
    # ------------------------------------------------------------------

    def _check_loop_terminal(
        self,
        plan: TaskPlan,
        turn_count: int,
        elapsed_ms: float,
        start_time: float,
    ) -> LoopTerminal | None:
        """检查循环是否应终止 — 独立于 LLM 判断的硬性约束.

        检查条件:
        - 所有子任务全部失败 → UNRECOVERABLE_ERROR
        - Token 预算耗尽 → BUDGET_EXHAUSTED
        - 时间上限超出 → ABORTED
        - 已完成最后一轮 → MAX_TURNS

        Returns:
            LoopTerminal 如果应终止，None 如果可以继续。
        """
        # 所有任务都失败 → 不可恢复
        all_failed = all(t.status == TaskStatus.FAILED for t in plan.sub_tasks)
        if all_failed:
            return LoopTerminal(
                reason=LoopTerminalReason.UNRECOVERABLE_ERROR,
                message="所有子任务执行失败",
                turn_count=turn_count,
                elapsed_ms=elapsed_ms,
            )

        # Token 预算检查
        if self._loop_config.max_tokens is not None:
            llm_calls = self._llm_factory.get_pending_llm_calls()
            total_tokens = sum(c.get("tokens", 0) for c in llm_calls)
            if total_tokens >= self._loop_config.max_tokens:
                return LoopTerminal(
                    reason=LoopTerminalReason.BUDGET_EXHAUSTED,
                    message=f"Token 预算耗尽 ({total_tokens}/{self._loop_config.max_tokens})",
                    turn_count=turn_count,
                    total_tokens=total_tokens,
                    elapsed_ms=elapsed_ms,
                )

        # 时间上限检查
        if self._loop_config.max_elapsed_ms is not None:
            current_elapsed = (time.monotonic() - start_time) * 1000
            if current_elapsed >= self._loop_config.max_elapsed_ms:
                return LoopTerminal(
                    reason=LoopTerminalReason.ABORTED,
                    message=f"执行时间超出上限 ({current_elapsed:.0f}/{self._loop_config.max_elapsed_ms:.0f}ms)",
                    turn_count=turn_count,
                    elapsed_ms=current_elapsed,
                )

        return None

    async def _should_continue_loop(
        self,
        user_input: str,
        plan: TaskPlan,
        turn_count: int,
        session_id: str,
    ) -> bool:
        """询问 LLM 是否需要继续执行 — Re-entrant loop 的核心决策.

        LLM 收到当前执行结果，判断:
        - 任务已全部完成，用户需求已满足 → 返回 False
        - 还需要更多操作（如查更多信息、执行修复步骤等）→ 返回 True

        Returns:
            True 表示应继续，False 表示应终止。
        """
        system_msg = (
            "你是 AIOps 智能运维助手的循环决策模块。\n"
            "基于当前任务执行结果，判断是否需要继续执行更多操作。\n"
            "判断标准:\n"
            "- 如果当前结果已充分回答了用户的问题，无需更多操作 → 回答 COMPLETED\n"
            "- 如果需要更多信息、修复步骤或后续操作 → 回答 CONTINUE\n"
            "请只回答 COMPLETED 或 CONTINUE，不要添加其他内容。"
        )

        # 构建结果摘要
        results_summary = self._summarize_plan_results(plan)
        user_content = (
            f"用户请求: {user_input}\n"
            f"当前循环轮数: {turn_count}\n"
            f"执行结果摘要:\n{results_summary}\n\n"
            f"是否需要继续执行？请回答 COMPLETED 或 CONTINUE。"
        )

        messages = [
            Message(role="system", content=system_msg),
            Message(role="user", content=user_content),
        ]

        try:
            response = await self._llm_factory.chat(messages, temperature=0.1, max_tokens=10)
            decision = response.content.strip().upper()
            logger.info("LLM loop decision: %s (turn=%d)", decision, turn_count)

            # 解析 LLM 回答
            if "COMPLETED" in decision:
                return False
            elif "CONTINUE" in decision:
                return True
            else:
                # 无法解析时，默认不继续（安全策略）
                logger.warning("无法解析 LLM 循环决策: %s, 默认终止", decision)
                return False
        except Exception as exc:
            logger.warning("LLM 循环决策失败: %s, 默认终止", exc)
            return False

    @staticmethod
    def _summarize_plan_results(plan: TaskPlan) -> str:
        """将任务计划结果摘要为文本 — 供 LLM 循环决策参考."""
        import json as _json

        lines = []
        for task in plan.sub_tasks:
            status_icon = "✓" if task.status == TaskStatus.COMPLETED else "✗"
            lines.append(f"{status_icon} [{task.skill_name}.{task.action}] → {task.status.value}")
            if task.result:
                try:
                    result_str = _json.dumps(task.result, ensure_ascii=False, indent=2)
                    # 限制长度，避免 Token 过多
                    if len(result_str) > 500:
                        result_str = result_str[:500] + "..."
                except (TypeError, ValueError):
                    result_str = str(task.result)[:500]
                lines.append(f"   结果: {result_str}")
            if task.error:
                lines.append(f"   错误: {task.error[:200]}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 健康监控
    # ------------------------------------------------------------------

    def _build_synthesis_prompt(
        self, user_input: str, plan: TaskPlan
    ) -> list[Message]:
        """构建 LLM 总结分析的 prompt."""
        system_msg = (
            "你是 AIOps 智能运维助手。基于以下已执行完成的任务结果，"
            "为用户提供清晰、专业的分析总结。包括关键发现、数据解读和后续建议。"
            "请用简洁的中文回答，使用 Markdown 格式。"
        )

        # 收集任务结果
        results_text = ""
        for task in plan.sub_tasks:
            results_text += f"\n### 任务: {task.skill_name} · {task.action}\n"
            results_text += f"状态: {task.status.value}\n"
            if task.result:
                import json as _json
                try:
                    result_str = _json.dumps(task.result, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    result_str = str(task.result)
                results_text += f"结果:\n```json\n{result_str}\n```\n"

        user_content = (
            f"用户原始请求: {user_input}\n\n"
            f"## 任务执行结果\n{results_text}\n\n"
            f"请基于以上结果，回答用户的问题并提供分析建议。"
        )

        return [
            Message(role="system", content=system_msg),
            Message(role="user", content=user_content),
        ]

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
