# Credential Management

<cite>
**Referenced Files in This Document**
- [credential_manager.py](file://src/aiops_agent/security/credential_manager.py)
- [identity.py](file://src/aiops_agent/security/identity.py)
- [schemas.py](file://src/aiops_agent/models/schemas.py)
- [executor.py](file://src/aiops_agent/tools/executor.py)
- [main.py](file://src/aiops_agent/main.py)
- [settings.yaml](file://config/settings.yaml)
- [security_rules.yaml](file://config/security_rules.yaml)
- [audit_logger.py](file://src/aiops_agent/security/audit_logger.py)
- [sanitizer.py](file://src/aiops_agent/security/sanitizer.py)
- [test_credential_manager.py](file://tests/test_credential_manager.py)
- [test_workload_identity.py](file://tests/test_workload_identity.py)
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
This document explains the AIOps Agent’s Credential Manager token vault functionality with a focus on secure storage and retrieval of sensitive credentials, including Alibaba Cloud STS temporary credentials, third-party OAuth tokens, and API keys. It covers the credential lifecycle, caching and automatic refresh, Workload Identity integration for dynamic credential provisioning, token rotation, and secure transmission. It also provides examples of credential registration/access patterns, cleanup procedures, and security best practices for integrating with external secret management systems.

## Project Structure
The credential management capability spans several modules:
- Security: credential manager and identity provider
- Models: shared schemas for credentials and scopes
- Tools: tool executor integrates permission checks, credential retrieval, and auditing
- Config: settings and security rules
- Tests: unit tests validating cache behavior, OIDC flows, and error conditions

```mermaid
graph TB
subgraph "Security"
CM["CredentialManager<br/>src/aiops_agent/security/credential_manager.py"]
WIM["WorkloadIdentityManager<br/>src/aiops_agent/security/identity.py"]
end
subgraph "Models"
SCH["Schemas<br/>src/aiops_agent/models/schemas.py"]
end
subgraph "Tools"
TE["ToolExecutor<br/>src/aiops_agent/tools/executor.py"]
end
subgraph "App Init"
MAIN["create_agent()<br/>src/aiops_agent/main.py"]
end
subgraph "Config"
SET["settings.yaml<br/>config/settings.yaml"]
SEC["security_rules.yaml<br/>config/security_rules.yaml"]
end
subgraph "Audit"
AUD["AuditLogger<br/>src/aiops_agent/security/audit_logger.py"]
SAN["Sanitizer<br/>src/aiops_agent/security/sanitizer.py"]
end
MAIN --> WIM
MAIN --> CM
TE --> CM
TE --> WIM
CM --> WIM
CM --> SCH
TE --> SCH
TE --> AUD
AUD --> SAN
SET --> MAIN
SEC --> AUD
```

**Diagram sources**
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)
- [executor.py:45-314](file://src/aiops_agent/tools/executor.py#L45-L314)
- [main.py:70-222](file://src/aiops_agent/main.py#L70-L222)
- [settings.yaml:27-42](file://config/settings.yaml#L27-L42)
- [security_rules.yaml:1-70](file://config/security_rules.yaml#L1-L70)
- [audit_logger.py:24-253](file://src/aiops_agent/security/audit_logger.py#L24-L253)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)

**Section sources**
- [credential_manager.py:1-261](file://src/aiops_agent/security/credential_manager.py#L1-L261)
- [identity.py:1-247](file://src/aiops_agent/security/identity.py#L1-L247)
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)
- [executor.py:1-314](file://src/aiops_agent/tools/executor.py#L1-L314)
- [main.py:70-222](file://src/aiops_agent/main.py#L70-L222)
- [settings.yaml:27-42](file://config/settings.yaml#L27-L42)
- [security_rules.yaml:1-70](file://config/security_rules.yaml#L1-L70)
- [audit_logger.py:1-253](file://src/aiops_agent/security/audit_logger.py#L1-L253)
- [sanitizer.py:1-80](file://src/aiops_agent/security/sanitizer.py#L1-L80)

## Core Components
- CredentialManager: central vault for Alibaba Cloud STS and third-party credentials; caches and refreshes before expiry; supports skill-scoped isolation; retries on failures.
- WorkloadIdentityManager: obtains Alibaba Cloud STS temporary credentials via OIDC AssumeRoleWithOIDC; manages auto-refresh tasks; validates current credential freshness.
- ToolExecutor: orchestrates permission checks, credential acquisition, tool execution, sanitization, and audit logging.
- Schemas: typed models for credential scopes, cached credentials, Alibaba Cloud credentials, and third-party credentials.
- AuditLogger + Sanitizer: sanitize sensitive fields in logs and events; write structured audit logs and optionally integrate with ActionTrail.

**Section sources**
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [executor.py:45-314](file://src/aiops_agent/tools/executor.py#L45-L314)
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)
- [audit_logger.py:24-253](file://src/aiops_agent/security/audit_logger.py#L24-L253)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)

## Architecture Overview
The credential lifecycle integrates Workload Identity for Alibaba Cloud and environment-based third-party credentials. ToolExecutor coordinates permission checks and injects credentials into tool calls. AuditLogger records sanitized events.

```mermaid
sequenceDiagram
participant Skill as "Skill/Tool Caller"
participant TE as "ToolExecutor"
participant PG as "PermissionGate"
participant CM as "CredentialManager"
participant WIM as "WorkloadIdentityManager"
participant STS as "Alibaba Cloud STS"
participant AUD as "AuditLogger"
Skill->>TE : execute(tool_name, args, skill_identity, credential_scope)
TE->>PG : check_permission(...)
PG-->>TE : PermissionCheckResult
alt credential_scope.target_service == "aliyun"
TE->>CM : get_aliyun_credential(scope, workload_identity_manager)
CM->>WIM : is_valid()/credential
alt valid
WIM-->>CM : AliyunCredential
else invalid
CM->>WIM : assume_role()
WIM->>STS : AssumeRoleWithOIDC
STS-->>WIM : Temporary Credentials
WIM-->>CM : AliyunCredential
WIM->>WIM : schedule auto-refresh
end
CM-->>TE : AliyunCredential
else third_party
TE->>CM : get_third_party_credential(scope)
CM-->>TE : ThirdPartyCredential (env)
end
TE->>AUD : log(AuditEvent with sanitized params)
TE-->>Skill : ToolResult
```

**Diagram sources**
- [executor.py:80-226](file://src/aiops_agent/tools/executor.py#L80-L226)
- [credential_manager.py:63-214](file://src/aiops_agent/security/credential_manager.py#L63-L214)
- [identity.py:119-173](file://src/aiops_agent/security/identity.py#L119-L173)
- [audit_logger.py:65-103](file://src/aiops_agent/security/audit_logger.py#L65-L103)

## Detailed Component Analysis

### CredentialManager
Responsibilities:
- Obtain Alibaba Cloud STS temporary credentials via WorkloadIdentityManager.
- Retrieve third-party credentials from environment variables.
- Cache credentials keyed by scope to support skill-level isolation.
- Refresh before expiry and retry on transient failures.

Key behaviors:
- Cache key composition includes target service, provider name, optional RAM Role ARN, and sorted scopes.
- Expiry-based refresh window defaults to five minutes before expiry.
- Exponential backoff retry for STS credential retrieval.

```mermaid
classDiagram
class CredentialManager {
-int _refresh_before
-dict _credential_cache
+get_aliyun_credential(scope, workload_identity_manager) AliyunCredential
+get_third_party_credential(scope) ThirdPartyCredential
-_get_from_workload_identity(manager, scope, max_retries) AliyunCredential
-_is_credential_valid(cached) bool
-_make_cache_key(scope) str
+clear_cache() void
+close() void
}
class WorkloadIdentityManager {
+assume_role(jwt_token, duration) AliyunCredential
+is_valid() bool
+close() void
}
class AliyunCredential {
+string access_key_id
+string access_key_secret
+string security_token
+datetime expires_at
}
class ThirdPartyCredential {
+string oauth_token
+string api_key
+datetime expires_at
+string[] scopes
}
CredentialManager --> WorkloadIdentityManager : "uses"
CredentialManager --> AliyunCredential : "returns"
CredentialManager --> ThirdPartyCredential : "returns"
```

**Diagram sources**
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [schemas.py:121-137](file://src/aiops_agent/models/schemas.py#L121-L137)

**Section sources**
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [test_credential_manager.py:25-86](file://tests/test_credential_manager.py#L25-L86)

### WorkloadIdentityManager
Responsibilities:
- Read Kubernetes ServiceAccount JWT from mounted path.
- Call Alibaba Cloud STS AssumeRoleWithOIDC to obtain temporary credentials.
- Schedule auto-refresh tasks before expiry.
- Support manual JWT override for non-Kubernetes environments.

```mermaid
flowchart TD
Start(["assume_role()"]) --> ReadJWT["Read or accept JWT"]
ReadJWT --> BuildReq["Build AssumeRoleWithOIDC request"]
BuildReq --> CallSTS["Call STS client"]
CallSTS --> ParseResp["Parse credentials and expiry"]
ParseResp --> SetCred["Set current credential"]
SetCred --> Schedule["Schedule auto-refresh task"]
Schedule --> End(["Return AliyunCredential"])
```

**Diagram sources**
- [identity.py:119-173](file://src/aiops_agent/security/identity.py#L119-L173)

**Section sources**
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [test_workload_identity.py:92-148](file://tests/test_workload_identity.py#L92-L148)

### ToolExecutor Integration
- Injects credentials into tool arguments under a reserved internal key, ensuring they are not exposed to tools.
- Executes tools with timeouts and exponential backoff retry.
- Logs sanitized audit events after completion.

```mermaid
sequenceDiagram
participant TE as "ToolExecutor"
participant CM as "CredentialManager"
participant WIM as "WorkloadIdentityManager"
participant Tool as "MCP/Local Tool"
participant AUD as "AuditLogger"
TE->>CM : get_aliyun_credential(...) or get_third_party_credential(...)
CM-->>TE : Credential
TE->>Tool : call_tool(..., _credential=<injected>)
Tool-->>TE : result
TE->>AUD : log(AuditEvent with sanitized args)
TE-->>Caller : ToolResult
```

**Diagram sources**
- [executor.py:135-226](file://src/aiops_agent/tools/executor.py#L135-L226)

**Section sources**
- [executor.py:45-314](file://src/aiops_agent/tools/executor.py#L45-L314)

### Schemas and Scope Isolation
- CredentialScope defines target_service ("aliyun" or "third_party"), provider name, optional RAM Role ARN, and scopes.
- CachedCredential stores either Alibaba Cloud or third-party credentials with expiry and refresh-before timestamps.
- AliyunCredential and ThirdPartyCredential encapsulate sensitive fields.

```mermaid
classDiagram
class CredentialScope {
+string target_service
+string credential_provider_name
+string ram_role_arn
+string[] scopes
}
class CachedCredential {
+CredentialScope credential_scope
+string access_key_id
+string access_key_secret
+string security_token
+string oauth_token
+string api_key
+datetime expires_at
+datetime refresh_before
}
class AliyunCredential {
+string access_key_id
+string access_key_secret
+string security_token
+datetime expires_at
}
class ThirdPartyCredential {
+string oauth_token
+string api_key
+datetime expires_at
+string[] scopes
}
```

**Diagram sources**
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)

**Section sources**
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)

### Audit and Sanitization
- AuditLogger writes structured JSONL logs locally and optionally integrates with ActionTrail.
- Sanitizer recursively redacts sensitive fields by matching field name patterns.
- ToolExecutor removes injected credentials from arguments before logging.

```mermaid
flowchart TD
Evt["AuditEvent(parameters)"] --> San["sanitize_parameters()"]
San --> Log["AuditLogger.log()"]
Log --> AT["ActionTrail write (optional)"]
Log --> Local["Local JSONL append"]
AT --> |Failure| Backup["Backup JSONL write"]
```

**Diagram sources**
- [audit_logger.py:65-103](file://src/aiops_agent/security/audit_logger.py#L65-L103)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)
- [executor.py:203-226](file://src/aiops_agent/tools/executor.py#L203-L226)

**Section sources**
- [audit_logger.py:24-253](file://src/aiops_agent/security/audit_logger.py#L24-L253)
- [sanitizer.py:1-80](file://src/aiops_agent/security/sanitizer.py#L1-L80)
- [executor.py:203-226](file://src/aiops_agent/tools/executor.py#L203-L226)

## Dependency Analysis
- CredentialManager depends on WorkloadIdentityManager for Alibaba Cloud STS and on environment variables for third-party credentials.
- ToolExecutor depends on CredentialManager and WorkloadIdentityManager to supply credentials to tools.
- AuditLogger depends on Sanitizer to redact sensitive fields prior to logging.
- Application initialization wires WorkloadIdentityManager and CredentialManager during agent creation.

```mermaid
graph LR
MAIN["main.py:create_agent"] --> WIM["WorkloadIdentityManager"]
MAIN --> CM["CredentialManager"]
TE["ToolExecutor"] --> CM
TE --> WIM
CM --> SCH["Schemas"]
TE --> AUD["AuditLogger"]
AUD --> SAN["Sanitizer"]
```

**Diagram sources**
- [main.py:70-222](file://src/aiops_agent/main.py#L70-L222)
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [executor.py:45-314](file://src/aiops_agent/tools/executor.py#L45-L314)
- [audit_logger.py:24-253](file://src/aiops_agent/security/audit_logger.py#L24-L253)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)

**Section sources**
- [main.py:70-222](file://src/aiops_agent/main.py#L70-L222)
- [credential_manager.py:38-261](file://src/aiops_agent/security/credential_manager.py#L38-L261)
- [identity.py:38-247](file://src/aiops_agent/security/identity.py#L38-L247)
- [executor.py:45-314](file://src/aiops_agent/tools/executor.py#L45-L314)
- [audit_logger.py:24-253](file://src/aiops_agent/security/audit_logger.py#L24-L253)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)
- [schemas.py:99-137](file://src/aiops_agent/models/schemas.py#L99-L137)

## Performance Considerations
- Caching reduces repeated STS calls and environment reads; refresh window prevents late expiry.
- Auto-refresh tasks avoid blocking calls by scheduling pre-expiry renewal.
- Exponential backoff limits retry load on transient failures.
- Asynchronous execution and thread-offloaded SDK calls minimize latency.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Missing WorkloadIdentityManager when fetching Alibaba Cloud credentials:
  - Symptom: CredentialError indicating WorkloadIdentityManager not provided.
  - Resolution: Ensure agent initialization sets role_arn and oidc_provider_arn and calls assume_role during startup.
- K8s ServiceAccount token missing:
  - Symptom: CredentialError stating token file does not exist.
  - Resolution: Verify pod mounting or pass explicit jwt_token; confirm environment variable ALIBABA_CLOUD_OIDC_TOKEN if needed.
- Third-party credential not found:
  - Symptom: CredentialError indicating provider credentials not found.
  - Resolution: Set environment variables using the provider name uppercase with suffixes _API_KEY or _OAUTH_TOKEN.
- Excessive retries or failures:
  - Symptom: Repeated warnings and eventual failure.
  - Resolution: Check IAM role trust policy, OIDC provider ARN, and network connectivity; verify retry configuration aligns with settings.

**Section sources**
- [credential_manager.py:97-157](file://src/aiops_agent/security/credential_manager.py#L97-L157)
- [identity.py:98-105](file://src/aiops_agent/security/identity.py#L98-L105)
- [test_credential_manager.py:144-154](file://tests/test_credential_manager.py#L144-L154)
- [test_workload_identity.py:54-61](file://tests/test_workload_identity.py#L54-L61)
- [settings.yaml:49-54](file://config/settings.yaml#L49-L54)

## Conclusion
The Credential Manager provides a robust, secure, and auditable mechanism for managing Alibaba Cloud STS and third-party credentials. It leverages Workload Identity for dynamic, short-lived credentials, enforces strict scope isolation, and integrates tightly with the tool execution pipeline and audit/logging subsystem. The design balances operational simplicity with strong security controls, including caching, pre-expiry refresh, and sensitive data sanitization.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Credential Lifecycle and Rotation
- Alibaba Cloud STS:
  - Initial acquisition via OIDC AssumeRoleWithOIDC.
  - Auto-refresh triggered before expiry to maintain uninterrupted operation.
- Third-party credentials:
  - Loaded from environment variables at runtime; cache refreshed based on expiry window.

**Section sources**
- [identity.py:179-213](file://src/aiops_agent/security/identity.py#L179-L213)
- [credential_manager.py:235-247](file://src/aiops_agent/security/credential_manager.py#L235-L247)

### Secure Transmission Protocols
- TLS enforcement for outbound communications is configurable and recommended.
- AuditLogger uses HTTPS with SSL for ActionTrail integration when configured.

**Section sources**
- [security_rules.yaml:66-70](file://config/security_rules.yaml#L66-L70)
- [audit_logger.py:167-185](file://src/aiops_agent/security/audit_logger.py#L167-L185)

### Examples: Registration, Access Patterns, and Cleanup
- Registration:
  - Configure agent_identity in settings.yaml with role_arn, oidc_provider_arn, and token_refresh_before_minutes.
  - Initialize WorkloadIdentityManager and CredentialManager in create_agent().
- Access patterns:
  - For Alibaba Cloud tools, pass a CredentialScope with target_service="aliyun".
  - For third-party tools, pass a CredentialScope with target_service="third_party" and appropriate scopes.
  - ToolExecutor injects credentials into tool arguments internally; tools receive them via a reserved key.
- Cleanup:
  - Clear credential cache programmatically or close managers to cancel auto-refresh tasks and reset state.

**Section sources**
- [settings.yaml:27-42](file://config/settings.yaml#L27-L42)
- [main.py:112-146](file://src/aiops_agent/main.py#L112-L146)
- [executor.py:135-147](file://src/aiops_agent/tools/executor.py#L135-L147)
- [credential_manager.py:249-261](file://src/aiops_agent/security/credential_manager.py#L249-L261)
- [identity.py:238-247](file://src/aiops_agent/security/identity.py#L238-L247)

### Security Best Practices and External Secret Management Integration
- Prefer Workload Identity for Alibaba Cloud to eliminate long-lived secrets.
- Store third-party credentials in environment variables or a managed secret store; avoid embedding in code.
- Enforce HTTPS/TLS 1.2+ for all external communications.
- Regularly rotate credentials and reduce scope privileges using RAM policies and fine-grained permissions.
- Integrate with external secret managers by adapting the third-party credential loading path to fetch from your platform.
- Keep audit logs enabled and monitor anomalies; configure alerts for ActionTrail write failures.

**Section sources**
- [security_rules.yaml:66-70](file://config/security_rules.yaml#L66-L70)
- [audit_logger.py:161-185](file://src/aiops_agent/security/audit_logger.py#L161-L185)
- [sanitizer.py:39-80](file://src/aiops_agent/security/sanitizer.py#L39-L80)