"""A test module: every kind of declaration, and ways to misbehave."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from talyx_sdk import wire
from talyx_sdk.declare import Model, Module
from talyx_sdk.jobs import spawn
from talyx_sdk.process import Call, Context

module = Module("echo")
module.setting("color", str, "A", title="Color")
module.setting("apiKey", str, "", title="Key", global_only=True)
module.event("pinged", dict[str, Any], "Someone pinged")
CTX: Context | None = None
STARTED = time.time()


class Text(Model):
    text: str


class Number(Model):
    value: float


class Big(Model):
    value: int


class Empty(Model):
    pass


class Seconds(Model):
    seconds: float


class Page(Model):
    rows: int


class Columns(Model):
    time: list[str]
    open: list[str]
    high: list[str]
    low: list[str]
    close: list[str]
    volume: list[str]


class PageOut(Model):
    columns: Columns


class Tick(Model):
    n: int
    sentAt: float
    """Milliseconds since the epoch."""


class TicksParams(Model):
    every: float = 0.02


class Work(Model):
    folder: str
    stuck: bool = False


class WorkOut(Model):
    activity: str
    child: int


class RunIn(Model):
    folder: str


@module.on_start
async def start(ctx: Context) -> None:
    global CTX
    CTX = ctx


@module.action(effects=["read"])
async def echo(input: Text, call: Call) -> Text:
    """Answer with the input."""
    return {"text": input.text}


@module.action(effects=["read"])
async def whoami(input: Empty, call: Call) -> dict[str, Any]:
    """Who called, as the platform stamped it."""
    caller = call.caller
    return {"origin": caller.origin, "module": caller.module, "workspaceId": caller.workspace_id, "ai": caller.ai,
            "run": caller.run, "pid": os.getpid(), "started": STARTED}


@module.action(effects=["read"])
async def slow(input: Seconds, call: Call) -> Text:
    """Answer after a while."""
    await asyncio.sleep(input.seconds)
    return {"text": "done"}


@module.action(effects=["read"])
async def not_finite(input: Empty, call: Call) -> Number:
    """A NaN where a number is declared."""
    return {"value": float("nan")}


@module.action(effects=["read"])
async def infinite_untyped(input: Empty, call: Call) -> dict[str, Any]:
    """An infinity inside untyped JSON."""
    return {"nested": [{"value": float("inf")}]}


@module.action(effects=["read"])
async def big_integer(input: Empty, call: Call) -> Big:
    """An integer beyond 2^53."""
    return {"value": 2 ** 60}


@module.action(effects=["read"])
async def page(input: Page, call: Call) -> PageOut:
    """A page of bars by column, like Market's (decimal strings). Built once per size: what is measured is the
    platform carrying it, not this fixture formatting strings."""
    if input.rows not in PAGES:
        n = input.rows
        base = 1_600_000_000_000_000_000
        PAGES[input.rows] = {"columns": {"time": [str(base + i * 86_400_000_000_000) for i in range(n)],
                                         "open": [f"{100 + i % 50}.{i % 97:02d}" for i in range(n)],
                                         "high": [f"{101 + i % 50}.{i % 89:02d}" for i in range(n)],
                                         "low": [f"{99 + i % 50}.{i % 83:02d}" for i in range(n)],
                                         "close": [f"{100 + i % 50}.{i % 79:02d}" for i in range(n)],
                                         "volume": [f"{1000 + i * 7}.{i % 13}" for i in range(n)]}}
    return PAGES[input.rows]


PAGES: dict[int, dict] = {}


@module.action(effects=["read"])
async def long_answer(input: Page, call: Call) -> Text:
    """A long text."""
    return {"text": "x" * input.rows}


WRITTEN_ROWS = []


@module.action(effects=["write"])
async def write_long_answer(input: Page, call: Call) -> Text:
    """Write the row count and return a large receipt."""
    WRITTEN_ROWS.append(input.rows)
    return {"text": "文" * input.rows}


@module.action(effects=["write"])
async def crash(input: Empty, call: Call) -> Empty:
    """The process ends at once."""
    os._exit(9)


@module.action(effects=["write"], task=True)
async def count(input: Seconds, call: Call) -> Text:
    """A long task that reports progress."""
    for i in range(int(input.seconds * 10)):
        call.progress({"done": i})
        await asyncio.sleep(0.1)
    return {"text": "counted"}


@module.action(effects=["write"], task=True)
async def count_in_background(input: Seconds, call: Call) -> Text:
    """A long task whose counting runs as a background task of its own and registers itself, as a download does.
    The description is long and written for AI, so it is not what anyone reads in the list of running work."""
    worker = asyncio.create_task(asyncio.sleep(input.seconds))
    CTX.activity(call.workspace_id, "Counting").add_task(worker)
    await asyncio.shield(worker)
    return {"text": "counted"}


@module.action(effects=["read"])
async def impersonate(input: Text, call: Call) -> dict[str, Any]:
    """Try to call as someone else, or for a workspace that does not exist."""
    process = CTX.process
    results = {}
    attempts = {
        "as_user": {"t": "call", "cap": "echo.whoami", "from": {"origin": "user", "module": "platform"}},
        "as_ai": {"t": "call", "cap": "echo.whoami", "from": {"origin": "ai", "module": "agent", "workspaceId": input.text}},
        "unknown_workspace": {"t": "call", "cap": "echo.whoami", "ws": "no-such-workspace"},
        "own_workspace": {"t": "call", "cap": "echo.whoami", "ws": input.text},
    }
    for name, envelope in attempts.items():
        try:
            results[name] = await process.request(dict(envelope), wire.encode({}))
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": getattr(exc, "code", repr(exc))}
    return results


@module.stream()
async def ticks(params: TicksParams, call: Call) -> AsyncIterator[Tick]:
    """A tick every so often, with when it was sent."""
    n = 0
    while True:
        n += 1
        yield {"n": n, "sentAt": time.time() * 1000}
        await asyncio.sleep(params.every)


CHILD = ("import time, sys\n"
         "while True: time.sleep(0.2)\n")


@module.action(effects=["write"])
async def start_work(input: Work, call: Call) -> WorkOut:
    """Background work of the caller's workspace: a task writing a counter, and a child process (with a child of
    its own) that runs until stopped."""
    activity = CTX.activity(call.workspace_id, "Counting")
    path = Path(input.folder) / "counter.txt"

    async def counter():
        n = 0
        while True:
            n += 1
            path.write_text(str(n))
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                if input.stuck:
                    time.sleep(60)   # never answers the stop: the platform must restart the process
                raise

    activity.add_task(asyncio.create_task(counter()))
    tree = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {CHILD!r}])\nwhile True: time.sleep(0.2)\n"
    process, job = await spawn([sys.executable, "-c", tree])
    activity.add_job(job)
    return {"activity": activity.id, "child": process.pid}


@module.action(effects=["code"])
async def run_code(input: RunIn, call: Call) -> dict[str, Any]:
    """Start code with a run token; the code calls echo.whoami back through the platform."""
    token = await CTX.run_token(call.workspace_id, {"note": "from echo", "n": 7})
    script = ("import json, os, urllib.request\n"
              "request = urllib.request.Request(os.environ['TALYX_CALL_URL'], method='POST',"
              " data=json.dumps({'action': 'echo.whoami', 'input': {}}).encode(),"
              " headers={'authorization': 'Bearer ' + os.environ['TALYX_RUN_TOKEN']})\n"
              "print(urllib.request.urlopen(request).read().decode())\n")
    process, _job = await spawn([sys.executable, "-c", script], stdout=asyncio.subprocess.PIPE,
                                env={**os.environ, "TALYX_CALL_URL": token["url"], "TALYX_RUN_TOKEN": token["token"]})
    output, _ = await process.communicate()
    calls = await CTX.call("platform.runs.calls", {"runId": token["runId"]})
    return {"answer": output.decode(), "calls": calls, "runId": token["runId"]}


@module.action(effects=["read"])
async def setting(input: Text, call: Call) -> dict[str, Any]:
    """This module's color in a workspace (or "global")."""
    return {"color": await CTX.platform.setting("color", workspace=input.text)}


