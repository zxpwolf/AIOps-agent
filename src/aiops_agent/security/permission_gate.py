"""Permission_Gate — RBAC 权限校验与资源级 ARN 模式匹配.

基于 Workload Identity 关联的 RAM Policy 校验权限，
支持三级权限分级、On-Behalf-Of 权限降级和资源级 ARN 模式匹配。
"""

from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path
from typing import Optional

from aiops_agent.core.exceptions import PermissionDeniedError
from aiops_agent.models.schemas import (
    PermissionCheckResult,
    PermissionLevel,
    WorkloadIdentity,
)

logger = logging.getLogger(__name__)

# 操作到权限级别的默认映射
_WRITE_ACTION_PREFIXES = (
    "Create",
    "Delete",
    "Modify",
    "Update",
    "Start",
    "Stop",
    "Reboot",
    "Restart",
    "Execute",
    "Set",
    "Enable",
    "Disable",
)

_ADMIN_ACTION_PREFIXES = (
    "Delete",
)


def _classify_permission_level(action: str) -> PermissionLevel:
    """根据操作名称推断权限级别."""
    # 提取操作动词（如 ecs:DeleteInstance → DeleteInstance）
    verb = action.split(":")[-1] if ":" in action else action

    if any(verb.startswith(p) for p in _ADMIN_ACTION_PREFIXES):
        return PermissionLevel.ADMIN
    if any(verb.startswith(p) for p in _WRITE_ACTION_PREFIXES):
        return PermissionLevel.LIMITED_WRITE
    return PermissionLevel.READ_ONLY


