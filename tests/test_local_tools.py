"""Tests for aiops_agent.tools.local_tools."""

import asyncio

import pytest

from aiops_agent.tools.local_tools import (
    LocalToolDefinition,
    LocalToolRegistry,
    _check_type,
)


# ────────────────────────────────────────────────────────────
# LocalToolDefinition
# ────────────────────────────────────────────────────────────

class TestLocalToolDefinition:
    def test_basic_attributes(self):
        def handler():
            pass

        tool = LocalToolDefinition(
            name="greet",
            description="Say hello",
            handler=handler,
        )
        assert tool.name == "greet"
        assert tool.description == "Say hello"
        assert tool.handler is handler
        assert tool.parameters_schema == {}

    def test_with_schema(self):
        def handler(x: int):
            return x

        schema = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
        tool = LocalToolDefinition(
            name="double",
            description="Double a number",
            handler=handler,
            parameters_schema=schema,
        )
        assert tool.parameters_schema == schema

    def test_repr(self):
        def handler():
            pass

        tool = LocalToolDefinition(name="test_tool", description="desc", handler=handler)
        assert "test_tool" in repr(tool)
        assert "LocalToolDefinition" in repr(tool)


# ────────────────────────────────────────────────────────────
# Register / Unregister lifecycle
# ────────────────────────────────────────────────────────────

class TestRegisterUnregister:
    def test_register_tool(self):
        registry = LocalToolRegistry()
        registry.register("hello", "Say hello", lambda: "hello")
        assert registry.has_tool("hello")

    def test_unregister_tool(self):
        registry = LocalToolRegistry()
        registry.register("temp", "Temporary", lambda: "x")
        assert registry.has_tool("temp")
        registry.unregister("temp")
        assert not registry.has_tool("temp")

    def test_unregister_nonexistent_does_not_raise(self):
        registry = LocalToolRegistry()
        registry.unregister("does_not_exist")  # Should not raise

    def test_register_multiple_tools(self):
        registry = LocalToolRegistry()
        registry.register("a", "Tool A", lambda: "a")
        registry.register("b", "Tool B", lambda: "b")
        registry.register("c", "Tool C", lambda: "c")
        assert registry.has_tool("a")
        assert registry.has_tool("b")
        assert registry.has_tool("c")

    def test_duplicate_name_raises_value_error(self):
        registry = LocalToolRegistry()
        registry.register("dup", "First", lambda: 1)
        with pytest.raises(ValueError, match="已注册"):
            registry.register("dup", "Second", lambda: 2)


# ────────────────────────────────────────────────────────────
# get() and has_tool()
# ────────────────────────────────────────────────────────────

class TestGetAndHasTool:
    def test_get_returns_definition(self):
        registry = LocalToolRegistry()
        registry.register("fetch", "Fetch data", lambda: "data")
        tool = registry.get("fetch")
        assert tool is not None
        assert tool.name == "fetch"
        assert tool.description == "Fetch data"

    def test_get_returns_none_for_unknown(self):
        registry = LocalToolRegistry()
        assert registry.get("unknown") is None

    def test_has_tool_true(self):
        registry = LocalToolRegistry()
        registry.register("exist", "Exists", lambda: 1)
        assert registry.has_tool("exist") is True

    def test_has_tool_false(self):
        registry = LocalToolRegistry()
        assert registry.has_tool("nonexistent") is False

    def test_has_tool_after_unregister(self):
        registry = LocalToolRegistry()
        registry.register("tmp", "Temp", lambda: 1)
        assert registry.has_tool("tmp") is True
        registry.unregister("tmp")
        assert registry.has_tool("tmp") is False


# ────────────────────────────────────────────────────────────
# list_tools()
# ────────────────────────────────────────────────────────────

class TestListTools:
    def test_empty_registry(self):
        registry = LocalToolRegistry()
        assert registry.list_tools() == []

    def test_returns_all_tools(self):
        registry = LocalToolRegistry()
        registry.register("a", "A", lambda: 1)
        registry.register("b", "B", lambda: 2)
        registry.register("c", "C", lambda: 3)
        tools = registry.list_tools()
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"a", "b", "c"}

    def test_returns_copy(self):
        """list_tools should return a copy so mutations don't affect registry."""
        registry = LocalToolRegistry()
        registry.register("x", "X", lambda: 1)
        tools = registry.list_tools()
        tools.clear()
        assert len(registry.list_tools()) == 1


