"""The Host's HTTP face: the window's bridge, modules' private routes, running code's calls, MCP."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
import orjson
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route as StarletteRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from talyx_sdk import wire
from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t

from kernel.router import QUEUE_SECONDS as READY_SECONDS
from kernel.router import Endpoint

from .app import Host
from .files import PLATFORM_DIRECTORY, PLATFORM_LOCALES

log = logging.getLogger("talyx.server")
# The window's frames: an envelope line, then the body (JSON), in one binary WebSocket message.
WINDOW_FRAME_LIMIT = 32 * 1024 * 1024
# The window's session and workspace on a module's socket. A module's own query parameters cannot be named so (the window
# refuses `talyx-` names), so the Host takes these two off and passes every other one on untouched.
WINDOW_SESSION, WINDOW_WORKSPACE = "talyx-session", "talyx-workspace"
SENDABLE_CLOSE = {1000, 1001, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}


class WindowEndpoint(Endpoint):
    """One window (a bridge session). Its calls are the user's, in the workspace the window shows."""

    module = None

    def __init__(self, host: Host, socket: WebSocket, session: str):
        self.host, self.socket, self.session = host, socket, session
        self.name = f"window:{session}"
        self.outgoing: asyncio.Queue[bytes | None] = asyncio.Queue()

    def deliver(self, envelope: dict, body: bytes = b"") -> None:
        self.outgoing.put_nowait(orjson.dumps(envelope) + b"\n" + body)

    def caller(self, envelope: dict) -> dict:
        """The user, in the workspace the frame names. Every module's frontend shares the window, so the Host cannot
        tell which one sent a frame: a window call is never a module's (a module frontend reaches its own backend
        through its private routes)."""
        if "from" in envelope:
            raise ContractError("permission_denied", t("A window cannot say who is calling; the Host does"))
        return {"origin": "user", "module": None,
                "workspaceId": window_workspace(self.host, self.session, envelope.get("ws")), "session": self.session}

    async def pump(self) -> None:
        while (data := await self.outgoing.get()) is not None:
            await self.socket.send_bytes(data)


def failed(error: ContractError, status: int) -> JSONResponse:
    """Every error the Host answers over HTTP has one body: ``{"error": <the error>}``."""
    return JSONResponse({"error": error.to_dict()}, status_code=status)


