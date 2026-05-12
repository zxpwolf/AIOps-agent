"""Tests for ResourceResolver — 云资源引用解析器."""

from __future__ import annotations

import pytest

from aiops_agent.context.resource_resolver import ResourceResolver
from aiops_agent.models.schemas import ResourceReference


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver() -> ResourceResolver:
    """Default ResourceResolver with cn-hangzhou region."""
    return ResourceResolver(default_region="cn-hangzhou")


@pytest.fixture
def resolver_eu() -> ResourceResolver:
    """ResourceResolver with a different default region."""
    return ResourceResolver(default_region="eu-west-1")


# ---------------------------------------------------------------------------
# Valid resource ID patterns
# ---------------------------------------------------------------------------


class TestValidResourceIds:
    """Each resource type regex matches valid IDs."""

    async def test_ecs_instance_id(self, resolver: ResourceResolver) -> None:
        """ECS: i-[a-z0-9]{12,17}"""
        refs = resolver.resolve("Check instance i-abc123456789 status")
        assert len(refs) == 1
        assert refs[0].resource_type == "ecs"
        assert refs[0].resource_id == "i-abc123456789"

    async def test_rds_instance_id(self, resolver: ResourceResolver) -> None:
        """RDS: rm-[a-z0-9]{12,17}"""
        refs = resolver.resolve("RDS rm-abc123456789 is slow")
        assert len(refs) == 1
        assert refs[0].resource_type == "rds"
        assert refs[0].resource_id == "rm-abc123456789"

    async def test_vpc_id(self, resolver: ResourceResolver) -> None:
        """VPC: vpc-[a-z0-9]{12,17}"""
        refs = resolver.resolve("VPC vpc-abc123456789 subnet")
        assert len(refs) == 1
        assert refs[0].resource_type == "vpc"
        assert refs[0].resource_id == "vpc-abc123456789"

    async def test_vswitch_id(self, resolver: ResourceResolver) -> None:
        """VSwitch: vsw-[a-z0-9]{12,17}"""
        refs = resolver.resolve("VSwitch vsw-abc123456789 config")
        assert len(refs) == 1
        assert refs[0].resource_type == "vswitch"
        assert refs[0].resource_id == "vsw-abc123456789"

    async def test_slb_id(self, resolver: ResourceResolver) -> None:
        """SLB: lb-[a-z0-9]{12,17}"""
        refs = resolver.resolve("SLB lb-abc123456789 health")
        assert len(refs) == 1
        assert refs[0].resource_type == "slb"
        assert refs[0].resource_id == "lb-abc123456789"

    async def test_eip_id(self, resolver: ResourceResolver) -> None:
        """EIP: eip-[a-z0-9]{12,17}"""
        refs = resolver.resolve("EIP eip-abc123456789 binding")
        assert len(refs) == 1
        assert refs[0].resource_type == "eip"
        assert refs[0].resource_id == "eip-abc123456789"

    async def test_security_group_id(self, resolver: ResourceResolver) -> None:
        """SG: sg-[a-z0-9]{12,17}"""
        refs = resolver.resolve("Security group sg-abc123456789 rules")
        assert len(refs) == 1
        assert refs[0].resource_type == "sg"
        assert refs[0].resource_id == "sg-abc123456789"

    async def test_disk_id(self, resolver: ResourceResolver) -> None:
        """Disk: d-[a-z0-9]{12,17}"""
        refs = resolver.resolve("Disk d-abc123456789 snapshot")
        assert len(refs) == 1
        assert refs[0].resource_type == "disk"
        assert refs[0].resource_id == "d-abc123456789"

    async def test_snapshot_id(self, resolver: ResourceResolver) -> None:
        """Snapshot: s-[a-z0-9]{12,17}"""
        refs = resolver.resolve("Snapshot s-abc123456789 restore")
        assert len(refs) == 1
        assert refs[0].resource_type == "snapshot"
        assert refs[0].resource_id == "s-abc123456789"

    async def test_image_id(self, resolver: ResourceResolver) -> None:
        """Image: m-[a-z0-9]{12,17}"""
        refs = resolver.resolve("Image m-abc123456789 deploy")
        assert len(refs) == 1
        assert refs[0].resource_type == "image"
        assert refs[0].resource_id == "m-abc123456789"

    async def test_oss_bucket(self, resolver: ResourceResolver) -> None:
        """OSS: oss://bucket-name"""
        refs = resolver.resolve("Check oss://my-bucket/logs/2024")
        assert len(refs) == 1
        assert refs[0].resource_type == "oss"
        assert refs[0].resource_id == "oss://my-bucket/logs/2024"

    async def test_oss_bucket_root(self, resolver: ResourceResolver) -> None:
        """OSS: oss://bucket-name (no path)"""
        refs = resolver.resolve("Bucket oss://data-store is full")
        assert len(refs) == 1
        assert refs[0].resource_type == "oss"
        assert refs[0].resource_id == "oss://data-store"

    def test_minimum_length_ids(self, resolver: ResourceResolver) -> None:
        """IDs with minimum length (12 chars after prefix) should match."""
        refs = resolver.resolve("i-123456789012")  # exactly 12 chars
        assert len(refs) == 1
        assert refs[0].resource_id == "i-123456789012"

    def test_maximum_length_ids(self, resolver: ResourceResolver) -> None:
        """IDs with maximum length (17 chars after prefix) should match."""
        refs = resolver.resolve("i-12345678901234567")  # exactly 17 chars
        assert len(refs) == 1
        assert refs[0].resource_id == "i-12345678901234567"


