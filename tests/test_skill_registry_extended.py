"""Extended tests for SkillRegistry.

Covers: multi-version registration, unregister variants, discover (fuzzy match,
health filtering), health_check, mark_unhealthy/mark_healthy,
_update_default_version, _validate_definition, get_skill/get_definition with
version parameter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import SkillDefinition
from aiops_agent.skills.base import SkillInstance
from aiops_agent.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_def(name: str, version: str, capabilities: list[str] | None = None,
                    status: str = "healthy") -> SkillDefinition:
    return SkillDefinition(
        skill_name=name,
        description=f"{name} skill",
        version=version,
        capabilities=capabilities or [name],
        status=status,
    )


class _DummySkill(SkillInstance):
    """Concrete SkillInstance for testing."""

    def __init__(self, health_ok: bool = True) -> None:
        super().__init__()
        self._health_ok = health_ok

    async def execute(self, input_data: dict) -> dict:
        return {"status": "ok"}

    async def validate(self, input_data: dict):
        from aiops_agent.models.schemas import ValidationResult
        return ValidationResult(valid=True)

    async def health_check(self) -> bool:
        if not self._health_ok:
            raise RuntimeError("unhealthy")
        return True


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


# ---------------------------------------------------------------------------
# Multi-version registration
# ---------------------------------------------------------------------------


async def test_register_multiple_versions_same_skill(registry):
    """Register multiple versions of same skill."""
    d1 = _make_skill_def("deploy", "1.0.0")
    d2 = _make_skill_def("deploy", "2.0.0")
    await registry.register(d1, _DummySkill())
    await registry.register(d2, _DummySkill())

    assert len(registry._skills["deploy"]) == 2
    # Default should be latest registered healthy version
    assert registry._default_versions["deploy"] == "2.0.0"


# ---------------------------------------------------------------------------
# Unregister
# ---------------------------------------------------------------------------


async def test_unregister_specific_version_keeps_others(registry):
    """Unregister specific version keeps others."""
    d1 = _make_skill_def("deploy", "1.0.0")
    d2 = _make_skill_def("deploy", "2.0.0")
    await registry.register(d1, _DummySkill())
    await registry.register(d2, _DummySkill())

    await registry.unregister("deploy", version="1.0.0")

    assert "1.0.0" not in registry._skills.get("deploy", {})
    assert "2.0.0" in registry._skills["deploy"]


async def test_unregister_all_versions_cleans_up_defaults(registry):
    """Unregister all versions cleans up defaults."""
    d1 = _make_skill_def("deploy", "1.0.0")
    await registry.register(d1, _DummySkill())
    assert "deploy" in registry._default_versions

    await registry.unregister("deploy")

    assert "deploy" not in registry._skills
    assert "deploy" not in registry._default_versions


# ---------------------------------------------------------------------------
# Discover — fuzzy match
# ---------------------------------------------------------------------------


async def test_discover_fuzzy_match_sorted_by_count(registry):
    """discover: fuzzy match by capability overlap, sorted by count."""
    s1 = _make_skill_def("monitor", "1.0.0", ["monitoring", "alerting", "logging"])
    s2 = _make_skill_def("alert", "1.0.0", ["alerting"])
    s3 = _make_skill_def("log", "1.0.0", ["logging", "storage"])

    await registry.register(s1, _DummySkill())
    await registry.register(s2, _DummySkill())
    await registry.register(s3, _DummySkill())

    results = await registry.discover(["monitoring", "alerting"])

    # s1 has 2 matching capabilities, s2 has 1, s3 has 0
    assert len(results) == 2
    assert results[0].skill_name == "monitor"  # overlap=2
    assert results[1].skill_name == "alert"    # overlap=1


# ---------------------------------------------------------------------------
# Discover — health filtering
# ---------------------------------------------------------------------------


async def test_discover_only_healthy_default_versions(registry):
    """discover: only returns healthy skills with default versions."""
    healthy = _make_skill_def("good", "1.0.0", ["ops"])
    unhealthy = _make_skill_def("bad", "1.0.0", ["ops"], status="unhealthy")

    await registry.register(healthy, _DummySkill())
    await registry.register(unhealthy, _DummySkill())

    results = await registry.discover(["ops"])

    assert len(results) == 1
    assert results[0].skill_name == "good"


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


async def test_health_check_calls_instance_method(registry):
    """health_check: calls instance method, updates status on exception."""
    skill = _DummySkill(health_ok=True)
    d = _make_skill_def("check", "1.0.0")
    await registry.register(d, skill)

    result = await registry.health_check("check")
    assert result is True
    assert d.status == "healthy"


async def test_health_check_updates_status_on_exception(registry):
    """health_check: updates status to unhealthy on instance exception."""
    skill = _DummySkill(health_ok=False)
    d = _make_skill_def("failing", "1.0.0")
    await registry.register(d, skill)

    result = await registry.health_check("failing")
    assert result is False
    assert d.status == "unhealthy"


# ---------------------------------------------------------------------------
# mark_unhealthy / mark_healthy
# ---------------------------------------------------------------------------


async def test_mark_unhealthy_updates_status(registry):
    """mark_unhealthy: updates status."""
    skill = _DummySkill()
    d = _make_skill_def("tag", "1.0.0")
    await registry.register(d, skill)

    assert d.status == "healthy"
    await registry.mark_unhealthy("tag")
    assert d.status == "unhealthy"


async def test_mark_healthy_updates_status(registry):
    """mark_healthy: updates status."""
    skill = _DummySkill()
    d = _make_skill_def("recover", "1.0.0")  # register as healthy first
    await registry.register(d, skill)
    assert d.status == "healthy"

    await registry.mark_unhealthy("recover")
    assert d.status == "unhealthy"

    await registry.mark_healthy("recover")
    assert d.status == "healthy"


# ---------------------------------------------------------------------------
# _update_default_version
# ---------------------------------------------------------------------------


def test_update_default_version_picks_latest_healthy(registry):
    """_update_default_version: picks latest healthy version."""
    d1 = _make_skill_def("ver", "1.0.0")
    d2 = _make_skill_def("ver", "2.0.0")
    d3 = _make_skill_def("ver", "3.0.0", status="unhealthy")

    registry._skills["ver"]["1.0.0"] = (d1, _DummySkill())
    registry._skills["ver"]["2.0.0"] = (d2, _DummySkill())
    registry._skills["ver"]["3.0.0"] = (d3, _DummySkill())

    # Set default to 3.0.0, then update
    registry._default_versions["ver"] = "3.0.0"
    registry._update_default_version("ver")

    # Should pick latest healthy = 2.0.0 (3.0.0 is unhealthy)
    assert registry._default_versions["ver"] == "2.0.0"


# ---------------------------------------------------------------------------
# _validate_definition
# ---------------------------------------------------------------------------


def test_validate_definition_empty_name():
    """_validate_definition: empty name returns errors."""
    d = SkillDefinition(skill_name="", description="desc", version="1.0")
    errors = SkillRegistry._validate_definition(d)
    assert any("skill_name" in e for e in errors)


def test_validate_definition_empty_description():
    """_validate_definition: empty description returns errors."""
    d = SkillDefinition(skill_name="test", description="", version="1.0")
    errors = SkillRegistry._validate_definition(d)
    assert any("description" in e for e in errors)


def test_validate_definition_empty_version():
    """_validate_definition: empty version returns errors."""
    d = SkillDefinition(skill_name="test", description="desc", version="")
    errors = SkillRegistry._validate_definition(d)
    assert any("version" in e for e in errors)


def test_validate_definition_all_valid():
    """_validate_definition: valid definition returns no errors."""
    d = SkillDefinition(skill_name="test", description="desc", version="1.0")
    errors = SkillRegistry._validate_definition(d)
    assert errors == []


# ---------------------------------------------------------------------------
# get_skill with version parameter
# ---------------------------------------------------------------------------


async def test_get_skill_with_version(registry):
    """get_skill with version parameter."""
    d1 = _make_skill_def("multi", "1.0.0")
    d2 = _make_skill_def("multi", "2.0.0")
    s1, s2 = _DummySkill(), _DummySkill()
    await registry.register(d1, s1)
    await registry.register(d2, s2)

    # Default version
    result = await registry.get_skill("multi")
    assert result is s2

    # Specific version
    result = await registry.get_skill("multi", version="1.0.0")
    assert result is s1

    # Non-existent version
    result = await registry.get_skill("multi", version="99.0.0")
    assert result is None


# ---------------------------------------------------------------------------
# get_definition with version parameter
# ---------------------------------------------------------------------------


def test_get_definition_with_version(registry):
    """get_definition with version parameter."""
    d1 = _make_skill_def("def", "1.0.0")
    d2 = _make_skill_def("def", "2.0.0")
    registry._skills["def"]["1.0.0"] = (d1, _DummySkill())
    registry._skills["def"]["2.0.0"] = (d2, _DummySkill())
    registry._default_versions["def"] = "2.0.0"

    # Default version
    result = registry.get_definition("def")
    assert result is d2

    # Specific version
    result = registry.get_definition("def", version="1.0.0")
    assert result is d1

    # Non-existent skill
    result = registry.get_definition("nonexistent")
    assert result is None
