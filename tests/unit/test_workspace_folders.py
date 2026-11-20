"""A workspace's folder follows it: a copy gets its own copy of the folder TALYX made (a folder the user bound is
shared), and deleting the workspace moves TALYX's folder to the Recycle Bin once everything else is cleaned up, while
a bound folder stays."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from host.app import Host, HostConfig
from host.secrets import MemorySecrets
from host.workspaces import Workspaces


def host(tmp_path: Path) -> Host:
    (tmp_path / "official").mkdir(exist_ok=True)
    return Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user", official_root=tmp_path / "official",
                           secrets_store=MemorySecrets()))


async def saved(app: Host, name: str) -> tuple[str, dict]:
    workspace = (await app.workspaces.open({"name": name}))["workspaceId"]
    return workspace, await app.workspaces.save({"name": name, "workspace": {}}, workspace)


def test_a_copy_gets_its_own_copy_of_the_folder_talyx_made_and_shares_a_folder_the_user_bound(tmp_path):
    async def scenario():
        app = host(tmp_path)
        workspace, _ = await saved(app, "Desk")
        own = Path((await app.workspaces.folder(workspace))["path"])
        (own / "skills" / "volatility").mkdir(parents=True)
        (own / "AGENTS.md").write_text("Use daily bars", encoding="utf-8")
        (own / "skills" / "volatility" / "SKILL.md").write_text("Annualise", encoding="utf-8")

        copy = await app.copy_workspace(workspace, None)
        copied = Path((await app.workspaces.folder(copy["workspaceId"]))["path"])
        assert copied != own and copied.parent == own.parent
        assert (copied / "AGENTS.md").read_text(encoding="utf-8") == "Use daily bars"
        assert (copied / "skills" / "volatility" / "SKILL.md").read_text(encoding="utf-8") == "Annualise"
        (copied / "AGENTS.md").write_text("Use hourly bars", encoding="utf-8")
        (own / "notes.md").write_text("Only in the original", encoding="utf-8")
        assert (own / "AGENTS.md").read_text(encoding="utf-8") == "Use daily bars"
        assert not (copied / "notes.md").exists()

        bound = tmp_path / "research"
        bound.mkdir()
        mine, _ = await saved(app, "Mine")
        await app.workspaces.set_folder(mine, str(bound))
        again = await app.copy_workspace(mine, "Mine 2")
        assert (await app.workspaces.folder(again["workspaceId"]))["path"] == str(bound.resolve())

        # A folder the user removed by hand leaves nothing to copy; the copy still goes through.
        gone, _ = await saved(app, "Gone")
        Path((await app.workspaces.folder(gone))["path"]).rmdir()
        later = await app.copy_workspace(gone, "Gone 2")
        assert later["workspaceId"] not in app.workspaces.folders

    asyncio.run(scenario())


@pytest.mark.parametrize("saved_before", [True, False])
def test_deleting_a_workspace_recycles_the_folder_talyx_made_last_and_keeps_a_bound_one(tmp_path, monkeypatch,
                                                                                       saved_before):
    recycle_bin = tmp_path / "recycle-bin"
    recycle_bin.mkdir()
    failing = {"recycle": False}

    def recycle(path: Path) -> None:
        if failing["recycle"]:
            raise OSError("Windows did not confirm moving the folder to the Recycle Bin.")
        path.rename(recycle_bin / path.name)

    monkeypatch.setattr("host.workspaces.recycle_directory", recycle)

    async def scenario():
        app = host(tmp_path)

        async def delete(workspace: str, snapshot: dict | None) -> None:
            if snapshot is None:
                await app.close_workspace(workspace)
            else:
                await app.workspaces.delete(snapshot["snapshotId"], snapshot["revision"], app.delete_workspace_contents)

        async def workspace(name: str) -> tuple[str, dict | None]:
            if saved_before:
                return await saved(app, name)
            return (await app.workspaces.open({"name": name}))["workspaceId"], None

        desk, snapshot = await workspace("Desk")
        own = Path((await app.workspaces.folder(desk))["path"])
        (own / "notes").mkdir()
        (own / "AGENTS.md").write_text("Use daily bars", encoding="utf-8")
        (own / "notes" / "btc.md").write_text("Breakout", encoding="utf-8")
        assert await app.deletion_preview(desk) == [f"Workspace folder · {own} (2 files)"]

        # The folder is only moved once the other cleanup succeeded, and a failed move is reported.
        cleanup = app.settings.delete_workspace

        async def broken(workspace_id):
            raise RuntimeError("settings could not be deleted")
        app.settings.delete_workspace = broken
        with pytest.raises(RuntimeError):
            await delete(desk, snapshot)
        assert own.is_dir() and app.workspaces.is_open(desk)
        app.settings.delete_workspace = cleanup
        failing["recycle"] = True
        with pytest.raises(OSError, match="Recycle Bin"):
            await delete(desk, snapshot)
        assert own.is_dir() and app.workspaces.is_open(desk) and desk in app.workspaces.folders
        failing["recycle"] = False
        await delete(desk, snapshot)
        assert not own.exists() and desk not in app.workspaces.folders and not app.workspaces.is_open(desk)
        assert (recycle_bin / own.name / "notes" / "btc.md").read_text(encoding="utf-8") == "Breakout"

        bound = tmp_path / "research"
        bound.mkdir()
        (bound / "keep.md").write_text("keep", encoding="utf-8")
        mine, snapshot = await workspace("Mine")
        await app.workspaces.set_folder(mine, str(bound))
        assert await app.deletion_preview(mine) == []
        await delete(mine, snapshot)
        assert (bound / "keep.md").read_text(encoding="utf-8") == "keep" and mine not in app.workspaces.folders
        assert [path.name for path in recycle_bin.iterdir()] == [own.name]
        assert Workspaces(tmp_path / "data" / "platform", tmp_path / "user" / "workspaces", ()).folders == {}

    asyncio.run(scenario())
