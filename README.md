# AIOps Agent

面向阿里云环境的企业级智能运维代理。基于 Python 3.10+ 异步架构，集成阿里云 Agent Identity 安全体系，支持 MCP 协议工具调用和多 LLM 后端。

## 架构概览

```
用户请求 → API Gateway → Agent Orchestrator → LLM 任务分解 → Skill 路由
                                                                    ↓
                                              Permission Gate ← Tool Executor → MCP Server / 本地工具
                                              Credential Manager ↗        ↘ Audit Logger
                                              (Agent Identity)              (ActionTrail)
```

**五大核心模块：**

| 模块 | 职责 |
|------|------|
| **Agent Orchestrator** | 任务分解、DAG 编排、并行执行、失败处理、健康监控 |
| **Skill Registry** | 技能注册/发现、版本管理、能力匹配、运行时热插拔 |
| **Context Manager** | 多轮对话、资源引用解析、Chat/Task/Watch 模式切换 |
| **Tool Executor** | MCP 协议集成、权限校验、凭证注入、超时重试、脱敏审计 |
| **Security Layer** | Workload Identity、Token Vault 凭证托管、RBAC 三级权限、全链路审计 |

## 快速开始

### 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
cd aiops-agent
uv sync --all-extras
```

### 启动 Web 服务

```bash
uv run python -m aiops_agent.web.server
```

打开浏览器访问 http://localhost:8080，即可使用 Chat UI。

内置 Demo LLM Provider，无需 API Key 即可体验完整流程。

### 配置真实 LLM

```bash
cp .env.example .env
# 编辑 .env，填入 QWEN_API_KEY
export QWEN_API_KEY=your-api-key
uv run python -m aiops_agent.web.server
```

### 运行测试

```bash
uv run pytest -v
```

## 项目结构

```
aiops-agent/
├── config/                          # 配置文件
│   ├── settings.yaml                # 主配置（LLM、超时、重试）
│   ├── skills.yaml                  # 技能配置
│   ├── mcp_servers.yaml             # MCP Server 配置
│   ├── security_rules.yaml          # 安全规则（黑名单、频率限制）
│   └── ram_policies/                # RAM Policy 模板
├── src/aiops_agent/
│   ├── core/                        # 编排层
│   │   ├── orchestrator.py          # Agent 编排器
│   │   ├── task_planner.py          # LLM 任务分解 + DAG
│   │   ├── state_machine.py         # 任务状态机
│   │   └── exceptions.py            # 异常层次结构
│   ├── skills/                      # 技能层
│   │   ├── registry.py              # 技能注册中心
│   │   ├── base.py                  # 技能基类
│   │   ├── monitoring.py            # 监控诊断
│   │   ├── troubleshooting.py       # 故障排查
│   │   └── change_management.py     # 变更管理
│   ├── context/                     # 上下文管理
│   │   ├── manager.py               # Context Manager
│   │   ├── session.py               # 会话状态
│   │   ├── memory.py                # 短期/长期记忆
│   │   └── resource_resolver.py     # 资源引用解析
│   ├── tools/                       # 工具执行层
│   │   ├── executor.py              # Tool Executor
│   │   ├── mcp_client.py            # MCP 协议客户端
│   │   ├── mcp_registry.py          # MCP Server 管理
│   │   └── local_tools.py           # 本地工具注册
│   ├── security/                    # 安全层
│   │   ├── identity.py              # Workload Identity
│   │   ├── credential_manager.py    # 凭证管理（Token Vault）
│   │   ├── permission_gate.py       # RBAC 权限校验
│   │   ├── audit_logger.py          # 审计日志
│   │   ├── security_guard.py        # 安全防护引擎
│   │   └── sanitizer.py             # 敏感数据脱敏
│   ├── llm/                         # LLM 抽象层
│   │   ├── provider.py              # Provider 接口 + 工厂
│   │   ├── demo.py                  # 内置演示 Provider
│   │   ├── qwen.py                  # 通义千问
│   │   ├── claude.py                # Claude
│   │   └── gpt.py                   # GPT
│   ├── observability/               # 可观测性
│   │   ├── tracing.py               # OpenTelemetry Tracing
│   │   ├── metrics.py               # 核心指标采集
│   │   └── logging.py               # JSON 结构化日志
│   ├── web/                         # Web 服务
│   │   ├── server.py                # aiohttp API 服务器
│   │   └── static/index.html        # Chat UI 前端
│   ├── models/schemas.py            # Pydantic 数据模型
│   └── main.py                      # 应用入口
├── tests/                           # 测试
│   ├── conftest.py                  # 共享 fixtures
│   ├── test_schemas.py              # 数据模型测试
│   └── properties/                  # 属性测试
└── deploy/                          # 部署
    ├── Dockerfile                   # 多阶段构建
    ├── docker-compose.yaml          # 本地开发
    └── k8s/                         # ACK 部署
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Chat UI 前端页面 |
| POST | `/api/chat` | 发送对话请求 |
| GET | `/api/skills` | 列出可用技能 |
| GET | `/health` | 健康检查 |
| GET | `/ready` | 就绪检查 |

### POST /api/chat

```json
{
  "message": "查看 ECS 实例 i-bp1234567890 的 CPU 使用率",
  "session_id": "optional-session-id",
  "user_id": "optional-user-id"
}
```

响应：

```json
{
  "success": true,
  "message": "任务执行完成",
  "data": { "plan": { ... } },
  "session_id": "...",
  "trace_id": "..."
}
```

## 安全体系

基于阿里云 Agent Identity 服务构建：

- **Workload Identity** — 每个 Agent 实例拥有唯一 ARN 数字身份
- **Token Vault** — KMS 加密凭证库，Agent 代码不接触长期凭证
- **On-Behalf-Of** — 委托授权，Agent 权限 = Agent ∩ 用户权限
- **三级 RBAC** — Read-Only（默认）→ Limited-Write（需审批）→ Admin（强制审批）
- **全链路审计** — ActionTrail 集成 + 本地 JSONL 备份
- **安全防护** — 高危操作黑名单、API 频率限制、异常行为检测

## 可观测性

- OpenTelemetry 全链路 Tracing（`@traced` 装饰器）
- 核心指标：任务完成率、响应时间、权限拒绝次数、安全事件数
- JSON 结构化日志，自动注入 trace_id / span_id
- 支持 SLS 日志服务导出

## 技术栈

| 组件 | 选型 |
|------|------|
| 异步框架 | asyncio + aiohttp |
| 数据模型 | Pydantic v2 |
| LLM 后端 | 通义千问 / Claude / GPT（可切换） |
| 工具协议 | MCP (JSON-RPC over stdio/SSE) |
| 身份安全 | 阿里云 Agent Identity |
| 可观测性 | OpenTelemetry |
| 测试 | pytest + hypothesis |

## License

MIT
