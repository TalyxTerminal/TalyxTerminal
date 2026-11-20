"""Immutable conversation instructions and append-only contextual observations."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from .store import encode

SYSTEM = Path(__file__).with_name("prompt.md").read_text(encoding="utf-8").strip()


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def catalog_action(item):
    """A catalog action as the conversation keeps it."""
    return {"action": item["name"], "description": item["description"], "effects": item["effects"], "task": item["task"],
            "inputSchema": item["input"], "examples": item["examples"], "errors": item["errors"],
            "effectBranches": item["effectBranches"], "target": item["target"], "summaryPaths": item.get("summaryPaths", [])}


def model_tools(tools):
    """Preserve the shared JSON schema; framework Pydantic tools use its converter."""
    return [item if isinstance(item, dict) else {"type": "function", "function": {
        "name": item.name, "description": item.description, "parameters": item.args_schema}}
        if isinstance(item, StructuredTool) and isinstance(item.args_schema, dict)
        else convert_to_openai_tool(item) for item in tools]


class Profiles:
    def __init__(self, service):
        self.service = service
        self.lock = asyncio.Lock()
        self.version, self.current = None, None

    async def read(self, turn):
        async with self.lock:
            index = await self.service.ctx.call("platform.ai.catalog", {})
            if self.current is not None and self.version == index["version"]:
                return self.current
            modules, actions = await self.service.catalog(index)
            tools = [catalog_action(item) for item in actions]
            guides = []
            for module in modules:
                if not module["guide"]:
                    continue
                guide = await self.service.ctx.call("platform.ai.guide", {"module": module["module"]})
                docs = "".join(f"\n- {doc['topic']}: {doc['title']}" for doc in module["docs"])
                guides.append(module["module"] + "\n" + guide["text"] + (
                    f"\nAuthoring docs (read first: platform.ai.guide {{module:\"{module['module']}\", topic}}):"
                    + docs if docs else ""))
            # The model sees three discovery/call tools. Show it the open action's current declaration beside the
            # module guides, so even a direct call can use the installed panel names.
            panel_guide = [item["description"] for item in actions if item["name"] == "platform.panels.open"]
            system = "\n\n".join([SYSTEM, *panel_guide, *guides])
            self.current = {"system": system, "tools": tools, "digest": await asyncio.to_thread(digest, [system, tools])}
            self.version = index["version"]
            return self.current
