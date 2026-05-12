"""WorkloadIdentityManager — 阿里云 RAM OIDC 工作负载身份管理.

通过 Kubernetes ServiceAccount JWT + STS AssumeRoleWithOIDC
获取阿里云临时 STS 凭证，实现无密钥工作负载身份。

流程:
1. 读取 Pod 内挂载的 K8s ServiceAccount JWT
2. 调用 STS AssumeRoleWithOIDC 换取临时凭证
3. 自动刷新（过期前 5 分钟）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from alibabacloud_sts20150401.client import Client as StsClient
from alibabacloud_sts20150401 import models as sts_models
from alibabacloud_tea_openapi.models import Config as OpenApiConfig

from aiops_agent.core.exceptions import CredentialError
from aiops_agent.models.schemas import AliyunCredential

logger = logging.getLogger(__name__)

# K8s ServiceAccount Token 默认路径
_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

# 阿里云 STS 端点
_STS_ENDPOINT = "sts.aliyuncs.com"


class WorkloadIdentityManager:
    """管理阿里云工作负载身份（OIDC 联合）.

    职责:
    - 读取 K8s ServiceAccount JWT 令牌
    - 通过 STS AssumeRoleWithOIDC 获取临时 STS 凭证
    - 自动刷新凭证（过期前 5 分钟）
    - 支持手动指定 JWT（用于非 K8s 环境调试）

    前置配置（一次性 IAM 设置，非运行时）:
    1. 在 RAM 创建 OIDC 身份提供商
    2. 创建 RAM 角色，信任策略指向 OIDC 提供商
    3. 为角色附加所需权限策略
    """

    def __init__(
        self,
        role_arn: str,
        oidc_provider_arn: str,
        region: str = "cn-hangzhou",
        session_name: str = "aiops-agent",
        token_refresh_before_minutes: int = 5,
        k8s_token_path: str = _K8S_SA_TOKEN_PATH,
    ) -> None:
        self._role_arn = role_arn
        self._oidc_provider_arn = oidc_provider_arn
        self._region = region
        self._session_name = session_name
        self._refresh_before = timedelta(minutes=token_refresh_before_minutes)
        self._k8s_token_path = k8s_token_path

        # 凭证状态
        self._credential: Optional[AliyunCredential] = None
        self._refresh_task: Optional[asyncio.Task[None]] = None
        self._sts_client: Optional[StsClient] = None

    # ------------------------------------------------------------------
    # STS 客户端
    # ------------------------------------------------------------------

    def _get_sts_client(self) -> StsClient:
        """获取或创建 STS SDK 客户端（无需 AK/SK，使用匿名调用）."""
        if self._sts_client is None:
            config = OpenApiConfig(
                endpoint=_STS_ENDPOINT,
                region_id=self._region,
            )
            self._sts_client = StsClient(config)
        return self._sts_client

    # ------------------------------------------------------------------
    # JWT 读取
    # ------------------------------------------------------------------

    def _read_k8s_token(self) -> str:
        """读取 Kubernetes ServiceAccount JWT 令牌.

        Raises:
            CredentialError: 无法读取 Token 文件时抛出。
        """
        path = Path(self._k8s_token_path)
        if not path.exists():
            raise CredentialError(
                message=f"K8s ServiceAccount Token 不存在: {path}",
                credential_scope="workload_identity",
                suggestion="确认运行在 K8s 环境中，或手动传入 jwt_token 参数。",
            )
        return path.read_text().strip()

    async def _get_jwt_token(self, jwt_token: str | None = None) -> str:
        """获取 JWT 令牌：优先使用传入参数，否则从 K8s 读取."""
        if jwt_token:
            return jwt_token
        # 支持异步读取（避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._read_k8s_token)

    # ------------------------------------------------------------------
    # STS AssumeRoleWithOIDC
    # ------------------------------------------------------------------

    async def assume_role(self, jwt_token: str | None = None, duration: int = 3600) -> AliyunCredential:
        """通过 OIDC 联合 AssumeRole 获取 STS 临时凭证.

        Args:
            jwt_token: K8s ServiceAccount JWT，None 时自动从 Pod 挂载路径读取。
            duration: 凭证有效期（秒），默认 3600。

        Returns:
            AliyunCredential 临时凭证。

        Raises:
            CredentialError: AssumeRole 失败时抛出。
        """
        token = await self._get_jwt_token(jwt_token)
        client = self._get_sts_client()

        request = sts_models.AssumeRoleWithOIDCRequest(
            role_arn=self._role_arn,
            oidcprovider_arn=self._oidc_provider_arn,
            oidctoken=token,
            role_session_name=self._session_name,
            duration_seconds=duration,
        )

        try:
            response = await asyncio.to_thread(
                client.assume_role_with_oidc, request
            )
        except Exception as exc:
            raise CredentialError(
                message=f"STS AssumeRoleWithOIDC 失败: {exc}",
                credential_scope="workload_identity",
                suggestion="检查 Role ARN、OIDC Provider ARN 和 JWT 是否正确。",
            ) from exc

        creds = response.body.credentials
        expires_at = datetime.fromisoformat(creds.expiration.replace("Z", "+00:00"))

        self._credential = AliyunCredential(
            access_key_id=creds.access_key_id,
            access_key_secret=creds.access_key_secret,
            security_token=creds.security_token,
            expires_at=expires_at,
        )

        logger.info(
            "STS 临时凭证获取成功: %s, 有效期至 %s",
            self._credential.access_key_id[:12] + "...",
            expires_at.isoformat(),
        )

        # 启动自动刷新
        self._start_auto_refresh(token, duration)

        return self._credential

    # ------------------------------------------------------------------
    # 自动刷新
    # ------------------------------------------------------------------

    def _start_auto_refresh(self, jwt_token: str, duration: int = 3600) -> None:
        """启动凭证自动刷新后台任务."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(
            self._auto_refresh_loop(jwt_token, duration),
            name="sts-credential-refresh",
        )

    async def _auto_refresh_loop(self, jwt_token: str, duration: int) -> None:
        """后台循环，在凭证过期前 5 分钟自动刷新."""
        while True:
            try:
                if self._credential is None:
                    break

                refresh_at = self._credential.expires_at - self._refresh_before
                now = datetime.now(timezone.utc)
                wait_seconds = max((refresh_at - now).total_seconds(), 0)

                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

                logger.info("自动刷新 STS 临时凭证...")
                await self.assume_role(jwt_token=jwt_token, duration=duration)
                # assume_role 会重新启动 refresh task，退出当前循环
                break

            except asyncio.CancelledError:
                logger.debug("凭证自动刷新任务已取消")
                break
            except Exception:
                logger.exception("STS 凭证自动刷新失败，30 秒后重试")
                await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def credential(self) -> Optional[AliyunCredential]:
        """当前有效的 STS 临时凭证."""
        return self._credential

    @property
    def role_arn(self) -> str:
        """RAM 角色 ARN."""
        return self._role_arn

    def is_valid(self) -> bool:
        """检查当前凭证是否仍然有效（未到刷新时间）."""
        if self._credential is None:
            return False
        return datetime.now(timezone.utc) < (self._credential.expires_at - self._refresh_before)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭管理器，清理后台任务."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._credential = None
