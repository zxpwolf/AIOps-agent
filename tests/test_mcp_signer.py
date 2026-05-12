"""阿里云签名工具测试."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from mcp_servers.aliyun_signer import (
    build_api_params,
    percent_encode,
    sign_request,
)


class TestPercentEncode:
    def test_normal_string(self) -> None:
        assert percent_encode("hello") == "hello"

    def test_spaces(self) -> None:
        assert percent_encode("hello world") == "hello%20world"

    def test_special_chars(self) -> None:
        result = percent_encode("a*b~c")
        assert "*" not in result
        assert "%2A" in result
        assert "~" in result

    def test_chinese(self) -> None:
        result = percent_encode("测试")
        assert "%" in result

    def test_empty(self) -> None:
        assert percent_encode("") == ""


class TestSignRequest:
    def test_signature_format(self) -> None:
        params = {
            "Action": "DescribeInstances",
            "Version": "2014-05-26",
            "AccessKeyId": "test-key",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "Timestamp": "2024-01-01T00:00:00Z",
            "SignatureNonce": "test-nonce",
            "RegionId": "cn-hangzhou",
        }
        sig = sign_request("GET", params, "test-secret")
        assert isinstance(sig, str)
        assert len(sig) > 0
        assert re.match(r'^[A-Za-z0-9+/=]+$', sig)

    def test_signature_deterministic(self) -> None:
        params = {
            "Action": "Test",
            "Version": "2014-05-26",
            "AccessKeyId": "key",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "Timestamp": "2024-01-01T00:00:00Z",
            "SignatureNonce": "nonce",
        }
        sig1 = sign_request("GET", params, "secret")
        sig2 = sign_request("GET", params, "secret")
        assert sig1 == sig2

    def test_different_secrets(self) -> None:
        params = {
            "Action": "Test",
            "Version": "2014-05-26",
            "AccessKeyId": "key",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "Timestamp": "2024-01-01T00:00:00Z",
            "SignatureNonce": "nonce",
        }
        sig1 = sign_request("GET", params, "secret1")
        sig2 = sign_request("GET", params, "secret2")
        assert sig1 != sig2


class TestBuildApiParams:
    def test_basic_params(self) -> None:
        result = build_api_params(
            action="DescribeInstances",
            version="2014-05-26",
            access_key_id="test-key",
            region_id="cn-hangzhou",
        )
        assert result["Action"] == "DescribeInstances"
        assert result["Version"] == "2014-05-26"
        assert result["AccessKeyId"] == "test-key"
        assert result["RegionId"] == "cn-hangzhou"
        assert result["SignatureMethod"] == "HMAC-SHA1"
        assert result["SignatureVersion"] == "1.0"
        assert result["Format"] == "JSON"
        assert "SignatureNonce" in result
        assert "Timestamp" in result

    def test_extra_params(self) -> None:
        result = build_api_params(
            action="Test",
            version="2014-05-26",
            access_key_id="key",
            region_id="cn-hangzhou",
            InstanceId="i-test123",
        )
        assert result["InstanceId"] == "i-test123"

    def test_timestamp_format(self) -> None:
        result = build_api_params(
            action="Test",
            version="2014-05-26",
            access_key_id="key",
        )
        # Should be ISO 8601 format
        assert "T" in result["Timestamp"]
        assert result["Timestamp"].endswith("Z")

    def test_converts_values_to_string(self) -> None:
        result = build_api_params(
            action="Test",
            version="2014-05-26",
            access_key_id="key",
            PageNumber=1,
            PageSize=10,
        )
        assert result["PageNumber"] == "1"
        assert result["PageSize"] == "10"
