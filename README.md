# AIOps Agent

Enterprise-grade intelligent operations agent for Alibaba Cloud environments. Built on Python 3.10+ async architecture, integrating Alibaba Cloud Agent Identity security, MCP protocol tool calling, and multi-LLM backends.

## Architecture Overview

```
User Request → API Gateway → Agent Orchestrator ─── Re-entrant Loop ───┐
                                   │                                     │
                              LLM Decompose                         LLM Decide
                                   │                              (CONTINUE/COMPLETED)
                              Skill Router                               │
                                   │                              ← Hook System ←
                         ┌─────────┴──────────┐
                    Parallel Tasks       Sequential Tasks
                  (concurrency_safe)    (!concurrency_safe)
                         └─────────┬──────────┘
                              Tool Executor → MCP Server / Local Tools
                         Permission Gate ↗              ↘ Audit Logger
                         Credential Manager                 (ActionTrail)
                         (Agent Identity)
```

**Core Modules:**

| Module | Responsibility |
|--------|----------------|
| **Agent Orchestrator** | Re-entrant LLM loop, DAG execution, parallel/sequential dispatch, hook integration, health monitoring |
| **Skill Registry** | Skill registration/discovery, version management, capability matching, hot-swap |
| **Context Manager** | Multi-turn dialogue, resource reference resolution, Chat/Task/Watch mode switching |
| **Tool Executor** | MCP protocol integration, permission check, credential injection, timeout/retry, audit sanitization |
| **Security Layer** | Workload Identity, Token Vault credential management, three-tier RBAC, full-chain audit |
| **Hook System** | Lifecycle interceptors at decompose, loop turn, skill execute, and terminal events |

## Quick Start

### Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### Install

```bash
cd aiops-agent
uv sync --all-extras
```

### Start Web Server

```bash
uv run python -m aiops_agent.web.server
```

Open http://localhost:8080 for the Chat UI. The built-in Demo LLM Provider works out of the box — no API key required.

#### Hot-reload development server

```bash
pip install aiohttp-devtools
adev runserver src/aiops_agent/web/server.py
```

Serves on port 8080 with automatic reload on code changes.

### Configure a real LLM

```bash
cp .env.example .env
# Edit .env, set QWEN_API_KEY
export QWEN_API_KEY=your-api-key
uv run python -m aiops_agent.web.server
```

### Run tests

```bash
uv run pytest -v

# With coverage report
uv run pytest --cov=src/aiops_agent --cov-report=html
open htmlcov/index.html
```

## Project Structure

