"""A real multi-process platform for tests: the Host in this process (with its HTTP server), each module in its own.

Modules are the test modules under ``tests/fixtures/platform`` (copied, contracts generated from their code), or
real modules from ``modules/``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from talyx_sdk.contract import build

from host.app import Host, HostConfig
from host.secrets import MemorySecrets
from host.server import create_app, server_options

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "platform"
REPO = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def prepare(root: Path, names: tuple[str, ...]) -> Path:
    """Copy test modules into ``root`` and write each one's contract from its code."""
    root.mkdir(parents=True, exist_ok=True)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name in names:
        target = root / name
        shutil.copytree(FIXTURES / name, target, dirs_exist_ok=True)
        write_contract(target)
    return root


def write_contract(directory: Path) -> dict:
    name = directory.name
    for loaded in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
        del sys.modules[loaded]
    importlib.invalidate_caches()
    contract = build(importlib.import_module(f"{name}.backend.entry").module)
    (directory / "public").mkdir(exist_ok=True)
    (directory / "public" / "contract.json").write_text(json.dumps(contract, indent=1), encoding="utf-8")
    return contract


def user(workspace: str | None) -> dict:
    return {"origin": "user", "module": None, "workspaceId": workspace, "session": None}


class Running:
    def __init__(self, host: Host, port: int):
        self.host, self.port = host, port

    async def call(self, capability: str, value=None, *, workspace: str | None = None, caller: dict | None = None):
        return await self.host.internal.call(caller or user(workspace), capability, {} if value is None else value)

    def process(self, module_id: str):
        return self.host.supervisor.processes[module_id]

    async def ready(self, *modules: str, seconds: float = 30) -> None:
        deadline = time.monotonic() + seconds
        while any(self.process(m).state != "ready" for m in modules):
            if time.monotonic() > deadline:
                states = {m: (self.process(m).state, self.process(m).problem) for m in modules}
                raise TimeoutError(f"Modules not ready: {states}")
            await asyncio.sleep(0.05)

    async def workspace(self, name: str = "test") -> str:
        return (await self.call("platform.workspace.open", {"name": name}))["workspaceId"]

    async def ai_tools(self, module: str) -> list[dict]:
        tools = []
        cursor = 0
        while cursor is not None:
            page = await self.call("platform.ai.catalog", {"module": module, "cursor": cursor})
            tools.extend(page["tools"])
            cursor = page["nextCursor"]
        return tools


@asynccontextmanager
async def running(tmp_path: Path, test_modules: tuple[str, ...] = ("echo", "consumer", "late"),
                  official_root: Path | None = None, only: set[str] | None = None, wait: tuple[str, ...] | None = None):
    root = official_root or prepare(tmp_path / "modules", test_modules)
    port = free_port()
    host = Host(HostConfig(data_dir=tmp_path / "data", user_dir=tmp_path / "user", port=port, official_root=root,
                           ui_url=f"http://127.0.0.1:{port}/",
                           secrets_store=MemorySecrets(), only=only))
    server = uvicorn.Server(uvicorn.Config(create_app(host), **server_options(port)))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        if serving.done():
            serving.result()
        await asyncio.sleep(0.02)
    platform = Running(host, port)
    try:
        await platform.ready(*(wait if wait is not None else tuple(host.supervisor.processes)))
        yield platform
    finally:
        server.should_exit = True
        await serving


def run(coroutine):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(coroutine)


@asynccontextmanager
async def real_modules(tmp_path: Path, *names: str, root: Path | None = None):
    """Real modules from ``modules/`` with the test Market (``fixtures/platform/market``), or with the Market already
    in ``root``: each module's folder is joined into the test's module root (a directory junction, removed
    afterwards), so it runs from the repository."""
    import os
    import subprocess
    root = root or prepare(tmp_path / "modules", ("market",))
    links = [root / name for name in names]
    for link in links:
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(REPO / "modules" / link.name)], check=True,
                           capture_output=True)
        else:
            link.symlink_to(REPO / "modules" / link.name, target_is_directory=True)
    try:
        async with running(tmp_path, official_root=root, wait=("market", *names)) as platform:
            yield platform
    finally:
        for link in links:
            os.unlink(link) if sys.platform != "win32" else os.rmdir(link)


def real_chart(tmp_path: Path):
    """The real Chart with the test Market."""
    return real_modules(tmp_path, "chart")


async def route(platform: Running, module: str, method: str, path: str, workspace: str, body=None):
    """A module's private route, as the Host forwards it for a window of ``workspace``."""
    import httpx
    routes = platform.process(module).ready_info["routes"]
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.request(method, f"http://127.0.0.1:{routes['port']}{path}", json=body, headers={
            "x-talyx-route-secret": routes["secret"], "x-talyx-workspace": workspace})
        assert response.status_code == 200, response.text
        return response.json()


async def until(read, done, seconds: float = 20):
    """Read again until ``done(value)``; the last value."""
    deadline = time.monotonic() + seconds
    while True:
        value = await read()
        if done(value):
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"Still not there after {seconds} s: {value}")
        await asyncio.sleep(0.1)
