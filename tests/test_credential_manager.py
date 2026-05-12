"""CredentialManager 单元测试."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.core.exceptions import CredentialError
from aiops_agent.models.schemas import (
    AliyunCredential,
    CachedCredential,
    CredentialScope,
    ThirdPartyCredential,
)
from aiops_agent.security.credential_manager import CredentialManager


# ---------------------------------------------------------------------------
# Test: Cache management
# ---------------------------------------------------------------------------


class TestCredentialCache:
    def test_is_credential_valid(self, credential_manager: CredentialManager) -> None:
        now = datetime.now(timezone.utc)
        valid = CachedCredential(
            credential_scope=CredentialScope(
                target_service="aliyun",
                credential_provider_name="test",
            ),
            expires_at=now + timedelta(hours=1),
            refresh_before=now + timedelta(minutes=10),
        )
        assert credential_manager._is_credential_valid(valid) is True

    def test_is_credential_expired(self, credential_manager: CredentialManager) -> None:
        now = datetime.now(timezone.utc)
        expired = CachedCredential(
            credential_scope=CredentialScope(
                target_service="aliyun",
                credential_provider_name="test",
            ),
            expires_at=now + timedelta(hours=1),
            refresh_before=now - timedelta(minutes=1),  # already past refresh time
        )
        assert credential_manager._is_credential_valid(expired) is False

    def test_make_cache_key_basic(self) -> None:
        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="provider1",
        )
        key = CredentialManager._make_cache_key(scope)
        assert "aliyun" in key
        assert "provider1" in key

    def test_make_cache_key_with_ram_role(self) -> None:
        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="provider1",
            ram_role_arn="acs:ram::123:role/test",
        )
        key1 = CredentialManager._make_cache_key(scope)
        assert "acs:ram::123:role/test" in key1

    def test_make_cache_key_with_scopes(self) -> None:
        scope = CredentialScope(
            target_service="third_party",
            credential_provider_name="oauth",
            scopes=["read", "write"],
        )
        key = CredentialManager._make_cache_key(scope)
        assert "read,write" in key or "write,read" in key  # sorted

    def test_clear_cache(self, credential_manager: CredentialManager) -> None:
        now = datetime.now(timezone.utc)
        credential_manager._credential_cache["key1"] = CachedCredential(
            credential_scope=CredentialScope(target_service="aliyun", credential_provider_name="p"),
            expires_at=now + timedelta(hours=1),
            refresh_before=now + timedelta(minutes=30),
        )
        credential_manager.clear_cache()
        assert len(credential_manager._credential_cache) == 0


# ---------------------------------------------------------------------------
# Test: get_aliyun_credential — cache hit
# ---------------------------------------------------------------------------


class TestGetAliyunCredential:
    @pytest.mark.asyncio
    async def test_cache_hit(self, credential_manager: CredentialManager) -> None:
        now = datetime.now(timezone.utc)
        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="vault1",
            ram_role_arn="acs:ram::123:role/test",
        )
        cached = CachedCredential(
            credential_scope=scope,
            access_key_id="cached-ak",
            access_key_secret="cached-sk",
            security_token="cached-st",
            expires_at=now + timedelta(hours=1),
            refresh_before=now + timedelta(minutes=30),
        )
        credential_manager._credential_cache[CredentialManager._make_cache_key(scope)] = cached

        result = await credential_manager.get_aliyun_credential(scope)
        assert result.access_key_id == "cached-ak"
        assert result.access_key_secret == "cached-sk"

    @pytest.mark.asyncio
    async def test_cache_miss_uses_workload_identity(
        self, credential_manager: CredentialManager, workload_identity_manager
    ) -> None:
        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="vault1",
            ram_role_arn="acs:ram::123:role/test",
        )

        now = datetime.now(timezone.utc)
        mock_cred = AliyunCredential(
            access_key_id="new-ak",
            access_key_secret="new-sk",
            security_token="new-st",
            expires_at=now + timedelta(hours=1),
        )

        workload_identity_manager.is_valid = MagicMock(return_value=True)
        object.__setattr__(workload_identity_manager, "_credential", mock_cred)

        result = await credential_manager.get_aliyun_credential(
            scope, workload_identity_manager=workload_identity_manager
        )

        assert result.access_key_id == "new-ak"
        assert result.security_token == "new-st"

    @pytest.mark.asyncio
    async def test_raises_without_workload_identity_manager(
        self, credential_manager: CredentialManager
    ) -> None:
        scope = CredentialScope(
            target_service="aliyun",
            credential_provider_name="vault1",
        )
        # No cached credential + no manager → should raise
        with pytest.raises(CredentialError, match="未提供 WorkloadIdentityManager"):
            await credential_manager.get_aliyun_credential(scope)


# ---------------------------------------------------------------------------
# Test: get_third_party_credential
# ---------------------------------------------------------------------------


class TestGetThirdPartyCredential:
    @pytest.mark.asyncio
    async def test_cache_hit(self, credential_manager: CredentialManager) -> None:
        now = datetime.now(timezone.utc)
        scope = CredentialScope(
            target_service="third_party",
            credential_provider_name="oauth_provider",
            scopes=["read"],
        )
        cached = CachedCredential(
            credential_scope=scope,
            oauth_token="token123",
            expires_at=now + timedelta(hours=1),
            refresh_before=now + timedelta(minutes=30),
        )
        credential_manager._credential_cache[CredentialManager._make_cache_key(scope)] = cached

        result = await credential_manager.get_third_party_credential(scope)
        assert result.oauth_token == "token123"

    @pytest.mark.asyncio
    async def test_from_env_api_key(self, credential_manager: CredentialManager) -> None:
        import os
        scope = CredentialScope(
            target_service="third_party",
            credential_provider_name="test_service",
            scopes=["read"],
        )
        os.environ["TEST_SERVICE_API_KEY"] = "env-api-key-123"
        try:
            result = await credential_manager.get_third_party_credential(scope)
            assert result.api_key == "env-api-key-123"
        finally:
            del os.environ["TEST_SERVICE_API_KEY"]

    @pytest.mark.asyncio
    async def test_raises_when_not_found(self, credential_manager: CredentialManager) -> None:
        scope = CredentialScope(
            target_service="third_party",
            credential_provider_name="nonexistent_provider",
        )
        with pytest.raises(CredentialError, match="未找到第三方凭证"):
            await credential_manager.get_third_party_credential(scope)
