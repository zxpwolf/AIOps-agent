"""OpenTelemetry Tracing 集成.

配置 TracerProvider 和 SpanProcessor，提供 trace 装饰器
为异步函数自动创建 Span，支持 SLS 导出器配置。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# 模块级 tracer 实例
_tracer: Optional[trace.Tracer] = None


def setup_tracing(
    service_name: str = "aiops-agent",
    exporter: str = "console",
    sls_endpoint: str = "",
    sls_project: str = "",
    sls_logstore: str = "",
) -> trace.Tracer:
    """配置 OpenTelemetry TracerProvider 和 SpanProcessor.

    Args:
        service_name: 服务名称，用于标识 trace 来源。
        exporter: 导出器类型（"console" 或 "sls"）。
        sls_endpoint: SLS 端点（仅 sls 导出器需要）。
        sls_project: SLS 项目名（仅 sls 导出器需要）。
        sls_logstore: SLS 日志库名（仅 sls 导出器需要）。

    Returns:
        配置好的 Tracer 实例。
    """
    global _tracer

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
        }
    )

    provider = TracerProvider(resource=resource)

    # 配置导出器
    span_exporter: SpanExporter
    if exporter == "sls" and sls_endpoint:
        # SLS 导出器 — 使用 OTLP 兼容端点
        # 实际部署时可替换为 SLS 专用导出器
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            span_exporter = OTLPSpanExporter(endpoint=sls_endpoint)
            provider.add_span_processor(BatchSpanProcessor(span_exporter))
            logger.info("Tracing 已配置 SLS 导出器: %s", sls_endpoint)
        except ImportError:
            logger.warning("OTLP 导出器不可用，回退到 Console 导出器")
            span_exporter = ConsoleSpanExporter()
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        span_exporter = ConsoleSpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        logger.info("Tracing 已配置 Console 导出器")

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)

    return _tracer


def get_tracer() -> trace.Tracer:
    """获取当前 Tracer 实例，未初始化时自动创建默认 Tracer."""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("aiops-agent")
    return _tracer


def traced(
    span_name: str | None = None,
    attributes: dict[str, str] | None = None,
) -> Callable[[F], F]:
    """Trace 装饰器，为异步函数自动创建 Span.

    Args:
        span_name: Span 名称，默认使用函数名。
        attributes: 附加到 Span 的属性。

    Usage::

        @traced("process_request")
        async def process_request(self, user_input: str) -> AgentResponse:
            ...
    """

    def decorator(func: F) -> F:
        name = span_name or func.__qualname__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return wrapper  # type: ignore[return-value]

    return decorator
