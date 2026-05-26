# Context Management

<cite>
**Referenced Files in This Document**
- [manager.py](file://src/aiops_agent/context/manager.py)
- [memory.py](file://src/aiops_agent/context/memory.py)
- [session.py](file://src/aiops_agent/context/session.py)
- [resource_resolver.py](file://src/aiops_agent/context/resource_resolver.py)
- [schemas.py](file://src/aiops_agent/models/schemas.py)
- [main.py](file://src/aiops_agent/main.py)
- [test_context_manager.py](file://tests/test_context_manager.py)
- [test_memory.py](file://tests/test_memory.py)
- [test_session.py](file://tests/test_session.py)
- [test_resource_resolver.py](file://tests/test_resource_resolver.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Security Considerations](#security-considerations)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Conclusion](#conclusion)

## Introduction
This document describes the Context Management system responsible for multi-turn conversations, session lifecycle management, message history tracking, interaction mode switching between CHAT and TASK modes, and resource resolution for cloud resources. It explains how the ContextManager integrates SessionStore, MemoryLayer, and ResourceResolver to provide persistent and stateful interactions, and outlines patterns for context updates, session handling, and resource resolution workflows.

## Project Structure
The Context Management module resides under src/aiops_agent/context and includes:
- manager.py: Orchestration of session, memory, and resource resolution
- session.py: SessionStore for session lifecycle and persistence
- memory.py: MemoryLayer for short-term and long-term memory
- resource_resolver.py: ResourceResolver for parsing cloud resource references
- schemas.py: Shared data models including SessionState, Message, ResourceReference, TaskProgress, and InteractionMode

```mermaid
graph TB
subgraph "Context Module"
CM["ContextManager<br/>manager.py"]
SS["SessionStore<br/>session.py"]
ML["MemoryLayer<br/>memory.py"]
RR["ResourceResolver<br/>resource_resolver.py"]
end
subgraph "Models"
MS["SessionState<br/>schemas.py"]
MSG["Message<br/>schemas.py"]
RR_MODEL["ResourceReference<br/>schemas.py"]
TP["TaskProgress<br/>schemas.py"]
IM["InteractionMode<br/>schemas.py"]
end
CM --> SS
CM --> ML
CM --> RR
SS --> MS
ML --> MSG
RR --> RR_MODEL
CM --> IM
CM --> TP
```

**Diagram sources**
- [manager.py:1-193](file://src/aiops_agent/context/manager.py#L1-L193)
- [session.py:19-131](file://src/aiops_agent/context/session.py#L19-L131)
- [memory.py:20-149](file://src/aiops_agent/context/memory.py#L20-L149)
- [resource_resolver.py:33-81](file://src/aiops_agent/context/resource_resolver.py#L33-L81)
- [schemas.py:264-276](file://src/aiops_agent/models/schemas.py#L264-L276)
- [schemas.py:64-71](file://src/aiops_agent/models/schemas.py#L64-L71)
- [schemas.py:246-253](file://src/aiops_agent/models/schemas.py#L246-L253)
- [schemas.py:255-262](file://src/aiops_agent/models/schemas.py#L255-L262)
- [schemas.py:238-244](file://src/aiops_agent/models/schemas.py#L238-L244)

**Section sources**
- [manager.py:1-193](file://src/aiops_agent/context/manager.py#L1-L193)
- [session.py:19-131](file://src/aiops_agent/context/session.py#L19-L131)
- [memory.py:20-149](file://src/aiops_agent/context/memory.py#L20-L149)
- [resource_resolver.py:33-81](file://src/aiops_agent/context/resource_resolver.py#L33-L81)
- [schemas.py:264-276](file://src/aiops_agent/models/schemas.py#L264-L276)
- [schemas.py:64-71](file://src/aiops_agent/models/schemas.py#L64-L71)
- [schemas.py:246-253](file://src/aiops_agent/models/schemas.py#L246-L253)
- [schemas.py:255-262](file://src/aiops_agent/models/schemas.py#L255-L262)
- [schemas.py:238-244](file://src/aiops_agent/models/schemas.py#L238-L244)

## Core Components
- ContextManager: Central coordinator integrating SessionStore, MemoryLayer, and ResourceResolver. Provides session retrieval, context updates, mode switching, task progress tracking, and persistence controls.
- SessionStore: Manages session creation, retrieval, persistence to disk, restoration, and idle eviction.
- MemoryLayer: Implements short-term memory (in-memory per session) and long-term memory (persistent JSON files) with basic keyword-based search.
- ResourceResolver: Parses cloud resource identifiers from text and produces ResourceReference objects.

Key responsibilities:
- Multi-turn conversation: ContextManager appends messages, resolves resources, and stores short-term memory.
- Session lifecycle: SessionStore manages creation, persistence, restoration, and idle timeout eviction.
- Interaction modes: ContextManager switches between CHAT, TASK, and WATCH modes while preserving context.
- Task progress: TASK mode initializes and tracks progress; supports pause and cancel.
- Resource resolution: ResourceResolver identifies ECS, RDS, VPC, SLB, EIP, security groups, disks, snapshots, images, and OSS URIs.

**Section sources**
- [manager.py:25-193](file://src/aiops_agent/context/manager.py#L25-L193)
- [session.py:19-131](file://src/aiops_agent/context/session.py#L19-L131)
- [memory.py:20-149](file://src/aiops_agent/context/memory.py#L20-L149)
- [resource_resolver.py:33-81](file://src/aiops_agent/context/resource_resolver.py#L33-L81)

## Architecture Overview
The ContextManager acts as the central orchestrator, delegating to SessionStore for session state, MemoryLayer for memory, and ResourceResolver for resource identification.

```mermaid
sequenceDiagram
participant Client as "Client"
participant CM as "ContextManager"
participant SS as "SessionStore"
participant RR as "ResourceResolver"
participant ML as "MemoryLayer"
Client->>CM : "update_context(session_id, message)"
CM->>SS : "get(session_id)"
SS-->>CM : "SessionState or None"
alt "Session exists"
CM->>SS : "Append message to session.messages"
CM->>RR : "resolve(message.content)"
RR-->>CM : "List[ResourceReference]"
CM->>SS : "Store references in session.resources"
CM->>ML : "store_short_term(session_id, {role, content})"
CM-->>Client : "Context updated"
else "Session not found"
CM-->>Client : "Warning logged"
end
```

**Diagram sources**
- [manager.py:58-88](file://src/aiops_agent/context/manager.py#L58-L88)
- [session.py:53-65](file://src/aiops_agent/context/session.py#L53-L65)
- [resource_resolver.py:44-71](file://src/aiops_agent/context/resource_resolver.py#L44-L71)
- [memory.py:46-59](file://src/aiops_agent/context/memory.py#L46-L59)

**Section sources**
- [manager.py:58-88](file://src/aiops_agent/context/manager.py#L58-L88)
- [session.py:53-65](file://src/aiops_agent/context/session.py#L53-L65)
- [resource_resolver.py:44-71](file://src/aiops_agent/context/resource_resolver.py#L44-L71)
- [memory.py:46-59](file://src/aiops_agent/context/memory.py#L46-L59)

## Detailed Component Analysis

### ContextManager
Responsibilities:
- Session management: get_session delegates to SessionStore.get_or_create.
- Context updates: append Message to session.history, resolve resource references, and store short-term memory.
- Interaction mode switching: switch_mode transitions between CHAT, TASK, WATCH, initializing/clearing TaskProgress accordingly.
- Task progress: update_task_progress, pause_task, cancel_task.
- Persistence: persist_session and check_idle_sessions delegate to SessionStore.

```mermaid
classDiagram
class ContextManager {
-SessionStore _session_store
-MemoryLayer _memory
-ResourceResolver _resolver
+get_session(session_id, user_id) SessionState
+update_context(session_id, message) void
+switch_mode(session_id, mode) void
+update_task_progress(session_id, percentage, current_step, total_steps, completed_steps) void
+pause_task(session_id) void
+cancel_task(session_id) void
+persist_session(session_id) void
+check_idle_sessions() str[]
+memory MemoryLayer
+resolver ResourceResolver
}
```

**Diagram sources**
- [manager.py:25-193](file://src/aiops_agent/context/manager.py#L25-L193)

**Section sources**
- [manager.py:25-193](file://src/aiops_agent/context/manager.py#L25-L193)

### SessionStore
Responsibilities:
- Create sessions with initial state (mode CHAT, timestamps, TTL).
- Retrieve sessions from memory or restore from persisted JSON.
- Persist sessions to JSON files and remove them from memory.
- Detect idle sessions beyond TTL and persist them automatically.

```mermaid
flowchart TD
Start(["Get or Create"]) --> Get["get(session_id)"]
Get --> Found{"Found in memory?"}
Found --> |Yes| Touch["Update last_active_at"] --> Return["Return SessionState"]
Found --> |No| Restore["Try restore from file"] --> PutBack["Put back in memory"] --> Touch --> Return
Return --> End(["Done"])
IdleStart(["check_idle_sessions"]) --> Iterate["Iterate sessions"]
Iterate --> IdleCheck{"last_active_at + ttl < now?"}
IdleCheck --> |Yes| Persist["persist(session_id)"] --> Evict["remove from memory"] --> Collect["collect id"] --> Iterate
IdleCheck --> |No| Iterate
Iterate --> Done(["Return idle ids"])
```

**Diagram sources**
- [session.py:53-115](file://src/aiops_agent/context/session.py#L53-L115)
- [session.py:98-115](file://src/aiops_agent/context/session.py#L98-L115)

**Section sources**
- [session.py:19-131](file://src/aiops_agent/context/session.py#L19-L131)

### MemoryLayer
Responsibilities:
- Short-term memory: Store and retrieve per-session arrays of message-like dictionaries.
- Long-term memory: Persist structured cases to JSON files with timestamps and load them into an in-memory index.
- Keyword-based search: Simple scoring over title/description/tags for long-term retrieval.

```mermaid
classDiagram
class MemoryLayer {
-dict~str,dict[]~ _short_term
-Path _long_term_dir
-dict[] _long_term_index
+store_short_term(session_id, data) void
+get_short_term(session_id) dict[]
+clear_short_term(session_id) void
+store_long_term(case) void
+search_long_term(query, top_k) dict[]
-_load_long_term_index() void
}
```

**Diagram sources**
- [memory.py:20-149](file://src/aiops_agent/context/memory.py#L20-L149)

**Section sources**
- [memory.py:20-149](file://src/aiops_agent/context/memory.py#L20-L149)

### ResourceResolver
Responsibilities:
- Parse text for cloud resource identifiers using predefined regex patterns.
- Produce ResourceReference objects with resource_type, resource_id, and default region.
- Deduplicate matches and support adding custom patterns.

```mermaid
flowchart TD
In["Input text"] --> Patterns["Iterate regex patterns"]
Patterns --> Match{"Match found?"}
Match --> |Yes| BuildRef["Build ResourceReference with default region"]
BuildRef --> Seen{"Already seen?"}
Seen --> |No| Add["Add to results and seen set"]
Seen --> |Yes| Patterns
Match --> |No| Patterns
Patterns --> Done["Return list of ResourceReference"]
```

**Diagram sources**
- [resource_resolver.py:44-71](file://src/aiops_agent/context/resource_resolver.py#L44-L71)

**Section sources**
- [resource_resolver.py:33-81](file://src/aiops_agent/context/resource_resolver.py#L33-L81)

### Data Models
Shared models used across the context system:
- SessionState: Holds session_id, user_id, mode, messages, resources, task_progress, timestamps, and TTL.
- Message: role, content, timestamp, metadata.
- ResourceReference: resource_type, resource_id, region, display_name.
- TaskProgress: percentage, current_step, total_steps, completed_steps.
- InteractionMode: CHAT, TASK, WATCH.

```mermaid
erDiagram
SESSION_STATE {
string session_id PK
string user_id
enum mode
datetime created_at
datetime last_active_at
int ttl_minutes
}
MESSAGE {
string role
string content
datetime timestamp
}
RESOURCE_REFERENCE {
string resource_type
string resource_id
string region
string display_name
}
TASK_PROGRESS {
float percentage
string current_step
int total_steps
int completed_steps
}
SESSION_STATE ||--o{ MESSAGE : "messages"
SESSION_STATE ||--o{ RESOURCE_REFERENCE : "resources"
SESSION_STATE ||--o| TASK_PROGRESS : "task_progress"
```

**Diagram sources**
- [schemas.py:264-276](file://src/aiops_agent/models/schemas.py#L264-L276)
- [schemas.py:64-71](file://src/aiops_agent/models/schemas.py#L64-L71)
- [schemas.py:246-253](file://src/aiops_agent/models/schemas.py#L246-L253)
- [schemas.py:255-262](file://src/aiops_agent/models/schemas.py#L255-L262)
- [schemas.py:238-244](file://src/aiops_agent/models/schemas.py#L238-L244)

**Section sources**
- [schemas.py:264-276](file://src/aiops_agent/models/schemas.py#L264-L276)
- [schemas.py:64-71](file://src/aiops_agent/models/schemas.py#L64-L71)
- [schemas.py:246-253](file://src/aiops_agent/models/schemas.py#L246-L253)
- [schemas.py:255-262](file://src/aiops_agent/models/schemas.py#L255-L262)
- [schemas.py:238-244](file://src/aiops_agent/models/schemas.py#L238-L244)

## Dependency Analysis
ContextManager depends on SessionStore, MemoryLayer, and ResourceResolver. SessionStore persists to JSON files; MemoryLayer persists long-term cases to JSON; ResourceResolver parses text into ResourceReference objects.

```mermaid
graph LR
CM["ContextManager"] --> SS["SessionStore"]
CM --> ML["MemoryLayer"]
CM --> RR["ResourceResolver"]
SS --> FS["Filesystem JSON"]
ML --> FS
RR --> PATTERNS["Regex patterns"]
```

**Diagram sources**
- [manager.py:12-14](file://src/aiops_agent/context/manager.py#L12-L14)
- [session.py:74-96](file://src/aiops_agent/context/session.py#L74-L96)
- [memory.py:75-91](file://src/aiops_agent/context/memory.py#L75-L91)
- [resource_resolver.py:18-30](file://src/aiops_agent/context/resource_resolver.py#L18-L30)

**Section sources**
- [manager.py:12-14](file://src/aiops_agent/context/manager.py#L12-L14)
- [session.py:74-96](file://src/aiops_agent/context/session.py#L74-L96)
- [memory.py:75-91](file://src/aiops_agent/context/memory.py#L75-L91)
- [resource_resolver.py:18-30](file://src/aiops_agent/context/resource_resolver.py#L18-L30)

## Performance Considerations
- Short-term memory is in-memory and O(1) append/get/clear per session; scale with concurrent sessions.
- Long-term memory search is O(N) over the index with simple scoring; consider vector database integration for production.
- File I/O for persistence and restoration is synchronous; consider async file operations for high throughput.
- Regex scanning is linear in input length; tune patterns and consider pre-tokenization for large messages.

[No sources needed since this section provides general guidance]

## Security Considerations
- Context storage: Sessions and long-term memory are stored as JSON files. Ensure filesystem permissions restrict access to sensitive data.
- Resource references: ResourceReference includes region and resource_id; avoid embedding secrets in content parsed by ResourceResolver.
- Privacy: Messages may contain sensitive operational data; consider sanitization before persistence and limit retention to TTL.
- Access control: Combine ContextManager usage with permission gates and audit logging to enforce least privilege and track actions.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and diagnostics:
- Session not found when updating context: ContextManager logs a warning and returns early. Verify session_id correctness and that SessionStore.get_or_create was invoked.
- Persistence failures: SessionStore and MemoryLayer catch OS errors and log exceptions; check filesystem permissions and disk availability.
- Idle sessions evicted unexpectedly: Confirm TTL settings and last_active_at updates; use check_idle_sessions to proactively persist.
- Resource resolution misses: Validate regex patterns and ensure default region alignment; add custom patterns via add_pattern.

**Section sources**
- [manager.py:66-68](file://src/aiops_agent/context/manager.py#L66-L68)
- [session.py:88-89](file://src/aiops_agent/context/session.py#L88-L89)
- [memory.py:85-87](file://src/aiops_agent/context/memory.py#L85-L87)
- [session.py:98-115](file://src/aiops_agent/context/session.py#L98-L115)

## Conclusion
The Context Management system provides robust multi-turn conversation handling with integrated session lifecycle, memory persistence, and cloud resource resolution. ContextManager coordinates SessionStore, MemoryLayer, and ResourceResolver to support CHAT and TASK modes, enabling task progress tracking and safe, auditable interactions. For production deployments, consider enhancing long-term memory search with vector databases, securing file-based persistence, and applying sanitization and retention policies.