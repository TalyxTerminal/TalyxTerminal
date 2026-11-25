"""Standards 6 and 7: closing a workspace really stops its work; copying one copies modules' content."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from talyx_sdk.errors import ContractError

from tests.platform.harness import real_modules, route, run, running, window_state


def alive(pid: int) -> bool:
    import ctypes
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def descendants(pid: int) -> list[int]:
    import subprocess
    output = subprocess.run(["powershell", "-NoProfile", "-Command",
                             f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],
                            capture_output=True, text=True).stdout
    return [int(line) for line in output.split()]


async def close_and_check(platform, tmp_path, *, stuck: bool) -> None:
    workspace = await platform.workspace("work")
    folder = tmp_path / ("stuck" if stuck else "plain")
    folder.mkdir()
    started = await platform.call("echo.start_work", {"folder": str(folder), "stuck": stuck}, workspace=workspace)
    await asyncio.sleep(0.5)
    grandchildren = descendants(started["child"])
    assert grandchildren, "the child started a child of its own"
    activities = await platform.call("platform.activity.list", {"workspaceId": workspace})
    assert [a["module"] for a in activities["activities"]] == ["echo"]
    closing = time.monotonic()
    await platform.call("platform.workspace.close", {"workspaceId": workspace})
    assert time.monotonic() - closing < (8 if stuck else 5)
    counter = folder / "counter.txt"
    stopped_at = counter.read_text()
    assert not alive(started["child"])
    assert not any(alive(pid) for pid in grandchildren)
    await platform.call("platform.workspace.open", {"workspaceId": workspace, "name": "work"})
    await asyncio.sleep(0.5)
    assert counter.read_text() == stopped_at          # the old work does not write into the reopened workspace


def test_closing_a_workspace_stops_its_tasks_and_process_trees(tmp_path):
    """Standard 6: within 5 s the task is cancelled and the whole process tree has ended; reopened, nothing old
    writes again."""
    async def scenario():
        async with running(tmp_path) as platform:
            await close_and_check(platform, tmp_path, stuck=False)

    run(scenario())


def test_a_stuck_module_is_restarted_so_the_workspace_still_closes(tmp_path):
    """Standard 6: the module never confirms the stop; the platform restarts it and the same holds."""
    async def scenario():
        async with running(tmp_path) as platform:
            pid = platform.process("echo").process.pid
            await close_and_check(platform, tmp_path, stuck=True)
            await platform.ready("echo")
            assert platform.process("echo").process.pid != pid

    run(scenario())


def test_stopping_the_work_ai_asked_for_leaves_the_users(tmp_path):
    """Work carries who asked for it; stopping AI's ends only that, its process tree included."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("work")
            ai = {"origin": "ai", "module": None, "workspaceId": workspace, "ai": {"client": "test"}}
            started = {}
            for who, caller in (("user", None), ("ai", ai)):
                (tmp_path / who).mkdir()
                started[who] = await platform.call("echo.start_work", {"folder": str(tmp_path / who), "stuck": False},
                                                   workspace=workspace, caller=caller)
            listed = (await platform.call("platform.activity.list", {"workspaceId": workspace}))["activities"]
            assert sorted((a["origin"], a["title"]) for a in listed) == [("ai", "Counting"), ("user", "Counting")]
            await platform.call("platform.activity.stop", {"workspaceId": workspace, "activities": [
                {"module": a["module"], "id": a["id"]} for a in listed if a["origin"] == "ai"]}, workspace=workspace)
            assert not alive(started["ai"]["child"]) and alive(started["user"]["child"])
            stopped_at = (tmp_path / "ai" / "counter.txt").read_text()
            running_at = (tmp_path / "user" / "counter.txt").read_text()
            await asyncio.sleep(0.3)
            assert (tmp_path / "ai" / "counter.txt").read_text() == stopped_at
            assert (tmp_path / "user" / "counter.txt").read_text() != running_at
            listed = (await platform.call("platform.activity.list", {"workspaceId": workspace}))["activities"]
            assert [a["origin"] for a in listed] == ["user"]

    run(scenario())


def test_copying_a_workspace_asks_every_module_to_copy_its_content(tmp_path, monkeypatch):
    """Standard 7 (platform part): each module copies what belongs to the workspace; workspace settings follow."""
    copies = tmp_path / "copies"
    copies.mkdir()
    monkeypatch.setenv("ECHO_COPIES", str(copies))

    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("original")
            await platform.call("platform.settings.write", {"module": "echo", "workspaceId": workspace,
                                                            "values": {"color": "B"}})
            with pytest.raises(ContractError, match="Save the workspace"):
                await platform.call("platform.workspace.copy", {"workspaceId": workspace})
            await platform.call("platform.workspace.save", {"name": "original", "workspace": window_state()},
                                workspace=workspace)
            copy = await platform.call("platform.workspace.copy", {"workspaceId": workspace})
            assert (copies / f"{workspace}-{copy['workspaceId']}").read_text() == "copied"
            await platform.call("platform.workspace.open", {"snapshotId": copy["snapshotId"]})
            assert (await platform.call("echo.setting", {"text": copy["workspaceId"]}))["color"] == "B"
            assert copy["name"] == "original copy"
            again = await platform.call("platform.workspace.copy", {"workspaceId": workspace})
            assert again["name"] == "original copy 2"

    run(scenario())


