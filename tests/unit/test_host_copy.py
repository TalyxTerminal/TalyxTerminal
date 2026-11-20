"""Copying a workspace whose saved layout has panels of a module that is not running is refused, naming the module:
a copy never comes out without what those panels show."""

from __future__ import annotations

import asyncio

import pytest
from talyx_sdk.errors import ContractError

from host.app import Host, HostConfig
from host.secrets import MemorySecrets
from tests.platform.harness import prepare

USER = {"origin": "user", "module": None, "workspaceId": None}


class Ready:
    def __init__(self, module_id: str):
        self.module_id, self.state, self.activities, self.endpoint = module_id, "ready", {}, None


def test_a_copy_needs_every_module_with_panels_in_the_saved_layout_running(tmp_path):
    async def scenario():
        root = prepare(tmp_path / "official", ("late", "echo"))
        host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user", official_root=root,
                               secrets_store=MemorySecrets()))
        host.catalog.discover()
        copied = []

        async def copy_in(process, source, copy):
            copied.append(process.module_id)

        host._copy_in = copy_in
        host.supervisor.processes["echo"] = Ready("echo")
        workspace = (await host.workspaces.open({"name": "Desk"}))["workspaceId"]
        state = {"panels": [{"id": "p1", "kind": "module:echo", "state": None},
                            {"id": "p2", "kind": "module:late", "state": None}]}
        await host.workspaces.save({"name": "Desk", "workspace": state}, workspace)

        with pytest.raises(ContractError) as refused:
            await host.copy_workspace(workspace, None)
        assert refused.value.code == "unavailable" and "Late" in refused.value.message
        assert copied == [] and [item["name"] for item in (await host.workspaces.list_saved())["saved"]] == ["Desk"]

        await host.disable("late")
        with pytest.raises(ContractError, match="Late is turned off"):
            await host.copy_workspace(workspace, None)

        host.supervisor.processes["late"] = Ready("late")
        await host.workspaces.folder(workspace)
        with pytest.raises(ContractError, match="already called"):
            await host.copy_workspace(workspace, " Desk ")
        assert set(host.workspaces.folders) == {workspace} and not copied
        assert len(list(host.workspaces.folders_root.iterdir())) == 1
        copy = await host.copy_workspace(workspace, None)
        assert copy["name"] == "Desk copy" and sorted(copied) == ["echo", "late"]

    asyncio.run(scenario())


def test_failed_module_copy_cleans_up_only_the_new_workspace(tmp_path):
    from pathlib import Path

    async def scenario():
        root = prepare(tmp_path / "official", ("echo",))
        host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user", official_root=root,
                               secrets_store=MemorySecrets()))
        host.catalog.discover()
        host.supervisor.processes["echo"] = Ready("echo")
        host.catalog.get("echo").contract["deleteWorkspace"] = True
        workspace = (await host.workspaces.open({"name": "Desk"}))["workspaceId"]
        await host.workspaces.save({"name": "Desk", "workspace": {}}, workspace)
        source = Path((await host.workspaces.folder(workspace))["path"])
        (source / "note.md").write_text("keep")
        host.settings.values["workspaces"][workspace] = {"echo": {"color": "blue"}}
        copied = set()

        async def fail(process, source_id, copy_id):
            copied.add(copy_id)
            raise ContractError("conflict", "Copy failed")

        async def remove(process, envelope, seconds):
            assert envelope["t"] == "delete_workspace" and not envelope["preview"]
            copied.remove(envelope["ws"])

        host._copy_in, host._ask_module = fail, remove
        with pytest.raises(ContractError, match="Copy failed"):
            await host.copy_workspace(workspace, "Another")
        assert not copied and not host.workspaces.copy_names
        assert list(host.workspaces.folders_root.iterdir()) == [source]
        assert set(host.workspaces.folders) == set(host.settings.values["workspaces"]) == {workspace}
        assert (source / "note.md").read_text() == "keep"
        assert [row["name"] for row in (await host.workspaces.list_saved())["saved"]] == ["Desk"]
        await host.notifications.close()

    asyncio.run(scenario())
