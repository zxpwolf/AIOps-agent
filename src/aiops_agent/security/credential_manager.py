"""CredentialManager — 凭证管理与作用域隔离.

职责:
- 从 WorkloadIdentityManager 获取 STS 临时凭证（阿里云服务）
- 管理第三方凭证（OAuth Token / API Key）
- 凭证缓存和自动刷新（过期前 5 分钟）
- 技能级凭证作用域隔离
- 指数退避重试策略
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiops_agent.core.exceptions import CredentialError
from aiops_agent.models.schemas import (
    AliyunCredential,
    CachedCredential,
    CredentialScope,
    ThirdPartyCredential,
)

# Forward reference for type hint
if False:  # pragma: no cover
    from aiops_agent.security.identity import WorkloadIdentityManager

logger = logging.getLogger(__name__)

# 重试配置
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # 秒
_MAX_DELAY = 30.0


class CredentialManager:
    """凭证管理器.

    通过 WorkloadIdentityManager 获取 STS 临时凭证，
    实现无密钥工作负载身份。

    特性:
    - STS 临时凭证获取（阿里云服务）
    - OAuth Token / API Key 获取（第三方应用）
    - 凭证缓存和自动刷新（过期前 5 分钟）
    - 技能级凭证作用域隔离
    - 指数退避重试策略（最多 3 次）
    """

    def __init__(
        self,
        token_refresh_before_minutes: int = 5,
    ) -> None:
        self._refresh_before = timedelta(minutes=token_refresh_before_minutes)
        self._credential_cache: dict[str, CachedCredential] = {}

    # ------------------------------------------------------------------
    # 阿里云凭证获取 — 委托给 WorkloadIdentityManager
    # ------------------------------------------------------------------

    async def get_aliyun_credential(
        self,
        scope: CredentialScope,
        workload_identity_manager: "WorkloadIdentityManager | None" = None,
    ) -> AliyunCredential:
        """获取阿里云服务的 STS 临时凭证.

        优先使用传入的 WorkloadIdentityManager 的当前凭证，
        如果未提供或凭证无效，则通过 assume_role 刷新。

        Args:
            scope: 凭证作用域（用于缓存键和审计）。
            workload_identity_manager: 工作负载身份管理器。

        Returns:
            AliyunCredential 临时凭证。

        Raises:
            CredentialError: 凭证获取失败时抛出。
        """
        cache_key = self._make_cache_key(scope)

        # 检查缓存
        cached = self._credential_cache.get(cache_key)
        if cached is not None and self._is_credential_valid(cached):
            logger.debug("使用缓存的阿里云凭证: %s", scope.credential_provider_name)
            return AliyunCredential(
                access_key_id=cached.access_key_id or "",
                access_key_secret=cached.access_key_secret or "",
                security_token=cached.security_token or "",
                expires_at=cached.expires_at,
            )

        # 从 WorkloadIdentityManager 获取
        if workload_identity_manager is None:
            raise CredentialError(
                message="未提供 WorkloadIdentityManager，无法获取阿里云凭证",
                credential_scope=scope.credential_provider_name,
                suggestion="在 create_agent() 中传入 WorkloadIdentityManager 实例。",
            )

        credential = await self._get_from_workload_identity(
            workload_identity_manager, scope
        )

        refresh_before = credential.expires_at - self._refresh_before

        # 更新缓存
        self._credential_cache[cache_key] = CachedCredential(
            credential_scope=scope,
            access_key_id=credential.access_key_id,
            access_key_secret=credential.access_key_secret,
            security_token=credential.security_token,
            expires_at=credential.expires_at,
            refresh_before=refresh_before,
        )

        logger.info("阿里云 STS 凭证获取成功: %s", scope.credential_provider_name)
        return credential

    async def _get_from_workload_identity(
        self,
        manager: "WorkloadIdentityManager",
        scope: CredentialScope,
        max_retries: int = _MAX_RETRIES,
    ) -> AliyunCredential:
        """从 WorkloadIdentityManager 获取凭证，含指数退避重试."""
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                # 如果已有有效凭证，直接返回
                if manager.is_valid() and manager.credential is not None:
                    return manager.credential

                # 否则重新 AssumeRole
                return await manager.assume_role()

            except Exception as exc:
                last_error = exc
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "STS 凭证获取失败 (尝试 %d/%d)，%0.1f 秒后重试: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        raise CredentialError(
            message=f"获取 STS 凭证失败（已重试 {max_retries} 次）: {last_error}",
            credential_scope=scope.credential_provider_name,
            suggestion="请检查 RAM 角色、OIDC 身份提供商配置和 K8s ServiceAccount Token。",
        )

    # ------------------------------------------------------------------
    # 第三方凭证获取
    # ------------------------------------------------------------------

    async def get_third_party_credential(
        self,
        scope: CredentialScope,
    ) -> ThirdPartyCredential:
        """获取第三方应用的 OAuth Token 或 API Key.

        目前从环境变量或配置获取，后续可接入外部密钥管理。

        Args:
            scope: 凭证作用域。

        Returns:
            ThirdPartyCredential 凭证。

        Raises:
            CredentialError: 凭证获取失败时抛出。
        """
        cache_key = self._make_cache_key(scope)

        # 检查缓存
        cached = self._credential_cache.get(cache_key)
        if cached is not None and self._is_credential_valid(cached):
            logger.debug("使用缓存的第三方凭证: %s", scope.credential_provider_name)
            return ThirdPartyCredential(
                oauth_token=cached.oauth_token,
                api_key=cached.api_key,
                expires_at=cached.expires_at,
                scopes=scope.scopes,
            )

        # 从环境变量获取（按 scope 名称）
        credential = self._load_from_env(scope)

        if credential is None:
            raise CredentialError(
                message=f"未找到第三方凭证: {scope.credential_provider_name}",
                credential_scope=scope.credential_provider_name,
                suggestion=f"请设置环境变量 {scope.credential_provider_name.upper()}_API_KEY 或 _OAUTH_TOKEN",
            )

        refresh_before = credential.expires_at - self._refresh_before if credential.expires_at else datetime.now(timezone.utc)

        self._credential_cache[cache_key] = CachedCredential(
            credential_scope=scope,
            oauth_token=credential.oauth_token,
            api_key=credential.api_key,
            expires_at=credential.expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
            refresh_before=refresh_before,
        )

        logger.info("第三方凭证获取成功: %s", scope.credential_provider_name)
        return credential

    def _load_from_env(self, scope: CredentialScope) -> ThirdPartyCredential | None:
        """从环境变量加载第三方凭证."""
        prefix = scope.credential_provider_name.upper()
        api_key = __import__("os").environ.get(f"{prefix}_API_KEY")
        oauth_token = __import__("os").environ.get(f"{prefix}_OAUTH_TOKEN")

        if not api_key and not oauth_token:
            return None

        return ThirdPartyCredential(
            oauth_token=oauth_token,
            api_key=api_key,
            scopes=scope.scopes,
        )

    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------

    def _is_credential_valid(self, cached: CachedCredential) -> bool:
        """检查缓存凭证是否仍然有效（未到刷新时间）."""
        return datetime.now(timezone.utc) < cached.refresh_before

    @staticmethod
    def _make_cache_key(scope: CredentialScope) -> str:
        """生成凭证缓存键，实现技能级作用域隔离."""
        parts = [scope.target_service, scope.credential_provider_name]
        if scope.ram_role_arn:
            parts.append(scope.ram_role_arn)
        if scope.scopes:
            parts.append(",".join(sorted(scope.scopes)))
        return "|".join(parts)

    def clear_cache(self) -> None:
        """清除所有缓存的凭证."""
        self._credential_cache.clear()
        logger.info("凭证缓存已清除")

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭管理器，清理资源."""
        self._credential_cache.clear()