def test_copying_a_closed_workspace_copies_its_charts_watchlists_and_conversations(tmp_path):
    """Standard 7: the original is saved and closed; its copy is saved (not opened) with the same layout, its own
    copies of the charts, watchlists and conversations, its panels' chart ids lead to those copies, and the original
    is unchanged."""
    async def scenario():
        async with real_modules(tmp_path, "chart", "agent") as platform:
            original = await platform.workspace("Desk")
            created = await route(platform, "chart", "POST", "/charts/create", original, {"timeframe": "1h"})
            chart = created["chartId"]
            await route(platform, "chart", "POST", "/charts/save", original,
                        {"charts": [{"chartId": chart, "revision": created["revision"]}]})
            await platform.call("chart.write_watchlist", {"operation": {"op": "create", "name": "Mine", "sections": [
                {"name": "Crypto", "symbols": ["okx:BTC/USDT"]}]}}, workspace=original)
            talk = (await route(platform, "agent", "POST", "/conversation", original,
                                {"operation": "create", "value": {"title": "Plan"}}))["id"]
            layout = window_state({"id": "p1", "kind": "module:chart", "state": {"chartId": chart}},
                                  {"id": "p2", "kind": "module:agent", "state": {"conversationId": talk}})
            await platform.call("platform.workspace.save", {"name": "Desk", "workspace": layout}, workspace=original)
            await platform.call("platform.workspace.close", {"workspaceId": original})

            copy = await platform.call("platform.workspace.copy", {"workspaceId": original})
            assert copy["name"] == "Desk copy" and copy["workspaceId"] != original
            assert copy["workspaceId"] not in [w["workspaceId"] for w in
                                               await platform.call("platform.workspace.list_open")]
            assert (await platform.call("platform.workspace.load", {"snapshotId": copy["snapshotId"]}))["workspace"] \
                == layout

            ws = (await platform.call("platform.workspace.open", {"snapshotId": copy["snapshotId"]}))["workspaceId"]
            assert ws == copy["workspaceId"]
            charts = (await platform.call("chart.read_chart", {"view": "list"}, workspace=ws))["charts"]
            assert len(charts) == 1 and charts[0]["chartId"] != chart and charts[0]["timeframe"] == "1h"
            adopted = await route(platform, "chart", "POST", "/charts/adopt", ws, {"chartIds": [chart]})
            assert adopted["charts"] == {chart: charts[0]["chartId"]}
            lists = (await platform.call("chart.read_watchlist", {"view": "list"}, workspace=ws))["watchlists"]
            assert [item["name"] for item in lists] == ["Mine"]
            [conversation] = (await route(platform, "agent", "POST", "/conversation", ws, {"operation": "list"}))["items"]
            assert conversation["title"] == "Plan" and conversation["id"] != talk and conversation["copiedFrom"] == talk

            await platform.call("platform.workspace.open", {"workspaceId": original, "name": "Desk"})
            own = (await platform.call("chart.read_chart", {"view": "list"}, workspace=original))["charts"]
            assert [item["chartId"] for item in own] == [chart]
            assert (await route(platform, "chart", "POST", "/charts/adopt", original, {"chartIds": [chart]}))[
                "charts"] == {chart: chart}
            own = (await platform.call("chart.read_watchlist", {"view": "list"}, workspace=original))["watchlists"]
            assert [item["name"] for item in own] == ["Mine"] and own[0]["id"] != lists[0]["id"]
            talks = (await route(platform, "agent", "POST", "/conversation", original, {"operation": "list"}))["items"]
            assert [item["id"] for item in talks] == [talk] and "copiedFrom" not in talks[0]

    run(scenario())


@pytest.mark.parametrize("saved", [True, False])
def test_delete_and_discard_clean_the_modules_and_preserve_the_bound_folder(tmp_path, saved):
    async def scenario():
        async with real_modules(tmp_path, "chart", "developer") as platform:
            ws = await platform.workspace("Delete trial")
            folder = tmp_path / "bound"
            folder.mkdir()
            keep = folder / "user-note.md"
            keep.write_text("keep", encoding="utf-8")
            await platform.call("platform.workspace.set_folder", {"workspaceId": ws, "path": str(folder)})
            await platform.call("chart.write_watchlist", {"operation": {"op": "create", "name": "Delete me"}},
                                workspace=ws)
            preview = await platform.call("platform.workspace.delete_preview", {"workspaceId": ws})
            assert "Watchlist · Delete me" in preview
            if saved:
                snapshot = await platform.call("platform.workspace.save", {"name": "Delete trial", "workspace": window_state()},
                                               workspace=ws)
                action = "platform.workspace.delete"
                args = {"snapshotId": snapshot["snapshotId"], "expectedRevision": snapshot["revision"]}
            else:
                action, args = "platform.workspace.close", {"workspaceId": ws}
            await platform.call("platform.modules.disable", {"id": "chart"})
            with pytest.raises(ContractError, match="Start Chart") as refused:
                await platform.call(action, args)
            assert refused.value.code == "unavailable" and platform.host.workspaces.is_open(ws)
            if saved:
                assert (await platform.call("platform.workspace.load", {"snapshotId": args["snapshotId"]}))["name"] == "Delete trial"
            await platform.call("platform.modules.enable", {"id": "chart"})
            await platform.ready("chart")
            await platform.call(action, args)
            assert not platform.host.workspaces.is_open(ws) and ws not in platform.host.workspaces.folders
            assert (await platform.call("platform.workspace.list"))["saved"] == []
            assert keep.read_text(encoding="utf-8") == "keep"
            await platform.call("platform.workspace.open", {"workspaceId": ws})
            lists = await platform.call("chart.read_watchlist", {"view": "list"}, workspace=ws)
            assert all(item["name"] != "Delete me" for item in lists["watchlists"])

    run(scenario())