class HostRequests:
    """Cancel connection-owned work before Uvicorn drains connections and closes the Host lifespan."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.pending: set[asyncio.Task] = set()
        self.closing = False

    def stop(self) -> None:
        self.closing = True
        for task in self.pending:
            task.cancel()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if self.closing:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1012})
            else:
                await failed(ContractError("unavailable", t("TALYX is closing")), 503)(scope, receive, send)
            return
        task = asyncio.current_task()
        self.pending.add(task)
        started = False

        async def send_response(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_response if scope["type"] == "http" else send)
        except asyncio.CancelledError:
            # After headers, abort the stream: a truncated body must never look complete.
            if not self.closing or started:
                raise
            if scope["type"] == "websocket":
                # Uvicorn has already sent the socket's shutdown frame.
                return
            error = ContractError("unavailable", t("TALYX is closing"), execution="unknown", effects="may_have_occurred")
            await failed(error, 503)(scope, receive, send)
        finally:
            self.pending.remove(task)


def window_workspace(host: Host, session: str, claimed: str | None) -> str | None:
    """The workspace a window's call belongs to. The window says which one it shows on every call (it is the user,
    who may act in any open workspace), so no call waits for, or races, the window's binding; without one, the
    workspace the window is bound to."""
    if claimed:
        if not host.workspaces.is_open(claimed):
            raise ContractError("target_gone", t("Workspace {claimed} is not open", claimed=claimed))
        return claimed
    return host.workspaces.session_workspace(session)


def create_app(host: Host) -> FastAPI:
    ui = httpx.URL(host.config.ui_url)
    ui_origin = str(ui.copy_with(path="/", query=None, fragment=None, username=None, password=None)).rstrip("/")
    proxy = httpx.AsyncClient(timeout=None, trust_env=False)

    @asynccontextmanager
    async def lifespan(_app):
        await host.start()
        async with host.mcp.manager.run():
            yield
        await host.close()
        await proxy.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/platform/health")
    async def health():
        return {"instance": host.instance, "modules": host.modules_list()}

    @app.websocket("/ws/bridge")
    async def bridge(socket: WebSocket):
        origins = socket.headers.getlist("origin")
        desktop = host.config.desktop_token
        if origins != [ui_origin] and not (
            not origins and desktop and secrets.compare_digest(
                socket.headers.get("authorization", "").encode(), f"Bearer {desktop}".encode())
        ):
            await socket.close(code=4403)
            return
        await socket.accept()
        session = "s_" + secrets.token_hex(8)
        window = WindowEndpoint(host, socket, session)
        host.sessions[session] = window
        writer = asyncio.create_task(window.pump())
        window.deliver({"t": "welcome", "session": session, "host": host.instance})
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes") or message.get("text", "").encode()
                if len(data) > WINDOW_FRAME_LIMIT:
                    raise ContractError("too_large", t("A window frame is at most 32 MB"))
                head, _, body = data.partition(b"\n")
                envelope = orjson.loads(head)
                try:
                    host.router.receive(window, envelope, body)
                except ContractError as exc:
                    if envelope.get("t") in ("call", "sub"):
                        window.deliver({"t": "error", "id": envelope.get("id", envelope.get("sub")),
                                        "err": exc.to_dict()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            host.sessions.pop(session, None)
            host.workspaces.window_closed(session)
            host.router.endpoint_closed(window)
            window.outgoing.put_nowait(None)
            await asyncio.gather(writer, return_exceptions=True)

    async def private_target(module_id: str, request_headers) -> tuple[str, dict]:
        session = request_headers.get("x-talyx-session")
        if session not in host.sessions:
            raise ContractError("permission_denied", t("Private routes belong to an open window"))
        process = host.supervisor.processes.get(module_id)
        if process is not None and process.state == "starting":
            # Like calls (standard 5): a request to a module still starting waits for it, within the same limit.
            try:
                await asyncio.wait_for(process.ready_event.wait(), READY_SECONDS)
            except TimeoutError:
                raise ContractError("unavailable",
                                    t("{module_id} did not finish starting within {READY_SECONDS} s", module_id=module_id, READY_SECONDS=f'{READY_SECONDS:g}')) from None
        routes = (process.ready_info or {}).get("routes") if process is not None and process.state == "ready" else None
        if routes is None:
            raise ContractError("unavailable", t("{module_id} is not running or has no private routes", module_id=module_id))
        headers = {"x-talyx-route-secret": routes["secret"], "x-talyx-module": module_id,
                   "x-talyx-workspace": window_workspace(host, session, request_headers.get("x-talyx-workspace")) or ""}
        return f"127.0.0.1:{routes['port']}", headers

    @app.api_route("/m/{module_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def private_route(module_id: str, path: str, request: Request):
        try:
            address, extra = await private_target(module_id, request.headers)
        except ContractError as exc:
            return failed(exc, 403 if exc.code == "permission_denied" else 503)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")
                   and not k.lower().startswith("x-talyx-")}
        upstream = proxy.build_request(request.method, f"http://{address}/{path}", params=request.query_params.multi_items(),
                                       headers={**headers, **extra}, content=await request.body())
        response = await proxy.send(upstream, stream=True)
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers={k: v for k, v in response.headers.items()
                                          if k.lower() not in ("content-length", "transfer-encoding", "connection")},
                                 background=None)

    @app.websocket("/m/{module_id}/{path:path}")
    async def private_socket(socket: WebSocket, module_id: str, path: str):
        import websockets
        from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

        try:
            address, extra = await private_target(module_id, {
                "x-talyx-session": socket.query_params.get(WINDOW_SESSION),
                "x-talyx-workspace": socket.query_params.get(WINDOW_WORKSPACE)})
        except ContractError:
            await socket.close(code=4403)
            return
        # The module's own query parameters go on untouched, whatever their names; only the window's two are the Host's.
        query = urlencode([(k, v) for k, v in socket.query_params.multi_items() if k not in (WINDOW_SESSION, WINDOW_WORKSPACE)])
        try:
            # No per-message compression on loopback (``server_options``): the module would deflate each page of bars on its loop.
            upstream = await websockets.connect(f"ws://{address}/{path}" + (f"?{query}" if query else ""),
                                                additional_headers=extra, proxy=None, max_size=None, compression=None)
        except InvalidStatus as exc:
            # The module refused the handshake (no such socket, refused): the window hears it as a close code, 4000 + the
            # HTTP status, not as a broken connection.
            status = exc.response.status_code
            await socket.accept()
            await socket.close(code=4000 + status if 400 <= status < 1000 else 1011,
                               reason=f"{module_id} refused the connection (HTTP {status})")
            return
        except (InvalidHandshake, OSError, TimeoutError):
            await socket.accept()
            await socket.close(code=1011, reason=f"{module_id} could not be reached")
            return

        async def down():
            with contextlib.suppress(ConnectionClosed):
                async for message in upstream:
                    await (socket.send_bytes(message) if isinstance(message, bytes) else socket.send_text(message))

        async def up():
            with contextlib.suppress(ConnectionClosed):
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await upstream.send(message.get("bytes") or message.get("text"))

        async with upstream:
            await socket.accept()
            tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if tasks[0] in done:
            # The module ended it: the window gets the module's close code (one it can be sent) and reason.
            code = upstream.close_code if upstream.close_code in SENDABLE_CLOSE or 3000 <= (upstream.close_code or 0) < 5000 else 1000
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=code, reason=(upstream.close_reason or "").encode()[:120].decode(errors="ignore"))

    @app.post("/api/runs/call")
    async def run_call(request: Request):
        """A call from running code: the token says which run (module, workspace, context) it belongs to."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        try:
            caller = host.runs.caller(token)
            value = orjson.loads(await request.body())
            body = await host.internal.call_raw(caller, value["action"], value.get("input", {}))
            return Response(body, media_type="application/json")
        except ContractError as exc:
            return failed(exc, 400 if exc.execution == "not_started" else 500)

    @app.get("/api/modules/{module_id}/files/{digest}/{path:path}")
    async def module_file(module_id: str, digest: str, path: str):
        # The digest only names the version: a frontend's files imported from one another all get new addresses when
        # any of them changed, so a window never keeps an old one it had already loaded.
        item = host.catalog.modules.get(module_id)
        if item is None:
            return failed(ContractError("not_found", t("No module {module_id} is installed", module_id=module_id)), 404)
        target = (item.directory / path).resolve()
        if not target.is_relative_to(item.directory.resolve()) or not target.is_file():
            return failed(ContractError("not_found", t("{module_id} has no file {path}", module_id=module_id, path=path)), 404)
        return FileResponse(target)

    @app.get("/api/platform/files/{path:path}")
    async def platform_file(path: str):
        if path not in PLATFORM_LOCALES.values():
            return failed(ContractError("not_found", t("The platform has no file {path}", path=path)), 404)
        return FileResponse(PLATFORM_DIRECTORY / path)

    app.router.routes.append(StarletteRoute("/mcp/workspaces/{workspace_id}", host.mcp, methods=["GET", "POST",
                                                                                             "DELETE"]))
    return app