```
aiops-agent/
├── config/                          # Configuration files
│   ├── settings.yaml                # Main config (LLM, timeouts, retry)
│   ├── skills.yaml                  # Skill configuration
│   ├── mcp_servers.yaml             # MCP Server config
│   ├── security_rules.yaml          # Security rules (blacklist, rate limit)
│   └── ram_policies/                # RAM Policy templates
├── src/aiops_agent/
│   ├── core/                        # Orchestration layer
│   │   ├── orchestrator.py          # Agent orchestrator (re-entrant loop)
│   │   ├── hooks.py                 # Lifecycle hook system (HookRegistry)
│   │   ├── task_planner.py          # LLM task decomposition + DAG
│   │   ├── state_machine.py         # Task state machine
│   │   └── exceptions.py            # Exception hierarchy
│   ├── skills/                      # Skill layer
│   │   ├── registry.py              # Skill registry
│   │   ├── base.py                  # SkillInstance base class (self-describing)
│   │   ├── monitoring.py            # Monitoring & diagnostics
│   │   ├── troubleshooting.py       # Fault troubleshooting
│   │   ├── change_management.py     # Change management
│   │   ├── capacity_planning.py     # Capacity planning
│   │   ├── incident_response.py     # Incident response
│   │   └── knowledge_base.py        # Knowledge base retrieval
│   ├── context/                     # Context management
│   │   ├── manager.py               # Context Manager
│   │   ├── session.py               # Session state
│   │   ├── memory.py                # Short/long-term memory
│   │   └── resource_resolver.py     # Resource reference resolution
│   ├── tools/                       # Tool execution layer
│   │   ├── executor.py              # Tool Executor
│   │   ├── mcp_client.py            # MCP protocol client
│   │   ├── mcp_registry.py          # MCP Server management
│   │   └── local_tools.py           # Local tool registry
│   ├── security/                    # Security layer
│   │   ├── identity.py              # Workload Identity
│   │   ├── credential_manager.py    # Credential management (Token Vault)
│   │   ├── permission_gate.py       # RBAC permission check
│   │   ├── audit_logger.py          # Audit logging
│   │   ├── security_guard.py        # Security guard engine
│   │   └── sanitizer.py             # Sensitive data sanitization
│   ├── llm/                         # LLM abstraction layer
│   │   ├── provider.py              # Provider interface + factory
│   │   ├── demo.py                  # Built-in demo provider
│   │   ├── qwen.py                  # Tongyi Qianwen
│   │   ├── claude.py                # Claude
│   │   └── gpt.py                   # GPT
│   ├── observability/               # Observability
│   │   ├── tracing.py               # OpenTelemetry tracing
│   │   ├── metrics.py               # Core metrics collection
│   │   ├── metrics_store.py         # Request/skill call event store
│   │   └── logging.py               # JSON structured logging
│   ├── web/                         # Web service
│   │   ├── server.py                # aiohttp API server (SSE streaming)
│   │   └── static/index.html        # Chat UI frontend
│   ├── models/schemas.py            # Pydantic v2 data models
│   └── main.py                      # Application entry point
├── tests/                           # Tests (915 passing)
│   ├── conftest.py                  # Shared fixtures
│   ├── test_orchestrator.py         # Orchestrator tests
│   ├── test_schemas.py              # Data model tests
│   ├── test_monitoring_skill.py     # Monitoring skill tests
│   ├── test_capacity_planning_skill.py
│   ├── test_incident_response_skill.py
│   ├── test_knowledge_base_skill.py
│   └── properties/                  # Property-based tests (hypothesis)
└── deploy/                          # Deployment
    ├── Dockerfile                   # Multi-stage build
    ├── docker-compose.yaml          # Local development
    └── k8s/                         # ACK deployment
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Chat UI frontend |
| POST | `/api/chat` | Send a chat request (synchronous) |
| GET | `/api/stream` | SSE streaming chat (real-time task events) |
| GET | `/api/skills` | List registered skills |
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check |

### POST /api/chat

```json
{
  "message": "Check CPU usage of ECS instance i-bp1234567890",
  "session_id": "optional-session-id",
  "user_id": "optional-user-id"
}
```

Response:

```json
{
  "success": true,
  "message": "Task completed",
  "data": {
    "plan": { "..." },
    "terminal": {
      "reason": "completed",
      "turn_count": 1,
      "elapsed_ms": 312.5
    }
  },
  "session_id": "...",
  "trace_id": "..."
}
```

### SSE Event Stream (`/api/stream`)

The streaming endpoint emits newline-delimited JSON events:

| Event type | Description |
|------------|-------------|
| `planning` | Task decomposition started / completed |
| `task_start` | A sub-task begins (includes `mode: parallel\|sequential`) |
| `task_done` | A sub-task finishes (includes `status`, `result`, `progress`) |
| `token` | LLM synthesis token (streamed after all tasks complete) |
| `loop_turn` | A new re-entrant loop turn begins |
| `done` | All work complete — includes `terminal` with loop reason |
| `error` | Execution error |

## Re-entrant Agent Loop

The orchestrator runs a **multi-turn loop** modeled after Claude Code's generator loop pattern:

```
Decompose → Execute Plan → LLM Decision
                               │
               ┌───────────────┴───────────────┐
           COMPLETED                        CONTINUE
               │                               │
           Terminate                   Generate supplementary
        (LoopTerminal)                  tasks → next turn
