"""Checkup item 10: every error the Host and a module's private routes answer over HTTP has one body,
``{"error": {code, message, execution, effects}}``, as calls' errors do."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from websockets.asyncio.client import connect

from host.files import finish_before_cancel, write_atomic
from host.server import HostRequests, HostServer
from tests.platform.harness import free_port, run, running


def test_every_http_error_has_the_one_error_body(tmp_path):
    async def scenario():
        async with running(tmp_path) as platform:
            await platform.workspace("open")
            base = f"http://127.0.0.1:{platform.port}"
            routes = platform.process("echo").ready_info["routes"]
            async with httpx.AsyncClient(timeout=30) as client:
                answers = {
                    "private route without a window": await client.get(f"{base}/m/echo/hello"),
                    "missing module file": await client.get(f"{base}/api/modules/nobody/files/a.js"),
                    "missing file": await client.get(f"{base}/api/modules/echo/files/none.js"),
                    "running code without a run": await client.post(f"{base}/api/runs/call",
                                                                    json={"action": "echo.whoami"}),
                    "MCP of a closed workspace": await client.post(f"{base}/mcp/workspaces/closed", json={}),
                    "module route without the Host": await client.get(f"http://127.0.0.1:{routes['port']}/hello"),
                }
            for case, response in answers.items():
                assert response.status_code >= 400, case
                error = response.json()["error"]
                assert {"code", "message", "execution", "effects"} <= set(error), (case, error)

    run(scenario())


def test_quit_cancels_pending_and_streaming_reads_before_lifespan_closes():
    async def scenario():
        pending, release = asyncio.Event(), asyncio.Event()
        finished = []

        @asynccontextmanager
        async def lifespan(_):
            yield
            finished.append("lifespan")

        app = FastAPI(lifespan=lifespan)

        @app.get("/data")
        async def read():
            pending.set()
            try:
                await release.wait()
            finally:
                finished.append("read")

        @app.get("/stream")
        async def stream():
            async def chunks():
                try:
                    yield b"first"
                    await release.wait()
                    yield b"last"
                finally:
                    finished.append("stream")
            return StreamingResponse(chunks())

        @app.websocket("/feed")
        async def feed(socket: WebSocket):
            await socket.accept()
            try:
                await release.wait()
            finally:
                finished.append("feed")

        port = free_port()
        url = f"http://127.0.0.1:{port}"
        server = HostServer(app, port, url)
        serving = asyncio.create_task(server.serve())
        try:
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
            async with (
                httpx.AsyncClient(base_url=url, trust_env=False) as client,
                connect(f"ws://127.0.0.1:{port}/feed"),
                asyncio.TaskGroup() as clients,
            ):
                reading = clients.create_task(client.get("/data"))
                await asyncio.wait_for(pending.wait(), 5)
                async with client.stream("GET", "/stream") as response:
                    body = response.aiter_bytes()
                    assert await anext(body) == b"first"
                    server.should_exit = True
                    await asyncio.wait_for(asyncio.shield(serving), 3)
                    reply = await reading
                    assert reply.status_code == 503
                    assert reply.json()["error"]["effects"] == "may_have_occurred"
                    with pytest.raises(httpx.RemoteProtocolError):
                        await anext(body)
            assert sorted(finished[:-1]) == ["feed", "read", "stream"] and finished[-1] == "lifespan"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(server.requests), base_url=url) as client:
                rejected = await client.get("/data")
                assert rejected.status_code == 503
                assert rejected.json()["error"]["execution"] == "not_started"
        finally:
            release.set()
            server.should_exit = True
            await serving

    run(scenario())


def test_quit_lets_an_already_started_atomic_commit_finish(tmp_path):
    async def scenario():
        started, publish = asyncio.Event(), asyncio.Event()
        target = tmp_path / "saved"
        write_atomic(target, b"previous")
        app = FastAPI()

        @app.post("/save")
        async def save():
            async def commit():
                started.set()
                await publish.wait()
                await asyncio.to_thread(write_atomic, target, b"committed")

            await finish_before_cancel(commit())

        requests = HostRequests(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(requests), base_url="http://test") as client:
            saving = asyncio.create_task(client.post("/save"))
            await asyncio.wait_for(started.wait(), 5)
            requests.stop()
            assert target.read_bytes() == b"previous"
            publish.set()
            reply = await saving
        assert reply.status_code == 503
        assert reply.json()["error"]["effects"] == "may_have_occurred"
        assert target.read_bytes() == b"committed"

    run(scenario())
