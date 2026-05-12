"""Audit_Logger — 全链路操作审计日志记录器.

利用 Agent Identity 与 ActionTrail 的深度集成，
实现 Agent 级和用户级的全链路审计。
支持 ActionTrail 写入、本地 JSON 结构化日志、失败备份和告警。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from aiops_agent.models.schemas import AuditEvent
from aiops_agent.security.sanitizer import sanitize_parameters

logger = logging.getLogger(__name__)


class AuditLogger:
    """操作审计日志记录器.

    职责:
    - 为每次 Agent 操作生成审计日志
    - 敏感字段脱敏
    - 写入 ActionTrail（Agent Identity 集成）
    - 写入本地 JSON 结构化日志
    - ActionTrail 写入失败时写入本地备份 + 触发告警
    - On-Behalf-Of 模式下同时记录 Agent 和用户身份
    """

    def __init__(
        self,
        action_trail_endpoint: str | None = None,
        local_log_dir: str | Path = "logs/audit",
        backup_log_dir: str | Path = "logs/audit_backup",
        alert_callback=None,
    ) -> None:
        """初始化审计日志记录器.

        Args:
            action_trail_endpoint: ActionTrail 写入端点 URL。为 None 时仅写本地日志。
            local_log_dir: 本地审计日志目录。
            backup_log_dir: ActionTrail 失败时的备份日志目录。
            alert_callback: 告警回调函数，签名 async def callback(message: str) -> None。
        """
        self._action_trail_endpoint = action_trail_endpoint
        self._local_log_dir = Path(local_log_dir)
        self._backup_log_dir = Path(backup_log_dir)
        self._alert_callback = alert_callback
        self._session: Optional[aiohttp.ClientSession] = None

        # 确保日志目录存在
        self._local_log_dir.mkdir(parents=True, exist_ok=True)
        self._backup_log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    async def log(self, event: AuditEvent) -> None:
        """记录审计事件.

        流程:
        1. 敏感字段脱敏
        2. 写入 ActionTrail（Agent Identity 集成）
        3. 写入本地 JSON 结构化日志
        4. ActionTrail 写入失败时写入本地备份 + 触发告警
        """
        # 1. 脱敏
        sanitized_event = self._sanitize_event(event)
        event_dict = sanitized_event.model_dump(mode="json")

        # 确保 timestamp 为 ISO 8601 字符串
        if isinstance(event_dict.get("timestamp"), datetime):
            event_dict["timestamp"] = event_dict["timestamp"].isoformat()

        # 2. 写入 ActionTrail
        action_trail_ok = False
        if self._action_trail_endpoint:
            action_trail_ok = await self._write_to_action_trail(event_dict)

            # 4. 失败时写备份 + 告警
            if not action_trail_ok:
                self._write_backup_log(event_dict)
                await self._trigger_alert(
                    f"ActionTrail 写入失败: event_id={event.event_id}, "
                    f"action={event.action}"
                )

        # 3. 写入本地 JSON 结构化日志
        self._write_local_log(event_dict)

        logger.debug(
            "审计事件已记录: event_id=%s, action=%s, result=%s",
            event.event_id,
            event.action,
            event.result,
        )

    async def query(
        self,
        start_time: datetime,
        end_time: datetime,
        workload_identity_arn: str | None = None,
        action: str | None = None,
        resource_arn: str | None = None,
    ) -> list[AuditEvent]:
        """查询本地审计日志.

        Args:
            start_time: 查询起始时间。
            end_time: 查询结束时间。
            workload_identity_arn: 按 Agent 身份过滤。
            action: 按操作类型过滤。
            resource_arn: 按目标资源过滤。

        Returns:
            匹配的审计事件列表。
        """
        results: list[AuditEvent] = []

        for log_file in sorted(self._local_log_dir.glob("*.jsonl")):
            try:
                text = log_file.read_text(encoding="utf-8")
                for line in text.strip().splitlines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    event = AuditEvent.model_validate(data)

                    # 时间范围过滤
                    ts = event.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if not (start_time <= ts <= end_time):
                        continue

                    # 可选过滤
                    if workload_identity_arn and event.workload_identity_arn != workload_identity_arn:
                        continue
                    if action and event.action != action:
                        continue
                    if resource_arn and event.resource_arn != resource_arn:
                        continue

                    results.append(event)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("读取审计日志文件失败 %s: %s", log_file, exc)

        return results

    # ------------------------------------------------------------------
    # ActionTrail 集成
    # ------------------------------------------------------------------

    async def _write_to_action_trail(self, event_dict: dict) -> bool:
        """写入 ActionTrail.

        Returns:
            True 表示写入成功，False 表示失败。
        """
        try:
            session = await self._get_session()
            async with session.post(
                self._action_trail_endpoint,
                json=event_dict,
                ssl=True,
            ) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                logger.error(
                    "ActionTrail 写入失败: HTTP %d - %s",
                    resp.status,
                    body,
                )
                return False
        except Exception:
            logger.exception("ActionTrail 写入异常")
            return False

    # ------------------------------------------------------------------
    # 本地日志
    # ------------------------------------------------------------------

    def _write_local_log(self, event_dict: dict) -> None:
        """写入本地 JSON 结构化日志（JSONL 格式，按日期分文件）."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self._local_log_dir / f"audit-{today}.jsonl"
        try:
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.exception("本地审计日志写入失败: %s", log_file)

    def _write_backup_log(self, event_dict: dict) -> None:
        """ActionTrail 失败时写入备份日志."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        backup_file = self._backup_log_dir / f"backup-{today}.jsonl"
        try:
            with backup_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict, ensure_ascii=False, default=str) + "\n")
            logger.info("审计事件已写入备份日志: %s", backup_file)
        except OSError:
            logger.exception("备份审计日志写入失败: %s", backup_file)

    # ------------------------------------------------------------------
    # 脱敏
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_event(event: AuditEvent) -> AuditEvent:
        """对审计事件中的敏感参数进行脱敏."""
        sanitized_params = sanitize_parameters(event.parameters)
        return event.model_copy(update={"parameters": sanitized_params})

    # ------------------------------------------------------------------
    # 告警
    # ------------------------------------------------------------------

    async def _trigger_alert(self, message: str) -> None:
        """触发告警通知."""
        logger.error("审计告警: %s", message)
        if self._alert_callback is not None:
            try:
                await self._alert_callback(message)
            except Exception:
                logger.exception("告警回调执行失败")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭审计日志记录器."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP 会话."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )
        return self._session
