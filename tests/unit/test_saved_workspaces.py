"""Saved workspaces: a saved state belongs to one workspace id, is read back exactly, and is never overwritten by a
save that did not read the latest revision."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import orjson
import pytest
from talyx_sdk.errors import ContractError

from host.workspaces import Workspaces


def workspaces(tmp_path) -> Workspaces:
    return Workspaces(tmp_path / "platform", tmp_path / "folders", (tmp_path / "platform",))


def test_a_saved_workspace_opens_again_as_the_same_workspace_with_exactly_what_was_saved(tmp_path):
    async def scenario():
        store = workspaces(tmp_path)
        opened = await store.open({"name": "Desk"})
        state = {"layout": {"grid": {"root": {"type": "leaf"}}}, "panels": [
            {"id": "p1", "kind": "chart.main", "state": {"symbol": "BTC/USDT", "zoom": 1.5}},
            {"id": "p2", "kind": "unknown.future", "state": {"x": [1, 2, None]}}]}
        saved = await store.save({"name": "Desk", "workspace": state}, opened["workspaceId"])
        assert saved["workspaceId"] == opened["workspaceId"] and len(saved["revision"]) == 64
        assert store.list_open()[0]["snapshotId"] == saved["snapshotId"]
        other = await store.open({"name": "Other"})
        await store.open({"workspaceId": opened["workspaceId"], "name": "Desk", "presentation": state})
        assert store.list_open()[0]["snapshotId"] == saved["snapshotId"]
        assert [record["name"] for record in store.list_open()] == ["Desk", "Other"]
        await store.closed(other["workspaceId"])
        await store.closed(opened["workspaceId"])
        again = await store.open({"snapshotId": saved["snapshotId"]})
        assert (again["workspaceId"], again["name"]) == (opened["workspaceId"], "Desk")
        loaded = await store.load(saved["snapshotId"])
        assert loaded["workspace"] == state and loaded["revision"] == saved["revision"]
        # A fresh Host reads the same.
        assert (await workspaces(tmp_path).load(saved["snapshotId"]))["workspace"] == state
        await store.closed(opened["workspaceId"])
        # A desktop window or bookmarked tab restores by workspaceId, without the separate snapshotId.
        restored = workspaces(tmp_path)
        await restored.open({"workspaceId": opened["workspaceId"]})
        assert [(r["workspaceId"], r["name"], r["snapshotId"]) for r in restored.list_open()] == [
            (opened["workspaceId"], "Desk", saved["snapshotId"])]

    asyncio.run(scenario())


def test_saving_over_needs_the_revision_read_and_a_workspace_has_one_saved_state(tmp_path):
    async def scenario():
        store = workspaces(tmp_path)
        workspace = (await store.open({}))["workspaceId"]
        first = await store.save({"name": "Desk", "workspace": {"n": 1}}, workspace)
        with pytest.raises(ContractError) as twice:
            await store.save({"name": "Other", "workspace": {"n": 2}}, workspace)
        assert twice.value.code == "conflict"
        with pytest.raises(ContractError) as stale:
            await store.save({"name": "Desk", "workspace": {"n": 2}, "snapshotId": first["snapshotId"],
                              "expectedRevision": "0" * 64}, workspace)
        assert stale.value.code == "conflict"
        second = await store.save({"name": "Desk 2", "workspace": {"n": 2}, "snapshotId": first["snapshotId"],
                                   "expectedRevision": first["revision"]}, workspace)
        assert second["snapshotId"] == first["snapshotId"] and second["revision"] != first["revision"]
        assert store.list_open()[0]["name"] == "Desk 2"
        with pytest.raises(ContractError) as old:
            await store.delete(first["snapshotId"], first["revision"], AsyncMock())
        assert old.value.code == "conflict"
        cleanup = AsyncMock()
        await store.delete(first["snapshotId"], second["revision"], cleanup)
        cleanup.assert_awaited_once_with(workspace)
        assert (await store.list_saved())["saved"] == []
        assert store.list_open() == []

    asyncio.run(scenario())


def test_names_are_unique_a_workspace_is_saved_from_its_window_and_copies_are_named_after_it(tmp_path):
    async def scenario():
        store = workspaces(tmp_path)
        workspace = (await store.open({}))["workspaceId"]
        await store.save({"name": "Desk", "workspace": {"n": 1}}, workspace)
        other = (await store.open({}))["workspaceId"]
        with pytest.raises(ContractError) as taken:
            await store.save({"name": "desk", "workspace": {}}, other)
        assert taken.value.code == "already_exists"
        with pytest.raises(ContractError) as nowhere:
            await store.save({"name": "Elsewhere", "workspace": {}}, None)
        assert nowhere.value.code == "invalid_target"
        source = await store.saved_of(workspace)
        async with store.reserve_copy(source, "copy-1", None) as first_name:
            async with store.reserve_copy(source, "copy-2", None) as second_name:
                first = await store.save({"name": first_name, "workspace": source["workspace"]}, "copy-1")
                await store.save({"name": second_name, "workspace": source["workspace"]}, "copy-2")
                assert (first_name, second_name) == ("Desk copy", "Desk copy 2")
        assert (await store.load(first["snapshotId"]))["workspace"] == {"n": 1}
        assert sorted(item["name"] for item in (await store.list_saved())["saved"]) == ["Desk", "Desk copy", "Desk copy 2"]

    asyncio.run(scenario())


def test_a_saved_state_is_bounded(tmp_path):
    async def scenario():
        with pytest.raises(ContractError) as large:
            await workspaces(tmp_path).save({"name": "Big", "workspace": {"x": "y" * (5 * 1024 * 1024)}}, None)
        assert large.value.code == "too_large"

    asyncio.run(scenario())


def test_the_index_of_open_workspaces_always_reads_while_eight_open_and_close_at_once(tmp_path):
    """Opening and closing from several windows at once: every write of the index lands whole, the last one is the
    state now, and a Host started afterwards reads it."""
    async def scenario():
        store = workspaces(tmp_path)
        for _ in range(5):
            ids = [item["workspaceId"] for item in await asyncio.gather(*(store.open({"name": f"W{n}"})
                                                                          for n in range(8)))]
            assert set(orjson.loads(store.path.read_bytes())["opened"]) == set(ids)
            await asyncio.gather(*(store.closed(workspace) for workspace in ids))
            assert orjson.loads(store.path.read_bytes())["opened"] == {}
        assert list(store.path.parent.glob("*.tmp")) == []
        assert workspaces(tmp_path).opened == {}

    asyncio.run(scenario())

def test_the_window_holding_a_workspace_hears_its_saved_state_written_renamed_and_deleted(tmp_path):
    """Whichever window saves, renames or deletes an open workspace's saved state, the change names the workspace,
    so the window holding it follows; deleting its contents closes it."""
    async def scenario():
        store = workspaces(tmp_path)
        changes = store.changes()
        opened = asyncio.ensure_future(anext(changes))
        await asyncio.sleep(0)                          # listening from here
        workspace = (await store.open({"name": "Desk"}))["workspaceId"]
        assert (await opened)["kind"] == "opened"
        first = await store.save({"name": "Desk", "workspace": {"n": 1}}, workspace)
        assert await anext(changes) == {"kind": "saved", "workspaceId": workspace, "snapshotId": first["snapshotId"],
                                        "name": "Desk", "revision": first["revision"]}
        renamed = await store.save({"name": "Desk 2", "workspace": {"n": 1}, "snapshotId": first["snapshotId"],
                                    "expectedRevision": first["revision"]}, None)
        assert await anext(changes) == {"kind": "saved", "workspaceId": workspace, "snapshotId": first["snapshotId"],
                                        "name": "Desk 2", "revision": renamed["revision"]}
        await store.delete(first["snapshotId"], renamed["revision"], AsyncMock())
        assert await anext(changes) == {"kind": "closed", "workspaceId": workspace}
        assert store.list_open() == []

    asyncio.run(scenario())


def test_cancelled_index_write_cannot_land_after_the_next_open(tmp_path, monkeypatch):
    import threading

    from host import workspaces as module

    async def scenario():
        store = workspaces(tmp_path)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        write = module.write_atomic

        def delayed(path, data):
            first_write = not entered.is_set()
            if first_write:
                entered.set()
                assert release.wait(5)
            write(path, data)
            if first_write:
                finished.set()

        monkeypatch.setattr(module, "write_atomic", delayed)
        first = asyncio.create_task(store.open({"workspaceId": "first"}))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        second = asyncio.create_task(store.open({"workspaceId": "second"}))
        try:
            await asyncio.wait_for(asyncio.shield(second), 0.1)
        except TimeoutError:
            pass
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert await asyncio.to_thread(finished.wait, 5)
        assert set(workspaces(tmp_path).opened) == {"first", "second"}

    asyncio.run(scenario())


def test_corruption_is_reported_per_snapshot_and_other_workspaces_still_save(tmp_path):
    async def scenario():
        store = workspaces(tmp_path)
        a = await store.save({"name": "A", "workspace": {}}, "a")
        bad = store.snapshots / ("c" * 32 + ".json")
        bad.write_bytes(b"{")
        await store.save({"name": "B", "workspace": {}}, "b")
        listing = await store.list_saved()
        assert [row["name"] for row in listing["saved"]] == ["B", "A"]
        assert listing["unreadable"] == [bad.name]
        assert (await store.load(a["snapshotId"]))["name"] == "A"
        with pytest.raises(ContractError, match="cannot be read"):
            await store.load(bad.stem)

    asyncio.run(scenario())


def test_the_save_action_takes_only_a_workspace_the_window_can_open():
    """workspace.save checks what the window's restore checks: format 6, a layout whose panels are exactly the panels,
    each panel a module's with at most 512 KB of state. Its own example saves."""
    from pydantic import ValidationError
    from talyx_sdk.process import Codec

    from host.platform.declarations import module

    spec = module.actions["workspace.save"]
    codec = Codec(spec.input)
    for example in spec.examples:
        codec.parse(orjson.dumps(example))
    panel = {"id": "p1", "kind": "module:chart", "state": {"chartId": "c1"}}
    from tests.platform.harness import window_state
    good = window_state(panel)
    assert codec.parse(orjson.dumps({"name": "Desk", "workspace": good})).workspace.layout.panels == good["layout"]["panels"]
    floating = {**good["layout"], "grid": window_state()["layout"]["grid"], "floatingGroups": [{
        "data": {"id": "floating", "views": ["p1"]}, "position": {"left": 20, "top": 20, "width": 100, "height": 100}}]}
    codec.parse(orjson.dumps({"name": "Floating", "workspace": {**good, "layout": floating}}))
    # What restore opens anyway (a grid it rebuilds, an active id naming no panel) is saved too.
    for opened in ({**good, "layout": {**good["layout"], "grid": {}}}, {**good, "activePanelId": "missing"}):
        codec.parse(orjson.dumps({"name": "Desk", "workspace": opened}))
    refused = [{"tiles": []}, {**good, "layout": {**good["layout"], "grid": []}}, {**good, "panels": [{**panel, "id": 1}]},
               {**good, "version": 5}, {**good, "layout": {**good["layout"], "panels": {}}},
               {**good, "panels": [panel, panel], "layout": good["layout"]},
               {**good, "panels": [{**panel, "kind": "chart"}]}, {**good, "layout": {"panels": {"p1": {}}}},
               {**good, "panels": [{**panel, "state": {"text": "x" * (512 * 1024)}}]}]
    for workspace in refused:
        with pytest.raises(ValidationError):
            codec.parse(orjson.dumps({"name": "Desk", "workspace": workspace}))