# ---------------------------------------------------------------------------
# Invalid / malformed IDs
# ---------------------------------------------------------------------------


class TestInvalidResourceIds:
    """Rejects invalid or malformed resource IDs."""

    async def test_too_short_id(self, resolver: ResourceResolver) -> None:
        """IDs shorter than 12 chars after prefix should not match."""
        refs = resolver.resolve("i-abc123")
        assert len(refs) == 0

    async def test_too_long_id(self, resolver: ResourceResolver) -> None:
        """IDs longer than 17 chars after prefix should not match."""
        refs = resolver.resolve("i-abc123456789012345678")  # 21 chars after prefix
        assert len(refs) == 0

    async def test_uppercase_letters(self, resolver: ResourceResolver) -> None:
        """IDs with uppercase letters should not match."""
        refs = resolver.resolve("i-ABC123456789")
        assert len(refs) == 0

    async def test_special_characters_in_id(self, resolver: ResourceResolver) -> None:
        """IDs with special characters should not match."""
        refs = resolver.resolve("i-abc_123456789")
        assert len(refs) == 0

    async def test_missing_prefix(self, resolver: ResourceResolver) -> None:
        """Random string without a valid prefix should not match."""
        refs = resolver.resolve("abc123456789")
        assert len(refs) == 0

    async def test_wrong_prefix(self, resolver: ResourceResolver) -> None:
        """An unrecognized prefix should not match any resource type."""
        refs = resolver.resolve("xx-abc123456789")
        assert len(refs) == 0

    async def test_partial_match_not_boundary(self, resolver: ResourceResolver) -> None:
        """ID embedded in a longer string without word boundary should not match."""
        refs = resolver.resolve("prefixi-abc123456789suffix")
        assert len(refs) == 0

    async def test_invalid_oss_format(self, resolver: ResourceResolver) -> None:
        """Invalid OSS URI format should not match."""
        refs = resolver.resolve("oss//bucket-name")  # missing colon
        assert len(refs) == 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Same ID appearing multiple times yields one reference."""

    async def test_duplicate_same_id(self, resolver: ResourceResolver) -> None:
        """Same resource ID appearing twice returns one reference."""
        refs = resolver.resolve(
            "Check i-abc123456789 and also i-abc123456789 again"
        )
        assert len(refs) == 1
        assert refs[0].resource_id == "i-abc123456789"

    async def test_duplicate_different_types_same_suffix(
        self, resolver: ResourceResolver
    ) -> None:
        """Different resource types with same suffix are NOT deduplicated
        (they have different full IDs)."""
        refs = resolver.resolve("i-abc123456789 and vpc-abc123456789")
        assert len(refs) == 2
        resource_ids = {r.resource_id for r in refs}
        assert resource_ids == {"i-abc123456789", "vpc-abc123456789"}

    async def test_multiple_duplicates(self, resolver: ResourceResolver) -> None:
        """Multiple IDs each repeated many times are all deduplicated."""
        text = " ".join(["i-abc123456789"] * 5 + ["vpc-abc123456789"] * 3)
        refs = resolver.resolve(text)
        assert len(refs) == 2


# ---------------------------------------------------------------------------
# Empty and no-match inputs
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Empty text and text with no resource IDs return empty list."""

    async def test_empty_string(self, resolver: ResourceResolver) -> None:
        """Empty input returns empty list."""
        assert resolver.resolve("") == []

    async def test_whitespace_only(self, resolver: ResourceResolver) -> None:
        """Whitespace-only input returns empty list."""
        assert resolver.resolve("   \n\t  ") == []

    async def test_no_resource_ids(self, resolver: ResourceResolver) -> None:
        """Text with no resource IDs returns empty list."""
        refs = resolver.resolve("The server is running fine, no issues found")
        assert refs == []

    async def test_only_numbers(self, resolver: ResourceResolver) -> None:
        """Text with only numbers returns empty list."""
        refs = resolver.resolve("12345 67890")
        assert refs == []


