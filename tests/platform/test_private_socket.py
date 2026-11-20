"""A module's private socket through the Host: the window's session and workspace ride as talyx- parameters the Host
takes off, the module's own parameters (whatever their names) arrive untouched, the module's close reaches the window,
and a socket the module refuses closes the window's connection with a code instead of a stack trace."""

from __future__ import annotations

import json
import logging

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from tests.platform.harness import run, running
from tests.platform.test_window import Window


class Kept(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_the_modules_own_parameters_arrive_untouched_and_a_refusal_closes_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    kept = Kept()  # uvicorn's loggers do not propagate: what the Host's server logs is kept here
    for name in ("uvicorn", "talyx"):
        logging.getLogger(name).addHandler(kept)
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("shown")
            base = f"ws://127.0.0.1:{platform.port}"
            async with websockets.connect(f"{base}/ws/bridge", proxy=None, origin=platform.host.config.ui_url.rstrip("/"), max_size=None) as bridge:
                session = (await Window(bridge).start())["session"]
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{platform.port}/m/echo/keys?keys=a&keys=b",
                                                headers={"x-talyx-session": session, "x-talyx-workspace": workspace})
                    assert response.json() == ["a", "b"]
                window = f"talyx-session={session}&talyx-workspace={workspace}"
                async with websockets.connect(f"{base}/m/echo/socket?session=mine&workspace=other&panel=p1&{window}", proxy=None) as socket:
                    opened = json.loads(await socket.recv())
                    assert opened == {"query": [["session", "mine"], ["workspace", "other"], ["panel", "p1"]], "workspaceId": workspace}
                    await socket.send("hi")
                    assert await socket.recv() == "echo:hi"
                    try:
                        await socket.recv()
                        raise AssertionError("the module closed the socket")
                    except ConnectionClosed as closed:
                        assert (closed.rcvd.code, closed.rcvd.reason) == (4321, "done")

                async with websockets.connect(f"{base}/m/echo/no-such-socket?{window}", proxy=None) as refused:
                    try:
                        await refused.recv()
                        raise AssertionError("a refused socket stayed open")
                    except ConnectionClosed as closed:
                        # Starlette refuses a socket path it has no route for with HTTP 403.
                        assert (closed.rcvd.code, closed.rcvd.reason) == (4403, "echo refused the connection (HTTP 403)")
                assert not [record for record in kept.records if record.exc_info], "the refusal left a stack trace"

                # Without the window's session the Host refuses it itself (the module's own "session" does not count).
                try:
                    async with websockets.connect(f"{base}/m/echo/socket?session={session}", proxy=None) as stranger:
                        await stranger.recv()
                    raise AssertionError("a socket without the window's session was let through")
                except (ConnectionClosed, websockets.exceptions.InvalidStatus):
                    pass
    try:
        run(scenario())
    finally:
        for name in ("uvicorn", "talyx"):
            logging.getLogger(name).removeHandler(kept)
