"""阿里云 CloudMonitor MCP Server — 云监控指标查询."""

from __future__ import annotations

import asyncio
import logging
import os

from mcp_servers.aliyun_signer import build_api_params, sign_request
from mcp_servers.base import McpServer

CMS_ENDPOINT = "https://metrics.cn-hangzhou.aliyuncs.com"
_DEMO_AK = "demo-access-key-id"
_DEMO_SK = "demo-secret-key"


class CloudMonitorClient:
    def __init__(self, access_key_id: str, access_key_secret: str, region: str) -> None:
        self._ak = access_key_id
        self._sk = access_key_secret
        self._region = region

    async def _do_request(self, action: str, params: dict) -> dict:
        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not installed"}

        api_params = build_api_params(
            action=action, version="2019-01-01",
            access_key_id=self._ak, region_id=self._region,
            **params,
        )
        signature = sign_request("GET", api_params, self._sk)
        api_params["Signature"] = signature

        async with aiohttp.ClientSession() as session:
            async with session.get(CMS_ENDPOINT, params=api_params) as resp:
                return await resp.json()

    async def query_metric_last(self, args: dict) -> dict:
        result = await self._do_request("DescribeMetricLast", {
            "Namespace": args.get("namespace", ""),
            "MetricName": args.get("metric_name", ""),
            "Dimensions": f'[{{"instanceId":"{args.get("instance_id", "")}"}}]',
        })
        return {"data": result.get("Datapoints", [])}

    async def query_metric_list(self, args: dict) -> dict:
        params = {
            "Namespace": args.get("namespace", ""),
            "MetricName": args.get("metric_name", ""),
            "Dimensions": f'[{{"instanceId":"{args.get("instance_id", "")}"}}]',
        }
        if args.get("start_time"):
            params["StartTime"] = args["start_time"]
        if args.get("end_time"):
            params["EndTime"] = args["end_time"]
        result = await self._do_request("DescribeMetricList", params)
        return {"data": result.get("Datapoints", [])}

    async def query_alarm_history(self, args: dict) -> dict:
        params = {}
        if args.get("namespace"):
            params["Namespace"] = args["namespace"]
        if args.get("start_time"):
            params["StartTime"] = args["start_time"]
        if args.get("end_time"):
            params["EndTime"] = args["end_time"]
        result = await self._do_request("DescribeSystemEventHistory", params)
        return {"data": result.get("SystemEventHistory", [])}


def create_server() -> McpServer:
    region = os.environ.get("REGION", "cn-hangzhou")
    ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", _DEMO_AK)
    sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", _DEMO_SK)

    client = CloudMonitorClient(ak, sk, region)
    server = McpServer("cloud_monitor", "0.1.0")

    server.register_tool(
        name="query_metric_last",
        description="查询云监控最新指标数据",
        handler=client.query_metric_last,
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "metric_name": {"type": "string"},
                "instance_id": {"type": "string"},
            },
            "required": ["namespace", "metric_name", "instance_id"],
        },
    )
    server.register_tool(
        name="query_metric_list",
        description="查询云监控历史指标列表",
        handler=client.query_metric_list,
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "metric_name": {"type": "string"},
                "instance_id": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
            "required": ["namespace", "metric_name", "instance_id"],
        },
    )
    server.register_tool(
        name="query_alarm_history",
        description="查询告警历史",
        handler=client.query_alarm_history,
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
        },
    )
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(create_server().run())
