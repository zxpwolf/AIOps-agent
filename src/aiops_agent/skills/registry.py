"""Skill_Registry — 技能注册、发现、版本管理和健康状态维护.

支持技能注册、注销、基于 capabilities 的模糊匹配和排序、
版本管理（多版本共存）、运行时动态加载/卸载和健康状态管理。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from aiops_agent.models.schemas import SkillDefinition
from aiops_agent.skills.base import SkillInstance

logger = logging.getLogger(__name__)


class SkillRegistry:
    """技能注册中心.

    职责:
    - 技能注册、注销、发现
    - 注册时完整性校验（必填字段、skill_name 唯一性）
    - 基于 capabilities 的模糊匹配和排序
    - 版本管理（多版本共存，默认路由最新稳定版）
    - 运行时动态加载和卸载（无需重启）
    - 健康状态维护和不健康 Skill 自动移除
    """

    def __init__(self) -> None:
        # {skill_name: {version: (definition, instance)}}
        self._skills: dict[str, dict[str, tuple[SkillDefinition, SkillInstance]]] = defaultdict(dict)
        # {skill_name: latest_stable_version}
        self._default_versions: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    async def register(
        self,
        definition: SkillDefinition,
        instance: SkillInstance,
    ) -> None:
        """注册技能，校验完整性和唯一性.

        Args:
            definition: 技能定义。
            instance: 技能实例。

        Raises:
            ValueError: 校验失败时抛出。
        """
        # 完整性校验
        errors = self._validate_definition(definition)
        if errors:
            raise ValueError(f"技能注册校验失败: {'; '.join(errors)}")

        # 唯一性校验（同名同版本不允许重复注册）
        if definition.version in self._skills[definition.skill_name]:
            raise ValueError(
                f"技能 '{definition.skill_name}' 版本 '{definition.version}' 已注册"
            )

        self._skills[definition.skill_name][definition.version] = (definition, instance)

        # 更新默认版本（最新注册的稳定版）
        if definition.status == "healthy":
            self._default_versions[definition.skill_name] = definition.version

        # 调用生命周期钩子
        await instance.on_register()

        logger.info(
            "技能已注册: %s v%s (capabilities: %s)",
            definition.skill_name,
            definition.version,
            definition.capabilities,
        )

    async def unregister(
        self,
        skill_name: str,
        version: str | None = None,
    ) -> None:
        """注销技能.

        Args:
            skill_name: 技能名称。
            version: 指定版本，None 则注销所有版本。
        """
        if skill_name not in self._skills:
            return

        if version is not None:
            entry = self._skills[skill_name].pop(version, None)
            if entry:
                _, instance = entry
                await instance.on_unregister()
                logger.info("技能已注销: %s v%s", skill_name, version)

            # 如果没有剩余版本，清理
            if not self._skills[skill_name]:
                del self._skills[skill_name]
                self._default_versions.pop(skill_name, None)
            elif self._default_versions.get(skill_name) == version:
                # 重新选择默认版本
                self._update_default_version(skill_name)
        else:
            # 注销所有版本
            for ver, (_, instance) in list(self._skills[skill_name].items()):
                await instance.on_unregister()
            del self._skills[skill_name]
            self._default_versions.pop(skill_name, None)
            logger.info("技能已注销（所有版本）: %s", skill_name)

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------

    async def discover(self, capabilities: list[str]) -> list[SkillDefinition]:
        """基于能力匹配发现技能，返回匹配度排序列表.

        Args:
            capabilities: 需要的能力列表。

        Returns:
            按匹配度降序排列的 SkillDefinition 列表（仅健康的技能）。
        """
        matches: list[tuple[int, SkillDefinition]] = []

        for skill_name, versions in self._skills.items():
            # 使用默认版本
            version = self._default_versions.get(skill_name)
            if version is None or version not in versions:
                continue

            definition, _ = versions[version]
            if definition.status != "healthy":
                continue

            # 计算匹配度
            skill_caps = set(definition.capabilities)
            requested_caps = set(capabilities)
            overlap = len(skill_caps & requested_caps)

            if overlap > 0:
                matches.append((overlap, definition))

        # 按匹配度降序排序
        matches.sort(key=lambda x: x[0], reverse=True)
        return [defn for _, defn in matches]

    # ------------------------------------------------------------------
    # 获取技能
    # ------------------------------------------------------------------

    async def get_skill(
        self,
        skill_name: str,
        version: str | None = None,
    ) -> Optional[SkillInstance]:
        """获取技能实例，默认返回最新稳定版本.

        Args:
            skill_name: 技能名称。
            version: 指定版本，None 使用默认版本。

        Returns:
            SkillInstance 或 None。
        """
        if skill_name not in self._skills:
            return None

        ver = version or self._default_versions.get(skill_name)
        if ver is None or ver not in self._skills[skill_name]:
            return None

        _, instance = self._skills[skill_name][ver]
        return instance

    def get_definition(
        self,
        skill_name: str,
        version: str | None = None,
    ) -> Optional[SkillDefinition]:
        """获取技能定义."""
        if skill_name not in self._skills:
            return None

        ver = version or self._default_versions.get(skill_name)
        if ver is None or ver not in self._skills[skill_name]:
            return None

        definition, _ = self._skills[skill_name][ver]
        return definition

    def list_skills(self) -> list[SkillDefinition]:
        """列出所有已注册技能的默认版本定义."""
        result = []
        for skill_name, versions in self._skills.items():
            ver = self._default_versions.get(skill_name)
            if ver and ver in versions:
                defn, _ = versions[ver]
                result.append(defn)
        return result

    # ------------------------------------------------------------------
    # 健康管理
    # ------------------------------------------------------------------

    async def health_check(self, skill_name: str) -> bool:
        """检查技能健康状态.

        Args:
            skill_name: 技能名称。

        Returns:
            True 表示健康。
        """
        instance = await self.get_skill(skill_name)
        if instance is None:
            return False

        try:
            healthy = await instance.health_check()
        except Exception:
            logger.exception("技能 '%s' 健康检查异常", skill_name)
            healthy = False

        # 更新状态
        defn = self.get_definition(skill_name)
        if defn is not None:
            defn.status = "healthy" if healthy else "unhealthy"

        return healthy

    async def mark_unhealthy(self, skill_name: str) -> None:
        """将技能标记为不健康，从路由候选列表中移除."""
        defn = self.get_definition(skill_name)
        if defn is not None:
            defn.status = "unhealthy"
            logger.warning("技能 '%s' 已标记为不健康", skill_name)

    async def mark_healthy(self, skill_name: str) -> None:
        """恢复技能健康状态."""
        defn = self.get_definition(skill_name)
        if defn is not None:
            defn.status = "healthy"
            logger.info("技能 '%s' 已恢复健康", skill_name)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_definition(definition: SkillDefinition) -> list[str]:
        """校验技能定义的完整性."""
        errors = []
        if not definition.skill_name:
            errors.append("skill_name 不能为空")
        if not definition.description:
            errors.append("description 不能为空")
        if not definition.version:
            errors.append("version 不能为空")
        return errors

    def _update_default_version(self, skill_name: str) -> None:
        """重新选择默认版本（最新的健康版本）."""
        if skill_name not in self._skills:
            self._default_versions.pop(skill_name, None)
            return

        versions = self._skills[skill_name]
        healthy_versions = [
            ver for ver, (defn, _) in versions.items() if defn.status == "healthy"
        ]

        if healthy_versions:
            self._default_versions[skill_name] = healthy_versions[-1]
        else:
            self._default_versions.pop(skill_name, None)
