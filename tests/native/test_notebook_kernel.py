"""A notebook kernel in the real research environment, started the way Quant starts it (its own process in a job, ``runtime.Processes``).

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.quant.backend.kernels import Kernels
from modules.quant.backend.runtime import WORKSPACE, Quant
from tests.native.environments import environment
from tests.unit.quant_runtime_fakes import ModuleProcess

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")


class Environments:
    """The holders a kernel puts on its environment."""

    def __init__(self):
        self.refs = []

    def hold(self, identity, holder):
        self.refs.append(("hold", holder))

    def release(self, identity, holder):
        self.refs.append(("release", holder))


def context(tmp_path):
    """Quant's runtime as the kernels use it: the real process service (each process in a job of its own)."""
    WORKSPACE.set("notebook-test")
    process = ModuleProcess()
    sdk = SimpleNamespace(activity=process.activity, call=AsyncMock(return_value=[]),
                          run_token=AsyncMock(return_value={"runId": "run", "url": "http://127.0.0.1:8899", "token": "test"}))
    ctx = Quant(sdk, {"dataDir": tmp_path / "data", "userDir": tmp_path / "user", "environmentsDir": tmp_path / "envs"})
    ctx.environments = Environments()
    return ctx


class Packages:
    kernels = None

    def __init__(self, interpreter):
        self.interpreter = interpreter

    async def kernel_environment(self, environment, publish=None):
        return {"interpreterPath": str(self.interpreter), "preparationId": "prep", "lockDigest": "d1", "lockPath": "lock", "pythonVersion": "3.12"}

    async def point_vscode(self, installed, records=None):
        return None


def start(tmp_path):
    interpreter = environment("research.lock", "3.12")
    ctx = context(tmp_path)
    common = SimpleNamespace(root=tmp_path / "common")
    common.root.mkdir()
    (tmp_path / "book").mkdir()
    kernels = Kernels(ctx, SimpleNamespace(root=tmp_path / "records"), common, Packages(interpreter))
    record = {"id": "nb1", "name": "Notebook", "directory": str(tmp_path / "book"), "kind": "notebook"}
    return ctx, kernels, kernels.ensure(record)


async def until(panel, test, seconds=120):
    """The panel's messages until one passes `test`; returns it with everything seen before."""
    seen = []
    async with asyncio.timeout(seconds):
        while True:
            message = await panel.queue.get()
            seen.append(message)
            if test(message):
                return message, seen


def idle(message):
    return message["type"] == "state" and message["state"] == "idle"


def test_a_kernel_runs_cells_interrupts_keeps_variables_restarts_and_stops(tmp_path):
    async def scenario():
        ctx, kernels, session = start(tmp_path)
        panel = session.attach("p1")
        other = session.attach("p2")
        await until(panel, idle)
        first_session = session.view()["sessionId"]
        # output kinds: stream, a table, an image, plotly, an error; every panel gets every message
        code = "\n".join([
            "import pandas as pd, matplotlib", "matplotlib.use('module://matplotlib_inline.backend_inline')", "import matplotlib.pyplot as plt",
            "x = 41", "print('hello')", "plt.plot([1, 2, 3]); plt.show()", "display(pd.DataFrame({'a': [1, 2]}))", "x + 1"])
        await session.execute("c1", code)
        reply, seen = await until(panel, lambda message: message["type"] == "reply")
        outputs = [item["output"] for item in seen if item["type"] == "output"]
        assert reply["status"] == "ok" and reply["cell"] == "c1" and reply["executionCount"] == 1
        assert outputs[0] == {"output_type": "stream", "name": "stdout", "text": "hello\n"}
        mimes = [sorted(item["data"]) for item in outputs if "data" in item]
        assert ["image/png", "text/plain"] in mimes and any("text/html" in item for item in mimes) and outputs[-1]["data"]["text/plain"] == "42"
        _, others = await until(other, lambda message: message["type"] == "reply")
        assert [item["type"] for item in others if item["type"] == "output"] == ["output"] * len(outputs)
        await session.execute("c2", "raise ValueError('boom')")
        reply, seen = await until(panel, lambda message: message["type"] == "reply")
        assert reply["status"] == "error" and any(item["type"] == "output" and item["output"]["ename"] == "ValueError" for item in seen)
        # a running cell stops at once; the variable stays
        await session.execute("c3", "while True: pass")
        await until(panel, lambda message: message["type"] == "state" and message["running"] and message["running"][0]["cell"] == "c3")
        view = session.view()
        assert view["busy"] and view["running"][0]["cell"] == "c3"
        await asyncio.sleep(1)
        started = time.monotonic()
        session.interrupt()
        reply, seen = await until(panel, lambda message: message["type"] == "reply", 20)
        stopped = time.monotonic() - started
        assert stopped < 1.5, stopped
        assert any(item["type"] == "output" and item["output"]["ename"] == "KeyboardInterrupt" for item in seen)
        await session.execute("c4", "print(x)")
        _, seen = await until(panel, lambda message: message["type"] == "reply")
        assert [item["output"]["text"] for item in seen if item["type"] == "output"] == ["41\n"]
        # completion and hover go to the asking panel only
        await session.ask("p1", "complete", 7, "import pandas as pd\npd.read_c", 29)
        answer, _ = await until(panel, lambda message: message["type"] == "complete_reply")
        assert answer["id"] == 7 and "pd.read_csv" in answer["matches"] or "read_csv" in answer["matches"]
        # restart: a new session id, the variable is gone, a cell that was running is interrupted
        await session.execute("c5", "import time; time.sleep(60)")
        await until(panel, lambda message: message["type"] == "state" and message["running"])
        await session.restart()
        _, seen = await until(panel, lambda message: idle(message) and message["sessionId"] != first_session, 180)
        assert any(item["type"] == "reply" and item["cell"] == "c5" and item["status"] == "interrupted" for item in seen)
        assert session.view()["sessionId"] != first_session
        await session.execute("c6", "print(x)")
        reply, _ = await until(panel, lambda message: message["type"] == "reply")
        assert reply["status"] == "error"
        # the job takes the process with it
        process = ctx.processes.running[session.lease].process
        await session.stop()
        assert process.returncode is not None and ("release", "qkernel:" + first_session) in ctx.environments.refs and kernels.find("nb1") is None
        await ctx.processes.close_all()
    asyncio.run(scenario())


def test_a_kernel_that_dies_is_reported_dead_and_can_be_restarted(tmp_path):
    async def scenario():
        ctx, kernels, session = start(tmp_path)
        panel = session.attach("p1")
        await until(panel, idle)
        await session.execute("c1", "import os; os._exit(3)")
        dead, seen = await until(panel, lambda message: message["type"] == "state" and message["state"] == "dead")
        assert any(item["type"] == "reply" and item["status"] == "interrupted" for item in seen) and "unexpectedly" in dead["message"]
        await session.restart()
        await until(panel, idle, 180)
        await session.execute("c2", "print(1)")
        reply, _ = await until(panel, lambda message: message["type"] == "reply")
        assert reply["status"] == "ok"
        await session.stop()
        await ctx.processes.close_all()
    asyncio.run(scenario())