# ---------------------------------------------------------------------------
# Multiple resource types
# ---------------------------------------------------------------------------


class TestMultipleResourceTypes:
    """Text containing multiple different resource types."""

    async def test_multiple_types(self, resolver: ResourceResolver) -> None:
        """Resolves all resource types in a single text."""
        text = (
            "Instance i-abc123456789 in VPC vpc-abc123456789 "
            "uses disk d-abc123456789 and SG sg-abc123456789"
        )
        refs = resolver.resolve(text)
        assert len(refs) == 4
        types = {r.resource_type for r in refs}
        assert types == {"ecs", "vpc", "disk", "sg"}

    async def test_all_types_at_once(self, resolver: ResourceResolver) -> None:
        """All supported resource types in one text."""
        text = (
            "i-abc123456789 rm-abc123456789 vpc-abc123456789 "
            "vsw-abc123456789 lb-abc123456789 eip-abc123456789 "
            "sg-abc123456789 d-abc123456789 s-abc123456789 "
            "m-abc123456789 oss://my-bucket/path"
        )
        refs = resolver.resolve(text)
        assert len(refs) == 11
        types = {r.resource_type for r in refs}
        assert types == {
            "ecs",
            "rds",
            "vpc",
            "vswitch",
            "slb",
            "eip",
            "sg",
            "disk",
            "snapshot",
            "image",
            "oss",
        }


# ---------------------------------------------------------------------------
# Default region
# ---------------------------------------------------------------------------


class TestDefaultRegion:
    """default_region is passed to all references."""

    async def test_default_region_applied(self, resolver: ResourceResolver) -> None:
        """All resolved references get the default region."""
        refs = resolver.resolve("i-abc123456789 and vpc-abc123456789")
        for ref in refs:
            assert ref.region == "cn-hangzhou"

    async def test_custom_region(self, resolver_eu: ResourceResolver) -> None:
        """Custom default_region is used."""
        refs = resolver_eu.resolve("i-abc123456789")
        assert len(refs) == 1
        assert refs[0].region == "eu-west-1"


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class TestReturnType:
    """resolve() returns list of ResourceReference objects."""

    async def test_returns_resource_reference_objects(
        self, resolver: ResourceResolver
    ) -> None:
        """All returned items are ResourceReference instances."""
        refs = resolver.resolve("i-abc123456789 vpc-abc123456789")
        assert len(refs) == 2
        for ref in refs:
            assert isinstance(ref, ResourceReference)

    async def test_resource_reference_fields(
        self, resolver: ResourceResolver
    ) -> None:
        """ResourceReference has all expected fields populated."""
        refs = resolver.resolve("i-abc123456789")
        assert len(refs) == 1
        ref = refs[0]
        assert ref.resource_type == "ecs"
        assert ref.resource_id == "i-abc123456789"
        assert ref.region == "cn-hangzhou"
        assert ref.display_name is None  # optional, not set by resolver


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestAddPattern:
    """add_pattern works with custom regexes."""

    async def test_add_custom_pattern(self, resolver: ResourceResolver) -> None:
        """Adding a custom pattern allows matching new resource types."""
        resolver.add_pattern("custom", r"\b(custom-[a-z0-9]{6,12})\b")
        refs = resolver.resolve("Check custom-abc123 status")
        assert len(refs) == 1
        assert refs[0].resource_type == "custom"
        assert refs[0].resource_id == "custom-abc123"

    async def test_custom_pattern_does_not_interfere(
        self, resolver: ResourceResolver
    ) -> None:
        """Custom patterns coexist with built-in patterns."""
        resolver.add_pattern("custom", r"\b(custom-[a-z0-9]{6,12})\b")
        refs = resolver.resolve("i-abc123456789 and custom-xyz789")
        assert len(refs) == 2
        types = {r.resource_type for r in refs}
        assert types == {"ecs", "custom"}

    async def test_custom_pattern_no_match(self, resolver: ResourceResolver) -> None:
        """Custom pattern correctly rejects non-matching strings."""
        resolver.add_pattern("custom", r"\b(custom-[a-z0-9]{6,12})\b")
        refs = resolver.resolve("custom-ab")  # "ab" is 2 chars, below min of 6
        assert len(refs) == 0

    async def test_multiple_custom_patterns(
        self, resolver: ResourceResolver
    ) -> None:
        """Multiple custom patterns can be added."""
        resolver.add_pattern("type_a", r"\b(a-[a-z0-9]{6,12})\b")
        resolver.add_pattern("type_b", r"\b(b-[a-z0-9]{6,12})\b")
        refs = resolver.resolve("a-abcdef and b-xyz123")
        assert len(refs) == 2
        types = {r.resource_type for r in refs}
        assert types == {"type_a", "type_b"}
