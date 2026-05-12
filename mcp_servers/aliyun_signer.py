"""Aliyun API HMAC-SHA1 签名工具 — 纯标准库，无 SDK 依赖."""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
from typing import Any


def percent_encode(s: str) -> str:
    """阿里云专用的 URL 编码."""
    if not s:
        return ""
    encoded = urllib.parse.quote(str(s), safe="")
    # Aliyun 特殊规则
    encoded = encoded.replace("+", "%20")
    encoded = encoded.replace("*", "%2A")
    encoded = encoded.replace("%7E", "~")
    return encoded


def build_api_params(
    action: str,
    version: str,
    access_key_id: str,
    region_id: str = "cn-hangzhou",
    **extra_params: Any,
) -> dict[str, str]:
    """构建阿里云 API 公共参数 + 业务参数."""
    params: dict[str, str] = {
        "Format": "JSON",
        "Version": version,
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": hashlib.sha256(
            datetime.now(timezone.utc).isoformat().encode()
        ).hexdigest()[:16],
        "RegionId": region_id,
        "Action": action,
    }
    for k, v in extra_params.items():
        params[k] = str(v)
    return params


def sign_request(
    method: str,
    params: dict[str, str],
    secret: str,
) -> str:
    """计算 HMAC-SHA1 签名.

    签名格式: {METHOD}&%2F&{percent_encode(canonical_query)}
    签名密钥: SECRET + "&"
    """
    # 按字母排序参数
    sorted_params = sorted(params.items())
    canonical_query = "&".join(
        f"{percent_encode(k)}={percent_encode(v)}" for k, v in sorted_params
    )

    string_to_sign = f"{method.upper()}&%2F&{percent_encode(canonical_query)}"
    signing_key = secret + "&"

    h = hmac.new(
        signing_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    )
    return base64.b64encode(h.digest()).decode("utf-8")
