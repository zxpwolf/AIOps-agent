"""Tests for observability/logging.py — JSONFormatter and setup_logging."""

from __future__ import annotations

import json
import logging
import logging.handlers

import pytest

from aiops_agent.observability.logging import JSONFormatter, setup_logging


class TestJSONFormatter:
    """Tests for JSONFormatter.format."""

    def setup_method(self):
        self.formatter = JSONFormatter()
        self.logger = logging.getLogger("test_json_formatter")

    def _make_record(self, msg: str, level: int = logging.INFO, exc_info=None, args=()) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test.py",
            lineno=42,
            msg=msg,
            args=args,
            exc_info=exc_info,
        )
        return record

    def test_format_produces_valid_json(self):
        record = self._make_record("hello world")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_format_includes_timestamp(self):
        record = self._make_record("test message")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert "timestamp" in data
        # Should be ISO 8601 format
        assert "T" in data["timestamp"]

    def test_format_includes_level(self):
        record = self._make_record("test", level=logging.WARNING)
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "WARNING"

    def test_format_includes_logger_name(self):
        record = self._make_record("test")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["logger"] == "test.logger"

    def test_format_includes_message(self):
        record = self._make_record("specific log message")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "specific log message"

    def test_format_with_format_args(self):
        record = self._make_record("user %s action %s", args=("alice", "login"))
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "user alice action login"

    def test_format_includes_exception_info(self):
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = self._make_record("error occurred", exc_info=sys.exc_info())

        output = self.formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]
        assert "test error" in data["exception"]

    def test_format_no_exception_when_exc_info_none(self):
        record = self._make_record("no error", exc_info=(None, None, None))
        output = self.formatter.format(record)
        data = json.loads(output)
        assert "exception" not in data

    def test_format_includes_extra_fields(self):
        record = self._make_record("extra test")
        record.extra_data = {"key": "value"}
        record.session_id = "sess-001"
        record.skill_name = "monitoring"
        record.tool_name = "describe_instances"

        output = self.formatter.format(record)
        data = json.loads(output)

        assert data["extra_data"] == {"key": "value"}
        assert data["session_id"] == "sess-001"
        assert data["skill_name"] == "monitoring"
        assert data["tool_name"] == "describe_instances"

    def test_format_excludes_none_extra_fields(self):
        record = self._make_record("test")
        # Do not set extra fields — they should be absent
        output = self.formatter.format(record)
        data = json.loads(output)
        assert "extra_data" not in data
        assert "session_id" not in data
        assert "skill_name" not in data
        assert "tool_name" not in data


class TestJSONFormatterWithOTel:
    """Tests for JSONFormatter with OpenTelemetry trace context."""

    def setup_method(self):
        self.formatter = JSONFormatter()

    def _make_record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_format_without_active_span(self):
        """When no OTel span is active, trace_id/span_id should be absent."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        # Reset to no-op provider to ensure clean state
        trace.set_tracer_provider(TracerProvider())

        record = self._make_record("no span")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert "trace_id" not in data
        assert "span_id" not in data

    def test_format_includes_trace_context_with_active_span(self):
        """When a span is active, trace_id and span_id should be present."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            record = self._make_record("with span")
            output = self.formatter.format(record)
            data = json.loads(output)
            assert "trace_id" in data
            assert "span_id" in data
            # trace_id should be 32 hex chars
            assert len(data["trace_id"]) == 32
            # span_id should be 16 hex chars
            assert len(data["span_id"]) == 16


class TestSetupLogging:
    """Tests for setup_logging function."""

    def teardown_method(self):
        """Reset root logger after each test."""
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_setup_logging_json_format(self):
        setup_logging(level="INFO", format_type="json")
        root = logging.getLogger()
        assert root.level == logging.INFO
        # Console handler should have JSONFormatter
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JSONFormatter)

    def test_setup_logging_text_format(self):
        setup_logging(level="INFO", format_type="text")
        root = logging.getLogger()
        handler = root.handlers[0]
        # Text format uses standard logging.Formatter, not JSONFormatter
        assert not isinstance(handler.formatter, JSONFormatter)
        assert isinstance(handler.formatter, logging.Formatter)

    def test_setup_logging_debug_level(self):
        setup_logging(level="DEBUG", format_type="json")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_logging_warning_level(self):
        setup_logging(level="WARNING", format_type="json")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_setup_logging_error_level(self):
        setup_logging(level="ERROR", format_type="json")
        root = logging.getLogger()
        assert root.level == logging.ERROR

    def test_setup_logging_sls_enabled(self):
        """SLS enabled with endpoint should log a config message without error."""
        setup_logging(
            level="INFO",
            format_type="json",
            sls_enabled=True,
            sls_endpoint="https://cn-hangzhou.log.aliyuncs.com",
            sls_project="test-project",
            sls_logstore="test-logstore",
        )
        root = logging.getLogger()
        assert root.level == logging.INFO
        # No exception should be raised

    def test_setup_logging_sls_disabled(self):
        """SLS disabled should not attempt any SLS configuration."""
        setup_logging(
            level="INFO",
            format_type="json",
            sls_enabled=False,
        )
        root = logging.getLogger()
        assert len(root.handlers) == 1  # Only console handler

    def test_setup_logging_sls_enabled_without_endpoint(self):
        """SLS enabled but no endpoint should not configure SLS."""
        setup_logging(
            level="INFO",
            format_type="json",
            sls_enabled=True,
            sls_endpoint="",
        )
        root = logging.getLogger()
        assert len(root.handlers) == 1  # Only console handler

    def test_setup_logging_clears_existing_handlers(self):
        """setup_logging should clear existing handlers before adding new ones."""
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())
        # Count pre-existing non-test handlers
        existing_count = len(root.handlers)
        assert existing_count >= 1

        setup_logging(level="INFO", format_type="json")
        # The new handler count should be exactly 1 (the console handler)
        # regardless of pre-existing pytest LogCaptureHandlers
        console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.NullHandler)]
        assert len(console_handlers) == 1
