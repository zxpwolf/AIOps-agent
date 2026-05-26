# Layered Architecture Patterns

<cite>
**Referenced Files in This Document**
- [src/aiops_agent/main.py](file://src/aiops_agent/main.py)
- [src/aiops_agent/web/server.py](file://src/aiops_agent/web/server.py)
- [src/aiops_agent/core/orchestrator.py](file://src/aiops_agent/core/orchestrator.py)
- [src/aiops_agent/core/task_planner.py](file://src/aiops_agent/core/task_planner.py)
- [src/aiops_agent/skills/registry.py](file://src/aiops_agent/skills/registry.py)
- [src/aiops_agent/skills/base.py](file://src/aiops_agent/skills/base.py)
- [src/aiops_agent/tools/executor.py](file://src/aiops_agent/tools/executor.py)
- [src/aiops_agent/tools/mcp_registry.py](file://src/aiops_agent/tools/mcp_registry.py)
- [src/aiops_agent/context/manager.py](file://src/aiops_agent/context/manager.py)
- [src/aiops_agent/llm/provider.py](file://src/aiops_agent/llm/provider.py)
- [src/aiops_agent/models/schemas.py](file://src/aiops_agent/models/schemas.py)
- [src/aiops_agent/observability/logging.py](file://src/aiops_agent/observability/logging.py)
- [src/aiops_agent/observability/metrics.py](file://src/aiops_agent/observability/metrics.py)
- [src/aiops_agent/observability/tracing.py](file://src/aiops_agent/observability/tracing.py)
- [src/aiops_agent/security/permission_gate.py](file://src/aiops_agent/security/permission_gate.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the layered architecture used by the AIOps Agent, focusing on four main layers:
- Presentation Layer: aiohttp web server exposing REST APIs and serving a frontend.
- Business Logic Layer: Agent Orchestrator, Task Planner, and Skills.
- Integration Layer: MCP servers and cloud services via ToolExecutor and MCPRegistry.
- Infrastructure Layer: Security (RBAC, Workload Identity, auditing) and Observability (logging, metrics, tracing).

We describe how each layer encapsulates responsibilities, maintains separation of concerns, and communicates with adjacent layers. We also detail the architectural patterns implemented: Orchestrator pattern, Registry pattern, Factory pattern, and Observer-like health monitoring. Cross-cutting concerns are addressed through dependency inversion and modular design to support testability and extensibility.

## Project Structure
The codebase follows a feature-oriented package layout under src/aiops_agent with clear boundaries:
- web/: HTTP server and routes
- core/: orchestration, planning, state machine
- skills/: skill registry, base skill interface, and skill implementations
- tools/: unified tool execution, MCP registry/client, and local tools
- context/: session, memory, and resource resolution
- llm/: provider abstraction and factories
- models/: shared Pydantic schemas
- observability/: logging, metrics, tracing
- security/: permission gate, identity, audit, sanitization

```mermaid
graph TB
subgraph "Presentation Layer"
WEB["web/server.py"]
end
subgraph "Business Logic Layer"
ORCH["core/orchestrator.py"]
TP["core/task_planner.py"]
REG["skills/registry.py"]
SKBASE["skills/base.py"]
end
subgraph "Integration Layer"
EXEC["tools/executor.py"]
MCPREG["tools/mcp_registry.py"]
end
subgraph "Infrastructure Layer"
CTX["context/manager.py"]
LLMF["llm/provider.py"]
SCHEMAS["models/schemas.py"]
OBSLOG["observability/logging.py"]
OBSMET["observability/metrics.py"]
OBSTRACE["observability/tracing.py"]
PERMG["security/permission_gate.py"]
end
WEB --> ORCH
ORCH --> TP
ORCH --> REG
ORCH --> CTX
ORCH --> EXEC
ORCH --> LLMF
EXEC --> MCPREG
ORCH --> OBSLOG
ORCH --> OBSTRACE
ORCH --> OBSTMET
ORCH --> PERMG
```

**Diagram sources**
- [src/aiops_agent/web/server.py:196-227](file://src/aiops_agent/web/server.py#L196-L227)
- [src/aiops_agent/core/orchestrator.py:47-220](file://src/aiops_agent/core/orchestrator.py#L47-L220)
- [src/aiops_agent/core/task_planner.py:32-114](file://src/aiops_agent/core/task_planner.py#L32-L114)
- [src/aiops_agent/skills/registry.py:19-81](file://src/aiops_agent/skills/registry.py#L19-L81)
- [src/aiops_agent/tools/executor.py:45-106](file://src/aiops_agent/tools/executor.py#L45-L106)
- [src/aiops_agent/tools/mcp_registry.py:20-70](file://src/aiops_agent/tools/mcp_registry.py#L20-L70)
- [src/aiops_agent/context/manager.py:25-45](file://src/aiops_agent/context/manager.py#L25-L45)
- [src/aiops_agent/llm/provider.py:97-146](file://src/aiops_agent/llm/provider.py#L97-L146)
- [src/aiops_agent/models/schemas.py:14-82](file://src/aiops_agent/models/schemas.py#L14-L82)
- [src/aiops_agent/observability/logging.py:60-111](file://src/aiops_agent/observability/logging.py#L60-L111)
- [src/aiops_agent/observability/metrics.py:26-106](file://src/aiops_agent/observability/metrics.py#L26-L106)
- [src/aiops_agent/observability/tracing.py:32-88](file://src/aiops_agent/observability/tracing.py#L32-L88)
- [src/aiops_agent/security/permission_gate.py:57-101](file://src/aiops_agent/security/permission_gate.py#L57-L101)

**Section sources**
- [src/aiops_agent/main.py:70-222](file://src/aiops_agent/main.py#L70-L222)
- [src/aiops_agent/web/server.py:196-227](file://src/aiops_agent/web/server.py#L196-L227)

## Core Components
This section summarizes the responsibilities and interactions of the core components across layers.

- Presentation Layer (aiohttp)
  - Exposes REST endpoints and serves a static frontend.
  - Delegates request handling to the Agent Orchestrator.
  - Provides health/ready checks and skills listing.

- Business Logic Layer
  - Agent Orchestrator: central coordinator receiving requests, invoking LLM for decomposition, routing tasks to skills, managing progress, and generating summaries.
  - Task Planner: converts natural language into a DAG of SubTasks using LLM and validates skill availability.
  - Skill Registry: manages skill registration, discovery, health, and default version selection.
  - Skill Base: defines the SkillInstance contract and lifecycle hooks.

- Integration Layer
  - ToolExecutor: unified execution pipeline enforcing permissions, acquiring credentials, dispatching to MCP or local tools, retry/backoff, sanitization, and audit logging.
  - MCPRegistry: dynamic registration/discovery of MCP servers and mapping tools to servers.

- Infrastructure Layer
  - Context Manager: session/state management, memory, resource resolution, and task progress tracking.
  - LLM Provider Factory: abstraction over multiple LLM providers with primary/fallback selection and automatic failover.
  - Observability: structured logging, metrics, and tracing; integrates OpenTelemetry spans and attributes.
  - Security: PermissionGate for RBAC and resource ARN matching, Workload Identity integration, audit logging, and input sanitization.

**Section sources**
- [src/aiops_agent/web/server.py:44-146](file://src/aiops_agent/web/server.py#L44-L146)
- [src/aiops_agent/core/orchestrator.py:47-198](file://src/aiops_agent/core/orchestrator.py#L47-L198)
- [src/aiops_agent/core/task_planner.py:32-114](file://src/aiops_agent/core/task_planner.py#L32-L114)
- [src/aiops_agent/skills/registry.py:19-81](file://src/aiops_agent/skills/registry.py#L19-L81)
- [src/aiops_agent/skills/base.py:21-93](file://src/aiops_agent/skills/base.py#L21-L93)
- [src/aiops_agent/tools/executor.py:45-106](file://src/aiops_agent/tools/executor.py#L45-L106)
- [src/aiops_agent/tools/mcp_registry.py:20-70](file://src/aiops_agent/tools/mcp_registry.py#L20-L70)
- [src/aiops_agent/context/manager.py:25-45](file://src/aiops_agent/context/manager.py#L25-L45)
- [src/aiops_agent/llm/provider.py:97-146](file://src/aiops_agent/llm/provider.py#L97-L146)
- [src/aiops_agent/observability/logging.py:60-111](file://src/aiops_agent/observability/logging.py#L60-L111)
- [src/aiops_agent/observability/metrics.py:26-106](file://src/aiops_agent/observability/metrics.py#L26-L106)
- [src/aiops_agent/observability/tracing.py:32-88](file://src/aiops_agent/observability/tracing.py#L32-L88)
- [src/aiops_agent/security/permission_gate.py:57-101](file://src/aiops_agent/security/permission_gate.py#L57-L101)

## Architecture Overview
The system is a layered pipeline:
- Presentation Layer receives HTTP requests and delegates to the Orchestrator.
- Orchestrator coordinates Task Planning, Skill Routing, and Execution.
- Tools are executed through ToolExecutor, which enforces security and resolves MCP/local targets.
- Observability and Security are cross-cutting concerns applied at each layer boundary.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Web as "Web Server"
participant Orchestrator as "AgentOrchestrator"
participant Planner as "TaskPlanner"
participant Registry as "SkillRegistry"
participant Skill as "SkillInstance"
participant Executor as "ToolExecutor"
participant MCPReg as "MCPRegistry"
Client->>Web : POST /api/chat
Web->>Orchestrator : process_request(user_input, session_id, user_id)
Orchestrator->>Planner : decompose(user_input, context)
Planner-->>Orchestrator : TaskPlan(sub_tasks)
Orchestrator->>Registry : get_skill(skill_name)
Registry-->>Orchestrator : SkillInstance
Orchestrator->>Skill : validate(parameters)
Skill-->>Orchestrator : ValidationResult
Orchestrator->>Skill : execute(parameters)
Skill->>Executor : tool_name, arguments, identity
Executor->>MCPReg : find_tool / get_client_for_tool
MCPReg-->>Executor : MCPClient
Executor-->>Skill : ToolResult
Skill-->>Orchestrator : result
Orchestrator-->>Web : AgentResponse
Web-->>Client : JSON response
```

**Diagram sources**
- [src/aiops_agent/web/server.py:44-82](file://src/aiops_agent/web/server.py#L44-L82)
- [src/aiops_agent/core/orchestrator.py:84-198](file://src/aiops_agent/core/orchestrator.py#L84-L198)
- [src/aiops_agent/core/task_planner.py:50-114](file://src/aiops_agent/core/task_planner.py#L50-L114)
- [src/aiops_agent/skills/registry.py:159-182](file://src/aiops_agent/skills/registry.py#L159-L182)
- [src/aiops_agent/tools/executor.py:80-106](file://src/aiops_agent/tools/executor.py#L80-L106)
- [src/aiops_agent/tools/mcp_registry.py:95-112](file://src/aiops_agent/tools/mcp_registry.py#L95-L112)

## Detailed Component Analysis

### Presentation Layer: aiohttp Web Server
Responsibilities:
- Define routes for chat, skills listing, health/ready checks, and serve static assets.
- Initialize the Agent Orchestrator lazily on first request.
- Stream responses via Server-Sent Events for long-running tasks.

Key interactions:
- Routes delegate to Orchestrator’s synchronous and streaming handlers.
- Uses JSON serialization helpers and handles malformed JSON gracefully.

```mermaid
flowchart TD
Start(["HTTP Request"]) --> Route["Route Resolution"]
Route --> Sync{"Sync or Stream?"}
Sync --> |Sync| GetOrchestrator["Get/create Orchestrator"]
Sync --> |Stream| GetOrchestrator
GetOrchestrator --> CallOrchestrator["Call Orchestrator.process_request/_stream"]
CallOrchestrator --> Respond["Build JSON/SSE Response"]
Respond --> End(["HTTP Response"])
```

**Diagram sources**
- [src/aiops_agent/web/server.py:44-146](file://src/aiops_agent/web/server.py#L44-L146)

**Section sources**
- [src/aiops_agent/web/server.py:196-227](file://src/aiops_agent/web/server.py#L196-L227)

### Business Logic Layer: Orchestrator Pattern
Responsibilities:
- Accept user requests, sanitize input, update context, switch to task mode, decompose via LLM, execute tasks in DAG order with parallelism, and summarize outcomes.
- Enforce security via input sanitization and health monitoring for skills.
- Track progress and emit structured telemetry.

Architectural pattern: Orchestrator
- Central coordination point that composes other subsystems (planner, registry, executor, context, metrics, tracing).
- Applies dependency inversion by accepting abstractions (LLMFactory, SkillRegistry, ToolExecutor).

```mermaid
classDiagram
class AgentOrchestrator {
+process_request(user_input, session_id, user_id) AgentResponse
+process_request_stream(...) AsyncIterator
-_execute_plan(plan, session_id) TaskPlan
-_route_to_skill(sub_task) void
-_sanitize_input(user_input) str
-_record_skill_failure(skill_name, error) void
}
class TaskPlanner {
+decompose(user_input, context) TaskPlan
+topological_sort(plan) list
}
class SkillRegistry {
+register(definition, instance) void
+get_skill(name, version) SkillInstance
+discover(capabilities) list
+mark_unhealthy(name) void
}
class ContextManager {
+get_session(id, user_id) SessionState
+update_context(session_id, message) void
+switch_mode(session_id, mode) void
+update_task_progress(...) void
}
class ToolExecutor {
+execute(tool_name, args, identity, ...) ToolResult
}
class LLMProviderFactory {
+chat(messages) ChatResponse
+chat_stream(messages) AsyncIterator
}
AgentOrchestrator --> TaskPlanner : "uses"
AgentOrchestrator --> SkillRegistry : "uses"
AgentOrchestrator --> ContextManager : "uses"
AgentOrchestrator --> ToolExecutor : "uses"
AgentOrchestrator --> LLMProviderFactory : "uses"
```

**Diagram sources**
- [src/aiops_agent/core/orchestrator.py:47-198](file://src/aiops_agent/core/orchestrator.py#L47-L198)
- [src/aiops_agent/core/task_planner.py:32-114](file://src/aiops_agent/core/task_planner.py#L32-L114)
- [src/aiops_agent/skills/registry.py:19-81](file://src/aiops_agent/skills/registry.py#L19-L81)
- [src/aiops_agent/context/manager.py:25-45](file://src/aiops_agent/context/manager.py#L25-L45)
- [src/aiops_agent/tools/executor.py:45-106](file://src/aiops_agent/tools/executor.py#L45-L106)
- [src/aiops_agent/llm/provider.py:97-146](file://src/aiops_agent/llm/provider.py#L97-L146)

**Section sources**
- [src/aiops_agent/core/orchestrator.py:47-198](file://src/aiops_agent/core/orchestrator.py#L47-L198)

### Business Logic Layer: Task Planner and DAG Execution
Responsibilities:
- Convert natural language into a TaskPlan with SubTasks and dependencies.
- Build a DAG and perform topological sorting to determine execution order.
- Validate skill mapping and mark unmappable tasks as failed.

```mermaid
flowchart TD
A["User Input"] --> B["Build Messages (system + skills info)"]
B --> C["LLM chat()"]
C --> D["Parse JSON to SubTasks"]
D --> E["Validate skill mapping"]
E --> F["Topological sort into levels"]
F --> G["Execute levels in order<br/>Parallel within level"]
```

**Diagram sources**
- [src/aiops_agent/core/task_planner.py:50-151](file://src/aiops_agent/core/task_planner.py#L50-L151)

**Section sources**
- [src/aiops_agent/core/task_planner.py:32-151](file://src/aiops_agent/core/task_planner.py#L32-L151)

### Business Logic Layer: Skill Registry Pattern
Responsibilities:
- Register/unregister skills with validation and uniqueness checks.
- Discover skills by capability overlap and default to latest healthy version.
- Health management: mark unhealthy, recover, and expose health status.

```mermaid
classDiagram
class SkillRegistry {
+register(definition, instance) void
+unregister(skill_name, version) void
+discover(capabilities) list
+get_skill(name, version) SkillInstance
+health_check(name) bool
+mark_unhealthy(name) void
+mark_healthy(name) void
}
class SkillInstance {
<<abstract>>
+execute(input_data) dict
+validate(input_data) ValidationResult
+on_register() void
+on_unregister() void
+health_check() bool
}
SkillRegistry --> SkillInstance : "manages"
```

**Diagram sources**
- [src/aiops_agent/skills/registry.py:19-81](file://src/aiops_agent/skills/registry.py#L19-L81)
- [src/aiops_agent/skills/base.py:21-93](file://src/aiops_agent/skills/base.py#L21-L93)

**Section sources**
- [src/aiops_agent/skills/registry.py:19-284](file://src/aiops_agent/skills/registry.py#L19-L284)
- [src/aiops_agent/skills/base.py:21-93](file://src/aiops_agent/skills/base.py#L21-L93)

### Integration Layer: ToolExecutor and MCP Registry
Responsibilities:
- Unified tool execution pipeline: permission check, credential acquisition, dispatch to MCP or local tool, retry/backoff, sanitization, audit logging, and tracing.
- MCPRegistry: dynamic registration of MCP servers, tool discovery, and mapping tool names to clients.

```mermaid
sequenceDiagram
participant Orchestrator as "AgentOrchestrator"
participant Skill as "SkillInstance"
participant Executor as "ToolExecutor"
participant Permg as "PermissionGate"
participant CM as "CredentialManager"
participant MCPReg as "MCPRegistry"
participant MCPClient as "MCPClient"
Orchestrator->>Skill : execute(parameters)
Skill->>Executor : execute(tool_name, args, identity)
Executor->>Permg : check_permission(...)
Permg-->>Executor : PermissionCheckResult
Executor->>CM : get_aliyun_credential(...) (optional)
Executor->>MCPReg : get_client_for_tool(tool_name)
MCPReg-->>Executor : MCPClient
Executor->>MCPClient : call_tool(tool_name, args)
MCPClient-->>Executor : ToolResult
Executor-->>Skill : ToolResult
```

**Diagram sources**
- [src/aiops_agent/tools/executor.py:80-106](file://src/aiops_agent/tools/executor.py#L80-L106)
- [src/aiops_agent/security/permission_gate.py:95-182](file://src/aiops_agent/security/permission_gate.py#L95-L182)
- [src/aiops_agent/tools/mcp_registry.py:95-112](file://src/aiops_agent/tools/mcp_registry.py#L95-L112)

**Section sources**
- [src/aiops_agent/tools/executor.py:45-202](file://src/aiops_agent/tools/executor.py#L45-L202)
- [src/aiops_agent/tools/mcp_registry.py:20-117](file://src/aiops_agent/tools/mcp_registry.py#L20-L117)

### Infrastructure Layer: Security and Observability
Responsibilities:
- Security: RBAC via PermissionGate, Workload Identity integration, audit logging, input sanitization, and security rule enforcement.
- Observability: structured logging with trace/span IDs, metrics counters/histograms, and tracing spans with decorators.

```mermaid
graph TB
subgraph "Security"
PERMG["PermissionGate"]
AUDIT["AuditLogger"]
SAN["Sanitizer"]
WIM["WorkloadIdentityManager"]
end
subgraph "Observability"
LOG["JSONFormatter"]
MET["AgentMetrics"]
TRACE["Tracing Decorator"]
end
PERMG --> AUDIT
PERMG --> SAN
WIM --> PERMG
LOG --> TRACE
MET --> TRACE
```

**Diagram sources**
- [src/aiops_agent/security/permission_gate.py:57-101](file://src/aiops_agent/security/permission_gate.py#L57-L101)
- [src/aiops_agent/observability/logging.py:18-58](file://src/aiops_agent/observability/logging.py#L18-L58)
- [src/aiops_agent/observability/metrics.py:26-106](file://src/aiops_agent/observability/metrics.py#L26-L106)
- [src/aiops_agent/observability/tracing.py:98-137](file://src/aiops_agent/observability/tracing.py#L98-L137)

**Section sources**
- [src/aiops_agent/observability/logging.py:60-111](file://src/aiops_agent/observability/logging.py#L60-L111)
- [src/aiops_agent/observability/metrics.py:26-106](file://src/aiops_agent/observability/metrics.py#L26-L106)
- [src/aiops_agent/observability/tracing.py:32-88](file://src/aiops_agent/observability/tracing.py#L32-L88)
- [src/aiops_agent/security/permission_gate.py:57-101](file://src/aiops_agent/security/permission_gate.py#L57-L101)

## Dependency Analysis
The system exhibits strong separation of concerns and low coupling between layers:
- Presentation depends on Orchestrator only.
- Orchestrator depends on abstractions (LLMFactory, SkillRegistry, ToolExecutor, ContextManager).
- Integration depends on Registry abstractions and schemas.
- Infrastructure components are injected and used via composition.

```mermaid
graph LR
Web["web/server.py"] --> Orchestrator["core/orchestrator.py"]
Orchestrator --> TaskPlanner["core/task_planner.py"]
Orchestrator --> SkillRegistry["skills/registry.py"]
Orchestrator --> ContextMgr["context/manager.py"]
Orchestrator --> ToolExec["tools/executor.py"]
Orchestrator --> LLMF["llm/provider.py"]
ToolExec --> MCPReg["tools/mcp_registry.py"]
Orchestrator --> ObsLog["observability/logging.py"]
Orchestrator --> ObsTrace["observability/tracing.py"]
Orchestrator --> ObsMet["observability/metrics.py"]
Orchestrator --> PermGate["security/permission_gate.py"]
```

**Diagram sources**
- [src/aiops_agent/web/server.py:196-227](file://src/aiops_agent/web/server.py#L196-L227)
- [src/aiops_agent/core/orchestrator.py:47-198](file://src/aiops_agent/core/orchestrator.py#L47-L198)
- [src/aiops_agent/core/task_planner.py:32-114](file://src/aiops_agent/core/task_planner.py#L32-L114)
- [src/aiops_agent/skills/registry.py:19-81](file://src/aiops_agent/skills/registry.py#L19-L81)
- [src/aiops_agent/context/manager.py:25-45](file://src/aiops_agent/context/manager.py#L25-L45)
- [src/aiops_agent/tools/executor.py:45-106](file://src/aiops_agent/tools/executor.py#L45-L106)
- [src/aiops_agent/tools/mcp_registry.py:20-70](file://src/aiops_agent/tools/mcp_registry.py#L20-L70)
- [src/aiops_agent/llm/provider.py:97-146](file://src/aiops_agent/llm/provider.py#L97-L146)
- [src/aiops_agent/observability/logging.py:60-111](file://src/aiops_agent/observability/logging.py#L60-L111)
- [src/aiops_agent/observability/tracing.py:32-88](file://src/aiops_agent/observability/tracing.py#L32-L88)
- [src/aiops_agent/observability/metrics.py:26-106](file://src/aiops_agent/observability/metrics.py#L26-L106)
- [src/aiops_agent/security/permission_gate.py:57-101](file://src/aiops_agent/security/permission_gate.py#L57-L101)

**Section sources**
- [src/aiops_agent/main.py:70-222](file://src/aiops_agent/main.py#L70-L222)

## Performance Considerations
- Concurrency and Parallelism
  - Orchestrator executes tasks per DAG level with bounded concurrency to avoid overload.
  - ToolExecutor applies exponential backoff and timeouts to improve resilience.
- Observability-Driven Tuning
  - Metrics track task durations and statuses; tracing annotates spans with attributes for latency analysis.
- LLM Resilience
  - Provider Factory supports primary/fallback switching to mitigate provider outages.
- Input Sanitization and Security Checks
  - Early detection of injection attempts reduces downstream failures.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and diagnostics:
- Permission Denied
  - Symptom: Tool execution fails with denied result.
  - Action: Inspect PermissionGate logs and required permissions; verify Workload Identity and RAM policies.
- Tool Not Found
  - Symptom: “Tool not registered” error.
  - Action: Confirm MCPRegistry loaded tools and Skill’s tool dependencies; check ToolExecutor dispatch logic.
- Skill Unhealthy
  - Symptom: Repeated failures lead to skill marked unhealthy.
  - Action: Use SkillRegistry health APIs; inspect Orchestrator’s failure recording window and threshold.
- Observability
  - Use structured logs with trace/span IDs; review metrics and traces for bottlenecks.

**Section sources**
- [src/aiops_agent/tools/executor.py:169-202](file://src/aiops_agent/tools/executor.py#L169-L202)
- [src/aiops_agent/tools/mcp_registry.py:122-153](file://src/aiops_agent/tools/mcp_registry.py#L122-L153)
- [src/aiops_agent/core/orchestrator.py:571-596](file://src/aiops_agent/core/orchestrator.py#L571-L596)
- [src/aiops_agent/observability/logging.py:30-58](file://src/aiops_agent/observability/logging.py#L30-L58)
- [src/aiops_agent/observability/metrics.py:81-106](file://src/aiops_agent/observability/metrics.py#L81-L106)
- [src/aiops_agent/observability/tracing.py:98-137](file://src/aiops_agent/observability/tracing.py#L98-L137)

## Conclusion
The AIOps Agent employs a clean layered architecture:
- Presentation Layer focuses on HTTP and SSE.
- Business Logic Layer coordinates planning, routing, and execution via the Orchestrator pattern.
- Integration Layer abstracts tool execution and MCP connectivity.
- Infrastructure Layer ensures security and observability through cross-cutting modules.

Patterns such as Registry, Factory, Orchestrator, and health monitoring enable modularity, testability, and resilience. Dependency inversion and schema-driven contracts further strengthen maintainability and extensibility.