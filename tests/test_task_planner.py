"""TaskPlanner 单元测试 — 任务分解、JSON 解析、DAG 拓扑排序、技能映射验证.

测试覆盖:
  1. _parse_subtasks: LLM 输出的各种 JSON 格式解析（数组、代码块、dict 包装、无效输入）
  2. topological_sort: DAG 拓扑排序（独立、链式、菱形、混合、单任务、空计划）
  3. decompose: 端到端任务分解流程（mock LLM，已注册/未注册技能、LLM 失败、上下文传递）
  4. _validate_skill_mapping: 技能映射验证（全部注册、全部未注册、空列表）
  5. Qwen 集成测试: 使用真实通义千问 API（需 QWEN_API_KEY 环境变量，无 key 自动跳过）

运行方式:
  # 仅单元测试（无需 API Key）
  uv run pytest tests/test_task_planner.py -v -k "not Qwen"

  # 含 Qwen 集成测试
  QWEN_API_KEY=sk-xxx uv run pytest tests/test_task_planner.py -v
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from aiops_agent.core.task_planner import TaskPlanner
from aiops_agent.llm.provider import ChatResponse, LLMProvider, LLMProviderFactory
from aiops_agent.models.schemas import (
    Message,
    SkillDefinition,
    SubTask,
    TaskPlan,
    TaskStatus,
)
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Test Helpers — Fake LLM、Stub Skill、工厂方法
# ---------------------------------------------------------------------------


class _FakeLLM(LLMProvider):
    """返回预设内容的 Fake LLM Provider，用于单元测试中隔离真实 API 调用."""

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def provider_name(self) -> str:
        return "fake"

    async def chat(self, messages: list[Message], **kwargs: Any) -> ChatResponse:
        # 直接返回预设内容，不做任何网络调用
        return ChatResponse(content=self._content, model="fake")

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return self._content

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]


class _StubSkill(SkillInstance):
    """最小化的 Skill 实现，用于注册到 SkillRegistry 满足映射验证."""

    async def execute(self, input_data: dict) -> dict:
        return {"ok": True}

    async def validate(self, input_data: dict) -> Any:
        from aiops_agent.models.schemas import ValidationResult
        return ValidationResult(valid=True)


def _make_factory(content: str) -> LLMProviderFactory:
    """创建包含 FakeLLM 的 LLMProviderFactory，返回预设的 content 字符串."""
    factory = LLMProviderFactory()
    factory.register("fake", _FakeLLM(content))
    factory.set_primary("fake")
    return factory


async def _make_registry(*names: str) -> SkillRegistry:
    """创建包含指定技能名称的 SkillRegistry，每个技能使用 StubSkill 实例."""
    registry = SkillRegistry()
    for name in names:
        defn = SkillDefinition(
            skill_name=name, description=f"{name} skill", version="1.0.0",
            capabilities=[name], required_permissions=[],
        )
        await registry.register(defn, _StubSkill())
    return registry


# ===========================================================================
# 1. _parse_subtasks — LLM 输出 JSON 解析
#
# TaskPlanner._parse_subtasks 负责将 LLM 返回的自由文本解析为 SubTask 列表。
# LLM 输出格式不可控，需要处理：纯 JSON 数组、```json 代码块、
# dict 包装（sub_tasks/tasks key）、单个 dict、无效 JSON 等情况。
# ===========================================================================


class TestParseSubtasks:
    """测试 LLM 输出解析为 SubTask 列表的各种格式."""

    def _planner(self) -> TaskPlanner:
        """创建一个用于测试解析方法的 TaskPlanner 实例."""
        return TaskPlanner(_make_factory("[]"), SkillRegistry())

    # --- 正常格式 ---

    def test_plain_json_array(self):
        """纯 JSON 数组 — 最标准的 LLM 输出格式."""
        raw = json.dumps([
            {"task_id": "t1", "skill_name": "monitoring", "action": "query_metrics",
             "parameters": {"ns": "acs_ecs"}, "dependencies": []},
        ])
        result = self._planner()._parse_subtasks(raw, "p1")
        assert len(result) == 1
        assert result[0].task_id == "t1"
        assert result[0].skill_name == "monitoring"
        assert result[0].parameters == {"ns": "acs_ecs"}

    def test_json_code_block(self):
        """```json ... ``` 代码块 — LLM 常用的 Markdown 格式输出."""
        raw = '说明文字\n```json\n[{"task_id":"t1","skill_name":"m","action":"a","parameters":{},"dependencies":[]}]\n```\n后续文字'
        result = self._planner()._parse_subtasks(raw, "p2")
        assert len(result) == 1

    def test_generic_code_block(self):
        """``` ... ``` 无语言标记的代码块."""
        raw = '```\n[{"task_id":"t1","skill_name":"s","action":"a","parameters":{},"dependencies":[]}]\n```'
        result = self._planner()._parse_subtasks(raw, "p3")
        assert len(result) == 1

    def test_dict_with_sub_tasks_key(self):
        """LLM 返回 {"sub_tasks": [...]} 格式 — 需要解包."""
        raw = json.dumps({"sub_tasks": [
            {"task_id": "t1", "skill_name": "m", "action": "q", "parameters": {}, "dependencies": []},
            {"task_id": "t2", "skill_name": "t", "action": "d", "parameters": {}, "dependencies": ["t1"]},
        ]})
        result = self._planner()._parse_subtasks(raw, "p4")
        assert len(result) == 2
        assert result[1].dependencies == ["t1"]

    def test_dict_with_tasks_key(self):
        """LLM 返回 {"tasks": [...]} 格式 — 另一种常见包装."""
        raw = json.dumps({"tasks": [
            {"task_id": "t1", "skill_name": "s", "action": "a", "parameters": {}, "dependencies": []},
        ]})
        result = self._planner()._parse_subtasks(raw, "p5")
        assert len(result) == 1

    def test_single_dict(self):
        """LLM 返回单个 dict（非数组）— 自动包装为列表."""
        raw = json.dumps({"task_id": "t1", "skill_name": "s", "action": "a", "parameters": {}, "dependencies": []})
        result = self._planner()._parse_subtasks(raw, "p6")
        assert len(result) == 1

    # --- 异常/边界情况 ---

    def test_invalid_json_returns_empty(self):
        """无效 JSON 文本 → 返回空列表，不抛异常."""
        assert self._planner()._parse_subtasks("这不是 JSON", "p7") == []

    def test_empty_string_returns_empty(self):
        """空字符串 → 返回空列表."""
        assert self._planner()._parse_subtasks("", "p8") == []

    # --- 字段缺省 ---

    def test_auto_generated_task_id(self):
        """缺少 task_id 时自动生成 t1, t2, ... 序号."""
        raw = json.dumps([{"skill_name": "s", "action": "a"}])
        result = self._planner()._parse_subtasks(raw, "p9")
        assert result[0].task_id == "t1"

    def test_missing_optional_fields_default(self):
        """缺少 parameters/dependencies 时使用空 dict/list 默认值."""
        raw = json.dumps([{"task_id": "t1", "skill_name": "s", "action": "a"}])
        result = self._planner()._parse_subtasks(raw, "p10")
        assert result[0].parameters == {}
        assert result[0].dependencies == []

    # --- 多任务 ---

    def test_five_tasks_with_chain_dependencies(self):
        """5 个链式依赖子任务 — 验证批量解析和依赖关系保留."""
        tasks = [
            {"task_id": f"t{i}", "skill_name": f"s{i}", "action": f"a{i}",
             "parameters": {}, "dependencies": [f"t{i-1}"] if i > 1 else []}
            for i in range(1, 6)
        ]
        result = self._planner()._parse_subtasks(json.dumps(tasks), "p11")
        assert len(result) == 5
        assert result[4].dependencies == ["t4"]


# ===========================================================================
# 2. topological_sort — DAG 拓扑排序
#
# 将 TaskPlan 中的子任务按依赖关系分层，同层任务可并行执行。
# 返回 list[list[SubTask]]，每个内层 list 是一组可并行的任务。
# ===========================================================================


class TestTopologicalSort:
    """测试 DAG 拓扑排序的各种依赖图结构."""

    def _planner(self) -> TaskPlanner:
        return TaskPlanner(_make_factory("[]"), SkillRegistry())

    def _plan(self, tasks: list[SubTask]) -> TaskPlan:
        return TaskPlan(plan_id="p1", user_request="test", sub_tasks=tasks)

    def test_no_dependencies_single_level(self):
        """3 个独立任务（无依赖）→ 1 层 3 个，全部可并行."""
        tasks = [SubTask(task_id=f"t{i}", skill_name="a", action="x") for i in range(1, 4)]
        levels = self._planner().topological_sort(self._plan(tasks))
        assert len(levels) == 1
        assert len(levels[0]) == 3

    def test_chain_three_levels(self):
        """链式依赖 t1→t2→t3 → 3 层各 1 个，严格串行."""
        tasks = [
            SubTask(task_id="t1", skill_name="a", action="x"),
            SubTask(task_id="t2", skill_name="b", action="y", dependencies=["t1"]),
            SubTask(task_id="t3", skill_name="c", action="z", dependencies=["t2"]),
        ]
        levels = self._planner().topological_sort(self._plan(tasks))
        assert len(levels) == 3
        assert [levels[i][0].task_id for i in range(3)] == ["t1", "t2", "t3"]

    def test_diamond_dependency(self):
        """菱形依赖: t1→(t2,t3)→t4 → 3 层，中间层 2 个可并行."""
        tasks = [
            SubTask(task_id="t1", skill_name="a", action="x"),
            SubTask(task_id="t2", skill_name="b", action="y", dependencies=["t1"]),
            SubTask(task_id="t3", skill_name="c", action="z", dependencies=["t1"]),
            SubTask(task_id="t4", skill_name="d", action="w", dependencies=["t2", "t3"]),
        ]
        levels = self._planner().topological_sort(self._plan(tasks))
        assert len(levels) == 3
        assert {t.task_id for t in levels[1]} == {"t2", "t3"}  # 中间层可并行
        assert levels[2][0].task_id == "t4"

    def test_mixed_parallel_and_sequential(self):
        """混合: t1,t2 独立并行 + t3 依赖两者 → 2 层."""
        tasks = [
            SubTask(task_id="t1", skill_name="a", action="x"),
            SubTask(task_id="t2", skill_name="b", action="y"),
            SubTask(task_id="t3", skill_name="c", action="z", dependencies=["t1", "t2"]),
        ]
        levels = self._planner().topological_sort(self._plan(tasks))
        assert len(levels) == 2
        assert len(levels[0]) == 2  # 第一层 2 个并行
        assert levels[1][0].task_id == "t3"

    def test_single_task(self):
        """单个任务 → 1 层 1 个."""
        tasks = [SubTask(task_id="t1", skill_name="a", action="x")]
        levels = self._planner().topological_sort(self._plan(tasks))
        assert len(levels) == 1

    def test_empty_plan(self):
        """空计划 → 空层级列表."""
        assert self._planner().topological_sort(self._plan([])) == []


# ===========================================================================
# 3. decompose — 端到端任务分解（使用 Mock LLM）
#
# 测试完整的 decompose 流程：
#   LLM 调用 → JSON 解析 → 技能映射验证 → 返回 TaskPlan
# ===========================================================================


class TestDecompose:
    """测试 decompose 方法的端到端流程（Mock LLM，不依赖真实 API）."""

    @pytest.mark.asyncio
    async def test_registered_skill_status_pending(self):
        """LLM 返回已注册技能 → 子任务状态为 PENDING（等待执行）."""
        llm_out = json.dumps([{"task_id": "t1", "skill_name": "monitoring", "action": "q", "parameters": {}, "dependencies": []}])
        plan = await TaskPlanner(_make_factory(llm_out), await _make_registry("monitoring")).decompose("查看 CPU")
        assert len(plan.sub_tasks) == 1
        assert plan.sub_tasks[0].status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_unregistered_skill_status_failed(self):
        """LLM 返回未注册技能 → 子任务标记为 FAILED，error 包含 '未注册'."""
        llm_out = json.dumps([{"task_id": "t1", "skill_name": "nonexistent", "action": "x", "parameters": {}, "dependencies": []}])
        plan = await TaskPlanner(_make_factory(llm_out), SkillRegistry()).decompose("test")
        assert plan.sub_tasks[0].status == TaskStatus.FAILED
        assert "未注册" in plan.sub_tasks[0].error

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_plan(self):
        """LLM 调用抛异常 → 返回空子任务列表（优雅降级，不崩溃）."""

        class _FailingLLM(LLMProvider):
            @property
            def provider_name(self): return "fail"
            async def chat(self, m, **kw): raise RuntimeError("LLM service down")
            async def complete(self, p, **kw): raise RuntimeError("LLM service down")
            async def embed(self, t, **kw): return []

        f = LLMProviderFactory()
        f.register("fail", _FailingLLM())
        f.set_primary("fail")
        plan = await TaskPlanner(f, SkillRegistry()).decompose("test")
        assert plan.sub_tasks == []

    @pytest.mark.asyncio
    async def test_context_passed_through(self):
        """传入 context dict → 原样保存到 plan.context."""
        llm_out = json.dumps([{"task_id": "t1", "skill_name": "monitoring", "action": "q", "parameters": {}, "dependencies": []}])
        ctx = {"session_id": "s1"}
        plan = await TaskPlanner(_make_factory(llm_out), await _make_registry("monitoring")).decompose("test", context=ctx)
        assert plan.context == ctx

    @pytest.mark.asyncio
    async def test_multiple_tasks_with_dependencies(self):
        """多个子任务 + 依赖关系 → 全部 PENDING，依赖保留."""
        llm_out = json.dumps([
            {"task_id": "t1", "skill_name": "monitoring", "action": "q", "parameters": {}, "dependencies": []},
            {"task_id": "t2", "skill_name": "troubleshooting", "action": "d", "parameters": {}, "dependencies": ["t1"]},
        ])
        plan = await TaskPlanner(_make_factory(llm_out), await _make_registry("monitoring", "troubleshooting")).decompose("排查")
        assert len(plan.sub_tasks) == 2
        assert all(t.status == TaskStatus.PENDING for t in plan.sub_tasks)

    @pytest.mark.asyncio
    async def test_plan_id_is_valid_uuid(self):
        """plan_id 是有效的 UUID 格式."""
        import uuid
        plan = await TaskPlanner(_make_factory("[]"), SkillRegistry()).decompose("test")
        uuid.UUID(plan.plan_id)  # 不抛异常即通过


# ===========================================================================
# 4. _validate_skill_mapping — 技能映射验证
#
# 检查 LLM 分解出的每个子任务的 skill_name 是否在 SkillRegistry 中已注册。
# 未注册的技能 → 标记为 FAILED + error 信息。
# ===========================================================================


class TestValidateSkillMapping:
    """测试技能映射验证逻辑."""

    @pytest.mark.asyncio
    async def test_all_skills_registered(self):
        """所有技能已注册 → 全部保持 PENDING 状态."""
        planner = TaskPlanner(_make_factory(""), await _make_registry("monitoring", "troubleshooting"))
        tasks = [SubTask(task_id="t1", skill_name="monitoring", action="q"), SubTask(task_id="t2", skill_name="troubleshooting", action="d")]
        result = await planner._validate_skill_mapping(tasks)
        assert all(t.status == TaskStatus.PENDING for t in result)

    @pytest.mark.asyncio
    async def test_no_skills_registered(self):
        """无技能注册 → 全部标记为 FAILED."""
        planner = TaskPlanner(_make_factory(""), SkillRegistry())
        tasks = [SubTask(task_id="t1", skill_name="monitoring", action="q")]
        result = await planner._validate_skill_mapping(tasks)
        assert all(t.status == TaskStatus.FAILED for t in result)

    @pytest.mark.asyncio
    async def test_empty_task_list(self):
        """空列表输入 → 返回空列表."""
        planner = TaskPlanner(_make_factory(""), SkillRegistry())
        assert await planner._validate_skill_mapping([]) == []


# ===========================================================================
# 5. 集成测试 — 通义千问真实 LLM 任务分解
#
# 使用真实的通义千问 API 测试端到端任务分解。
# 需要环境变量 QWEN_API_KEY，无 key 时自动跳过（不影响 CI）。
#
# 运行: QWEN_API_KEY=sk-xxx uv run pytest tests/test_task_planner.py::TestQwenIntegration -v
# ===========================================================================

_QWEN_KEY = os.getenv("QWEN_API_KEY", "")
_skip_no_qwen = pytest.mark.skipif(not _QWEN_KEY, reason="QWEN_API_KEY not set")


@_skip_no_qwen
class TestQwenIntegration:
    """使用通义千问真实 API 测试任务分解（需要有效的 QWEN_API_KEY）."""

    def _qwen_factory(self) -> LLMProviderFactory:
        """创建使用真实通义千问 API 的 LLMProviderFactory."""
        from aiops_agent.llm.qwen import QwenProvider
        factory = LLMProviderFactory()
        factory.register("qwen", QwenProvider(
            api_key=_QWEN_KEY,
            model="qwen3-235b-a22b",  # 百炼 Qwen3 MoE 模型
        ))
        factory.set_primary("qwen")
        return factory

    @pytest.mark.asyncio
    async def test_qwen_decompose_monitoring(self):
        """千问分解监控查询请求 → 至少 1 个子任务，包含 monitoring 技能."""
        registry = await _make_registry("monitoring", "troubleshooting", "change_management")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose("查看 ECS 实例 i-bp1234567890 的 CPU 和内存使用率")

        assert len(plan.sub_tasks) >= 1, f"期望至少 1 个子任务，实际 {len(plan.sub_tasks)}"
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "monitoring" in skill_names, f"期望包含 monitoring 技能，实际 {skill_names}"
        assert any(t.status == TaskStatus.PENDING for t in plan.sub_tasks)

    @pytest.mark.asyncio
    async def test_qwen_decompose_troubleshooting(self):
        """千问分解故障排查请求 → 包含 troubleshooting 技能."""
        registry = await _make_registry("monitoring", "troubleshooting", "change_management")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose("ECS 实例 i-bp1234567890 无法 SSH 登录，帮我排查原因")

        assert len(plan.sub_tasks) >= 1
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "troubleshooting" in skill_names, f"期望包含 troubleshooting，实际 {skill_names}"

    @pytest.mark.asyncio
    async def test_qwen_decompose_multi_skill(self):
        """千问分解复杂请求 → 至少 2 个子任务，涉及 2+ 种技能."""
        registry = await _make_registry("monitoring", "troubleshooting", "change_management")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose(
            "ECS 实例 i-bp1234567890 的 CPU 持续 100%，"
            "帮我查看监控指标、排查根因，并评估是否需要扩容"
        )

        assert len(plan.sub_tasks) >= 2, f"复杂请求期望至少 2 个子任务，实际 {len(plan.sub_tasks)}"
        skill_names = set(t.skill_name for t in plan.sub_tasks)
        assert len(skill_names) >= 2, f"期望至少 2 种技能，实际 {skill_names}"

    @pytest.mark.asyncio
    async def test_qwen_decompose_change_management(self):
        """千问分解变更管理请求 → 包含 change_management 技能."""
        registry = await _make_registry("monitoring", "troubleshooting", "change_management")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose("我要对生产环境的 RDS 实例 rm-bp1234567890 进行规格升级，帮我评估风险")

        assert len(plan.sub_tasks) >= 1
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "change_management" in skill_names, f"期望包含 change_management，实际 {skill_names}"

    @pytest.mark.asyncio
    async def test_qwen_output_has_valid_structure(self):
        """千问返回的子任务结构完整 — 所有必填字段非空."""
        registry = await _make_registry("monitoring")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose("查看云监控 CPU 指标")

        for task in plan.sub_tasks:
            assert task.task_id, "task_id 不能为空"
            assert task.skill_name, "skill_name 不能为空"
            assert task.action, "action 不能为空"
            assert isinstance(task.parameters, dict), "parameters 必须是 dict"
            assert isinstance(task.dependencies, list), "dependencies 必须是 list"

    @pytest.mark.asyncio
    async def test_qwen_topological_sort_on_real_plan(self):
        """千问分解结果可以正确拓扑排序 — 所有任务都出现在排序结果中."""
        registry = await _make_registry("monitoring", "troubleshooting")
        planner = TaskPlanner(self._qwen_factory(), registry)

        plan = await planner.decompose("先查看 ECS 监控指标，再排查网络问题")

        levels = planner.topological_sort(plan)
        assert len(levels) >= 1
        # 验证所有任务都在拓扑排序结果中（无遗漏）
        all_task_ids = {t.task_id for t in plan.sub_tasks}
        sorted_ids = {t.task_id for level in levels for t in level}
        assert all_task_ids == sorted_ids, f"拓扑排序遗漏任务: {all_task_ids - sorted_ids}"
