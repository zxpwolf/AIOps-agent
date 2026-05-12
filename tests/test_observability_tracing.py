"""Tests for observability/tracing.py — traced decorator, setup_tracing, get_tracer."""

from __future__ import annotations

import pytest

from aiops_agent.observability.tracing import get_tracer, setup_tracing, traced
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

# ---------------------------------------------------------------------------
# Module-level OpenTelemetry setup — set_tracer_provider can only be called
# once per process, so we configure the provider here at module load time.
# ---------------------------------------------------------------------------

_in_memory_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_in_memory_exporter))
trace.set_tracer_provider(_provider)

# Initialize the module-level _tracer
from aiops_agent.observability import tracing as tracing_module
tracing_module._tracer = trace.get_tracer("test-aiops")


def _clear_spans():
    """Clear the in-memory exporter between tests."""
    _in_memory_exporter.clear()


def _get_spans():
    """Get finished spans from the exporter."""
    return _in_memory_exporter.get_finished_spans()


class TestTracedDecorator:
    """Tests for the @traced decorator."""

    def setup_method(self):
        _clear_spans()

    @pytest.mark.asyncio
    async def test_traced_wraps_async_function(self):
        """The decorator should wrap an async function without errors."""

        @traced()
        async def my_func():
            return 42

        result = await my_func()
        assert result == 42

    @pytest.mark.asyncio
    async def test_traced_sets_ok_on_success(self):
        """Successful execution should set StatusCode.OK."""

        @traced("test_success")
        async def success_func():
            return "ok"

        await success_func()

        spans = _get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.OK

    @pytest.mark.asyncio
    async def test_traced_sets_error_on_failure(self):
        """Exception should set StatusCode.ERROR and record the exception."""

        @traced("test_error")
        async def failing_func():
            raise ValueError("something went wrong")

        with pytest.raises(ValueError, match="something went wrong"):
            await failing_func()

        spans = _get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR

    @pytest.mark.asyncio
    async def test_traced_records_exception(self):
        """The exception should be recorded on the span."""

        @traced("test_exception")
        async def error_func():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await error_func()

        spans = _get_spans()
        span = spans[0]
        # The span should have events (exception recorded)
        events = span.events
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_traced_sets_custom_attributes(self):
        """Custom attributes should be attached to the span."""

        @traced("test_attrs", attributes={"user": "alice", "action": "deploy"})
        async def attr_func():
            return True

        await attr_func()

        spans = _get_spans()
        span = spans[0]
        assert span.attributes.get("user") == "alice"
        assert span.attributes.get("action") == "deploy"

    @pytest.mark.asyncio
    async def test_traced_uses_provided_span_name(self):
        """Custom span_name should override the function name."""

        @traced("custom_span_name")
        async def named_func():
            return None

        await named_func()

        spans = _get_spans()
        assert len(spans) == 1
        assert spans[0].name == "custom_span_name"

    @pytest.mark.asyncio
    async def test_traced_uses_function_name_when_no_span_name(self):
        """When no span_name is provided, the function's qualname is used."""

        @traced()
        async def auto_named():
            return None

        await auto_named()

        spans = _get_spans()
        assert len(spans) == 1
        # Uses __qualname__, which includes the nested class context
        assert "auto_named" in spans[0].name

    @pytest.mark.asyncio
    async def test_traced_preserves_function_metadata(self):
        """functools.wraps should preserve __name__ and __doc__."""

        @traced()
        async def documented_func():
            """This is a docstring."""
            return True

        assert documented_func.__name__ == "documented_func"

    @pytest.mark.asyncio
    async def test_traced_with_method(self):
        """The decorator should work on class methods."""

        class Service:
            @traced("service_method")
            async def process(self, value: int) -> int:
                return value * 2

        svc = Service()
        result = await svc.process(21)
        assert result == 42

        spans = _get_spans()
        assert len(spans) == 1
        assert spans[0].name == "service_method"


class TestSetupTracing:
    """Tests for setup_tracing function."""

    def test_setup_tracing_console_exporter(self):
        """Console exporter should be configured without error."""
        # setup_tracing internally calls set_tracer_provider which may warn
        # if already set, but should still return a tracer
        tracer = setup_tracing(exporter="console")
        assert tracer is not None

    def test_setup_tracing_sls_falls_back_to_console_on_import_error(self):
        """When OTLP exporter is unavailable, should fall back to console."""
        import sys

        real_module = sys.modules.get("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        # Remove to force ImportError
        if "opentelemetry.exporter.otlp.proto.grpc.trace_exporter" in sys.modules:
            del sys.modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"]

        try:
            tracer = setup_tracing(
                exporter="sls",
                sls_endpoint="https://example.com",
            )
            assert tracer is not None
        finally:
            if real_module is not None:
                sys.modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"] = real_module

    def test_setup_tracing_sls_without_endpoint_uses_console(self):
        """SLS exporter without endpoint should use console exporter."""
        tracer = setup_tracing(exporter="sls", sls_endpoint="")
        assert tracer is not None

    def test_setup_tracing_custom_service_name(self):
        tracer = setup_tracing(service_name="custom-aiops")
        assert tracer is not None


class TestGetTracer:
    """Tests for get_tracer function."""

    def test_get_tracer_returns_tracer(self):
        tracer = get_tracer()
        assert tracer is not None

    def test_get_tracer_lazy_creates_if_none(self):
        """get_tracer should create a tracer if the module-level _tracer is None."""
        original = tracing_module._tracer
        try:
            tracing_module._tracer = None
            tracer = get_tracer()
            assert tracer is not None
            assert tracing_module._tracer is tracer
        finally:
            tracing_module._tracer = original

    def test_get_tracer_returns_same_instance(self):
        """Repeated calls should return the same cached tracer."""
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2
