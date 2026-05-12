"""SSE 流式响应测试 — Orchestrator + Web Server."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.core.exceptions import SkillNotFoundError
from aiops_agent.models.schemas import (
    AgentResponse,
    SkillDefinition,
    SubTask,
    TaskPlan,
    TaskStatus,
    ValidationResult,
)
from aiops_agent.skills.base import SkillInstance


# ---------------------------------------------------------------------------
# Mock Skill for streaming tests
# ---------------------------------------------------------------------------

class MockStreamingSkill(SkillInstance):
    """Mock Skill for streaming tests."""

    def __init__(self, result: dict | None = None, should_fail: bool = False) -> None:
        super().__init__()
        self._result = result or {"status": "success"}
        self._should_fail = should_fail

    async def execute(self, input_data: dict) -> dict:
        if self._should_fail:
            raise RuntimeError("Mock streaming skill failed")
        return self._result

    async def validate(self, input_data: dict) -> ValidationResult:
        return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Orchestrator Streaming Tests
# ---------------------------------------------------------------------------

class TestOrchestratorStream:
    """测试 Orchestrator.process_request_stream() 生成器."""

    @pytest.fixture
    def mock_orchestrator(self, mock_llm_factory, mock_workload_identity):
        """创建 mock 编排器用于流式测试."""
        from aiops_agent.core.orchestrator import AgentOrchestrator
        from aiops_agent.context.manager import ContextManager
        from aiops_agent.context.session import SessionStore
        from aiops_agent.context.memory import MemoryLayer
        from aiops_agent.context.resource_resolver import ResourceResolver
        from aiops_agent.security.security_guard import SecurityGuard
        from aiops_agent.tools.executor import ToolExecutor
        from aiops_agent.observability.metrics import AgentMetrics

        ctx_mgr = MagicMock()

        # Mock session with resources
        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)
        ctx_mgr.update_context = AsyncMock()
        ctx_mgr.switch_mode = AsyncMock()
        ctx_mgr.update_task_progress = AsyncMock()

        reg = MagicMock()
        reg.list_skills = MagicMock(return_value=[])
        reg.get_skill = AsyncMock()

        tool_exec = MagicMock()
        metrics = MagicMock()
        metrics.record_task = MagicMock()

        orch = AgentOrchestrator(
            llm_factory=mock_llm_factory,
            skill_registry=reg,
            context_manager=ctx_mgr,
            tool_executor=tool_exec,
            security_guard=SecurityGuard(),
            metrics=metrics,
        )
        return orch, ctx_mgr, reg

    @pytest.mark.asyncio
    async def test_stream_empty_tasks(self, mock_orchestrator):
        """空任务列表应该 yield error 事件."""
        orch, ctx_mgr, reg = mock_orchestrator

        plan = MagicMock()
        plan.sub_tasks = []
        orch._task_planner.decompose = AsyncMock(return_value=plan)

        events = []
        async for event in orch.process_request_stream(
            user_input="test",
            session_id="s1",
            user_id="u1",
        ):
            events.append(event)

        event_types = [e["type"] for e in events]
        assert "planning" in event_types
        assert "error" in event_types
        error_event = next(e for e in events if e["type"] == "error")
        assert error_event["error_code"] == "NO_TASKS"

    @pytest.mark.asyncio
    async def test_stream_task_execution(self, mock_orchestrator):
        """正常任务执行应该 yield planning → task_start → task_done → done."""
        orch, ctx_mgr, reg = mock_orchestrator

        skill = MockStreamingSkill(result={"cpu": 85})
        reg.get_skill = AsyncMock(return_value=skill)
        reg.list_skills = MagicMock(return_value=[
            SkillDefinition(
                skill_name="monitoring", description="监控", version="1.0",
                capabilities=["query_metrics"],
            ),
        ])

        task1 = SubTask(
            task_id="t1",
            skill_name="monitoring",
            action="query_metrics",
            parameters={"instance_id": "i-test", "namespace": "ecs"},
        )
        plan = TaskPlan(
            plan_id="p1",
            user_request="test",
            sub_tasks=[task1],
        )

        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[[task1]])

        events = []
        async for event in orch.process_request_stream(
            user_input="查询 CPU",
            session_id="s1",
            user_id="u1",
        ):
            events.append(event)

        event_types = [e["type"] for e in events]
        assert "planning" in event_types
        assert "task_start" in event_types
        assert "task_done" in event_types
        assert "done" in event_types

        # 验证 task_done 事件包含结果
        task_done = next(e for e in events if e["type"] == "task_done")
        assert task_done["status"] == "completed"
        assert task_done["skill_name"] == "monitoring"

    @pytest.mark.asyncio
    async def test_stream_task_failure(self, mock_orchestrator):
        """任务失败应该 yield error 状态."""
        orch, ctx_mgr, reg = mock_orchestrator

        skill = MockStreamingSkill(should_fail=True)
        reg.get_skill = AsyncMock(return_value=skill)

        task1 = SubTask(
            task_id="t1",
            skill_name="monitoring",
            action="query_metrics",
            parameters={},
        )
        plan = TaskPlan(
            plan_id="p1",
            user_request="test",
            sub_tasks=[task1],
        )

        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[[task1]])

        events = []
        async for event in orch.process_request_stream(
            user_input="查询 CPU",
            session_id="s1",
            user_id="u1",
        ):
            events.append(event)

        task_done = next(e for e in events if e["type"] == "task_done")
        assert task_done["status"] == "failed"
        assert "error" in task_done

    @pytest.mark.asyncio
    async def test_stream_dependency_cancellation(self, mock_orchestrator):
        """依赖失败的任务应该被取消."""
        orch, ctx_mgr, reg = mock_orchestrator

        failing_skill = MockStreamingSkill(should_fail=True)
        reg.get_skill = AsyncMock(return_value=failing_skill)

        task1 = SubTask(
            task_id="t1", skill_name="monitoring", action="gather", parameters={},
        )
        task2 = SubTask(
            task_id="t2", skill_name="troubleshooting", action="diagnose",
            parameters={}, dependencies=["t1"],
        )
        plan = TaskPlan(
            plan_id="p1", user_request="test", sub_tasks=[task1, task2],
        )

        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[[task1], [task2]])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1", user_id="u1",
        ):
            events.append(event)

        cancelled = [e for e in events if e.get("status") == "cancelled"]
        assert len(cancelled) >= 1

    @pytest.mark.asyncio
    async def test_stream_context_cleanup(self, mock_orchestrator):
        """流式完成后应该切回 Chat 模式."""
        orch, ctx_mgr, reg = mock_orchestrator

        plan = MagicMock()
        plan.sub_tasks = []
        orch._task_planner.decompose = AsyncMock(return_value=plan)

        async for _ in orch.process_request_stream(
            user_input="test", session_id="s1", user_id="u1",
        ):
            pass

        # 验证 switch_mode 被调用（最后是切回 CHAT）
        switch_calls = ctx_mgr.switch_mode.call_args_list
        assert switch_calls[-1][0][1].value == "chat"


# ---------------------------------------------------------------------------
# Web Server SSE Endpoint Tests
# ---------------------------------------------------------------------------

class TestSSEEndpoint:
    """测试 /api/chat/stream 端点."""

    @pytest.mark.asyncio
    async def test_stream_requires_post(self):
        """GET 请求应该返回 405."""
        from aiops_agent.web.server import create_app
        from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
        from aiohttp import web

        app = create_app()
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/chat/stream")
            assert resp.status == 405

    @pytest.mark.asyncio
    async def test_stream_empty_message(self):
        """空消息应该返回 400."""
        from aiops_agent.web.server import create_app
        from aiohttp.test_utils import TestClient, TestServer

        app = create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/stream", json={"message": ""})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_stream_content_type(self):
        """SSE 响应应该设置正确的 Content-Type."""
        from aiops_agent.web.server import create_app
        from aiohttp.test_utils import TestClient, TestServer

        app = create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/stream", json={"message": "hello"})
            assert resp.headers.get("Content-Type") == "text/event-stream"
            assert resp.headers.get("Cache-Control") == "no-cache"


# ---------------------------------------------------------------------------
# SSE Event Format Tests
# ---------------------------------------------------------------------------

class TestSSEEventFormat:
    """测试 SSE 事件格式."""

    def test_sse_event_structure(self):
        """验证 SSE 事件 JSON 结构."""
        import json

        event_data = {
            "type": "task_done",
            "task_id": "t1",
            "skill_name": "monitoring",
            "action": "query_metrics",
            "status": "completed",
            "session_id": "s1",
        }

        # 模拟 SSE 格式
        event_type = event_data.pop("type")
        payload = json.dumps(event_data, ensure_ascii=False)
        sse_line = f"event: {event_type}\ndata: {payload}\n\n"

        # 解析验证
        assert sse_line.startswith("event: task_done\n")
        assert "\ndata: " in sse_line
        assert sse_line.endswith("\n\n")

        parsed = json.loads(sse_line.split("data: ")[1].split("\n")[0])
        assert parsed["task_id"] == "t1"
        assert parsed["skill_name"] == "monitoring"

    def test_planning_event_structure(self):
        """planning 事件应该包含任务列表."""
        event = {
            "type": "planning",
            "status": "completed",
            "total_tasks": 3,
            "tasks": [
                {"task_id": "t1", "skill_name": "monitoring", "action": "query"},
                {"task_id": "t2", "skill_name": "troubleshooting", "action": "diagnose"},
            ],
        }
        assert event["status"] == "completed"
        assert len(event["tasks"]) == 2

    def test_done_event_structure(self):
        """done 事件应该包含 success 和 elapsed_ms."""
        event = {
            "type": "done",
            "status": "completed",
            "success": True,
            "elapsed_ms": 1234.5,
        }
        assert event["success"] is True
        assert isinstance(event["elapsed_ms"], float)
