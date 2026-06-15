"""Pydantic v2 数据模型 — 所有共享数据模型定义."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# 1. Agent 编排与任务路由 — 核心数据模型
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """任务生命周期状态."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubTask(BaseModel):
    """子任务定义，包含技能路由和依赖关系."""

    task_id: str
    skill_name: str
    action: str
    parameters: dict = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=_UTC))


class TaskPlan(BaseModel):
    """任务计划，由 LLM 分解用户请求后生成."""

    plan_id: str
    user_request: str
    sub_tasks: list[SubTask] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING


class AgentResponse(BaseModel):
    """Agent 统一响应结构."""

    success: bool
    message: str
    data: Optional[dict] = None
    error_code: Optional[str] = None
    suggestion: Optional[str] = None
    trace_id: Optional[str] = None


class Message(BaseModel):
    """对话消息."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=_UTC))
    metadata: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果."""

    tool_name: str
    success: bool
    output: Optional[dict] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    sanitized: bool = False


# ---------------------------------------------------------------------------
# 2. 安全相关模型 — Agent Identity 集成
# ---------------------------------------------------------------------------


class WorkloadIdentity(BaseModel):
    """工作负载身份，由阿里云 Agent Identity 服务签发."""

    workload_identity_arn: str
    agent_instance_id: str
    identity_provider: str  # "ram" | "okta" | "entra_id" | "custom_idp"
    permissions: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class CredentialScope(BaseModel):
    """凭证作用域，定义凭证的目标服务和权限范围."""

    target_service: str  # "aliyun" | "third_party"
    credential_provider_name: str
    ram_role_arn: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)


class CachedCredential(BaseModel):
    """缓存的临时凭证."""

    credential_scope: CredentialScope
    access_key_id: Optional[str] = None
    access_key_secret: Optional[str] = None
    security_token: Optional[str] = None
    oauth_token: Optional[str] = None
    api_key: Optional[str] = None
    expires_at: datetime
    refresh_before: datetime  # 过期前 5 分钟刷新


class AliyunCredential(BaseModel):
    """阿里云 STS 临时凭证."""

    access_key_id: str
    access_key_secret: str
    security_token: str
    expires_at: datetime


class ThirdPartyCredential(BaseModel):
    """第三方应用凭证（OAuth Token 或 API Key）."""

    oauth_token: Optional[str] = None
    api_key: Optional[str] = None
    expires_at: Optional[datetime] = None
    scopes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. MCP 相关模型
# ---------------------------------------------------------------------------


class MCPServerConfig(BaseModel):
    """MCP Server 配置."""

    server_name: str
    transport: str  # "stdio" | "sse" | "streamable-http"
    command: Optional[str] = None  # stdio 模式的启动命令
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None  # SSE/HTTP 模式的 URL
    env: dict[str, str] = Field(default_factory=dict)


class MCPTool(BaseModel):
    """MCP Server 提供的工具定义."""

    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)  # JSON Schema
    server_name: str


# ---------------------------------------------------------------------------
# 4. 审计相关模型
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """审计事件数据模型."""

    event_id: str
    timestamp: datetime  # ISO 8601
    workload_identity_arn: str  # Agent 身份
    user_identity: Optional[str] = None  # 最终用户身份（On-Behalf-Of）
    action: str  # 操作类型
    resource_arn: str  # 目标资源
    parameters: dict = Field(default_factory=dict)  # 请求参数（脱敏后）
    result: str  # "success" | "failure" | "denied"
    error_message: Optional[str] = None
    permission_level: str
    trace_id: str  # OpenTelemetry trace ID
    span_id: str


# ---------------------------------------------------------------------------
# 5. 权限相关模型
# ---------------------------------------------------------------------------


class PermissionLevel(str, Enum):
    """三级权限分级."""

    READ_ONLY = "read_only"  # 默认级别
    LIMITED_WRITE = "limited_write"  # 需审批
    ADMIN = "admin"  # 强制人工审批


