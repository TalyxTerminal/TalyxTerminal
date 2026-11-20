"""A single delete action that names its object (a declared target): in the auto tier Agent deletes what this
conversation made without asking, and asks before deleting anything else."""

import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage

from tests.unit.test_agent_runtime import agent, call, cards, start

ROOT = Path(__file__).resolve().parents[2]


def quant_tool(name):
    """A Quant action as the platform's AI catalog gives it, from Quant's contract."""
    spec = json.loads((ROOT / "modules/quant/public/contract.json").read_text(encoding="utf-8"))["actions"][name]
    return {"name": f"quant.{name}", "description": spec["description"], "effects": spec["effects"],
            "task": spec["task"], "input": {"type": "object"}, "examples": [], "errors": [],
            "effectBranches": None, "target": spec.get("target")}


def test_auto_tier_deletes_the_quant_object_this_chat_made_and_asks_for_the_users(tmp_path):
    async def scenario():
        assert quant_tool("delete_object")["target"] == "objectId"
        responses = [call("quant.create_object", {"kind": "factor", "name": "Mine"}, "make"),
                     call("quant.delete_object", {"objectId": "mine"}, "drop-mine"),
                     call("quant.delete_object", {"objectId": "users"}, "drop-users"),
                     AIMessage(content="unused")]
        async with agent(tmp_path, responses) as (service, platform):
            async def calls(name, value, **kw):
                if name == "platform.ai.catalog":
                    if "module" not in value:
                        return {"version": 1, "modules": [{"module": "quant", "guide": None, "docs": []}], "nextCursor": None}
                    return {"tools": [quant_tool("create_object"), quant_tool("delete_object")], "nextCursor": None}
                if name == "platform.ai.call" and value["action"] == "platform.changes.list":
                    made = {f"host-{index}" for index, item in enumerate(platform.calls, start=1)
                            if item["action"] == "quant.create_object"}
                    return {"result": [{"callId": identity, "objectId": "mine", "label": "Mine", "openTarget": None,
                                        "kind": "created"} for identity in value["input"]["callIds"] if identity in made],
                            "callId": "changes-list"}
                return await platform.call(name, value, **kw)
            service.ctx.call = calls
            _, snapshot = await start(service, "Make a factor, then delete it and the user's one")
            assert [(item["action"], item["input"]) for item in platform.calls] == [
                ("quant.create_object", {"kind": "factor", "name": "Mine"}),
                ("quant.delete_object", {"objectId": "mine"})]
            [approval] = cards(snapshot, "approval")
            assert approval["input"] == {"objectId": "users"} and approval["state"] == "waiting"
    asyncio.run(scenario())
