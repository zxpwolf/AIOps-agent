"""AuditLogger 单元测试."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aiops_agent.models.schemas import AuditEvent
from aiops_agent.security.audit_logger import AuditLogger


# ---------------------------------------------------------------------------
# Test: Local logging
# ---------------------------------------------------------------------------


class TestLocalLogging:
    @pytest.mark.asyncio
    async def test_log_event_creates_file(self, audit_logger: AuditLogger) -> None:
        event = AuditEvent(
            event_id="evt-001",
            timestamp=datetime.now(timezone.utc),
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:test",
            resource_arn="acs:ecs:cn-hangzhou:*:instance/i-xxx",
            parameters={"key": "value"},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        log_files = list(audit_logger._local_log_dir.glob("*.jsonl"))
        assert len(log_files) >= 1

    @pytest.mark.asyncio
    async def test_log_event_sanitizes_secrets(self, audit_logger: AuditLogger) -> None:
        event = AuditEvent(
            event_id="evt-002",
            timestamp=datetime.now(timezone.utc),
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:test",
            resource_arn="*",
            parameters={"password": "secret123", "token": "abc"},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        # Read the log file and verify sanitization
        log_files = list(audit_logger._local_log_dir.glob("*.jsonl"))
        content = log_files[0].read_text()
        assert "secret123" not in content
        assert "REDACTED" in content


# ---------------------------------------------------------------------------
# Test: Query
# ---------------------------------------------------------------------------


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_by_time_range(self, audit_logger: AuditLogger) -> None:
        now = datetime.now(timezone.utc)
        event = AuditEvent(
            event_id="evt-003",
            timestamp=now,
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:test",
            resource_arn="*",
            parameters={},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        results = await audit_logger.query(
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_query_filter_by_action(self, audit_logger: AuditLogger) -> None:
        now = datetime.now(timezone.utc)
        event = AuditEvent(
            event_id="evt-004",
            timestamp=now,
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:specific_action",
            resource_arn="*",
            parameters={},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        results = await audit_logger.query(
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            action="tool:specific_action",
        )
        matching = [e for e in results if e.action == "tool:specific_action"]
        assert len(matching) >= 1

    @pytest.mark.asyncio
    async def test_query_filter_by_identity(self, audit_logger: AuditLogger) -> None:
        now = datetime.now(timezone.utc)
        event = AuditEvent(
            event_id="evt-005",
            timestamp=now,
            workload_identity_arn="acs:ram::123:role/specific-agent",
            action="tool:test",
            resource_arn="*",
            parameters={},
            result="success",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        results = await audit_logger.query(
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            workload_identity_arn="acs:ram::123:role/specific-agent",
        )
        matching = [e for e in results if e.workload_identity_arn == "acs:ram::123:role/specific-agent"]
        assert len(matching) >= 1


# ---------------------------------------------------------------------------
# Test: Backup logging
# ---------------------------------------------------------------------------


class TestBackupLogging:
    @pytest.mark.asyncio
    async def test_backup_on_action_trail_failure(self, audit_logger: AuditLogger) -> None:
        audit_logger._action_trail_endpoint = "https://fake-endpoint.example.com"

        event = AuditEvent(
            event_id="evt-006",
            timestamp=datetime.now(timezone.utc),
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:test",
            resource_arn="*",
            parameters={},
            result="failure",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        # 备份目录应该有文件
        backup_files = list(audit_logger._backup_log_dir.glob("*.jsonl"))
        assert len(backup_files) >= 1


# ---------------------------------------------------------------------------
# Test: Alert callback
# ---------------------------------------------------------------------------


class TestAlertCallback:
    @pytest.mark.asyncio
    async def test_alert_triggered_on_action_trail_failure(self, audit_logger: AuditLogger) -> None:
        alerts = []

        async def mock_alert(message: str) -> None:
            alerts.append(message)

        audit_logger._alert_callback = mock_alert
        audit_logger._action_trail_endpoint = "https://fake-endpoint.example.com"

        event = AuditEvent(
            event_id="evt-007",
            timestamp=datetime.now(timezone.utc),
            workload_identity_arn="acs:ram::123:role/agent",
            action="tool:test",
            resource_arn="*",
            parameters={},
            result="failure",
            permission_level="read_only",
            trace_id="trace-001",
            span_id="span-001",
        )

        await audit_logger.log(event)

        assert len(alerts) >= 1
        assert "ActionTrail" in alerts[0]
