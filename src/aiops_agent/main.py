"""AIOps Agent 应用主入口.

实现 Agent 启动流程：
初始化 Workload Identity (STS AssumeRoleWithOIDC) →
初始化各组件 → 注册默认技能 → 连接 MCP Server。
支持优雅关闭和数据区域限制检查。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import yaml

from aiops_agent.context.manager import ContextManager
from aiops_agent.context.memory import MemoryLayer
from aiops_agent.context.resource_resolver import ResourceResolver
from aiops_agent.context.session import SessionStore
from aiops_agent.core.orchestrator import AgentOrchestrator
from aiops_agent.llm.provider import LLMProviderFactory
from aiops_agent.models.schemas import SkillDefinition
from aiops_agent.observability.logging import setup_logging
from aiops_agent.observability.metrics import AgentMetrics, setup_metrics
from aiops_agent.observability.metrics_store import get_metrics_store
from aiops_agent.observability.tracing import setup_tracing
from aiops_agent.security.audit_logger import AuditLogger
from aiops_agent.security.credential_manager import CredentialManager
from aiops_agent.security.identity import WorkloadIdentityManager
from aiops_agent.security.permission_gate import PermissionGate
from aiops_agent.security.security_guard import SecurityGuard
from aiops_agent.skills.change_management import ChangeManagementSkill
from aiops_agent.skills.monitoring import MonitoringSkill
from aiops_agent.skills.registry import SkillRegistry
from aiops_agent.skills.troubleshooting import TroubleshootingSkill
from aiops_agent.tools.executor import ToolExecutor
from aiops_agent.tools.mcp_registry import MCPRegistry

logger = logging.getLogger(__name__)

# 允许的数据区域
_ALLOWED_REGIONS = {"cn-hangzhou", "cn-shanghai", "cn-beijing", "cn-shenzhen"}


def _load_config(config_path: str = "config/settings.yaml") -> dict:
    """加载主配置文件."""
    path = Path(config_path)
    if not path.exists():
        logger.warning("配置文件不存在: %s，使用默认配置", config_path)
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _check_data_residency(config: dict) -> None:
    """检查数据区域限制（中国大陆区域）."""
    region = config.get("agent_identity", {}).get("region", "cn-hangzhou")
    allowed = set(config.get("data_residency", {}).get("allowed_regions", _ALLOWED_REGIONS))

    if region not in allowed:
        logger.error("数据区域 '%s' 不在允许范围内: %s", region, allowed)
        sys.exit(1)

    logger.info("数据区域检查通过: %s", region)


async def create_agent(config: dict | None = None) -> AgentOrchestrator:
    """创建并初始化 Agent 实例.

    Args:
        config: 配置字典，None 时从文件加载。

    Returns:
        初始化完成的 AgentOrchestrator 实例。
    """
    if config is None:
        config = _load_config()

    # 数据区域检查
    _check_data_residency(config)

    # 初始化可观测性
    obs_config = config.get("observability", {})
    setup_logging(
        level=obs_config.get("logging", {}).get("level", "INFO"),
        format_type=obs_config.get("logging", {}).get("format", "json"),
    )
    setup_tracing(
        exporter=obs_config.get("tracing", {}).get("exporter", "console"),
    )
    setup_metrics()
    metrics = AgentMetrics()

    # ------------------------------------------------------------------
    # 工作负载身份初始化（阿里云 Agent Identity — STS AssumeRoleWithOIDC）
    # ------------------------------------------------------------------
    ai_config = config.get("agent_identity", {})
    role_arn = ai_config.get(
        "role_arn",
        os.getenv("ALIBABA_CLOUD_ROLE_ARN", ""),
    )
    oidc_provider_arn = ai_config.get(
        "oidc_provider_arn",
        os.getenv("ALIBABA_CLOUD_OIDC_PROVIDER_ARN", ""),
    )
    region = ai_config.get("region", "cn-hangzhou")
    session_name = ai_config.get("session_name", "aiops-agent")

    workload_identity_manager = WorkloadIdentityManager(
        role_arn=role_arn,
        oidc_provider_arn=oidc_provider_arn,
        region=region,
        session_name=session_name,
        token_refresh_before_minutes=ai_config.get("token_refresh_before_minutes", 5),
    )

    # 初始化 STS 凭证（读取 K8s SA JWT → AssumeRoleWithOIDC）
    # 非 K8s 环境可通过环境变量 ALIBABA_CLOUD_OIDC_TOKEN 传入 JWT
    jwt_token = os.getenv("ALIBABA_CLOUD_OIDC_TOKEN") or None
    if role_arn and oidc_provider_arn:
        try:
            await workload_identity_manager.assume_role(jwt_token=jwt_token)
            logger.info("工作负载身份初始化成功: %s", role_arn)
        except Exception:
            logger.warning(
                "工作负载身份初始化失败（将在首次调用工具时重试）: %s",
                role_arn,
                exc_info=True,
            )
    else:
        logger.warning(
            "未配置 RAM Role ARN 或 OIDC Provider ARN，"
            "跳过 Workload Identity 初始化。"
            "设置 ALIBABA_CLOUD_ROLE_ARN 和 ALIBABA_CLOUD_OIDC_PROVIDER_ARN 环境变量，"
            "或在 config/settings.yaml 中配置 agent_identity.role_arn / oidc_provider_arn。"
        )

    # ------------------------------------------------------------------
    # 安全组件
    # ------------------------------------------------------------------
    credential_manager = CredentialManager(
        token_refresh_before_minutes=ai_config.get("token_refresh_before_minutes", 5),
    )

    permission_gate = PermissionGate(
        ram_policies_dir="config/ram_policies",
    )

    audit_logger = AuditLogger(
        local_log_dir="logs/audit",
        backup_log_dir="logs/audit_backup",
    )

    security_guard = SecurityGuard(
        security_rules_path="config/security_rules.yaml",
    )

    # ------------------------------------------------------------------
    # 工具执行层（注入 WorkloadIdentityManager）
    # ------------------------------------------------------------------
    mcp_registry = MCPRegistry()
    tool_executor = ToolExecutor(
        credential_manager=credential_manager,
        permission_gate=permission_gate,
        audit_logger=audit_logger,
        mcp_registry=mcp_registry,
        workload_identity_manager=workload_identity_manager,
    )

    # ------------------------------------------------------------------
    # LLM Provider
    # ------------------------------------------------------------------
    llm_factory = LLMProviderFactory()

    # 注册内置 Demo Provider（无需 API Key，用于本地开发和演示）
    from aiops_agent.llm.demo import DemoProvider

    llm_factory.register("demo", DemoProvider())

    # 如果配置了真实 API Key，注册对应 Provider 并设为主 Provider
    llm_config = config.get("llm", {})
    qwen_config = llm_config.get("providers", {}).get("qwen", {})
    qwen_key = os.getenv("QWEN_API_KEY") or qwen_config.get("api_key", "")
    qwen_model = qwen_config.get("model", "qwen3-235b-a22b")
    if qwen_key:
        from aiops_agent.llm.qwen import QwenProvider
        llm_factory.register("qwen", QwenProvider(
            api_key=qwen_key,
            model=qwen_model,
            api_base=qwen_config.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            max_tokens=qwen_config.get("max_tokens", 4096),
            temperature=qwen_config.get("temperature", 0.7),
            timeout_seconds=qwen_config.get("timeout_seconds", 120),
        ))
        llm_factory.set_primary("qwen")
        logger.info("Qwen Provider 已注册并设为主 Provider (model=%s)", qwen_model)
        
        # 注册 Claude 作为 fallback provider
        claude_config = llm_config.get("providers", {}).get("claude", {})
        claude_key = os.getenv("CLAUDE_API_KEY") or claude_config.get("api_key", "")
        if claude_key:
            from aiops_agent.llm.claude import ClaudeProvider
            llm_factory.register("claude", ClaudeProvider(
                api_key=claude_key,
                model=claude_config.get("model", "claude-3-sonnet-20240229"),
                api_base=claude_config.get("api_base", "https://api.anthropic.com/v1"),
                max_tokens=claude_config.get("max_tokens", 4096),
                temperature=claude_config.get("temperature", 0.7),
                timeout_seconds=claude_config.get("timeout_seconds", 60),
            ))
            llm_factory.set_fallback("claude")
            logger.info("Claude Provider 已注册并设为 Fallback Provider")
        else:
            # 如果没有 Claude API Key，不设置 fallback
            logger.warning("未配置 Claude API Key，将不使用 fallback provider")
        
        # 注册 GPT provider（可选）
        gpt_config = llm_config.get("providers", {}).get("gpt", {})
        gpt_key = os.getenv("OPENAI_API_KEY") or gpt_config.get("api_key", "")
        if gpt_key:
            from aiops_agent.llm.gpt import GPTProvider
            llm_factory.register("gpt", GPTProvider(
                api_key=gpt_key,
                model=gpt_config.get("model", "gpt-4"),
                api_base=gpt_config.get("api_base", "https://api.openai.com/v1"),
                max_tokens=gpt_config.get("max_tokens", 4096),
                temperature=gpt_config.get("temperature", 0.7),
                timeout_seconds=gpt_config.get("timeout_seconds", 60),
            ))
            logger.info("GPT Provider 已注册")
        
    else:
        llm_factory.set_primary("demo")
        logger.warning("未配置 Qwen API Key，使用 Demo Provider 作为主 Provider")

    # ------------------------------------------------------------------
    # 技能注册
    # ------------------------------------------------------------------
    skill_registry = SkillRegistry()
    await _register_default_skills(skill_registry, tool_executor)

    # ------------------------------------------------------------------
    # 上下文管理
    # ------------------------------------------------------------------
    context_manager = ContextManager(
        session_store=SessionStore(),
        memory_layer=MemoryLayer(),
        resource_resolver=ResourceResolver(),
    )

    # ------------------------------------------------------------------
    # 编排器
    # ------------------------------------------------------------------
    metrics_store = get_metrics_store()
    orchestrator = AgentOrchestrator(
        llm_factory=llm_factory,
        skill_registry=skill_registry,
        context_manager=context_manager,
        tool_executor=tool_executor,
        security_guard=security_guard,
        metrics=metrics,
        metrics_store=metrics_store,
    )

    logger.info("AIOps Agent 初始化完成")
    return orchestrator


async def _register_default_skills(
    registry: SkillRegistry,
    tool_executor: "ToolExecutor",
) -> None:
    """注册默认技能，并注入 ToolExecutor."""
    skills = [
        (
            SkillDefinition(
                skill_name="monitoring",
                description="云监控指标查询与 SLS 日志分析，支持 CloudMonitor 多维度指标查询、SLS 日志检索和智能告警分析",
                version="1.0.0",
                capabilities=["query_metrics", "query_logs", "analyze_metrics"],
                required_permissions=["cms:QueryMetricData", "sls:GetLogs"],
                author="AIOps Team",
                category="监控诊断",
                icon="📊",
                tags=["监控", "指标", "日志", "SLS", "CloudMonitor"],
                install_count=128,
                rating=4.7,
                updated_at="2026-04-20",
                readme="# 监控诊断 Skill\n\n实时查询阿里云 CloudMonitor 指标和 SLS 日志，支持多维度聚合分析和智能告警关联。\n\n## 能力\n- `query_metrics` — 查询 CPU、内存、磁盘、网络等监控指标\n- `query_logs` — SLS 日志全文检索和 SQL 分析\n- `analyze_metrics` — 指标趋势分析和异常检测",
            ),
            MonitoringSkill(),
        ),
        (
            SkillDefinition(
                skill_name="troubleshooting",
                description="ECS 健康检查、网络连通性诊断、RDS 慢查询分析，快速定位运维故障根因",
                version="1.0.0",
                capabilities=["ecs_health_check", "network_diagnosis", "rds_slow_query"],
                required_permissions=["ecs:DescribeInstances", "vpc:DescribeVpcs"],
                author="AIOps Team",
                category="故障排查",
                icon="🔍",
                tags=["排查", "ECS", "网络", "RDS", "慢查询"],
                install_count=96,
                rating=4.5,
                updated_at="2026-04-18",
                readme="# 故障排查 Skill\n\n自动化故障排查工具集，覆盖 ECS 实例健康检查、VPC 网络连通性诊断和 RDS 慢查询分析。\n\n## 能力\n- `ecs_health_check` — 检查 ECS 实例状态、系统盘、安全组\n- `network_diagnosis` — VPC/VSW 网络连通性和路由诊断\n- `rds_slow_query` — RDS 慢查询 Top N 分析和优化建议",
            ),
            TroubleshootingSkill(),
        ),
        (
            SkillDefinition(
                skill_name="change_management",
                description="变更风险评估与回滚方案推荐，在执行变更前自动评估影响范围和风险等级",
                version="1.0.0",
                capabilities=["risk_assessment", "rollback_plan"],
                required_permissions=["ecs:DescribeInstances"],
                author="AIOps Team",
                category="变更管理",
                icon="🔄",
                tags=["变更", "风险评估", "回滚", "发布"],
                install_count=64,
                rating=4.3,
                updated_at="2026-04-15",
                readme="# 变更管理 Skill\n\n变更前自动评估风险等级，生成回滚方案，降低变更事故概率。\n\n## 能力\n- `risk_assessment` — 评估变更影响范围、依赖关系和风险等级\n- `rollback_plan` — 生成分步回滚方案和验证检查点",
            ),
            ChangeManagementSkill(),
        ),
    ]

    for definition, instance in skills:
        try:
            instance.set_tool_executor(tool_executor)
            await registry.register(definition, instance)
        except Exception:
            logger.exception("注册技能 '%s' 失败", definition.skill_name)


async def shutdown(orchestrator: AgentOrchestrator) -> None:
    """优雅关闭."""
    logger.info("AIOps Agent 正在关闭...")
    # 清理资源由各组件的 close() 方法处理


def main() -> None:
    """应用入口 — 启动 Web 服务器."""
    from aiops_agent.web.server import run_server

    logging.basicConfig(level=logging.INFO)
    run_server(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
