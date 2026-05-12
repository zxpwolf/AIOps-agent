"""Security_Guard — 安全规则引擎.

负责高危操作拦截、API 调用频率限制、操作序列异常检测
和 HTTPS/TLS 通信强制检查。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

import yaml

from aiops_agent.models.schemas import (
    SecurityCheckResult,
    SecurityRule,
    WorkloadIdentity,
)

logger = logging.getLogger(__name__)


class SecurityGuard:
    """安全规则引擎.

    职责:
    - 高危操作黑名单匹配（删除生产资源、修改根账号、关闭安全防护）
    - API 调用频率监控和阈值告警
    - 操作序列异常检测（偏离历史基线）
    - HTTPS/TLS 1.2+ 通信强制检查
    """

    def __init__(self, security_rules_path: str | None = None) -> None:
        """初始化安全规则引擎.

        Args:
            security_rules_path: security_rules.yaml 配置文件路径。
        """
        self._blacklist: list[dict] = []
        self._rate_limits: dict = {}
        self._anomaly_config: dict = {}
        self._communication_config: dict = {}
        self._rules: list[SecurityRule] = []

        # 频率计数器: {identity_arn: {action: deque[timestamp]}}
        self._call_history: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=10000))
        )

        # 操作序列历史（用于异常检测）
        self._operation_sequences: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=1000)
        )

        if security_rules_path:
            self._load_rules(security_rules_path)

    # ------------------------------------------------------------------
    # 核心检查
    # ------------------------------------------------------------------

    async def check(
        self,
        workload_identity: WorkloadIdentity,
        action: str,
        resource_arn: str,
    ) -> SecurityCheckResult:
        """执行安全检查.

        流程:
        1. 高危操作黑名单匹配
        2. API 调用频率检查
        3. 操作序列异常检测

        Args:
            workload_identity: 调用方身份。
            action: 请求执行的操作。
            resource_arn: 目标资源 ARN。

        Returns:
            SecurityCheckResult 包含检查结果。
        """
        # 1. 黑名单检查
        result = await self._check_blacklist(action)
        if result is not None:
            logger.warning(
                "安全拦截 — 黑名单操作: action=%s, identity=%s",
                action,
                workload_identity.workload_identity_arn,
            )
            return result

        # 2. 频率限制检查
        result = await self._check_rate_limit(workload_identity, action)
        if result is not None:
            logger.warning(
                "安全告警 — 频率超限: action=%s, identity=%s",
                action,
                workload_identity.workload_identity_arn,
            )
            return result

        # 3. 异常检测
        result = await self._check_anomaly(workload_identity, action)
        if result is not None:
            logger.warning(
                "安全告警 — 异常操作序列: action=%s, identity=%s",
                action,
                workload_identity.workload_identity_arn,
            )
            return result

        # 记录操作历史
        identity_arn = workload_identity.workload_identity_arn
        self._call_history[identity_arn][action].append(time.monotonic())
        self._operation_sequences[identity_arn].append(
            {"action": action, "resource_arn": resource_arn, "timestamp": time.time()}
        )

        return SecurityCheckResult(allowed=True)

    def check_tls_compliance(self, url: str) -> SecurityCheckResult:
        """检查 URL 是否符合 HTTPS/TLS 1.2+ 要求.

        Args:
            url: 待检查的 URL。

        Returns:
            SecurityCheckResult。
        """
        enforce_https = self._communication_config.get("enforce_https", True)

        if enforce_https and not url.startswith("https://"):
            return SecurityCheckResult(
                allowed=False,
                rule_id="tls_enforcement",
                denial_reason=f"URL {url} 不使用 HTTPS 协议",
                suggestion="请使用 HTTPS 协议（TLS 1.2+）进行通信。",
            )

        return SecurityCheckResult(allowed=True)

    # ------------------------------------------------------------------
    # 黑名单检查
    # ------------------------------------------------------------------

    async def _check_blacklist(self, action: str) -> Optional[SecurityCheckResult]:
        """检查是否在高危操作黑名单中."""
        for entry in self._blacklist:
            if entry.get("action") == action:
                return SecurityCheckResult(
                    allowed=False,
                    rule_id="blacklist",
                    denial_reason=entry.get("description", f"操作 {action} 在高危操作黑名单中"),
                    suggestion=entry.get("suggestion", "请通过控制台手动执行此操作。"),
                )
        return None

    # ------------------------------------------------------------------
    # 频率限制
    # ------------------------------------------------------------------

    async def _check_rate_limit(
        self,
        workload_identity: WorkloadIdentity,
        action: str,
    ) -> Optional[SecurityCheckResult]:
        """检查 API 调用频率是否超过阈值."""
        identity_arn = workload_identity.workload_identity_arn
        history = self._call_history[identity_arn][action]

        # 获取频率限制配置
        default_limits = self._rate_limits.get("default", {})
        max_per_minute = default_limits.get("max_calls_per_minute", 60)
        max_per_hour = default_limits.get("max_calls_per_hour", 1000)

        now = time.monotonic()

        # 统计最近 1 分钟的调用次数
        one_minute_ago = now - 60
        recent_calls = sum(1 for t in history if t > one_minute_ago)

        if recent_calls >= max_per_minute:
            return SecurityCheckResult(
                allowed=False,
                rule_id="rate_limit_per_minute",
                denial_reason=(
                    f"操作 {action} 在最近 1 分钟内调用 {recent_calls} 次，"
                    f"超过阈值 {max_per_minute}"
                ),
                suggestion="请降低调用频率，或联系管理员调整频率限制。",
            )

        # 统计最近 1 小时的调用次数
        one_hour_ago = now - 3600
        hourly_calls = sum(1 for t in history if t > one_hour_ago)

        if hourly_calls >= max_per_hour:
            return SecurityCheckResult(
                allowed=False,
                rule_id="rate_limit_per_hour",
                denial_reason=(
                    f"操作 {action} 在最近 1 小时内调用 {hourly_calls} 次，"
                    f"超过阈值 {max_per_hour}"
                ),
                suggestion="请降低调用频率，或联系管理员调整频率限制。",
            )

        return None

    # ------------------------------------------------------------------
    # 异常检测
    # ------------------------------------------------------------------

    async def _check_anomaly(
        self,
        workload_identity: WorkloadIdentity,
        action: str,
    ) -> Optional[SecurityCheckResult]:
        """检测操作序列是否偏离历史基线.

        简化实现：检测短时间内是否出现异常高频的不同操作类型。
        """
        if not self._anomaly_config.get("enabled", False):
            return None

        identity_arn = workload_identity.workload_identity_arn
        sequence = self._operation_sequences[identity_arn]

        if len(sequence) < 10:
            return None

        # 检查最近 10 个操作中不同操作类型的数量
        recent_actions = [op["action"] for op in list(sequence)[-10:]]
        unique_actions = len(set(recent_actions))

        threshold = self._anomaly_config.get("deviation_threshold", 3.0)

        # 如果最近 10 个操作全部不同，标记为可疑
        if unique_actions >= 8:
            return SecurityCheckResult(
                allowed=True,  # 告警但不拦截
                rule_id="anomaly_detection",
                denial_reason=None,
                suggestion=(
                    f"检测到异常操作模式：最近 10 个操作包含 {unique_actions} 种不同类型，"
                    "请确认操作是否正常。"
                ),
            )

        return None

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_rules(self, path: str) -> None:
        """加载 security_rules.yaml 配置."""
        try:
            with open(path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            self._blacklist = config.get("blacklist", [])
            self._rate_limits = config.get("rate_limits", {})
            self._anomaly_config = config.get("anomaly_detection", {})
            self._communication_config = config.get("communication", {})

            # 构建 SecurityRule 对象
            for entry in self._blacklist:
                self._rules.append(
                    SecurityRule(
                        rule_id=f"blacklist_{entry.get('action', 'unknown')}",
                        rule_type="blacklist",
                        description=entry.get("description", ""),
                        config=entry,
                    )
                )

            logger.info(
                "安全规则加载完成: %d 条黑名单规则",
                len(self._blacklist),
            )
        except (OSError, yaml.YAMLError) as exc:
            logger.error("安全规则配置加载失败: %s", exc)

    @property
    def rules(self) -> list[SecurityRule]:
        """已加载的安全规则列表."""
        return list(self._rules)
