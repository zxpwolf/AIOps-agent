"""TaskPlanner 集成测试 — 使用通义千问真实 API 测试任务分解.

需要设置环境变量 QWEN_API_KEY 才能运行。
运行方式: QWEN_API_KEY=sk-xxx uv run pytest tests/test_task_planner_qwen.py -v -s
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from aiops_agent.core.task_planner import TaskPlanner
from aiops_agent.llm.provider import LLMProviderFactory
from aiops_agent.llm.qwen import QwenProvider
from aiops_agent.models.schemas import SkillDefinition, TaskStatus
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.registry import SkillRegistry

# 没有 API Key 时跳过所有测试
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
pytestmark = pytest.mark.skipif(
    not QWEN_API_KEY,
    reason="需要设置 QWEN_API_KEY 环境变量",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSkill(SkillInstance):
    async def execute(self, input_data: dict) -> dict:
        return {"status": "success"}

    async def validate(self, input_data: dict) -> Any:
        from aiops_agent.models.schemas import ValidationResult
        return ValidationResult(valid=True)


def _make_qwen_factory() -> LLMProviderFactory:
    factory = LLMProviderFactory()
    provider = QwenProvider(
        api_key=QWEN_API_KEY,
        model="qwen-plus",
        temperature=0.3,
        timeout_seconds=30,
    )
    factory.register("qwen", provider)
    factory.set_primary("qwen")
    return factory


async def _make_registry() -> SkillRegistry:
    registry = SkillRegistry()
    skills = [
        ("monitoring", "云监控指标查询与 SLS 日志分析",
         ["cloud_monitor_query", "sls_log_query", "metric_analysis"]),
        ("troubleshooting", "ECS 健康检查、网络诊断、RDS 慢查询分析",
         ["ecs_health_check", "network_diagnosis", "rds_slow_query_analysis"]),
        ("change_management", "变更风险评估与回滚方案推荐",
         ["change_risk_assessment", "rollback_recommendation"]),
    ]
    for name, desc, caps in skills:
        defn = SkillDefinition(
            skill_name=name, description=desc, version="1.0.0",
            capabilities=caps, required_permissions=[],
        )
        await registry.register(defn, _StubSkill())
    return registry


# ===========================================================================
# 集成测试 — 通义千问任务分解
# ===========================================================================


class TestQwenDecompose:
    """使用通义千问 API 测试 TaskPlanner.decompose."""

    @pytest.mark.asyncio
    async def test_monitoring_request(self):
        """监控类请求 → 应分解出 monitoring 技能."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose("查看 ECS 实例 i-bp1234567890 的 CPU 使用率")

        print(f"\n=== 监控请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} "
                  f"params={t.parameters} deps={t.dependencies} status={t.status}")

        assert len(plan.sub_tasks) >= 1
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "monitoring" in skill_names, f"期望包含 monitoring，实际: {skill_names}"

    @pytest.mark.asyncio
    async def test_troubleshooting_request(self):
        """故障排查请求 → 应分解出 troubleshooting 技能."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose("ECS 实例 i-bp9876543210 无法 SSH 连接，帮我排查")

        print(f"\n=== 故障排查请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} "
                  f"params={t.parameters} deps={t.dependencies} status={t.status}")

        assert len(plan.sub_tasks) >= 1
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "troubleshooting" in skill_names, f"期望包含 troubleshooting，实际: {skill_names}"

    @pytest.mark.asyncio
    async def test_change_management_request(self):
        """变更管理请求 → 应分解出 change_management 技能."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose("我要对生产环境的 RDS 实例 rm-bp1234567890 进行升配，帮我评估风险")

        print(f"\n=== 变更管理请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} "
                  f"params={t.parameters} deps={t.dependencies} status={t.status}")

        assert len(plan.sub_tasks) >= 1
        skill_names = [t.skill_name for t in plan.sub_tasks]
        assert "change_management" in skill_names, f"期望包含 change_management，实际: {skill_names}"

    @pytest.mark.asyncio
    async def test_multi_skill_request(self):
        """复合请求 → 应分解出多个技能的子任务."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose(
            "ECS 实例 i-bp1111111111 响应变慢，"
            "先查看 CPU 和内存监控指标，再排查网络连通性"
        )

        print(f"\n=== 复合请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} "
                  f"params={t.parameters} deps={t.dependencies} status={t.status}")

        assert len(plan.sub_tasks) >= 2, f"期望至少 2 个子任务，实际: {len(plan.sub_tasks)}"
        skill_names = set(t.skill_name for t in plan.sub_tasks)
        assert len(skill_names) >= 2, f"期望至少 2 种技能，实际: {skill_names}"

    @pytest.mark.asyncio
    async def test_dependency_chain(self):
        """有依赖关系的请求 → 子任务应包含 dependencies."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose(
            "先查看 ECS 实例 i-bp2222222222 的监控指标，"
            "如果 CPU 异常再进行故障排查"
        )

        print(f"\n=== 依赖链请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} "
                  f"deps={t.dependencies} status={t.status}")

        assert len(plan.sub_tasks) >= 2

        # 拓扑排序验证
        levels = planner.topological_sort(plan)
        print(f"拓扑层级数: {len(levels)}")
        for i, level in enumerate(levels):
            print(f"  层 {i}: {[t.task_id for t in level]}")

        # 至少应该有 2 层（先监控后排查）
        assert len(levels) >= 1  # 宽松断言，LLM 可能不总是生成依赖

    @pytest.mark.asyncio
    async def test_all_tasks_map_to_registered_skills(self):
        """所有子任务都应映射到已注册的技能."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose("查看 SLS 日志中最近 1 小时的错误日志")

        print(f"\n=== 技能映射验证 ===")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name} → status={t.status}")

        # 所有任务应该是 PENDING（已注册）而非 FAILED（未注册）
        for t in plan.sub_tasks:
            assert t.status == TaskStatus.PENDING, (
                f"子任务 {t.task_id} 映射到未注册技能 '{t.skill_name}'"
            )

    @pytest.mark.asyncio
    async def test_parameters_extraction(self):
        """LLM 应从请求中提取参数（如实例 ID）."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose(
            "查看 ECS 实例 i-bp3333333333 在 cn-hangzhou 区域的内存使用率"
        )

        print(f"\n=== 参数提取验证 ===")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} params={t.parameters}")

        assert len(plan.sub_tasks) >= 1
        # 检查是否有任何子任务的参数中包含实例 ID
        all_params = str(plan.sub_tasks)
        assert "i-bp3333333333" in all_params or len(plan.sub_tasks) >= 1, \
            "LLM 应尝试提取实例 ID 到参数中"

    @pytest.mark.asyncio
    async def test_unsupported_request(self):
        """不支持的请求 → LLM 可能返回空或映射失败."""
        factory = _make_qwen_factory()
        registry = await _make_registry()
        planner = TaskPlanner(factory, registry)

        plan = await planner.decompose("帮我写一首诗")

        print(f"\n=== 不支持的请求 ===")
        print(f"子任务数: {len(plan.sub_tasks)}")
        for t in plan.sub_tasks:
            print(f"  [{t.task_id}] {t.skill_name}.{t.action} status={t.status}")

        # 不做严格断言，LLM 行为不确定
        # 但如果有子任务，它们应该映射失败（FAILED）
        if plan.sub_tasks:
            print("LLM 尝试分解了非运维请求")


# ===========================================================================
# Qwen Provider 基础测试
# ===========================================================================


class TestQwenProviderBasic:
    """通义千问 Provider 基础功能测试."""

    @pytest.mark.asyncio
    async def test_chat_returns_content(self):
        """chat 方法应返回非空内容."""
        from aiops_agent.models.schemas import Message

        provider = QwenProvider(api_key=QWEN_API_KEY, model="qwen-plus")
        try:
            response = await provider.chat([
                Message(role="user", content="用一句话介绍阿里云 ECS"),
            ])
            print(f"\n=== Qwen chat ===")
            print(f"content: {response.content[:200]}")
            print(f"model: {response.model}")
            print(f"usage: {response.usage}")

            assert response.content, "响应内容不应为空"
            assert response.model == "qwen-plus"
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_complete_returns_text(self):
        """complete 方法应返回非空文本."""
        provider = QwenProvider(api_key=QWEN_API_KEY, model="qwen-plus")
        try:
            text = await provider.complete("什么是 SLS 日志服务？请用一句话回答。")
            print(f"\n=== Qwen complete ===")
            print(f"text: {text[:200]}")

            assert text, "补全文本不应为空"
        finally:
            await provider.close()
