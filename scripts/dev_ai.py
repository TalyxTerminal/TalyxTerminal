"""Persistent local MCP client. Read JSON requests from stdin; emit JSON results."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(url):
    async with streamablehttp_client(url, timeout=timedelta(seconds=30), sse_read_timeout=timedelta(minutes=10)) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(minutes=10)) as session:
            await session.initialize()
            print(json.dumps({"ready": True}), flush=True)
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = json.loads(line)
                if request["op"] == "list":
                    result = await session.list_tools()
                    output = result.model_dump(mode="json") if request.get("full") else {"tools": [tool.name for tool in result.tools]}
                elif request["op"] == "call":
                    result = await session.call_tool(request["name"], request.get("arguments", {}))
                    output = result.model_dump(mode="json")
                elif request["op"] == "resource":
                    result = await session.read_resource(request["uri"])
                    output = result.model_dump(mode="json")
                else:
                    raise ValueError("op must be list, call or resource")
                print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    asyncio.run(run(parser.parse_args().url))