```

Loop termination is controlled by `LoopConfig`:

```python
LoopConfig(
    max_turns=5,           # Hard cap on loop iterations
    max_tokens=None,       # Optional token budget
    max_elapsed_ms=None,   # Optional time budget (ms)
    continue_threshold=0.7 # LLM confidence threshold
)
```

Every response includes a `LoopTerminal` describing why the loop ended:

| Reason | Meaning |
|--------|---------|
| `completed` | LLM judged the task fully done |
| `max_turns` | Hit the `max_turns` limit |
| `budget_exhausted` | Token budget consumed |
| `aborted` | Time limit exceeded |
| `unrecoverable_error` | All sub-tasks failed |

## Self-Describing Skills

Each skill declares its own capabilities — the orchestrator reads these without needing to know each skill's internals:

```python
class MonitoringSkill(SkillInstance):
    concurrency_safe = True                              # Can run in parallel
    permission_requirements = [PermissionLevel.READ_ONLY]
    description = "Cloud monitor metrics & SLS log analysis"
    render_format = "json"                               # json | markdown | text
```

| Declaration | Effect |
|-------------|--------|
| `concurrency_safe = True` | Skill is batched with other safe skills and run via `asyncio.gather()` |
| `concurrency_safe = False` | Skill executes sequentially (e.g. change management, incident response) |
| `permission_requirements` | Enforced by Permission Gate before execution |
| `render_format` | Controls how `render_result()` formats output for LLM synthesis |

## Lifecycle Hook System

Register interceptors at any execution lifecycle point without modifying core code:

```python
from aiops_agent.core.hooks import HookRegistry, HookEvent, HookContext

registry = HookRegistry()

async def audit_decompose(ctx: HookContext) -> HookContext:
    print(f"Decomposing: {ctx.user_input}")
    return ctx

registry.register(HookEvent.PRE_DECOMPOSE, audit_decompose)

# Pass to orchestrator
orchestrator = AgentOrchestrator(..., hook_registry=registry)
```

Available events: `PRE_DECOMPOSE`, `POST_DECOMPOSE`, `PRE_SKILL_EXECUTE`, `POST_SKILL_EXECUTE`, `PRE_LOOP_TURN`, `POST_LOOP_TURN`, `ON_ERROR`, `ON_TERMINAL`.

## Security

Built on Alibaba Cloud Agent Identity:

- **Workload Identity** — Each agent instance has a unique ARN digital identity
- **Token Vault** — KMS-encrypted credential store; agent code never touches long-lived credentials
- **On-Behalf-Of** — Delegated authorization: agent permissions = Agent ∩ User permissions
- **Three-tier RBAC** — Read-Only (default) → Limited-Write (requires approval) → Admin (forced approval)
- **Full-chain audit** — ActionTrail integration + local JSONL backup
- **Security guard** — High-risk operation blacklist, API rate limiting, anomaly detection

## Observability

- OpenTelemetry full-chain tracing (`@traced` decorator)
- Core metrics: task completion rate, response time, permission denials, security events
- `MetricsStore` records per-request and per-skill-call events with LLM call counts
- JSON structured logging with automatic `trace_id` / `span_id` injection
- SLS log service export supported

## Tech Stack

| Component | Technology |
|-----------|------------|
| Async framework | asyncio + aiohttp |
| Data models | Pydantic v2 |
| LLM backends | Tongyi Qianwen / Claude / GPT (switchable) |
| Tool protocol | MCP (JSON-RPC over stdio/SSE) |
| Identity & security | Alibaba Cloud Agent Identity |
| Observability | OpenTelemetry |
| Testing | pytest + hypothesis + pytest-cov (915 tests, 81%+ coverage) |
| Dev server | aiohttp-devtools (hot-reload) |

## License

MIT
