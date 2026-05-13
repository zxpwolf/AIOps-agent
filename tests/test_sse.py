"""SSE 流式响应测试 — orchestrator + web server + 事件格式."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.core.exceptions import AgentError
from aiops_agent.models.schemas import (
    SkillDefinition,
    SubTask,
    TaskPlan,
    TaskStatus,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(sub_tasks: list[SubTask]) -> TaskPlan:
    return TaskPlan(
        plan_id="p1",
        user_request="test",
        sub_tasks=sub_tasks,
    )


@pytest.fixture
def mock_orchestrator(mock_llm_factory, skill_registry, context_manager, tool_executor, security_guard):
    """创建带 mock 依赖的 orchestrator."""
    from aiops_agent.core.orchestrator import AgentOrchestrator

    orch = AgentOrchestrator(
        llm_factory=mock_llm_factory,
        skill_registry=skill_registry,
        context_manager=context_manager,
        tool_executor=tool_executor,
        security_guard=security_guard,
    )
    return orch, context_manager, skill_registry


# ---------------------------------------------------------------------------
# Test: Orchestrator Stream — basic flow
# ---------------------------------------------------------------------------

class TestStreamBasicFlow:
    @pytest.mark.asyncio
    async def test_stream_planning_started(self, mock_orchestrator) -> None:
        """流式处理第一个事件应该是 planning:started."""
        orch, ctx_mgr, reg = mock_orchestrator

        # mock session
        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        plan = _make_plan([
            SubTask(task_id="t1", skill_name="test_skill", action="check", parameters={}),
        ])
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[])

        events = []
        async for event in orch.process_request_stream(
            user_input="查询 CPU", session_id="s1", user_id="u1",
        ):
            events.append(event)

        assert events[0]["type"] == "planning"
        assert events[0]["status"] == "started"
        assert "分析" in events[0]["message"] or "任务" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_stream_planning_completed(self, mock_orchestrator) -> None:
        """任务分解完成后应发送 planning:completed 事件."""
        orch, ctx_mgr, reg = mock_orchestrator

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        sub_tasks = [
            SubTask(task_id="t1", skill_name="test_skill", action="a", parameters={}),
            SubTask(task_id="t2", skill_name="test_skill", action="b", parameters={}),
        ]
        plan = _make_plan(sub_tasks)
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        planning_events = [e for e in events if e["type"] == "planning" and e["status"] == "completed"]
        assert len(planning_events) == 1
        assert planning_events[0]["total_tasks"] == 2
        assert len(planning_events[0]["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_stream_no_tasks_returns_error(self, mock_orchestrator) -> None:
        """LLM 返回空子任务 → 发送 error 事件."""
        orch, ctx_mgr, reg = mock_orchestrator

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        plan = _make_plan([])
        orch._task_planner.decompose = AsyncMock(return_value=plan)

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["error_code"] == "NO_TASKS"


# ---------------------------------------------------------------------------
# Test: Orchestrator Stream — task execution
# ---------------------------------------------------------------------------

class TestStreamTaskExecution:
    @pytest.mark.asyncio
    async def test_stream_single_task_execution(self, mock_orchestrator, mock_skill) -> None:
        """单任务流式执行: planning → task_start → task_done → done."""
        orch, ctx_mgr, reg = mock_orchestrator

        await reg.register(
            SkillDefinition(skill_name="test_skill", description="Test", version="1.0", capabilities=["test"]),
            mock_skill,
        )

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        sub_tasks = [
            SubTask(task_id="t1", skill_name="test_skill", action="check", parameters={}),
        ]
        plan = _make_plan(sub_tasks)
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[sub_tasks])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        event_types = [e["type"] for e in events]
        assert "planning" in event_types
        assert "task_start" in event_types
        assert "task_done" in event_types
        assert "done" in event_types

        # 验证 task_done 状态
        task_done = [e for e in events if e["type"] == "task_done"][0]
        assert task_done["status"] == "completed"
        assert task_done["task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_stream_multiple_tasks_sequential(self, mock_orchestrator, mock_skill) -> None:
        """多任务顺序执行，每个任务都有独立的 task_start/task_done."""
        orch, ctx_mgr, reg = mock_orchestrator

        await reg.register(
            SkillDefinition(skill_name="test_skill", description="Test", version="1.0", capabilities=["test"]),
            mock_skill,
        )

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        sub_tasks = [
            SubTask(task_id="t1", skill_name="test_skill", action="a", parameters={}),
            SubTask(task_id="t2", skill_name="test_skill", action="b", parameters={}),
            SubTask(task_id="t3", skill_name="test_skill", action="c", parameters={}),
        ]
        plan = _make_plan(sub_tasks)
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[sub_tasks])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        task_starts = [e for e in events if e["type"] == "task_start"]
        task_dones = [e for e in events if e["type"] == "task_done"]
        assert len(task_starts) == 3
        assert len(task_dones) == 3

        # 验证 progress 递增
        for i, td in enumerate(task_dones):
            assert td["progress"] == f"{i + 1}/3"

    @pytest.mark.asyncio
    async def test_stream_task_failure(self, mock_orchestrator, mock_failing_skill) -> None:
        """任务失败 → task_done:failed + 最终 done:partial_failure."""
        orch, ctx_mgr, reg = mock_orchestrator

        await reg.register(
            SkillDefinition(skill_name="fail_skill", description="Failing", version="1.0", capabilities=["fail"]),
            mock_failing_skill,
        )

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        sub_tasks = [
            SubTask(task_id="t1", skill_name="fail_skill", action="check", parameters={}),
        ]
        plan = _make_plan(sub_tasks)
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[sub_tasks])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        task_done = [e for e in events if e["type"] == "task_done"][0]
        assert task_done["status"] == "failed"
        assert task_done["error"] is not None

        done_event = [e for e in events if e["type"] == "done"][0]
        assert done_event["status"] == "partial_failure"
        assert done_event["success"] is False


# ---------------------------------------------------------------------------
# Test: Orchestrator Stream — dependency cancellation
# ---------------------------------------------------------------------------

class TestStreamDependencyCancellation:
    @pytest.mark.asyncio
    async def test_stream_dependency_failure_cancels_dependents(
        self, mock_orchestrator, mock_failing_skill, mock_skill
    ) -> None:
        """t1 失败 → t2（依赖 t1）被取消."""
        orch, ctx_mgr, reg = mock_orchestrator

        await reg.register(
            SkillDefinition(skill_name="fail_skill", description="Failing", version="1.0", capabilities=["fail"]),
            mock_failing_skill,
        )
        await reg.register(
            SkillDefinition(skill_name="test_skill", description="Test", version="1.0", capabilities=["test"]),
            mock_skill,
        )

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)

        t1 = SubTask(task_id="t1", skill_name="fail_skill", action="a", parameters={})
        t2 = SubTask(task_id="t2", skill_name="test_skill", action="b", parameters={}, dependencies=["t1"])
        plan = _make_plan([t1, t2])
        orch._task_planner.decompose = AsyncMock(return_value=plan)
        orch._task_planner.topological_sort = MagicMock(return_value=[[t1], [t2]])

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        cancelled = [e for e in events if e["type"] == "task_done" and e.get("status") == "cancelled"]
        assert len(cancelled) == 1
        assert cancelled[0]["task_id"] == "t2"
        assert "依赖" in (cancelled[0].get("error") or "")


# ---------------------------------------------------------------------------
# Test: Orchestrator Stream — exception handling
# ---------------------------------------------------------------------------

class TestStreamExceptionHandling:
    @pytest.mark.asyncio
    async def test_stream_unexpected_exception(self, mock_orchestrator) -> None:
        """未预期异常 → error 事件."""
        orch, ctx_mgr, reg = mock_orchestrator

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)
        orch._task_planner.decompose = AsyncMock(side_effect=RuntimeError("unexpected error"))

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["error_code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_stream_agent_error(self, mock_orchestrator) -> None:
        """AgentError → error 事件."""
        orch, ctx_mgr, reg = mock_orchestrator

        mock_session = MagicMock()
        mock_session.resources = {}
        ctx_mgr.get_session = AsyncMock(return_value=mock_session)
        orch._task_planner.decompose = AsyncMock(
            side_effect=AgentError(message="test error", error_code="TEST_ERROR", suggestion="try again")
        )

        events = []
        async for event in orch.process_request_stream(
            user_input="test", session_id="s1",
        ):
            events.append(event)

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["error_code"] == "TEST_ERROR"
        assert error_events[0]["suggestion"] == "try again"


# ---------------------------------------------------------------------------
# Test: SSE Event Format (pure unit tests, no async)
# ---------------------------------------------------------------------------

class TestSSEEventFormat:
    def test_sse_event_structure(self) -> None:
        """验证 SSE 事件格式正确."""
        event_data = {
            "type": "task_done",
            "task_id": "t1",
            "skill_name": "test",
            "action": "check",
            "status": "completed",
            "result": {"cpu": 85},
            "progress": "1/3",
            "session_id": "s1",
        }
        event_type = event_data.pop("type")
        payload = json.dumps(event_data, ensure_ascii=False, default=str)
        sse_line = f"event: {event_type}\ndata: {payload}\n\n"

        assert sse_line.startswith("event: task_done\n")
        assert "data: " in sse_line
        assert sse_line.endswith("\n\n")

    def test_sse_chinese_encoding(self) -> None:
        """SSE 事件中的中文不能乱码."""
        event_data = {
            "type": "planning",
            "status": "completed",
            "message": "已生成 3 个子任务",
        }
        event_type = event_data.pop("type")
        payload = json.dumps(event_data, ensure_ascii=False)
        sse_line = f"event: {event_type}\ndata: {payload}\n\n"

        # 解码验证
        decoded = json.loads(sse_line.split("data: ")[1].split("\n")[0])
        assert decoded["message"] == "已生成 3 个子任务"

    def test_sse_multiple_events_parsing(self) -> None:
        """模拟前端正确解析多个 SSE 事件."""
        events_raw = (
            "event: planning\ndata: {\"status\":\"started\",\"message\":\"分析中\"}\n\n"
            "event: task_start\ndata: {\"skill_name\":\"monitoring\",\"action\":\"query\"}\n\n"
            "event: task_done\ndata: {\"status\":\"completed\",\"progress\":\"1/2\"}\n\n"
        )

        parts = events_raw.split("\n\n")
        parsed = []
        for part in parts:
            if not part.strip():
                continue
            lines = part.split("\n")
            event_type = "message"
            data_str = ""
            for line in lines:
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:]
            if data_str:
                parsed.append({"type": event_type, "data": json.loads(data_str)})

        assert len(parsed) == 3
        assert parsed[0]["type"] == "planning"
        assert parsed[1]["type"] == "task_start"
        assert parsed[2]["type"] == "task_done"


# ---------------------------------------------------------------------------
# Test: Web Server SSE Endpoint
# ---------------------------------------------------------------------------

class TestWebServerSSE:
    @pytest.mark.asyncio
    async def test_stream_content_type(self) -> None:
        """SSE 端点应返回正确的 Content-Type."""
        from aiohttp.test_utils import TestClient, TestServer

        from aiops_agent.web.server import create_app

        app = create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/stream", json={"message": "hello"})
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "text/event-stream"
            assert resp.headers.get("Cache-Control") == "no-cache"

    @pytest.mark.asyncio
    async def test_stream_empty_message_returns_400(self) -> None:
        """空消息 → 400."""
        from aiohttp.test_utils import TestClient, TestServer

        from aiops_agent.web.server import create_app

        app = create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/stream", json={"message": ""})
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_stream_events_are_emitted(self) -> None:
        """SSE 端点应发送完整的事件流."""
        from aiohttp.test_utils import TestClient, TestServer

        from aiops_agent.web.server import create_app

        app = create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/stream", json={"message": "查看 ECS 状态"})
            assert resp.status == 200

            body = await resp.text()
            assert "event: planning" in body
            assert "event: done" in body or "event: error" in body
            # 验证每个 data: 行都是有效 JSON
            lines = body.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("data:"):
                    json_data = line[5:]
                    parsed = json.loads(json_data)  # 不应抛异常
                    assert isinstance(parsed, dict)