class HostServer(uvicorn.Server):
    def __init__(self, app: ASGIApp, port: int, ui_url: str):
        self.requests = HostRequests(app)
        self.ui_url = ui_url
        super().__init__(uvicorn.Config(self.requests, **server_options(port)))

    async def main_loop(self) -> None:
        # Uvicorn enters this loop only after lifespan startup and a successful listen.
        print(f"TALYX UI: {self.ui_url}", flush=True)
        await super().main_loop()

    async def shutdown(self, sockets=None) -> None:
        self.requests.stop()
        await super().shutdown(sockets)


def run(host: Host, port: int, *, stop_on_stdin_close: bool = False) -> None:
    """Serve until interrupted; the desktop also closes this process's stdin to quit TALYX."""
    host.config.port = port
    server = HostServer(create_app(host), port, host.config.ui_url)
    if stop_on_stdin_close:
        def watch() -> None:
            while sys.stdin.buffer.read(65536):
                pass
            server.should_exit = True
        threading.Thread(target=watch, name="desktop-stdin", daemon=True).start()
    server.run()


def server_options(port: int) -> dict:
    """Loopback only. No per-message compression: deflating a page of bars costs more than sending it."""
    return {"host": "127.0.0.1", "port": port, "log_level": "warning", "ws": "websockets-sansio",
            "ws_per_message_deflate": False}


__all__ = ["create_app", "run", "wire", "Path"]
