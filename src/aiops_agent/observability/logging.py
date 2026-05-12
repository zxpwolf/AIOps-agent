"""结构化日志 — JSON 格式输出与 OpenTelemetry 集成.

配置 JSON 格式结构化日志，集成 OpenTelemetry trace_id 和 span_id
到日志上下文，支持 SLS 日志服务接入。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from opentelemetry import trace


class JSONFormatter(logging.Formatter):
    """JSON 格式日志 Formatter.

    输出包含:
    - timestamp (ISO 8601)
    - level
    - logger
    - message
    - trace_id / span_id (来自 OpenTelemetry 上下文)
    - 额外字段
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 注入 OpenTelemetry trace 上下文
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context and span_context.is_valid:
            log_entry["trace_id"] = format(span_context.trace_id, "032x")
            log_entry["span_id"] = format(span_context.span_id, "016x")

        # 异常信息
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # 额外字段（通过 extra 参数传入）
        for key in ("extra_data", "session_id", "skill_name", "tool_name"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_logging(
    level: str = "INFO",
    format_type: str = "json",
    sls_enabled: bool = False,
    sls_endpoint: str = "",
    sls_project: str = "",
    sls_logstore: str = "",
) -> None:
    """配置结构化日志.

    Args:
        level: 日志级别（DEBUG / INFO / WARNING / ERROR）。
        format_type: 日志格式（"json" 或 "text"）。
        sls_enabled: 是否启用 SLS 日志服务接入。
        sls_endpoint: SLS 端点。
        sls_project: SLS 项目名。
        sls_logstore: SLS 日志库名。
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handler
    root_logger.handlers.clear()

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)

    if format_type == "json":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root_logger.addHandler(console_handler)

    # SLS handler（预留接口）
    if sls_enabled and sls_endpoint:
        logger = logging.getLogger(__name__)
        logger.info(
            "SLS 日志服务接入已配置: endpoint=%s, project=%s, logstore=%s",
            sls_endpoint,
            sls_project,
            sls_logstore,
        )
        # 实际部署时集成 SLS SDK handler

    logging.getLogger(__name__).debug("结构化日志已配置: level=%s, format=%s", level, format_type)