# ────────────────────────────────────────────────────────────
# Calling sync handler
# ────────────────────────────────────────────────────────────

class TestCallSyncHandler:
    def test_call_sync_tool(self):
        registry = LocalToolRegistry()
        registry.register(
            "add",
            "Add two numbers",
            lambda a, b: a + b,
        )
        result = asyncio.run(registry.call("add", {"a": 3, "b": 4}))
        assert result == 7

    def test_call_sync_tool_no_args(self):
        registry = LocalToolRegistry()
        registry.register("ping", "Ping", lambda: "pong")
        result = asyncio.run(registry.call("ping", {}))
        assert result == "pong"

    def test_call_sync_tool_with_dict_return(self):
        registry = LocalToolRegistry()
        registry.register("info", "Get info", lambda: {"status": "ok"})
        result = asyncio.run(registry.call("info", {}))
        assert result == {"status": "ok"}


# ────────────────────────────────────────────────────────────
# Calling async handler
# ────────────────────────────────────────────────────────────

class TestCallAsyncHandler:
    @pytest.mark.asyncio
    async def test_call_async_tool(self):
        registry = LocalToolRegistry()

        async def async_multiply(x, y):
            return x * y

        registry.register("multiply", "Multiply", async_multiply)
        result = await registry.call("multiply", {"x": 6, "y": 7})
        assert result == 42

    @pytest.mark.asyncio
    async def test_call_async_tool_no_args(self):
        registry = LocalToolRegistry()

        async def async_hello():
            return "hello async"

        registry.register("async_hello", "Async hello", async_hello)
        result = await registry.call("async_hello", {})
        assert result == "hello async"


# ────────────────────────────────────────────────────────────
# Parameter validation — missing required fields
# ────────────────────────────────────────────────────────────

class TestParameterValidationRequired:
    """Missing required fields raise TypeError (as per source code)."""

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        registry.register("greet", "Greet", lambda name: f"Hi {name}", parameters_schema=schema)
        with pytest.raises(TypeError, match="缺少必填参数"):
            await registry.call("greet", {})

    @pytest.mark.asyncio
    async def test_missing_one_of_multiple_required(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required": ["host", "port"],
        }

        def connect(host, port):
            return f"{host}:{port}"

        registry.register("connect", "Connect", connect, parameters_schema=schema)
        with pytest.raises(TypeError, match="缺少必填参数"):
            await registry.call("connect", {"host": "localhost"})  # missing port

    @pytest.mark.asyncio
    async def test_all_required_present_passes(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required": ["host", "port"],
        }

        def connect(host, port):
            return f"{host}:{port}"

        registry.register("connect", "Connect", connect, parameters_schema=schema)
        result = await registry.call("connect", {"host": "localhost", "port": 8080})
        assert result == "localhost:8080"


# ────────────────────────────────────────────────────────────
# Parameter type mismatch
# ────────────────────────────────────────────────────────────

