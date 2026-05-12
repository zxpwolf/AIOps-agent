"""阿里云 ECS/VPC/RDS MCP Server."""

from __future__ import annotations

import asyncio
import logging
import os

from mcp_servers.aliyun_signer import build_api_params, sign_request
from mcp_servers.base import McpServer

ECS_ENDPOINT = "https://ecs.aliyuncs.com"
VPC_ENDPOINT = "https://vpc.aliyuncs.com"
RDS_ENDPOINT = "https://rds.aliyuncs.com"
_DEMO_AK = "demo-access-key-id"
_DEMO_SK = "demo-secret-key"


class AliyunClient:
    def __init__(self, access_key_id: str, access_key_secret: str, region: str) -> None:
        self._ak = access_key_id
        self._sk = access_key_secret
        self._region = region

    async def _do_request(self, endpoint: str, action: str, version: str, params: dict) -> dict:
        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not installed"}

        api_params = build_api_params(
            action=action, version=version,
            access_key_id=self._ak, region_id=self._region,
            **params,
        )
        signature = sign_request("GET", api_params, self._sk)
        api_params["Signature"] = signature

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, params=api_params) as resp:
                return await resp.json()

    async def describe_instances(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("instance_ids"):
            params["InstanceIds"] = args["instance_ids"]
        result = await self._do_request(ECS_ENDPOINT, "DescribeInstances", "2014-05-26", params)
        return {"instances": result.get("Instances", {}).get("Instance", [])}

    async def describe_instance_status(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("instance_ids"):
            params["InstanceId"] = args["instance_ids"].split(",")[0].strip()
        result = await self._do_request(ECS_ENDPOINT, "DescribeInstanceStatus", "2014-05-26", params)
        return {"instance_statuses": result.get("InstanceStatuses", {}).get("InstanceStatus", [])}

    async def describe_disks(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("instance_id"):
            params["InstanceId"] = args["instance_id"]
        result = await self._do_request(ECS_ENDPOINT, "DescribeDisks", "2014-05-26", params)
        return {"disks": result.get("Disks", {}).get("Disk", [])}

    async def describe_security_groups(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        result = await self._do_request(ECS_ENDPOINT, "DescribeSecurityGroups", "2014-05-26", params)
        return {"security_groups": result.get("SecurityGroups", {}).get("SecurityGroup", [])}

    async def describe_vpcs(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("vpc_id"):
            params["VpcId"] = args["vpc_id"]
        result = await self._do_request(VPC_ENDPOINT, "DescribeVpcs", "2016-04-28", params)
        return {"vpcs": result.get("Vpcs", {}).get("Vpc", [])}

    async def describe_vswitches(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("vpc_id"):
            params["VpcId"] = args["vpc_id"]
        result = await self._do_request(VPC_ENDPOINT, "DescribeVSwitches", "2016-04-28", params)
        return {"vswitches": result.get("VSwitches", {}).get("VSwitch", [])}

    async def describe_dbinstances(self, args: dict) -> dict:
        params = {"RegionId": self._region}
        if args.get("db_instance_id"):
            params["DBInstanceId"] = args["db_instance_id"]
        result = await self._do_request(RDS_ENDPOINT, "DescribeDBInstances", "2014-08-15", params)
        return {"db_instances": result.get("Items", {}).get("DBInstance", [])}

    async def describe_slowlog_records(self, args: dict) -> dict:
        params = {"DBInstanceId": args.get("db_instance_id", "")}
        result = await self._do_request(RDS_ENDPOINT, "DescribeSlowLogs", "2014-08-15", params)
        return {"items": result.get("Items", {}).get("SQLSlowLog", [])}

    async def describe_dbinstance_status(self, args: dict) -> dict:
        params = {"DBInstanceId": args.get("db_instance_id", "")}
        result = await self._do_request(RDS_ENDPOINT, "DescribeDBInstanceAttribute", "2014-08-15", params)
        return {"status": result.get("Items", {}).get("DBInstanceAttribute", [])}


def create_server() -> McpServer:
    region = os.environ.get("REGION", "cn-hangzhou")
    ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", _DEMO_AK)
    sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", _DEMO_SK)

    client = AliyunClient(ak, sk, region)
    server = McpServer("ecs_vpc_rds", "0.1.0")

    server.register_tool(name="describe_instances", description="查询 ECS 实例信息", handler=client.describe_instances, input_schema={"type": "object", "properties": {"instance_ids": {"type": "string"}}})
    server.register_tool(name="describe_instance_status", description="查询 ECS 实例状态", handler=client.describe_instance_status, input_schema={"type": "object", "properties": {"instance_ids": {"type": "string"}}})
    server.register_tool(name="describe_disks", description="查询 ECS 磁盘信息", handler=client.describe_disks, input_schema={"type": "object", "properties": {"instance_id": {"type": "string"}}})
    server.register_tool(name="describe_security_groups", description="查询安全组", handler=client.describe_security_groups, input_schema={"type": "object", "properties": {}})
    server.register_tool(name="describe_vpcs", description="查询 VPC 信息", handler=client.describe_vpcs, input_schema={"type": "object", "properties": {"vpc_id": {"type": "string"}}})
    server.register_tool(name="describe_vswitches", description="查询 VSwitch 信息", handler=client.describe_vswitches, input_schema={"type": "object", "properties": {"vpc_id": {"type": "string"}}})
    server.register_tool(name="describe_dbinstances", description="查询 RDS 实例", handler=client.describe_dbinstances, input_schema={"type": "object", "properties": {"db_instance_id": {"type": "string"}}})
    server.register_tool(name="describe_slowlog_records", description="查询 RDS 慢查询日志", handler=client.describe_slowlog_records, input_schema={"type": "object", "properties": {"db_instance_id": {"type": "string"}}, "required": ["db_instance_id"]})
    server.register_tool(name="describe_dbinstance_status", description="查询 RDS 实例状态", handler=client.describe_dbinstance_status, input_schema={"type": "object", "properties": {"db_instance_id": {"type": "string"}}, "required": ["db_instance_id"]})

    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(create_server().run())
