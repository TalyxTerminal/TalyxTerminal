"""Deleting an object and the folder actions are Quant public actions that the panel and an AI share: the panel calls them like any other
caller. A deletion declares the ``delete`` effect, so an AI client knows what it is about to do and asks its user the way it asks for any
deletion (the platform does not ask; design decision: the AI client decides from declared effects). The library's two deletions an AI could
already make (archiving a version, deleting a research list) declare it the same way. What cannot happen is refused before anything changes."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from tests.unit.quant_library_fakes import factor_spec
from tests.unit.test_quant_factor_runs import services

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "modules" / "quant" / "public" / "contract.json").read_text(encoding="utf-8"))
AI = SimpleNamespace(caller=SimpleNamespace(origin="ai", module=None, workspace_id="ws", ai={"client": "Agent"}, run=None))


def world(tmp_path, monkeypatch, library=False):
    from modules.quant.backend.library_routes import handle_library_actions
    from modules.quant.backend.object_routes import handle_actions
    ctx, shell, _ = services(tmp_path, monkeypatch, [])
    handle_actions(ctx, shell)
    if library:
        handle_library_actions(ctx, shell)
    return shell, ctx.handlers


def test_deletions_declare_that_they_delete_and_folder_changes_only_write():
    actions = CONTRACT["actions"]
    for name in ("delete_object", "delete_object_folder", "archive_library_version", "delete_research_list"):
        assert "delete" in actions[name]["effects"], name
    for name in ("create_object_folder", "rename_object_folder"):
        assert actions[name]["effects"] == ["write"], name


def test_a_user_creates_renames_and_deletes_a_folder_and_deletes_an_object(tmp_path, monkeypatch):
    async def scenario():
        shell, handlers = world(tmp_path, monkeypatch)
        assert await handlers["create_object_folder"]({"kind": "factor", "name": "Momentum"}, None) == {"kind": "factor", "name": "Momentum"}
        created = await shell.store.create({"kind": "factor", "name": "Reversal", "folder": "Momentum"})
        renamed = await handlers["rename_object_folder"]({"kind": "factor", "from": "Momentum", "name": "Trend"}, None)
        assert renamed == {"kind": "factor", "name": "Trend"} and shell.store.read(created["id"])["folder"] == "Trend"  # the object moved with it
        with pytest.raises(ContractError, match="still holds 1 object"):
            await handlers["delete_object_folder"]({"kind": "factor", "name": "Trend"}, None)
        assert await handlers["delete_object"]({"objectId": created["id"]}, None) == {"objectId": created["id"], "deleted": True}
        with pytest.raises(ContractError, match="does not exist"):
            shell.store.read(created["id"])
        assert (await handlers["delete_object_folder"]({"kind": "factor", "name": "Trend"}, None))["deleted"]
        with pytest.raises(ContractError, match="There is no folder"):
            await handlers["delete_object_folder"]({"kind": "factor", "name": "Trend"}, None)
    asyncio.run(scenario())


def test_an_ais_deletion_of_an_object_lets_go_of_its_library_version(tmp_path, monkeypatch):
    async def scenario():
        shell, handlers = world(tmp_path, monkeypatch)
        created = await shell.store.create({"kind": "strategy", "name": "Breakout"})
        shell.library.store.commit_versions([factor_spec("Momentum", "v1", 50)])
        shell.library.acquire("v1", "qobject:" + created["id"], "ws", kind="strategy", name="Breakout", how="research, pinned v1")
        assert shell.library.users("v1")
        assert (await handlers["delete_object"]({"objectId": created["id"]}, AI))["deleted"]
        assert shell.library.users("v1") == []
    asyncio.run(scenario())


def test_archiving_a_running_library_version_is_refused_and_keeps_its_values(tmp_path, monkeypatch):
    async def scenario():
        shell, handlers = world(tmp_path, monkeypatch, library=True)
        library = shell.library
        library.store.commit_versions([factor_spec("Momentum", "v1", 50)])
        with pytest.raises(ContractError, match="Stop this version first"):
            await handlers["archive_library_version"]({"versionId": "v1"}, AI)
        assert library.store.version("v1")["archived_at"] is None
        library.stop("v1")
        assert (await handlers["archive_library_version"]({"versionId": "v1"}, AI))["archived"]
    asyncio.run(scenario())


def test_a_research_list_is_deleted_by_whoever_asks(tmp_path, monkeypatch):
    async def scenario():
        shell, handlers = world(tmp_path, monkeypatch, library=True)
        made = await shell.lists.create({"name": "Large caps", "members": []})
        assert (await handlers["delete_research_list"]({"listId": made["id"]}, AI))["deleted"]
        with pytest.raises(ContractError):
            shell.lists.read(made["id"])
    asyncio.run(scenario())
