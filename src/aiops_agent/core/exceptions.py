"""自定义异常层次结构.

定义 Agent 运行时的异常类型，包含错误码和建议操作，
用于向用户返回结构化错误响应。
"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 基础异常.

    所有 Agent 特定异常的基类，携带错误码和建议操作信息，
    便于 Orchestrator 生成结构化错误响应。
    """

    def __init__(
        self,
        message: str,
        error_code: str,
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.suggestion = suggestion

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"error_code={self.error_code!r}, "
            f"suggestion={self.suggestion!r})"
        )


class SkillExecutionError(AgentError):
    """技能执行过程中发生的错误.

    当 Skill 的 execute 方法抛出异常或返回失败结果时使用。
    """

    def __init__(
        self,
        message: str,
        skill_name: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(
            message=message,
            error_code="SKILL_EXECUTION_ERROR",
            suggestion=suggestion,
        )
        self.skill_name = skill_name


class PermissionDeniedError(AgentError):
    """权限校验失败时抛出的异常.

    当 Permission_Gate 拒绝操作时使用，携带所需权限和当前权限信息。
    """

    def __init__(
        self,
        message: str,
        required_permission: str = "",
        current_permissions: list[str] | None = None,
        suggestion: str = "",
    ) -> None:
        super().__init__(
            message=message,
            error_code="PERMISSION_DENIED",
            suggestion=suggestion or "请联系管理员获取所需权限，或使用具有足够权限的身份重试。",
        )
        self.required_permission = required_permission
        self.current_permissions = current_permissions or []


class CredentialError(AgentError):
    """凭证获取或验证失败时抛出的异常.

    当 Credential_Manager 无法从 Token Vault 或 Credential Provider
    获取有效凭证时使用。
    """

    def __init__(
        self,
        message: str,
        credential_scope: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(
            message=message,
            error_code="CREDENTIAL_ERROR",
            suggestion=suggestion or "请检查 Agent Identity 配置和凭证提供商状态。",
        )
        self.credential_scope = credential_scope


class TimeoutError(AgentError):
    """操作超时异常.

    当工具调用或技能执行超过配置的超时时间时使用。
    不覆盖内置 TimeoutError，仅在 Agent 上下文中使用。
    """

    def __init__(
        self,
        message: str,
        timeout_seconds: float = 0.0,
        operation: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(
            message=message,
            error_code="TIMEOUT_ERROR",
            suggestion=suggestion or "请稍后重试，或增加超时配置。",
        )
        self.timeout_seconds = timeout_seconds
        self.operation = operation


class SkillNotFoundError(AgentError):
    """技能未找到异常.

    当 Orchestrator 无法将请求映射到任何已注册的 Skill 时使用。
    """

    def __init__(
        self,
        message: str,
        requested_capability: str = "",
        available_skills: list[str] | None = None,
        suggestion: str = "",
    ) -> None:
        super().__init__(
            message=message,
            error_code="SKILL_NOT_FOUND",
            suggestion=suggestion or "请检查可用技能列表，或注册新的技能模块。",
        )
        self.requested_capability = requested_capability
        self.available_skills = available_skills or []