class TestParameterTypeMismatch:
    """Type mismatch raises TypeError."""

    @pytest.mark.asyncio
    async def test_string_vs_integer_raises(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        registry.register("count", "Count", lambda count: count, parameters_schema=schema)
        with pytest.raises(TypeError, match="类型错误"):
            await registry.call("count", {"count": "not_a_number"})

    @pytest.mark.asyncio
    async def test_integer_vs_string_raises(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        registry.register("greet", "Greet", lambda name: name, parameters_schema=schema)
        with pytest.raises(TypeError, match="类型错误"):
            await registry.call("greet", {"name": 123})

    @pytest.mark.asyncio
    async def test_correct_type_passes(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        registry.register(
            "person",
            "Person info",
            lambda name, age: f"{name} is {age}",
            parameters_schema=schema,
        )
        result = await registry.call("person", {"name": "Alice", "age": 30})
        assert result == "Alice is 30"


# ────────────────────────────────────────────────────────────
# No schema = pass-through
# ────────────────────────────────────────────────────────────

class TestNoSchemaPassthrough:
    """Tools without a schema should accept any arguments."""

    @pytest.mark.asyncio
    async def test_no_schema_accepts_any_args(self):
        registry = LocalToolRegistry()
        registry.register("echo", "Echo", lambda **kwargs: kwargs)
        result = await registry.call("echo", {"anything": "goes", "foo": 123, "bar": True})
        assert result == {"anything": "goes", "foo": 123, "bar": True}

    @pytest.mark.asyncio
    async def test_empty_schema_accepts_any_args(self):
        registry = LocalToolRegistry()
        registry.register("echo", "Echo", lambda **kwargs: kwargs, parameters_schema={})
        result = await registry.call("echo", {"x": 1, "y": 2})
        assert result == {"x": 1, "y": 2}


# ────────────────────────────────────────────────────────────
# Unknown JSON Schema type = pass-through
# ────────────────────────────────────────────────────────────

class TestUnknownSchemaType:
    """Unknown JSON Schema types should not raise — _check_type returns True."""

    @pytest.mark.asyncio
    async def test_unknown_type_passes(self):
        registry = LocalToolRegistry()
        schema = {
            "type": "object",
            "properties": {"data": {"type": "unknown_type"}},
        }
        registry.register("test", "Test", lambda data: data, parameters_schema=schema)
        result = await registry.call("test", {"data": "anything"})
        assert result == "anything"


# ────────────────────────────────────────────────────────────
# Calling unregistered tool
# ────────────────────────────────────────────────────────────

class TestCallUnregisteredTool:
    @pytest.mark.asyncio
    async def test_unregistered_tool_raises_value_error(self):
        registry = LocalToolRegistry()
        with pytest.raises(ValueError, match="未注册"):
            await registry.call("nonexistent", {})

    @pytest.mark.asyncio
    async def test_unregistered_after_unregister(self):
        registry = LocalToolRegistry()
        registry.register("tmp", "Temp", lambda: 1)
        registry.unregister("tmp")
        with pytest.raises(ValueError, match="未注册"):
            await registry.call("tmp", {})


# ────────────────────────────────────────────────────────────
# Handler exceptions propagate
# ────────────────────────────────────────────────────────────

class TestHandlerExceptionPropagation:
    """Exceptions from handlers should propagate through call()."""

    @pytest.mark.asyncio
    async def test_sync_handler_exception_propagates(self):
        registry = LocalToolRegistry()

        def failing():
            raise RuntimeError("handler failed")

        registry.register("fail", "Fail", failing)
        with pytest.raises(RuntimeError, match="handler failed"):
            await registry.call("fail", {})

    @pytest.mark.asyncio
    async def test_async_handler_exception_propagates(self):
        registry = LocalToolRegistry()

        async def async_failing():
            raise ValueError("async handler failed")

        registry.register("async_fail", "Async Fail", async_failing)
        with pytest.raises(ValueError, match="async handler failed"):
            await registry.call("async_fail", {})


# ────────────────────────────────────────────────────────────
# _check_type function
# ────────────────────────────────────────────────────────────

class TestCheckType:
    """Test JSON Schema type checking for all supported types."""

    def test_string_type(self):
        assert _check_type("hello", "string") is True
        assert _check_type(123, "string") is False

    def test_integer_type(self):
        assert _check_type(42, "integer") is True
        assert _check_type(3.14, "integer") is False
        assert _check_type("42", "integer") is False
        # Note: bool is subclass of int in Python, so True is an int
        # but _check_type uses isinstance(True, int) → True
        # This is expected JSON Schema behavior.

    def test_number_type(self):
        assert _check_type(42, "number") is True
        assert _check_type(3.14, "number") is True
        assert _check_type("42", "number") is False

    def test_boolean_type(self):
        assert _check_type(True, "boolean") is True
        assert _check_type(False, "boolean") is True
        assert _check_type(1, "boolean") is False
        assert _check_type("true", "boolean") is False

    def test_array_type(self):
        assert _check_type([1, 2, 3], "array") is True
        assert _check_type("list", "array") is False

    def test_object_type(self):
        assert _check_type({"key": "value"}, "object") is True
        assert _check_type([1, 2], "object") is False

    def test_unknown_type_passes(self):
        """Unknown JSON Schema types should pass (return True)."""
        assert _check_type("anything", "binary") is True
        assert _check_type("anything", "null") is True
        assert _check_type("anything", "nonexistent") is True

    def test_none_value_with_string_type(self):
        """None is not a string."""
        assert _check_type(None, "string") is False

    def test_empty_values(self):
        assert _check_type("", "string") is True
        assert _check_type([], "array") is True
        assert _check_type({}, "object") is True
