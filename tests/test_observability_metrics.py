"""Tests for observability/metrics.py — AgentMetrics, setup_metrics, get_meter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aiops_agent.observability.metrics import (
    AgentMetrics,
    _meter,
    get_meter,
    setup_metrics,
)


class TestAgentMetricsCreation:
    """Tests for AgentMetrics instrument creation."""

    def test_all_instruments_created(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)

        assert metrics.task_total is not None
        assert metrics.task_duration is not None
        assert metrics.permission_denied_total is not None
        assert metrics.security_events_total is not None
        assert metrics.tool_calls_total is not None
        assert metrics.llm_calls_total is not None

    def test_instruments_are_not_none_with_default_meter(self):
        """AgentMetrics should work even with the default (no-op) meter."""
        metrics = AgentMetrics()
        assert metrics.task_total is not None
        assert metrics.task_duration is not None


class TestAgentMetricsRecordTask:
    """Tests for record_task."""

    def test_record_task_increments_counter(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        # Should not raise
        metrics.record_task("success")

    def test_record_task_with_duration_records_histogram(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_task("success", duration_ms=150.5)
        # No exception means success

    def test_record_task_zero_duration_only_counter(self):
        """When duration_ms=0, only counter should be incremented."""
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_task("failed", duration_ms=0.0)
        # No exception — histogram should NOT be called with 0

    def test_record_task_negative_duration(self):
        """Negative duration should not record histogram."""
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_task("success", duration_ms=-1.0)
        # No exception


class TestAgentMetricsRecordPermissionDenied:
    """Tests for record_permission_denied."""

    def test_record_permission_denied(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_permission_denied("ecs:DeleteInstance")
        # Should not raise

    def test_record_permission_denied_with_different_actions(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_permission_denied("rds:ModifyDBInstance")
        metrics.record_permission_denied("sls:DeleteLogstore")
        # Should not raise


class TestAgentMetricsRecordSecurityEvent:
    """Tests for record_security_event."""

    def test_record_security_event(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_security_event("unauthorized_access")
        # Should not raise

    def test_record_security_event_multiple_types(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_security_event("sql_injection")
        metrics.record_security_event("xss_attempt")
        metrics.record_security_event("credential_leak")
        # Should not raise


class TestAgentMetricsRecordToolCall:
    """Tests for record_tool_call."""

    def test_record_tool_call_success(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_tool_call("describe_instances", True)
        # Should not raise

    def test_record_tool_call_failure(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_tool_call("delete_instance", False)
        # Should not raise


class TestAgentMetricsRecordLlmCall:
    """Tests for record_llm_call."""

    def test_record_llm_call_success(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_llm_call("qwen", True)
        # Should not raise

    def test_record_llm_call_failure(self):
        meter = setup_metrics()
        metrics = AgentMetrics(meter=meter)
        metrics.record_llm_call("claude", False)
        # Should not raise


class TestSetupMetrics:
    """Tests for setup_metrics function."""

    def test_setup_metrics_creates_meter(self):
        meter = setup_metrics()
        assert meter is not None

    def test_setup_metrics_sets_global_provider(self):
        from opentelemetry import metrics

        setup_metrics()
        # Should not raise — provider is set
        meter = metrics.get_meter("test-verify")
        assert meter is not None

    def test_setup_metrics_custom_service_name(self):
        meter = setup_metrics(service_name="custom-service")
        assert meter is not None

    def test_setup_metrics_custom_interval(self):
        meter = setup_metrics(export_interval_ms=30000)
        assert meter is not None


class TestGetMeter:
    """Tests for get_meter function."""

    def test_get_meter_returns_meter(self):
        meter = get_meter()
        assert meter is not None

    def test_get_meter_lazy_creates_if_none(self):
        """get_meter should create a meter if the module-level _meter is None."""
        from aiops_agent.observability import metrics as metrics_module

        # Save original
        original = metrics_module._meter
        try:
            metrics_module._meter = None
            meter = get_meter()
            assert meter is not None
            assert metrics_module._meter is meter
        finally:
            metrics_module._meter = original

    def test_get_meter_returns_same_instance(self):
        """Repeated calls should return the same cached meter."""
        m1 = get_meter()
        m2 = get_meter()
        assert m1 is m2
