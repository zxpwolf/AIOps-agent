"""资源引用解析器 — 自动解析对话中的云资源引用.

支持正则匹配 ECS 实例 ID、RDS 实例名、VPC ID 等资源标识符，
并关联到 ResourceReference 对象。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from aiops_agent.models.schemas import ResourceReference

logger = logging.getLogger(__name__)

# 资源 ID 正则模式
_RESOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ecs", re.compile(r"\b(i-[a-z0-9]{12,17})\b")),
    ("rds", re.compile(r"\b(rm-[a-z0-9]{12,17})\b")),
    ("vpc", re.compile(r"\b(vpc-[a-z0-9]{12,17})\b")),
    ("vswitch", re.compile(r"\b(vsw-[a-z0-9]{12,17})\b")),
    ("slb", re.compile(r"\b(lb-[a-z0-9]{12,17})\b")),
    ("eip", re.compile(r"\b(eip-[a-z0-9]{12,17})\b")),
    ("sg", re.compile(r"\b(sg-[a-z0-9]{12,17})\b")),
    ("disk", re.compile(r"\b(d-[a-z0-9]{12,17})\b")),
    ("snapshot", re.compile(r"\b(s-[a-z0-9]{12,17})\b")),
    ("image", re.compile(r"\b(m-[a-z0-9]{12,17})\b")),
    ("oss", re.compile(r"\b(oss://[a-zA-Z0-9._-]+(?:/[^\s]*)?)\b")),
]


class ResourceResolver:
    """资源引用解析器.

    自动解析对话文本中的阿里云资源引用，
    支持正则匹配和上下文关联。
    """

    def __init__(self, default_region: str = "cn-hangzhou") -> None:
        self._default_region = default_region
        self._patterns = list(_RESOURCE_PATTERNS)

    def resolve(self, text: str) -> list[ResourceReference]:
        """解析文本中的资源引用.

        Args:
            text: 待解析的文本。

        Returns:
            解析出的 ResourceReference 列表。
        """
        references: list[ResourceReference] = []
        seen: set[str] = set()

        for resource_type, pattern in self._patterns:
            for match in pattern.finditer(text):
                resource_id = match.group(1)
                if resource_id in seen:
                    continue
                seen.add(resource_id)

                ref = ResourceReference(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    region=self._default_region,
                )
                references.append(ref)
                logger.debug("解析到资源引用: %s (%s)", resource_id, resource_type)

        return references

    def add_pattern(self, resource_type: str, pattern: str) -> None:
        """添加自定义资源 ID 匹配模式.

        Args:
            resource_type: 资源类型名称。
            pattern: 正则表达式字符串。
        """
        self._patterns.append((resource_type, re.compile(pattern)))
