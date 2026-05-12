"""Tests for SkillInstance abstract base class.

Covers: abstract class enforcement, tool executor injection, default lifecycle
hooks (on_register, on_unregister, health_check), and concrete subclass creation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiops_agent.models.schemas import ValidationResult
from aiops_agent.skills.base import SkillInstance


# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------


class ConcreteSkill(SkillInstance):
    """Concrete implementation of SkillInstance for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.execute_called = False
        self.validate_called = False

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.execute_called = True
        return {"result": "executed", "input": input_data}

    async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
        self.validate_called = True
        return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Abstract class enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_abstract_class():
    """Abstract class: cannot instantiate directly."""
    with pytest.raises(TypeError):
        SkillInstance()


# ---------------------------------------------------------------------------
# Tool executor injection
# ---------------------------------------------------------------------------


def test_tool_executor_injection():
    """Tool executor injection pattern."""
    skill = ConcreteSkill()
    assert skill.tool_executor is None

    mock_executor = AsyncMock()
    skill.set_tool_executor(mock_executor)

    assert skill.tool_executor is mock_executor


def test_tool_executor_can_be_set_and_retrieved():
    """Tool executor can be set and retrieved."""
    skill = ConcreteSkill()
    executor = AsyncMock(name="test-executor")

    skill.set_tool_executor(executor)
    assert skill.tool_executor is executor
    assert skill._tool_executor is executor


# ---------------------------------------------------------------------------
# on_register default implementation
# ---------------------------------------------------------------------------


async def test_on_register_default_noop():
    """on_register default implementation (no-op)."""
    skill = ConcreteSkill()
    # Should not raise, should return None
    result = await skill.on_register()
    assert result is None


# ---------------------------------------------------------------------------
# on_unregister default implementation
# ---------------------------------------------------------------------------


async def test_on_unregister_default_noop():
    """on_unregister default implementation (no-op)."""
    skill = ConcreteSkill()
    # Should not raise, should return None
    result = await skill.on_unregister()
    assert result is None


# ---------------------------------------------------------------------------
# health_check default implementation
# ---------------------------------------------------------------------------


async def test_health_check_default_returns_true():
    """health_check default returns True."""
    skill = ConcreteSkill()
    result = await skill.health_check()
    assert result is True


# ---------------------------------------------------------------------------
# Concrete subclass behavior
# ---------------------------------------------------------------------------


async def test_concrete_subclass_execute():
    """Create concrete subclass for testing — execute works."""
    skill = ConcreteSkill()
    result = await skill.execute({"key": "value"})

    assert skill.execute_called is True
    assert result["result"] == "executed"
    assert result["input"] == {"key": "value"}


async def test_concrete_subclass_validate():
    """Create concrete subclass for testing — validate works."""
    skill = ConcreteSkill()
    result = await skill.validate({"key": "value"})

    assert skill.validate_called is True
    assert isinstance(result, ValidationResult)
    assert result.valid is True


async def test_concrete_subclass_overrides_hooks():
    """Concrete subclass can override lifecycle hooks."""

    class HookSkill(SkillInstance):
        def __init__(self) -> None:
            super().__init__()
            self.registered = False
            self.unregistered = False
            self.health_value = False

        async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def validate(self, input_data: dict[str, Any]) -> ValidationResult:
            return ValidationResult(valid=True)

        async def on_register(self) -> None:
            self.registered = True

        async def on_unregister(self) -> None:
            self.unregistered = True

        async def health_check(self) -> bool:
            return self.health_value

    skill = HookSkill()

    # Test overridden on_register
    await skill.on_register()
    assert skill.registered is True

    # Test overridden on_unregister
    await skill.on_unregister()
    assert skill.unregistered is True

    # Test overridden health_check
    skill.health_value = True
    assert await skill.health_check() is True

    skill.health_value = False
    assert await skill.health_check() is False