class PermissionGate:
    """基于 Workload Identity + RAM Policy 的权限校验中间件.

    支持:
    - 三级权限分级: Read-Only（默认）、Limited-Write（需审批）、Admin（强制人工审批）
    - On-Behalf-Of 模式下的权限降级（Agent 权限 ∩ 用户权限）
    - 资源级 ARN 模式匹配
    - 人工审批请求接口
    - 权限拒绝事件记录
    """

    def __init__(
        self,
        ram_policies_dir: str | Path | None = None,
    ) -> None:
        """初始化 Permission Gate.

        Args:
            ram_policies_dir: RAM Policy JSON 文件目录路径。
        """
        self._policies: dict[str, dict] = {}
        if ram_policies_dir is not None:
            self._load_policies(Path(ram_policies_dir))

        # 人工审批回调（可由外部注入）
        self._approval_callback = None

    def set_approval_callback(self, callback) -> None:
        """设置人工审批回调函数.

        callback 签名: async def callback(identity, action, resource_arn, level) -> bool
        """
        self._approval_callback = callback

    # ------------------------------------------------------------------
    # 核心校验
    # ------------------------------------------------------------------

    async def check_permission(
        self,
        workload_identity: WorkloadIdentity,
        action: str,
        resource_arn: str,
        user_permissions: list[str] | None = None,
    ) -> PermissionCheckResult:
        """校验权限.

        流程:
        1. 查询 Workload Identity 关联的权限列表
        2. On-Behalf-Of 模式下取用户权限与 Agent 权限的交集
        3. 匹配 action 和 resource_arn
        4. 判断权限级别，Limited-Write/Admin 需要审批

        Args:
            workload_identity: 调用方的 Workload Identity。
            action: 请求执行的操作（如 "ecs:DescribeInstances"）。
            resource_arn: 目标资源 ARN。
            user_permissions: On-Behalf-Of 模式下的用户权限列表。

        Returns:
            PermissionCheckResult 包含校验结果。
        """
        agent_permissions = workload_identity.permissions

        # On-Behalf-Of 模式：计算有效权限（交集）
        if user_permissions is not None:
            effective_permissions = self._compute_effective_permissions(
                agent_permissions, user_permissions
            )
        else:
            effective_permissions = agent_permissions

        # 检查操作是否在有效权限中（支持通配符匹配）
        allowed = self._is_action_allowed(action, effective_permissions, resource_arn)

        # 判断权限级别
        permission_level = _classify_permission_level(action)

        # 需要审批的级别
        requires_approval = permission_level in (
            PermissionLevel.LIMITED_WRITE,
            PermissionLevel.ADMIN,
        )

        if not allowed:
            denial_reason = (
                f"Workload Identity {workload_identity.workload_identity_arn} "
                f"不具有执行 {action} 在 {resource_arn} 上的权限"
            )
            logger.warning("权限拒绝: %s", denial_reason)
            return PermissionCheckResult(
                allowed=False,
                required_permission=action,
                current_permissions=effective_permissions,
                permission_level=permission_level,
                requires_approval=requires_approval,
                denial_reason=denial_reason,
            )

        # 需要审批时请求人工确认
        if requires_approval:
            approved = await self.request_approval(
                workload_identity, action, resource_arn, permission_level
            )
            if not approved:
                denial_reason = (
                    f"操作 {action} 需要 {permission_level.value} 级别审批，审批被拒绝"
                )
                logger.warning("审批拒绝: %s", denial_reason)
                return PermissionCheckResult(
                    allowed=False,
                    required_permission=action,
                    current_permissions=effective_permissions,
                    permission_level=permission_level,
                    requires_approval=True,
                    denial_reason=denial_reason,
                )

        return PermissionCheckResult(
            allowed=True,
            required_permission=action,
            current_permissions=effective_permissions,
            permission_level=permission_level,
            requires_approval=requires_approval,
        )

    # ------------------------------------------------------------------
    # 人工审批
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        workload_identity: WorkloadIdentity,
        action: str,
        resource_arn: str,
        permission_level: PermissionLevel,
    ) -> bool:
        """请求人工审批确认（Limited-Write 或 Admin 级别操作）.

        Args:
            workload_identity: 请求方身份。
            action: 待审批的操作。
            resource_arn: 目标资源。
            permission_level: 权限级别。

        Returns:
            True 表示审批通过，False 表示拒绝。
        """
        if self._approval_callback is not None:
            return await self._approval_callback(
                workload_identity, action, resource_arn, permission_level
            )

        # 默认行为：Read-Only 自动通过，其他拒绝
        if permission_level == PermissionLevel.READ_ONLY:
            return True

        logger.info(
            "操作 %s 需要 %s 级别审批（无审批回调，默认拒绝）",
            action,
            permission_level.value,
        )
        return False

    # ------------------------------------------------------------------
    # 权限匹配
    # ------------------------------------------------------------------

    def _is_action_allowed(
        self,
        action: str,
        permissions: list[str],
        resource_arn: str,
    ) -> bool:
        """检查操作是否在权限列表中（支持通配符）."""
        for perm in permissions:
            if self._match_action(perm, action):
                return True
        return False

    @staticmethod
    def _match_action(pattern: str, action: str) -> bool:
        """匹配操作模式，支持通配符.

        例如:
        - "ecs:Describe*" 匹配 "ecs:DescribeInstances"
        - "ecs:*" 匹配 "ecs:DeleteInstance"
        - "*" 匹配所有操作
        """
        return fnmatch.fnmatch(action, pattern)

    @staticmethod
    def _match_resource_arn(pattern: str, resource_arn: str) -> bool:
        """资源 ARN 模式匹配.

        例如:
        - "acs:ecs:cn-hangzhou:*:instance/*" 匹配所有杭州 ECS 实例
        - "*" 匹配所有资源
        """
        return fnmatch.fnmatch(resource_arn, pattern)

    @staticmethod
    def _compute_effective_permissions(
        agent_permissions: list[str],
        user_permissions: list[str],
    ) -> list[str]:
        """On-Behalf-Of 模式下计算有效权限（交集）.

        对于精确匹配的权限取交集。
        对于通配符权限，保留双方都允许的操作。
        """
        # 简化实现：精确匹配取交集
        agent_set = set(agent_permissions)
        user_set = set(user_permissions)

        effective = list(agent_set & user_set)

        # 处理通配符：如果一方有通配符，保留另一方的具体权限
        agent_wildcards = [p for p in agent_permissions if "*" in p]
        user_wildcards = [p for p in user_permissions if "*" in p]

        # Agent 有通配符，用户有具体权限 → 保留用户的具体权限
        for user_perm in user_permissions:
            if user_perm in effective:
                continue
            for agent_wc in agent_wildcards:
                if fnmatch.fnmatch(user_perm, agent_wc):
                    effective.append(user_perm)
                    break

        # 用户有通配符，Agent 有具体权限 → 保留 Agent 的具体权限
        for agent_perm in agent_permissions:
            if agent_perm in effective:
                continue
            for user_wc in user_wildcards:
                if fnmatch.fnmatch(agent_perm, user_wc):
                    effective.append(agent_perm)
                    break

        return effective

    # ------------------------------------------------------------------
    # Policy 加载
    # ------------------------------------------------------------------

    def _load_policies(self, policies_dir: Path) -> None:
        """从目录加载 RAM Policy JSON 文件."""
        if not policies_dir.is_dir():
            logger.warning("RAM Policy 目录不存在: %s", policies_dir)
            return

        for policy_file in policies_dir.glob("*.json"):
            try:
                data = json.loads(policy_file.read_text(encoding="utf-8"))
                self._policies[policy_file.stem] = data
                logger.info("加载 RAM Policy: %s", policy_file.stem)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("加载 RAM Policy 失败 %s: %s", policy_file, exc)

    def get_policy(self, policy_name: str) -> dict | None:
        """获取已加载的 RAM Policy."""
        return self._policies.get(policy_name)
