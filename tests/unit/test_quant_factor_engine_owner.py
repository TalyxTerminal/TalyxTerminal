"""Quant's open factor process is Quant's own: it is not the background work of the workspace whose run started it.
Closing that workspace leaves another workspace's computation alone; the closed workspace's own computation stops
within the platform's five seconds, and the runs waiting behind it go on."""

from __future__ import annotations

import asyncio
import contextvars
import time
from types import SimpleNamespace

from modules.quant.backend import runtime
from modules.quant.backend.factor_engine import FactorEngine
from tests.unit.quant_runtime_fakes import adapt


class Resident:
    """The open process, simulated: one job at a time, each answered after its ``seconds``; cancelling ends it."""

    def __init__(self):
        self.started, self.inbox = 0, {}

    async def start(self, launch):
        self.started += 1
        lease = f"lease-{self.started}"
        self.inbox[lease] = asyncio.Queue()
        self.inbox[lease].put_nowait({"state": "message", "message": {"talyx": "factor", "ready": True}})
        return lease

    async def receive(self, args):
        try:
            return await asyncio.wait_for(self.inbox[args["lease"]].get(), args["timeout_ms"] / 1000)
        except TimeoutError:
            return {"state": "timeout"}

    async def send(self, args):
        lease, message = args["lease"], args["message"]

        async def answer():
            await asyncio.sleep(message["job"]["seconds"])
            self.inbox[lease].put_nowait({"state": "message", "message": {
                "talyx": "factor", "id": message["id"], "ok": True, "name": message["job"]["name"]}})
        asyncio.get_running_loop().create_task(answer())

    async def cancel(self, args):
        self.inbox[args["lease"]].put_nowait({"state": "closed"})


def test_closing_a_workspace_leaves_the_open_process_to_the_others_and_stops_its_own_run(tmp_path):
    async def scenario():
        resident = Resident()

        async def environment(_publish):
            return {"interpreterPath": "python", "preparationId": "p1"}

        ctx = adapt(SimpleNamespace(platform=SimpleNamespace(execution=resident)), tmp_path)
        engine = FactorEngine(ctx, SimpleNamespace(research_environment=environment), tmp_path / "engines")

        def run(workspace, seconds, name) -> asyncio.Task:
            context = contextvars.copy_context()
            context.run(runtime.WORKSPACE.set, workspace)
            return context.run(ctx.start, engine.run_open({"seconds": seconds, "name": name}))

        assert (await run("A", 0.05, "a1"))["name"] == "a1"      # A's run started the open process
        b1 = run("B", 1.0, "b1")
        await asyncio.sleep(0.2)
        await ctx.sdk.process.stop_workspace("A")
        assert (await b1)["name"] == "b1" and resident.started == 1

        a2 = run("A", 30, "a2")
        await asyncio.sleep(0.2)
        b2 = run("B", 0.05, "b2")                                   # waits behind A's
        await asyncio.sleep(0.2)
        stopping = time.monotonic()
        await ctx.sdk.process.stop_workspace("A")
        assert time.monotonic() - stopping < 5 and a2.cancelled()
        assert (await asyncio.wait_for(b2, 10))["name"] == "b2" and resident.started == 2
        await engine.close()

    asyncio.run(scenario())
