"""SkillRegistry 单元测试."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import SkillDefinition, ValidationResult
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SimpleSkill(SkillInstance):
    def __init__(self, healthy: bool = True):
        super().__init__()
        self._healthy = healthy

    async def execute(self, input_data: dict) -> dict:
        return {"status": "ok"}

    async def validate(self, input_data: dict) -> ValidationResult:
        return ValidationResult(valid=True)

    async def health_check(self) -> bool:
        return self._healthy


def _make_defn(name: str, version: str = "1.0.0", status: str = "healthy") -> SkillDefinition:
    return SkillDefinition(
        skill_name=name,
        description=f"{name} skill",
        version=version,
        capabilities=[f"{name}_cap"],
        status=status,
    )


# ---------------------------------------------------------------------------
# Test: Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_skill(self, skill_registry: SkillRegistry) -> None:
        skill = SimpleSkill()
        defn = _make_defn("test_skill")

        await skill_registry.register(defn, skill)

        result = await skill_registry.get_skill("test_skill")
        assert result is not None
        assert isinstance(result, SimpleSkill)

    @pytest.mark.asyncio
    async def test_register_duplicate_version(self, skill_registry: SkillRegistry) -> None:
        skill = SimpleSkill()
        defn = _make_defn("test_skill", version="1.0.0")

        await skill_registry.register(defn, skill)

        with pytest.raises(ValueError, match="已注册"):
            await skill_registry.register(defn, skill)

    @pytest.mark.asyncio
    async def test_register_different_version(self, skill_registry: SkillRegistry) -> None:
        skill1 = SimpleSkill()
        skill2 = SimpleSkill()
        defn1 = _make_defn("test_skill", version="1.0.0")
        defn2 = _make_defn("test_skill", version="2.0.0")

        await skill_registry.register(defn1, skill1)
        await skill_registry.register(defn2, skill2)

        # 默认是最新版本
        result = await skill_registry.get_skill("test_skill")
        assert result is not None


# ---------------------------------------------------------------------------
# Test: Unregistration
# ---------------------------------------------------------------------------


class TestUnregistration:
    @pytest.mark.asyncio
    async def test_unregister_specific_version(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test", "1.0.0"), SimpleSkill())
        await skill_registry.register(_make_defn("test", "2.0.0"), SimpleSkill())

        await skill_registry.unregister("test", version="1.0.0")

        assert await skill_registry.get_skill("test", version="1.0.0") is None
        assert await skill_registry.get_skill("test", version="2.0.0") is not None

    @pytest.mark.asyncio
    async def test_unregister_all_versions(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test", "1.0.0"), SimpleSkill())

        await skill_registry.unregister("test")

        assert await skill_registry.get_skill("test") is None


# ---------------------------------------------------------------------------
# Test: Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_discover_by_capability(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("monitoring"), SimpleSkill())
        await skill_registry.register(_make_defn("troubleshooting"), SimpleSkill())

        results = await skill_registry.discover(["monitoring_cap"])

        assert len(results) == 1
        assert results[0].skill_name == "monitoring"

    @pytest.mark.asyncio
    async def test_discover_ranked_by_match_count(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(SkillDefinition(
            skill_name="skill_a", description="Skill A", version="1.0.0",
            capabilities=["cap1", "cap2", "cap3"],
        ), SimpleSkill())
        await skill_registry.register(SkillDefinition(
            skill_name="skill_b", description="Skill B", version="1.0.0",
            capabilities=["cap1"],
        ), SimpleSkill())

        results = await skill_registry.discover(["cap1", "cap2", "cap3"])

        # skill_a has 3 matching caps, skill_b has 1 → skill_a ranks first
        assert len(results) == 2
        assert results[0].skill_name == "skill_a"

    @pytest.mark.asyncio
    async def test_discover_excludes_unhealthy(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("healthy_skill", status="healthy"), SimpleSkill())
        await skill_registry.register(_make_defn("unhealthy_skill", status="unhealthy"), SimpleSkill())

        results = await skill_registry.discover(["healthy_skill_cap", "unhealthy_skill_cap"])

        assert len(results) == 1
        assert results[0].skill_name == "healthy_skill"


# ---------------------------------------------------------------------------
# Test: Health management
# ---------------------------------------------------------------------------


class TestHealthManagement:
    @pytest.mark.asyncio
    async def test_health_check_healthy(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test"), SimpleSkill(healthy=True))
        assert await skill_registry.health_check("test") is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test"), SimpleSkill(healthy=False))
        assert await skill_registry.health_check("test") is False

    @pytest.mark.asyncio
    async def test_mark_unhealthy(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test"), SimpleSkill())
        await skill_registry.mark_unhealthy("test")

        defn = skill_registry.get_definition("test")
        assert defn is not None
        assert defn.status == "unhealthy"

    @pytest.mark.asyncio
    async def test_mark_healthy_restores(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test"), SimpleSkill())
        await skill_registry.mark_unhealthy("test")
        await skill_registry.mark_healthy("test")

        defn = skill_registry.get_definition("test")
        assert defn is not None
        assert defn.status == "healthy"


# ---------------------------------------------------------------------------
# Test: Version management
# ---------------------------------------------------------------------------


class TestVersionManagement:
    @pytest.mark.asyncio
    async def test_default_version_is_latest_healthy(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("test", "1.0.0"), SimpleSkill())
        await skill_registry.register(_make_defn("test", "2.0.0"), SimpleSkill())

        result = await skill_registry.get_skill("test")
        assert result is not None

        defn = skill_registry.get_definition("test")
        assert defn is not None
        assert defn.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_list_skills_returns_default_versions(self, skill_registry: SkillRegistry) -> None:
        await skill_registry.register(_make_defn("skill_a", "1.0.0"), SimpleSkill())
        await skill_registry.register(_make_defn("skill_b", "1.0.0"), SimpleSkill())

        skills = skill_registry.list_skills()
        assert len(skills) == 2


# ---------------------------------------------------------------------------
# Test: Validation
# ---------------------------------------------------------------------------


class TestDefinitionValidation:
    @pytest.mark.asyncio
    async def test_missing_skill_name(self, skill_registry: SkillRegistry) -> None:
        defn = SkillDefinition(
            skill_name="",
            description="test",
            version="1.0.0",
        )
        with pytest.raises(ValueError, match="skill_name"):
            await skill_registry.register(defn, SimpleSkill())

    @pytest.mark.asyncio
    async def test_missing_description(self, skill_registry: SkillRegistry) -> None:
        defn = SkillDefinition(
            skill_name="test",
            description="",
            version="1.0.0",
        )
        with pytest.raises(ValueError, match="description"):
            await skill_registry.register(defn, SimpleSkill())

    @pytest.mark.asyncio
    async def test_missing_version(self, skill_registry: SkillRegistry) -> None:
        defn = SkillDefinition(
            skill_name="test",
            description="test",
            version="",
        )
        with pytest.raises(ValueError, match="version"):
            await skill_registry.register(defn, SimpleSkill())
