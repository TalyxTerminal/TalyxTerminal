"""An activity is one module's: two modules numbering theirs alike (each from m1) never stop each other's."""

from __future__ import annotations

import asyncio

from host.app import Host, HostConfig
from host.secrets import MemorySecrets


class Running:
    def __init__(self, module_id: str, workspace_id: str):
        self.module_id = module_id
        self.activities = {"m1": {"id": "m1", "workspaceId": workspace_id, "title": "Work", "origin": "user"}}


def test_subscribing_after_activity_ended_refreshes_a_stale_tab(tmp_path):
    async def scenario():
        host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user",
                               secrets_store=MemorySecrets()))
        process = Running("sample", "desk")
        host.supervisor.processes["sample"] = process
        assert host.activities("desk")
        # The frame is disconnected when the final job ends. No other activity follows.
        process.activities.clear()
        stream = host.activity_changes()
        assert await asyncio.wait_for(anext(stream), .5) == {}
        assert host.activities("desk") == []
        await stream.aclose()

    asyncio.run(scenario())


def test_stopping_one_modules_activity_leaves_another_modules_with_the_same_id(tmp_path):
    async def scenario():
        (tmp_path / "official").mkdir()
        host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user",
                               official_root=tmp_path / "official", secrets_store=MemorySecrets()))
        workspace = (await host.workspaces.open({"name": "Desk"}))["workspaceId"]
        for module_id in ("quant", "agent"):
            host.supervisor.processes[module_id] = Running(module_id, workspace)
        asked = []

        async def stop_in(process, envelope, what):
            asked.append((process.module_id, envelope["ids"]))

        host._stop_in = stop_in
        listed = host.activities(workspace)
        assert sorted((item["module"], item["id"]) for item in listed) == [("agent", "m1"), ("quant", "m1")]
        await host.stop_activities(workspace, [("agent", "m1")])
        assert asked == [("agent", ["m1"])]

    asyncio.run(scenario())
