"""WorkloadIdentityManager 单元测试 — 阿里云 STS AssumeRoleWithOIDC."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiops_agent.core.exceptions import CredentialError
from aiops_agent.security.identity import WorkloadIdentityManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_sts_response(
    access_key_id: str = "STS.Nxxxxx123",
    access_key_secret: str = "secret123",
    security_token: str = "token123",
    expiration: str = "2030-01-01T12:00:00Z",
):
    """构造 STS AssumeRoleWithOIDC 的 mock 响应."""
    resp = MagicMock()
    resp.body = MagicMock()
    resp.body.credentials = MagicMock()
    resp.body.credentials.access_key_id = access_key_id
    resp.body.credentials.access_key_secret = access_key_secret
    resp.body.credentials.security_token = security_token
    resp.body.credentials.expiration = expiration
    return resp


# ---------------------------------------------------------------------------
# Test: K8s Token 读取
# ---------------------------------------------------------------------------


class TestReadK8sToken:
    def test_read_token_from_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt-token\n")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
        )
        assert mgr._read_k8s_token() == "test-jwt-token"

    def test_read_token_not_found(self) -> None:
        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path="/nonexistent/path/token",
        )
        with pytest.raises(CredentialError, match="K8s ServiceAccount Token 不存在"):
            mgr._read_k8s_token()

    @pytest.mark.asyncio
    async def test_get_jwt_token_from_param(self, tmp_path: Path) -> None:
        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path="/nonexistent/token",
        )
        token = await mgr._get_jwt_token(jwt_token="explicit-jwt")
        assert token == "explicit-jwt"

    @pytest.mark.asyncio
    async def test_get_jwt_token_from_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("file-jwt-token")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
        )
        token = await mgr._get_jwt_token()
        assert token == "file-jwt-token"


# ---------------------------------------------------------------------------
# Test: AssumeRole
# ---------------------------------------------------------------------------


class TestAssumeRole:
    @pytest.mark.asyncio
    async def test_assume_role_success(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123456:role/aiops-role",
            oidc_provider_arn="acs:ram::123456:oidc-provider/aiops-provider",
            region="cn-hangzhou",
            k8s_token_path=str(token_file),
        )

        mock_resp = _make_mock_sts_response()
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            cred = await mgr.assume_role()

        assert cred.access_key_id == "STS.Nxxxxx123"
        assert cred.security_token == "token123"
        assert mgr.is_valid() is True

    @pytest.mark.asyncio
    async def test_assume_role_uses_explicit_jwt(self, tmp_path: Path) -> None:
        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path="/nonexistent/token",
        )

        mock_resp = _make_mock_sts_response()
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            cred = await mgr.assume_role(jwt_token="explicit-jwt")

        assert cred.access_key_id == "STS.Nxxxxx123"
        # 验证调用了 STS client
        mock_client.return_value.assume_role_with_oidc.assert_called_once()
        call_args = mock_client.return_value.assume_role_with_oidc.call_args
        assert call_args[0][0].oidctoken == "explicit-jwt"

    @pytest.mark.asyncio
    async def test_assume_role_failure_raises_credential_error(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
        )

        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.side_effect = Exception("STS error")
            with pytest.raises(CredentialError, match="STS AssumeRoleWithOIDC 失败"):
                await mgr.assume_role()


# ---------------------------------------------------------------------------
# Test: Auto Refresh
# ---------------------------------------------------------------------------


class TestAutoRefresh:
    @pytest.mark.asyncio
    async def test_refresh_loop_starts(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
            token_refresh_before_minutes=5,
        )

        mock_resp = _make_mock_sts_response(
            expiration="2030-01-01T12:00:00Z",
        )
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            await mgr.assume_role()

        # 自动刷新任务已启动
        assert mgr._refresh_task is not None
        assert not mgr._refresh_task.done()

        # 清理
        await mgr.close()

    @pytest.mark.asyncio
    async def test_close_cancels_refresh_task(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
        )

        mock_resp = _make_mock_sts_response()
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            await mgr.assume_role()

        await mgr.close()

        assert mgr._refresh_task.done()
        assert mgr._credential is None


# ---------------------------------------------------------------------------
# Test: is_valid
# ---------------------------------------------------------------------------


class TestIsValid:
    def test_not_valid_when_no_credential(self) -> None:
        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
        )
        assert mgr.is_valid() is False

    @pytest.mark.asyncio
    async def test_valid_when_not_expired(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
            token_refresh_before_minutes=5,
        )

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_resp = _make_mock_sts_response(expiration=future)
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            await mgr.assume_role()

        assert mgr.is_valid() is True
        await mgr.close()

    @pytest.mark.asyncio
    async def test_not_valid_when_near_expiry(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("test-jwt")

        mgr = WorkloadIdentityManager(
            role_arn="acs:ram::123:role/test",
            oidc_provider_arn="acs:ram::123:oidc-provider/test",
            k8s_token_path=str(token_file),
            token_refresh_before_minutes=10,  # 刷新窗口 10 分钟
        )

        # 设置过期时间只比当前时间多 3 分钟（小于刷新窗口）
        near_expiry = (datetime.now(timezone.utc) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_resp = _make_mock_sts_response(expiration=near_expiry)
        with patch.object(mgr, "_get_sts_client") as mock_client:
            mock_client.return_value.assume_role_with_oidc.return_value = mock_resp
            await mgr.assume_role()

        assert mgr.is_valid() is False
        await mgr.close()
