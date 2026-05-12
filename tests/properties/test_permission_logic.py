"""属性测试 — 权限逻辑."""

from __future__ import annotations

import fnmatch
import string

import hypothesis.strategies as st
from hypothesis import given, settings

from aiops_agent.security.permission_gate import _classify_permission_level, PermissionLevel


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def permission_strings(draw: st.DrawFn) -> str:
    """生成随机权限字符串."""
    service = draw(st.sampled_from(["ecs", "rds", "vpc", "slb", "cms", "sls", "ram"]))
    action = draw(st.sampled_from([
        "Describe", "List", "Get", "Query",  # Read
        "Create", "Modify", "Update", "Set",  # Write
        "Start", "Stop", "Reboot", "Restart",  # Write
        "Enable", "Disable", "Execute",  # Write
        "Delete",  # Admin
    ]))
    resource = draw(st.sampled_from([
        "Instance", "Vpc", "DBInstance", "LoadBalancer",
        "SecurityGroup", "Disk", "Snapshot", "User",
    ]))
    return f"{service}:{action}{resource}"


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestPermissionLevelClassification:
    @given(permission_strings())
    @settings(max_examples=50)
    def test_delete_is_always_admin(self, permission: str) -> None:
        if ":Delete" in permission:
            level = _classify_permission_level(permission)
            assert level == PermissionLevel.ADMIN

    @given(permission_strings())
    @settings(max_examples=50)
    def test_describe_is_always_readonly(self, permission: str) -> None:
        if ":Describe" in permission:
            level = _classify_permission_level(permission)
            assert level == PermissionLevel.READ_ONLY

    @given(permission_strings())
    @settings(max_examples=50)
    def test_create_is_write(self, permission: str) -> None:
        if ":Create" in permission:
            level = _classify_permission_level(permission)
            assert level == PermissionLevel.LIMITED_WRITE

    @given(permission_strings())
    @settings(max_examples=50)
    def test_result_is_valid_enum(self, permission: str) -> None:
        level = _classify_permission_level(permission)
        assert isinstance(level, PermissionLevel)


class TestPermissionMatching:
    @given(permission_strings())
    @settings(max_examples=50)
    def test_exact_match_works(self, permission: str) -> None:
        assert fnmatch.fnmatch(permission, permission) is True

    @given(permission_strings())
    @settings(max_examples=50)
    def test_wildcard_matches_all(self, permission: str) -> None:
        assert fnmatch.fnmatch(permission, "*") is True

    @given(
        service=st.sampled_from(["ecs", "rds", "vpc"]),
        resource=st.sampled_from(["Instance", "Vpc", "DBInstance"]),
    )
    @settings(max_examples=50)
    def test_service_wildcard_matches_service_actions(
        self, service: str, resource: str
    ) -> None:
        pattern = f"{service}:*"
        action = f"{service}:Describe{resource}"
        assert fnmatch.fnmatch(action, pattern) is True

    @given(
        service=st.sampled_from(["ecs", "rds", "vpc"]),
    )
    @settings(max_examples=50)
    def test_different_services_do_not_match(self, service: str) -> None:
        other = {"ecs": "rds", "rds": "vpc", "vpc": "ecs"}[service]
        pattern = f"{service}:*"
        action = f"{other}:DescribeInstance"
        assert fnmatch.fnmatch(action, pattern) is False
