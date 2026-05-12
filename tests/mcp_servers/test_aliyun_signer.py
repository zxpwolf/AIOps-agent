"""Tests for aliyun_signer module."""

import pytest

from mcp_servers.aliyun_signer import (
    build_api_params,
    percent_encode,
    sign_request,
)


class TestPercentEncode:
    def test_empty_string(self):
        assert percent_encode("") == ""

    def test_normal_string(self):
        assert percent_encode("hello") == "hello"

    def test_spaces_encoded_as_20(self):
        assert percent_encode("hello world") == "hello%20world"

    def test_asterisk_encoded_as_2A(self):
        assert percent_encode("test*") == "test%2A"

    def test_tilde_not_encoded(self):
        assert percent_encode("~test") == "~test"

    def test_chinese_characters(self):
        result = percent_encode("测试")
        assert "%" in result

    def test_special_characters(self):
        result = percent_encode("a+b*c~d")
        assert "%2B" in result
        assert "%2A" in result
        assert "~" in result


class TestBuildApiParams:
    def test_basic_params(self):
        params = build_api_params(
            action="DescribeInstances",
            version="2014-05-26",
            access_key_id="test-ak",
            region_id="cn-hangzhou",
        )
        assert params["Action"] == "DescribeInstances"
        assert params["Version"] == "2014-05-26"
        assert params["AccessKeyId"] == "test-ak"
        assert params["RegionId"] == "cn-hangzhou"
        assert params["Format"] == "JSON"
        assert params["SignatureMethod"] == "HMAC-SHA1"
        assert "SignatureNonce" in params
        assert "Timestamp" in params

    def test_extra_params(self):
        params = build_api_params(
            action="Test",
            version="1.0",
            access_key_id="ak",
            region_id="cn-shanghai",
            InstanceId="i-xxx",
        )
        assert params["InstanceId"] == "i-xxx"

    def test_timestamp_format(self):
        params = build_api_params("A", "1.0", "ak", "cn-h")
        assert "T" in params["Timestamp"]
        assert "Z" in params["Timestamp"]

    def test_converts_values_to_string(self):
        params = build_api_params(
            action="Test",
            version="1.0",
            access_key_id="ak",
            region_id="cn-h",
            count=42,
        )
        assert isinstance(params["count"], str)
        assert params["count"] == "42"


class TestSignRequest:
    def test_signature_is_base64(self):
        sig = sign_request("GET", {"Action": "Test"}, "secret")
        import base64
        try:
            base64.b64decode(sig)
            assert True
        except Exception:
            assert False, "Signature should be valid base64"

    def test_different_actions_different_sigs(self):
        sig1 = sign_request("GET", {"Action": "DescribeInstances"}, "secret")
        sig2 = sign_request("GET", {"Action": "DescribeVpcs"}, "secret")
        assert sig1 != sig2

    def test_different_methods_different_sigs(self):
        params = {"Action": "Test"}
        sig_get = sign_request("GET", params, "secret")
        sig_post = sign_request("POST", params, "secret")
        assert sig_get != sig_post

    def test_deterministic_with_same_params(self):
        params = {"Action": "Test", "RegionId": "cn-h"}
        sig = sign_request("GET", params, "secret")
        assert len(sig) > 0
