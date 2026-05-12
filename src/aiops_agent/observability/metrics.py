"""核心指标采集 — OpenTelemetry Metrics.

定义并采集 AIOps Agent 核心运行指标：
任务完成率、平均响应时间、权限拒绝次数、安全事件数。
"""

from __future__ import annotations

import logging
from typing import Optional

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

# 模块级 meter 实例
_meter: Optional[metrics.Meter] = None


class AgentMetrics:
    """AIOps Agent 核心指标采集器.

    指标:
    - task_total: 任务总数（按状态分类）
    - task_duration_ms: 任务执行时长（毫秒）
    - permission_denied_total: 权限拒绝次数
    - security_events_total: 安全事件数（按类型分类）
    - tool_calls_total: 工具调用次数
    - llm_calls_total: LLM 调用次数
    """

    def __init__(self, meter: metrics.Meter | None = None) -> None:
        m = meter or get_meter()

        self.task_total = m.create_counter(
            name="aiops.task.total",
            description="任务总数",
            unit="1",
        )

        self.task_duration = m.create_histogram(
            name="aiops.task.duration",
            description="任务执行时长",
            unit="ms",
        )

        self.permission_denied_total = m.create_counter(
            name="aiops.permission.denied.total",
            description="权限拒绝次数",
            unit="1",
        )

        self.security_events_total = m.create_counter(
            name="aiops.security.events.total",
            description="安全事件数",
            unit="1",
        )

        self.tool_calls_total = m.create_counter(
            name="aiops.tool.calls.total",
            description="工具调用次数",
            unit="1",
        )

        self.llm_calls_total = m.create_counter(
            name="aiops.llm.calls.total",
            description="LLM 调用次数",
            unit="1",
        )

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def record_task(self, status: str, duration_ms: float = 0.0) -> None:
        """记录任务完成情况."""
        self.task_total.add(1, {"status": status})
        if duration_ms > 0:
            self.task_duration.record(duration_ms, {"status": status})

    def record_permission_denied(self, action: str) -> None:
        """记录权限拒绝事件."""
        self.permission_denied_total.add(1, {"action": action})

    def record_security_event(self, event_type: str) -> None:
        """记录安全事件."""
        self.security_events_total.add(1, {"event_type": event_type})

    def record_tool_call(self, tool_name: str, success: bool) -> None:
        """记录工具调用."""
        self.tool_calls_total.add(
            1, {"tool_name": tool_name, "success": str(success)}
        )

    def record_llm_call(self, provider: str, success: bool) -> None:
        """记录 LLM 调用."""
        self.llm_calls_total.add(
            1, {"provider": provider, "success": str(success)}
        )


def setup_metrics(
    service_name: str = "aiops-agent",
    export_interval_ms: int = 60000,
) -> metrics.Meter:
    """配置 OpenTelemetry MeterProvider.

    Args:
        service_name: 服务名称。
        export_interval_ms: 指标导出间隔（毫秒）。

    Returns:
        配置好的 Meter 实例。
    """
    global _meter

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
        }
    )

    exporter = ConsoleMetricExporter()
    reader = PeriodicExportingMetricReader(
        exporter, export_interval_millis=export_interval_ms
    )

    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    _meter = metrics.get_meter(service_name)
    logger.info("Metrics 已配置，导出间隔 %d ms", export_interval_ms)

    return _meter


def get_meter() -> metrics.Meter:
    """获取当前 Meter 实例，未初始化时自动创建默认 Meter."""
    global _meter
    if _meter is None:
        _meter = metrics.get_meter("aiops-agent")
    return _meter
