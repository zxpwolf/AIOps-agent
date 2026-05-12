"""Tool_Executor — 统一工具执行器.

集成 Permission_Gate、Credential_Manager、Audit_Logger，
提供统一执行入口：权限校验 → 凭证获取 → 工具匹配 → 执行 → 脱敏 → 审计。
支持同步、异步、流式三种执行模式，超时控制和指数退避重试。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from opentelemetry import trace

from aiops_agent.core.exceptions import (
    PermissionDeniedError,
    TimeoutError as AgentTimeoutError,
)
from aiops_agent.models.schemas import (
    AuditEvent,
    CredentialScope,
    ToolResult,
    WorkloadIdentity,
)
from aiops_agent.observability.tracing import get_tracer
from aiops_agent.security.audit_logger import AuditLogger
from aiops_agent.security.credential_manager import CredentialManager
from aiops_agent.security.identity import WorkloadIdentityManager
from aiops_agent.security.permission_gate import PermissionGate
from aiops_agent.security.sanitizer import sanitize_parameters
from aiops_agent.tools.local_tools import LocalToolRegistry
from aiops_agent.tools.mcp_registry import MCPRegistry

logger = logging.getLogger(__name__)

# 重试配置
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


class ToolExecutor:
    """统一工具执行器.

    执行流程:
    1. Permission_Gate 校验 skill_identity 的权限
    2. Credential_Manager 通过 Agent Identity 获取临时凭证
    3. 匹配 MCP Server 工具或本地工具
    4. 执行工具调用（含超时控制和重试）
    5. 敏感数据脱敏
    6. Audit_Logger 记录操作日志
    7. OpenTelemetry Span 记录调用链路
    """

    def __init__(
        self,
        credential_manager: CredentialManager,
        permission_gate: PermissionGate,
        audit_logger: AuditLogger,
        mcp_registry: Optional[MCPRegistry] = None,
        local_tools: Optional[LocalToolRegistry] = None,
        default_timeout_seconds: float = 120,
        workload_identity_manager: Optional[WorkloadIdentityManager] = None,
    ) -> None:
        self._credential_manager = credential_manager
        self._permission_gate = permission_gate
        self._audit_logger = audit_logger
        self._mcp_registry = mcp_registry or MCPRegistry()
        self._local_tools = local_tools or LocalToolRegistry()
        self._default_timeout = default_timeout_seconds
        self._workload_identity_manager = workload_identity_manager

    # ------------------------------------------------------------------
    # 核心执行入口
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        skill_identity: WorkloadIdentity,
        execution_mode: str = "async",
        timeout_seconds: float | None = None,
        credential_scope: CredentialScope | None = None,
        resource_arn: str = "*",
    ) -> ToolResult:
        """统一工具执行入口.

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。
            skill_identity: 调用方的 Workload Identity。
            execution_mode: 执行模式（"sync" | "async" | "stream"）。
            timeout_seconds: 超时时间（秒），None 使用默认值。
            credential_scope: 凭证作用域（需要凭证时提供）。
            resource_arn: 目标资源 ARN。

        Returns:
            ToolResult 包含执行结果。
        """
        tracer = get_tracer()
        timeout = timeout_seconds or self._default_timeout
        start_time = time.monotonic()

        with tracer.start_as_current_span(
            f"tool.execute.{tool_name}",
            attributes={
                "tool.name": tool_name,
                "tool.execution_mode": execution_mode,
            },
        ) as span:
            # 获取 trace 上下文
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else ""
            span_id = format(span_context.span_id, "016x") if span_context.is_valid else ""

            result_status = "success"
            error_message = None

            try:
                # 1. 权限校验
                perm_result = await self._permission_gate.check_permission(
                    skill_identity, tool_name, resource_arn
                )
                if not perm_result.allowed:
                    raise PermissionDeniedError(
                        message=perm_result.denial_reason or f"权限不足: {tool_name}",
                        required_permission=perm_result.required_permission,
                        current_permissions=perm_result.current_permissions,
                    )

                # 2. 凭证获取（如果需要）
                if credential_scope is not None:
                    if credential_scope.target_service == "aliyun":
                        cred = await self._credential_manager.get_aliyun_credential(
                            credential_scope,
                            workload_identity_manager=self._workload_identity_manager,
                        )
                        arguments["_credential"] = cred.model_dump()
                    else:
                        cred = await self._credential_manager.get_third_party_credential(
                            credential_scope
                        )
                        arguments["_credential"] = cred.model_dump()

                # 3. 工具匹配和执行（含超时和重试）
                output = await self._execute_with_retry(
                    tool_name, arguments, timeout
                )

                # 4. 脱敏
                sanitized_output = sanitize_parameters(output) if isinstance(output, dict) else output

                elapsed_ms = (time.monotonic() - start_time) * 1000
                span.set_attribute("tool.duration_ms", elapsed_ms)
                span.set_status(trace.StatusCode.OK)

                return ToolResult(
                    tool_name=tool_name,
                    success=True,
                    output=sanitized_output if isinstance(sanitized_output, dict) else {"result": sanitized_output},
                    execution_time_ms=elapsed_ms,
                    sanitized=True,
                )

            except PermissionDeniedError as exc:
                result_status = "denied"
                error_message = exc.message
                span.set_status(trace.StatusCode.ERROR, exc.message)
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=exc.message,
                    execution_time_ms=(time.monotonic() - start_time) * 1000,
                )

            except AgentTimeoutError as exc:
                result_status = "failure"
                error_message = exc.message
                span.set_status(trace.StatusCode.ERROR, exc.message)
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=exc.message,
                    execution_time_ms=(time.monotonic() - start_time) * 1000,
                )

            except Exception as exc:
                result_status = "failure"
                error_message = str(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=str(exc),
                    execution_time_ms=(time.monotonic() - start_time) * 1000,
                )

            finally:
                # 5. 审计日志
                sanitized_args = sanitize_parameters(arguments)
                # 移除注入的凭证
                sanitized_args.pop("_credential", None)

                audit_event = AuditEvent(
                    event_id=str(uuid.uuid4()),
                    timestamp=_utcnow(),
                    workload_identity_arn=skill_identity.workload_identity_arn,
                    action=f"tool:{tool_name}",
                    resource_arn=resource_arn,
                    parameters=sanitized_args,
                    result=result_status,
                    error_message=error_message,
                    permission_level=perm_result.permission_level.value if 'perm_result' in dir() else "unknown",
                    trace_id=trace_id,
                    span_id=span_id,
                )
                try:
                    await self._audit_logger.log(audit_event)
                except Exception:
                    logger.exception("审计日志记录失败")

    # ------------------------------------------------------------------
    # 工具匹配和执行
    # ------------------------------------------------------------------

    async def _execute_with_retry(
        self,
        tool_name: str,
        arguments: dict,
        timeout: float,
    ) -> dict:
        """执行工具调用，含超时控制和指数退避重试.

        MCP 工具优先，本地工具回退。
        """
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                return await asyncio.wait_for(
                    self._dispatch_tool(tool_name, arguments),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise AgentTimeoutError(
                    message=f"工具 '{tool_name}' 执行超时（{timeout}s）",
                    timeout_seconds=timeout,
                    operation=tool_name,
                )
            except (ConnectionError, OSError) as exc:
                # 网络错误，重试
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                    logger.warning(
                        "工具 '%s' 网络错误 (尝试 %d/%d)，%0.1f 秒后重试: %s",
                        tool_name,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            except Exception:
                raise

        raise RuntimeError(
            f"工具 '{tool_name}' 执行失败（已重试 {_MAX_RETRIES} 次）: {last_error}"
        )

    async def _dispatch_tool(self, tool_name: str, arguments: dict) -> dict:
        """分发工具调用：MCP 优先，本地回退."""
        # 移除内部凭证字段，不传给工具
        clean_args = {k: v for k, v in arguments.items() if not k.startswith("_")}

        # 尝试 MCP 工具
        mcp_client = self._mcp_registry.get_client_for_tool(tool_name)
        if mcp_client is not None:
            logger.debug("通过 MCP 调用工具: %s", tool_name)
            return await mcp_client.call_tool(tool_name, clean_args)

        # 回退到本地工具
        if self._local_tools.has_tool(tool_name):
            logger.debug("通过本地工具调用: %s", tool_name)
            result = await self._local_tools.call(tool_name, clean_args)
            if isinstance(result, dict):
                return result
            return {"result": result}

        raise ValueError(f"工具 '{tool_name}' 未注册（MCP 和本地均未找到）")

    # ------------------------------------------------------------------
    # MCP / 本地工具管理代理
    # ------------------------------------------------------------------

    @property
    def mcp_registry(self) -> MCPRegistry:
        return self._mcp_registry

    @property
    def local_tools(self) -> LocalToolRegistry:
        return self._local_tools


def _utcnow():
    """获取当前 UTC 时间（timezone-aware）."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
