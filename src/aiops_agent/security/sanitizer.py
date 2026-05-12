"""敏感数据脱敏 — 密码、Token、AccessKey 等敏感字段的递归脱敏.

支持嵌套字典和列表的递归脱敏，敏感字段名称模式可通过配置文件定义。
"""

from __future__ import annotations

import re
from typing import Any

# 默认敏感字段名称模式（小写匹配）
DEFAULT_SENSITIVE_PATTERNS: list[str] = [
    "password",
    "passwd",
    "secret",
    "token",
    "access_key",
    "accesskey",
    "access_key_id",
    "access_key_secret",
    "security_token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "authorization",
]

REDACTED = "***REDACTED***"


def _compile_patterns(patterns: list[str]) -> re.Pattern[str]:
    """将敏感字段名称模式编译为单个正则表达式（不区分大小写）."""
    escaped = [re.escape(p) for p in patterns]
    combined = "|".join(escaped)
    return re.compile(combined, re.IGNORECASE)


def sanitize_parameters(
    data: Any,
    *,
    sensitive_patterns: list[str] | None = None,
    redacted_value: str = REDACTED,
) -> Any:
    """对字典/列表中的敏感字段进行递归脱敏.

    Args:
        data: 待脱敏的数据，支持 dict、list 及基本类型。
        sensitive_patterns: 敏感字段名称模式列表。为 None 时使用默认模式。
        redacted_value: 脱敏后的替换值。

    Returns:
        脱敏后的数据副本（不修改原始数据）。
    """
    patterns = sensitive_patterns if sensitive_patterns is not None else DEFAULT_SENSITIVE_PATTERNS
    compiled = _compile_patterns(patterns)
    return _sanitize_recursive(data, compiled, redacted_value)


def _sanitize_recursive(
    data: Any,
    pattern: re.Pattern[str],
    redacted_value: str,
) -> Any:
    """递归遍历数据结构，对匹配的字段名进行脱敏."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and pattern.search(key):
                result[key] = redacted_value
            else:
                result[key] = _sanitize_recursive(value, pattern, redacted_value)
        return result

    if isinstance(data, list):
        return [_sanitize_recursive(item, pattern, redacted_value) for item in data]

    # 基本类型原样返回
    return data
