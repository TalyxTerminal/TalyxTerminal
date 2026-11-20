"""Training that Quant resumes after a restart belongs to the workspace it was started in: it is that workspace's
background work (closing the workspace stops it), and a run whose workspace is not open waits until it opens."""
from __future__ import annotations

import asyncio
import contextvars

from modules.quant.backend import runtime
from modules.quant.backend.entry import follow_workspaces, open_workspaces
from tests.unit.quant_model_fakes import model_world, wait_for


def in_workspace(workspace, function, *args):
    context = contextvars.copy_context()
    context.run(runtime.WORKSPACE.set, workspace)
    return context.run(function, *args)


def test_resumed_training_is_its_workspace_work_and_waits_for_it_to_open(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=200)
        models, process = world.models, world.ctx.sdk.process
        runs = {}
        for workspace in ("A", "B"):
            identity = await in_workspace(workspace, asyncio.ensure_future, world.make(f"Model {workspace}"))
            started = await in_workspace(workspace, asyncio.ensure_future, models.train(identity))
            runs[workspace] = started["runs"][0]["id"]
        await wait_for(lambda: all(len(models.read(run).get("windows") or []) >= 1 for run in runs.values()))
        # Quant is killed: the runs stay "running" in the records, with no task.
        for task in list(models.tasks.values()):
            task.cancel()
        await asyncio.gather(*list(models.tasks.values()), return_exceptions=True)
        for run in runs.values():
            models.save(models.read(run), state="running", stopped=None)
        models.tasks.clear()
        process.activities.clear()

        def owner(run):
            return next((a.workspace_id for a in process.activities.values() if models.tasks.get(run) in a.tasks), None)

        feed: asyncio.Queue = asyncio.Queue()

        async def changes():
            while True:
                yield await feed.get()

        async def subscribe(name):
            assert name == "platform.workspace.changes"
            return changes()

        async def listed():
            return ["A"]
        world.ctx.sdk.subscribe = subscribe
        world.ctx.sdk.platform = type("Platform", (), {"open_workspaces": staticmethod(listed)})

        following = asyncio.ensure_future(follow_workspaces(models, await open_workspaces(world.ctx.sdk, models)))
        assert runs["A"] in models.tasks and runs["B"] not in models.tasks, "B is not open: its run waits"
        assert owner(runs["A"]) == "A"

        feed.put_nowait({"kind": "opened", "workspaceId": "B"})
        await wait_for(lambda: runs["B"] in models.tasks)
        assert owner(runs["B"]) == "B"

        await process.stop_workspace("A")
        await wait_for(lambda: models.read(runs["A"])["state"] == "interrupted")
        assert runs["B"] in models.tasks and models.read(runs["B"])["state"] in ("queued", "preparing", "running")

        following.cancel()
        for task in list(models.tasks.values()):
            task.cancel()
        await asyncio.gather(following, *list(models.tasks.values()), return_exceptions=True)

    asyncio.run(scenario())