class PermissionCheckResult(BaseModel):
    """权限校验结果."""

    allowed: bool
    required_permission: str
    current_permissions: list[str] = Field(default_factory=list)
    permission_level: PermissionLevel
    requires_approval: bool
    denial_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# 6. 安全防护模型
# ---------------------------------------------------------------------------


class SecurityRule(BaseModel):
    """安全规则定义."""

    rule_id: str
    rule_type: str  # "blacklist" | "rate_limit" | "anomaly_detection"
    description: str
    config: dict = Field(default_factory=dict)


class SecurityCheckResult(BaseModel):
    """安全检查结果."""

    allowed: bool
    rule_id: Optional[str] = None
    denial_reason: Optional[str] = None
    suggestion: Optional[str] = None


# ---------------------------------------------------------------------------
# 7. 上下文相关模型
# ---------------------------------------------------------------------------


class InteractionMode(str, Enum):
    """交互模式."""

    CHAT = "chat"
    TASK = "task"
    WATCH = "watch"


class ResourceReference(BaseModel):
    """资源引用，关联对话中提到的云资源."""

    resource_type: str  # ecs, rds, slb, vpc ...
    resource_id: str  # 实例 ID
    region: str  # 区域
    display_name: Optional[str] = None


class TaskProgress(BaseModel):
    """任务执行进度."""

    percentage: float = 0.0
    current_step: str = ""
    total_steps: int = 0
    completed_steps: int = 0


class SessionState(BaseModel):
    """会话状态."""

    session_id: str
    user_id: str
    mode: InteractionMode = InteractionMode.CHAT
    messages: list[Message] = Field(default_factory=list)
    resources: dict[str, ResourceReference] = Field(default_factory=dict)
    task_progress: Optional[TaskProgress] = None
    created_at: datetime
    last_active_at: datetime
    ttl_minutes: int = 30


# ---------------------------------------------------------------------------
# 8. 技能相关模型
# ---------------------------------------------------------------------------


class SkillDefinition(BaseModel):
    """技能定义，用于注册到 Skill_Registry.

    遵循 Agent Skills 规范（类似 Claude SKILL.md），
    支持技能市场展示和安装管理。
    """

    skill_name: str
    description: str
    version: str
    capabilities: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    status: str = "healthy"  # "healthy" | "unhealthy" | "disabled"

    # 市场展示字段
    author: str = ""
    category: str = "通用"  # 监控诊断 / 故障排查 / 变更管理 / 开发工具 / 安全合规 / 通用
    icon: str = "🔧"
    tags: list[str] = Field(default_factory=list)
    install_count: int = 0
    rating: float = 0.0
    updated_at: Optional[str] = None  # ISO 8601
    readme: str = ""  # Markdown 格式的详细说明


class ValidationResult(BaseModel):
    """技能输入参数校验结果."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. Agent Loop — 终端状态 & 循环配置
# ---------------------------------------------------------------------------


class LoopTerminalReason(str, Enum):
    """Agent loop 终止原因 — discriminated union 类型."""

    COMPLETED = "completed"  # 正常完成
    ABORTED = "aborted"  # 用户中止
    BUDGET_EXHAUSTED = "budget_exhausted"  # Token 预算耗尽
    MAX_TURNS = "max_turns"  # 达到最大循环轮数
    UNRECOVERABLE_ERROR = "unrecoverable_error"  # 不可恢复错误


class LoopTerminal(BaseModel):
    """Agent loop 终端状态 — 编码循环结束的原因."""

    reason: LoopTerminalReason
    message: str = ""
    data: Optional[dict] = None
    turn_count: int = 0  # 循环执行了多少轮
    total_tokens: Optional[int] = None
    elapsed_ms: float = 0.0


class LoopConfig(BaseModel):
    """Agent loop 配置 — 控制循环行为."""

    max_turns: int = 5  # 最大循环轮数（防止无限循环）
    max_tokens: Optional[int] = None  # Token 预算上限
    max_elapsed_ms: Optional[float] = None  # 时间上限（毫秒）
    continue_threshold: float = 0.7  # LLM 判断“继续”的置信度阈值
