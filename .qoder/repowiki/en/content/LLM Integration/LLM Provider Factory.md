# LLM Provider Factory

<cite>
**Referenced Files in This Document**
- [provider.py](file://src/aiops_agent/llm/provider.py)
- [gpt.py](file://src/aiops_agent/llm/gpt.py)
- [claude.py](file://src/aiops_agent/llm/claude.py)
- [qwen.py](file://src/aiops_agent/llm/qwen.py)
- [demo.py](file://src/aiops_agent/llm/demo.py)
- [main.py](file://src/aiops_agent/main.py)
- [settings.yaml](file://config/settings.yaml)
- [test_llm_provider.py](file://tests/test_llm_provider.py)
- [test_gpt_provider.py](file://tests/test_gpt_provider.py)
- [test_claude_provider.py](file://tests/test_claude_provider.py)
- [test_qwen_provider.py](file://tests/test_qwen_provider.py)
- [test_demo_provider.py](file://tests/test_demo_provider.py)
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
10. [Appendices](#appendices)

## Introduction
This document explains the LLM Provider Factory pattern implementation used to dynamically register multiple LLM backends, switch between primary and fallback providers, and enable automatic failover across chat, completion, and streaming interfaces. It documents factory initialization, provider registration, configuration management, lifecycle management, error handling, and best practices for provider selection and performance.

## Project Structure
The LLM subsystem resides under src/aiops_agent/llm and includes:
- An abstract provider interface and a concrete factory
- Multiple provider implementations (Qwen, Claude, GPT, Demo)
- Application bootstrap wiring that initializes the factory and sets up providers
- Configuration-driven provider selection via settings.yaml
- Comprehensive unit tests validating registration, failover, and lifecycle behavior

```mermaid
graph TB
subgraph "LLM Layer"
PIF["LLMProvider (abstract)"]
F["LLMProviderFactory"]
Q["QwenProvider"]
C["ClaudeProvider"]
G["GPTProvider"]
D["DemoProvider"]
end
subgraph "App Bootstrap"
M["main.create_agent()"]
CFG["settings.yaml"]
end
M --> F
F --> D
F --> Q
F --> C
F --> G
CFG -. reads .-> M
```

**Diagram sources**
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)
- [qwen.py:30-205](file://src/aiops_agent/llm/qwen.py#L30-L205)
- [claude.py:20-118](file://src/aiops_agent/llm/claude.py#L20-L118)
- [gpt.py:20-128](file://src/aiops_agent/llm/gpt.py#L20-L128)
- [demo.py:40-144](file://src/aiops_agent/llm/demo.py#L40-L144)
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)
- [settings.yaml:4-26](file://config/settings.yaml#L4-L26)

**Section sources**
- [provider.py:1-242](file://src/aiops_agent/llm/provider.py#L1-L242)
- [main.py:170-222](file://src/aiops_agent/main.py#L170-L222)
- [settings.yaml:4-26](file://config/settings.yaml#L4-L26)

## Core Components
- LLMProvider: Abstract base defining chat, complete, embed, and optional streaming interfaces. Includes a default streaming fallback and a close hook for resource cleanup.
- LLMProviderFactory: Central factory managing provider registration, primary/fallback selection, and automatic failover across chat, complete, and streaming calls. Provides lifecycle management via close.

Key responsibilities:
- Registration: register(name, provider) stores providers by name
- Selection: set_primary(name), set_fallback(name), get_provider(name), primary property
- Failover: chat(), complete(), chat_stream() attempt primary first, then fallback if configured and distinct from primary
- Lifecycle: close() invokes close() on all registered providers

**Section sources**
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)

## Architecture Overview
The factory pattern decouples client code from specific provider implementations while enabling runtime configuration and failover. The application initializes the factory, registers providers, and optionally sets primary and fallback based on environment and configuration.

```mermaid
sequenceDiagram
participant App as "Application"
participant Factory as "LLMProviderFactory"
participant Prim as "Primary Provider"
participant Fallback as "Fallback Provider"
App->>Factory : register("demo", DemoProvider)
App->>Factory : register("qwen", QwenProvider)
App->>Factory : set_primary("qwen")
App->>Factory : set_fallback("demo")
App->>Factory : chat(messages)
alt Primary succeeds
Factory->>Prim : chat(messages)
Prim-->>Factory : ChatResponse
Factory-->>App : ChatResponse
else Primary fails
Factory->>Fallback : chat(messages)
Fallback-->>Factory : ChatResponse
Factory-->>App : ChatResponse
end
```

**Diagram sources**
- [provider.py:147-175](file://src/aiops_agent/llm/provider.py#L147-L175)
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)

**Section sources**
- [provider.py:147-233](file://src/aiops_agent/llm/provider.py#L147-L233)
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)

## Detailed Component Analysis

### LLMProvider and Streaming Behavior
- Defines the contract for chat, complete, embed, and optional streaming.
- Default chat_stream yields the entire content as a single chunk; providers may override for true streaming.
- close is a no-op hook intended to be overridden by providers to release resources.

```mermaid
classDiagram
class LLMProvider {
+provider_name : str
+chat(messages, **kwargs) ChatResponse
+complete(prompt, **kwargs) str
+embed(texts, **kwargs) list[]float~~
+chat_stream(messages, **kwargs) AsyncIterator~str~
+close() void
}
```

**Diagram sources**
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

**Section sources**
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

### LLMProviderFactory
- Maintains an internal registry of providers and selected primary/fallback names.
- Exposes registration and selection APIs with validation.
- Implements automatic failover for chat, complete, and streaming calls.
- Ensures graceful resource cleanup via close.

```mermaid
classDiagram
class LLMProviderFactory {
-_providers : dict~str, LLMProvider~
-_primary_name : str?
-_fallback_name : str?
+register(name, provider) void
+set_primary(name) void
+set_fallback(name) void
+get_provider(name) LLMProvider
+primary LLMProvider
+chat(messages, **kwargs) ChatResponse
+chat_stream(messages, **kwargs) AsyncIterator~str~
+complete(prompt, **kwargs) str
+close() void
}
```

**Diagram sources**
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)

**Section sources**
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)

### Provider Implementations

#### QwenProvider
- Implements chat, streaming chat, complete, and embed against DashScope-compatible endpoints.
- Supports optional reasoning content in responses and streaming via SSE-like chunks.
- Manages an aiohttp session lazily and closes it on demand.

```mermaid
classDiagram
class QwenProvider {
+provider_name : str
+chat(messages, **kwargs) ChatResponse
+chat_stream(messages, **kwargs) AsyncIterator~str~
+complete(prompt, **kwargs) str
+embed(texts, **kwargs) list[]float~~
+close() void
}
QwenProvider --|> LLMProvider
```

**Diagram sources**
- [qwen.py:30-205](file://src/aiops_agent/llm/qwen.py#L30-L205)
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

**Section sources**
- [qwen.py:30-205](file://src/aiops_agent/llm/qwen.py#L30-L205)

#### ClaudeProvider
- Implements chat, complete, and embed against Anthropic Messages API.
- Handles system messages and concatenates text content blocks.
- Embedding is not natively supported and raises NotImplementedError.

```mermaid
classDiagram
class ClaudeProvider {
+provider_name : str
+chat(messages, **kwargs) ChatResponse
+complete(prompt, **kwargs) str
+embed(texts, **kwargs) list[]float~~
+close() void
}
ClaudeProvider --|> LLMProvider
```

**Diagram sources**
- [claude.py:20-118](file://src/aiops_agent/llm/claude.py#L20-L118)
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

**Section sources**
- [claude.py:20-118](file://src/aiops_agent/llm/claude.py#L20-L118)

#### GPTProvider
- Implements chat, complete, and embed against OpenAI-compatible endpoints.
- Uses a lazily created aiohttp session and supports configurable timeouts.

```mermaid
classDiagram
class GPTProvider {
+provider_name : str
+chat(messages, **kwargs) ChatResponse
+complete(prompt, **kwargs) str
+embed(texts, **kwargs) list[]float~~
+close() void
}
GPTProvider --|> LLMProvider
```

**Diagram sources**
- [gpt.py:20-128](file://src/aiops_agent/llm/gpt.py#L20-L128)
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

**Section sources**
- [gpt.py:20-128](file://src/aiops_agent/llm/gpt.py#L20-L128)

#### DemoProvider
- Non-production provider for development and demos.
- Provides deterministic task decomposition and streaming simulation.

```mermaid
classDiagram
class DemoProvider {
+provider_name : str
+chat(messages, **kwargs) ChatResponse
+chat_stream(messages, **kwargs) AsyncIterator~str~
+complete(prompt, **kwargs) str
+embed(texts, **kwargs) list[]float~~
}
DemoProvider --|> LLMProvider
```

**Diagram sources**
- [demo.py:40-144](file://src/aiops_agent/llm/demo.py#L40-L144)
- [provider.py:31-96](file://src/aiops_agent/llm/provider.py#L31-L96)

**Section sources**
- [demo.py:40-144](file://src/aiops_agent/llm/demo.py#L40-L144)

### Automatic Failover Logic
The factory attempts the primary provider first; if it fails, it tries the fallback provider (if configured and different from primary). If both fail, it raises a runtime error indicating all providers are unavailable.

```mermaid
flowchart TD
Start(["Call chat()/complete()/chat_stream()"]) --> CheckPrimary["Primary configured?"]
CheckPrimary --> |No| TryFallback["Fallback configured and != Primary?"]
CheckPrimary --> |Yes| TryPrimary["Call Primary"]
TryPrimary --> PrimaryOK{"Primary succeeded?"}
PrimaryOK --> |Yes| ReturnPrimary["Return result"]
PrimaryOK --> |No| LogWarn["Log warning"] --> TryFallback
TryFallback --> |No| RaiseErr["Raise 'all providers unavailable'"]
TryFallback --> |Yes| TryFallbackCall["Call Fallback"]
TryFallbackCall --> FallbackOK{"Fallback succeeded?"}
FallbackOK --> |Yes| ReturnFallback["Return result"]
FallbackOK --> |No| LogErr["Log error"] --> RaiseErr
```

**Diagram sources**
- [provider.py:147-233](file://src/aiops_agent/llm/provider.py#L147-L233)

**Section sources**
- [provider.py:147-233](file://src/aiops_agent/llm/provider.py#L147-L233)

### Factory Initialization and Configuration Management
- The application constructs an LLMProviderFactory and registers providers.
- It registers a DemoProvider by default and sets it as primary.
- If a Qwen API key is present, it registers Qwen and sets it as primary and Demo as fallback.
- Configuration in settings.yaml defines provider models, endpoints, and timeouts; the application reads environment variables and applies them during provider construction.

```mermaid
sequenceDiagram
participant Main as "main.create_agent()"
participant Factory as "LLMProviderFactory"
participant Demo as "DemoProvider"
participant Qwen as "QwenProvider"
Main->>Factory : new LLMProviderFactory()
Main->>Factory : register("demo", Demo)
Main->>Factory : set_primary("demo")
alt QWEN_API_KEY present
Main->>Factory : register("qwen", Qwen)
Main->>Factory : set_primary("qwen")
Main->>Factory : set_fallback("demo")
end
```

**Diagram sources**
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)
- [settings.yaml:4-26](file://config/settings.yaml#L4-L26)

**Section sources**
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)
- [settings.yaml:4-26](file://config/settings.yaml#L4-L26)

## Dependency Analysis
- LLMProviderFactory depends on LLMProvider instances and orchestrates calls to them.
- Providers depend on aiohttp for HTTP transport and on shared tracing decorators for observability.
- The application wires the factory into the orchestrator and manages provider lifecycle alongside other components.

```mermaid
graph LR
Factory["LLMProviderFactory"] --> |calls| ProviderA["LLMProvider impls"]
Factory --> |calls| ProviderB["LLMProvider impls"]
Factory --> |calls| ProviderC["LLMProvider impls"]
ProviderA --> HTTP["aiohttp.ClientSession"]
ProviderB --> HTTP
ProviderC --> HTTP
```

**Diagram sources**
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)
- [qwen.py:124-204](file://src/aiops_agent/llm/qwen.py#L124-L204)
- [claude.py:114-118](file://src/aiops_agent/llm/claude.py#L114-L118)
- [gpt.py:124-128](file://src/aiops_agent/llm/gpt.py#L124-L128)

**Section sources**
- [provider.py:97-242](file://src/aiops_agent/llm/provider.py#L97-L242)
- [qwen.py:124-204](file://src/aiops_agent/llm/qwen.py#L124-L204)
- [claude.py:114-118](file://src/aiops_agent/llm/claude.py#L114-L118)
- [gpt.py:124-128](file://src/aiops_agent/llm/gpt.py#L124-L128)

## Performance Considerations
- Prefer streaming APIs where supported (QwenProvider) to reduce latency and improve responsiveness.
- Configure timeouts per provider to avoid long blocking calls; the providers already accept timeout_seconds.
- Reuse sessions when possible; providers lazily create and reuse aiohttp sessions.
- Monitor usage fields returned by providers to track token consumption and optimize prompts.
- Keep primary provider reliable and fast; use fallback for resilience rather than performance.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and strategies:
- All providers unavailable: The factory raises a runtime error when both primary and fallback fail. Verify provider registration and credentials.
- Primary provider errors: The factory logs warnings and attempts fallback. Inspect logs for detailed error messages.
- Fallback provider errors: The factory logs errors and raises a runtime error if both fail.
- Provider lifecycle: Call factory.close() to release resources; failures in individual provider.close() are suppressed and logged.

Validation and behavior are covered by tests:
- Registration and retrieval semantics
- Primary/fallback setting and property access
- Automatic failover for chat and complete
- Stream fallback behavior
- Lifecycle close semantics

**Section sources**
- [test_llm_provider.py:18-254](file://tests/test_llm_provider.py#L18-L254)
- [provider.py:147-242](file://src/aiops_agent/llm/provider.py#L147-L242)

## Conclusion
The LLM Provider Factory pattern cleanly separates concerns, supports dynamic registration and selection, and provides robust automatic failover across chat, completion, and streaming. Combined with configuration-driven setup and comprehensive testing, it offers a flexible, observable, and resilient foundation for multi-provider LLM integration.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Practical Usage Examples
- Initialize factory and register providers
- Set primary and fallback
- Invoke chat, complete, or chat_stream
- Close factory to release resources

These steps are demonstrated in the application bootstrap and tests.

**Section sources**
- [main.py:176-192](file://src/aiops_agent/main.py#L176-L192)
- [test_llm_provider.py:88-145](file://tests/test_llm_provider.py#L88-L145)

### Provider Selection Best Practices
- Choose a primary provider with low latency and high availability for typical workloads.
- Select a fallback provider with similar capabilities for resilience.
- Use environment variables and configuration files to manage provider credentials and endpoints.
- Monitor usage and latency metrics to inform provider selection decisions.

**Section sources**
- [settings.yaml:4-26](file://config/settings.yaml#L4-L26)
- [main.py:184-192](file://src/aiops_agent/main.py#L184-L192)