@module.on_copy_workspace
async def copy(source: str, copy: str) -> None:
    (Path(os.environ["ECHO_COPIES"]) / f"{source}-{copy}").write_text("copied")


class Greeting(Model):
    name: str


class Greeted(Model):
    text: str


module.extension_point("greeter", description="Says hello to someone.", descriptor=dict[str, Any],
                       actions={"greet": (Greeting, Greeted, "Greet someone by name.")})


async def own_greeting(input: dict, call) -> dict:
    return {"text": f"Hi {input['name']}"}


module.implement("echo.greeter", "own", actions={"greet": own_greeting})


@module.action(effects=["read"])
async def greet_all(input: Empty, call: Call) -> dict[str, Any]:
    """Ask every greeter, and once with a name that is not text: each answer, or the error code."""
    from talyx_sdk.errors import ContractError
    results = {}
    for item in await CTX.extensions("greeter"):
        try:
            results[f"{item['moduleId']}:{item['id']}"] = await CTX.call_extension(
                "greeter", item["moduleId"], item["id"], "greet", {"name": "Ann"})
        except ContractError as exc:
            results[f"{item['moduleId']}:{item['id']}"] = {"error": exc.code}
    try:
        await CTX.call_extension("greeter", "consumer", "polite", "greet", {"name": 5})
    except ContractError as exc:
        results["not text"] = {"error": exc.code}
    return results


@module.private_routes
def routes(ctx):
    """A route for echo's own frontend: what the Host says about the window's request."""
    app = FastAPI()

    @app.get("/hello")
    async def hello(request: Request) -> dict:
        return {"workspaceId": request.headers.get("x-talyx-workspace") or None}

    @app.get("/keys")
    async def keys(request: Request) -> list[str]:
        return request.query_params.getlist("keys")

    @app.websocket("/socket")
    async def socket(socket: WebSocket) -> None:
        """Says the query and workspace it was opened with, echoes one message, then closes with code 4321."""
        await socket.accept()
        await socket.send_json({"query": list(socket.query_params.multi_items()),
                                "workspaceId": socket.headers.get("x-talyx-workspace") or None})
        await socket.send_text("echo:" + await socket.receive_text())
        await socket.close(code=4321, reason="done")

    return app
