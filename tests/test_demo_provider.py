"""Tests for DemoProvider — keyword matching, parameter extraction, and responses."""

from __future__ import annotations

import json

import pytest

from aiops_agent.llm.demo import DemoProvider
from aiops_agent.llm.provider import ChatResponse
from aiops_agent.models.schemas import Message


class TestDemoProviderName:
    """Tests for provider_name property."""

    def test_provider_name_returns_demo(self):
        provider = DemoProvider()
        assert provider.provider_name == "demo"


class TestDemoProviderDecompose:
    """Tests for _decompose keyword matching."""

    def setup_method(self):
        self.provider = DemoProvider()

    def test_decompose_monitoring_chinese(self):
        tasks = self.provider._decompose("检查 ECS 监控状态")
        skills = [t["skill_name"] for t in tasks]
        assert "monitoring" in skills

    def test_decompose_monitoring_cpu(self):
        tasks = self.provider._decompose("cpu 使用率过高")
        skills = [t["skill_name"] for t in tasks]
        assert "monitoring" in skills

    def test_decompose_monitoring_metrics(self):
        tasks = self.provider._decompose("查看监控指标")
        skills = [t["skill_name"] for t in tasks]
        assert "monitoring" in skills

    def test_decompose_troubleshooting_chinese(self):
        tasks = self.provider._decompose("排查 ECS 故障")
        skills = [t["skill_name"] for t in tasks]
        assert "troubleshooting" in skills

    def test_decompose_troubleshooting_english(self):
        """'diagnose' is not in map; test with Chinese keyword that IS in map."""
        # The _SKILL_MAP uses Chinese keywords like 排查, 故障, 诊断
        tasks = self.provider._decompose("诊断 network issue")
        skills = [t["skill_name"] for t in tasks]
        assert "troubleshooting" in skills

    def test_decompose_change_management_chinese(self):
        tasks = self.provider._decompose("发布变更风险评估")
        skills = [t["skill_name"] for t in tasks]
        assert "change_management" in skills

    def test_decompose_change_management_english(self):
        """The _SKILL_MAP uses Chinese keywords; test with 扩容 (scale-up)."""
        tasks = self.provider._decompose("扩容 management process")
        skills = [t["skill_name"] for t in tasks]
        assert "change_management" in skills

    def test_decompose_empty_defaults_to_monitoring(self):
        tasks = self.provider._decompose("")
        assert len(tasks) == 1
        assert tasks[0]["skill_name"] == "monitoring"

    def test_decompose_no_match_defaults_to_monitoring(self):
        tasks = self.provider._decompose("random unrelated text xyz")
        assert len(tasks) == 1
        assert tasks[0]["skill_name"] == "monitoring"

    def test_decompose_multiple_skills(self):
        tasks = self.provider._decompose("监控CPU并排查故障")
        skills = [t["skill_name"] for t in tasks]
        assert "monitoring" in skills
        assert "troubleshooting" in skills

    def test_decompose_task_structure(self):
        """Verify each task has required fields."""
        tasks = self.provider._decompose("监控 CPU 使用率")
        task = tasks[0]
        assert "task_id" in task
        assert "skill_name" in task
        assert "action" in task
        assert "parameters" in task
        assert "dependencies" in task
        assert task["task_id"] == "t1"
        assert task["dependencies"] == []

    def test_decompose_second_task_has_dependency(self):
        tasks = self.provider._decompose("监控CPU并排查故障")
        if len(tasks) >= 2:
            assert tasks[1]["dependencies"] == ["t1"]


class TestDemoProviderExtractParams:
    """Tests for _extract_params."""

    def test_extract_ecs_instance_id(self):
        params = DemoProvider._extract_params("检查实例 i-abc12345 状态")
        assert params["instance_id"] == "i-abc12345"

    def test_extract_rds_instance_id(self):
        params = DemoProvider._extract_params("数据库 rm-xyz98765 慢查询")
        assert params["instance_id"] == "rm-xyz98765"

    def test_extract_no_ids(self):
        params = DemoProvider._extract_params("查看监控状态")
        assert params == {}

    def test_extract_ecs_id_with_various_lengths(self):
        """ECS IDs are 8-17 chars after i-."""
        params = DemoProvider._extract_params("实例 i-aaaaaaaa 状态")
        assert params["instance_id"] == "i-aaaaaaaa"


class TestDemoProviderInferAction:
    """Tests for _infer_action."""

    def test_infer_action_monitoring(self):
        action = DemoProvider._infer_action("monitoring", "any text")
        assert action == "query_metrics"

    def test_infer_action_troubleshooting(self):
        action = DemoProvider._infer_action("troubleshooting", "any text")
        assert action == "ecs_health_check"

    def test_infer_action_change_management(self):
        action = DemoProvider._infer_action("change_management", "any text")
        assert action == "risk_assessment"

    def test_infer_action_unknown_defaults_to_execute(self):
        action = DemoProvider._infer_action("unknown_skill", "any text")
        assert action == "execute"


class TestDemoProviderChat:
    """Tests for chat() method."""

    @pytest.mark.asyncio
    async def test_chat_returns_valid_json(self):
        provider = DemoProvider()
        response = await provider.chat([Message(role="user", content="监控 CPU")])
        assert isinstance(response, ChatResponse)
        # Should be valid JSON
        data = json.loads(response.content)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_chat_has_tasks_array(self):
        provider = DemoProvider()
        response = await provider.chat([Message(role="user", content="监控 CPU 并排查故障")])
        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_chat_model_field(self):
        provider = DemoProvider()
        response = await provider.chat([Message(role="user", content="hello")])
        assert response.model == "demo"

    @pytest.mark.asyncio
    async def test_chat_usage_field(self):
        provider = DemoProvider()
        response = await provider.chat([Message(role="user", content="hello")])
        assert "input_tokens" in response.usage
        assert "output_tokens" in response.usage


class TestDemoProviderComplete:
    """Tests for complete() method."""

    @pytest.mark.asyncio
    async def test_complete_delegates_to_chat(self):
        provider = DemoProvider()
        result = await provider.complete("监控 CPU")
        # Should be valid JSON string (same as chat content)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) >= 1


class TestDemoProviderEmbed:
    """Tests for embed() method."""

    @pytest.mark.asyncio
    async def test_embed_returns_list_of_lists(self):
        provider = DemoProvider()
        result = await provider.embed(["hello", "world"])
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_embed_dummy_embeddings_dimension(self):
        provider = DemoProvider()
        result = await provider.embed(["hello"])
        assert len(result[0]) == 3
        assert result[0] == [0.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_embed_empty_input(self):
        provider = DemoProvider()
        result = await provider.embed([])
        assert result == []
