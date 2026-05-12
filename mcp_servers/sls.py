"""阿里云 SLS 日志服务 MCP Server."""

from __future__ import annotations

import asyncio
import logging
import os

from mcp_servers.aliyun_signer import build_api_params, sign_request
from mcp_servers.base import McpServer

_DEMO_AK = "demo-access-key-id"
_DEMO_SK = "demo-secret-key"


class SLSClient:
    def __init__(self, access_key_id: str, access_key_secret: str, region: str) -> None:
        self._ak = access_key_id
        self._sk = access_key_secret
        self._region = region

    async def _do_request(self, project: str, path: str, params: dict) -> dict:
        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not installed"}

        url = f"https://{project}.{self._region}.log.aliyuncs.com{path}"
        headers = {"x-log-bodyrawsize": "0", "x-log-apiversion": "0.6.0"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                return await resp.json()

    async def query_logs(self, args: dict) -> dict:
        params = {"query": args.get("query", "*"), "type": "log", "line": 100, "offset": 0}
        result = await self._do_request(args.get("project", ""), f"/logstores/{args.get('logstore', '')}/logs", params)
        return {"logs": result.get("logs", [])}

    async def list_logstores(self, args: dict) -> dict:
        result = await self._do_request(args.get("project", ""), "/logstores", {})
        return {"logstores": result.get("logstores", [])}

    async def get_logstore_index(self, args: dict) -> dict:
        result = await self._do_request(args.get("project", ""), f"/logstores/{args.get('logstore', '')}/index", {})
        return {"index": result}


def create_server() -> McpServer:
    region = os.environ.get("REGION", "cn-hangzhou")
    ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", _DEMO_AK)
    sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", _DEMO_SK)

    client = SLSClient(ak, sk, region)
    server = McpServer("sls", "0.1.0")

    server.register_tool(
        name="query_logs",
        description="查询 SLS 日志",
        handler=client.query_logs,
        input_schema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "logstore": {"type": "string"},
                "query": {"type": "string", "default": "*"},
            },
            "required": ["project", "logstore"],
        },
    )
    server.register_tool(
        name="list_logstores",
        description="列出项目下的 Logstore",
        handler=client.list_logstores,
        input_schema={
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    )
    server.register_tool(
        name="get_logstore_index",
        description="获取 Logstore 索引配置",
        handler=client.get_logstore_index,
        input_schema={
            "type": "object",
            "properties": {"project": {"type": "string"}, "logstore": {"type": "string"}},
            "required": ["project", "logstore"],
        },
    )
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(create_server().run())
