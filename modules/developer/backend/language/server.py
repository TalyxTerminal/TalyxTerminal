"""One language-server process and the editor window attached to it.

The process is Developer's own child, in a job of its own (``talyx_sdk.jobs``), speaking Content-Length framed JSON
over its stdin and stdout. This class is the only client the server ever sees:
it initializes it once, answers the server's housekeeping requests (settings, workspace folders) itself, and relays the
editor's document and feature messages, so a window that reloads or takes over finds the same ready server.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from talyx_sdk import ContractError
from talyx_sdk.jobs import spawn

IDLE_SECONDS = 3.0
START_SECONDS = 60
# What the editor may ask of the server. Anything else (commands, edits to workspace folders) is refused.
CLIENT_MESSAGES = frozenset({
    "textDocument/didOpen", "textDocument/didChange", "textDocument/didClose", "textDocument/completion", "completionItem/resolve",
    "textDocument/hover", "textDocument/signatureHelp", "textDocument/definition", "textDocument/references",
    "textDocument/prepareRename", "textDocument/rename", "workspace/didChangeWatchedFiles", "$/cancelRequest"})
CLIENT_CAPABILITIES = {
    "workspace": {"configuration": True, "workspaceFolders": True, "didChangeConfiguration": {"dynamicRegistration": False}},
    "textDocument": {
        "synchronization": {"dynamicRegistration": False, "didSave": False, "willSave": False},
        "completion": {"completionItem": {"snippetSupport": False, "documentationFormat": ["markdown", "plaintext"],
                                         "resolveSupport": {"properties": ["documentation", "detail"]}}},
        "hover": {"contentFormat": ["markdown", "plaintext"]},
        "signatureHelp": {"signatureInformation": {"documentationFormat": ["markdown", "plaintext"], "parameterInformation": {"labelOffsetSupport": False}}},
        "definition": {"linkSupport": False}, "references": {}, "rename": {"prepareSupport": True},
        "publishDiagnostics": {"tagSupport": {"valueSet": [1, 2]}},
    },
}


async def read_message(stream: asyncio.StreamReader) -> dict | None:
    """One framed message from the server, or None once it ended."""
    length = None
    while True:
        header = await stream.readline()
        if not header:
            return None
        header = header.strip()
        if not header:
            if length is not None:
                return json.loads(await stream.readexactly(length))
            continue
        name, _, value = header.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())


def folder_name(uri: str) -> str:
    return Path(unquote(urlparse(uri).path)).name or uri


class Connection:
    """The editor window's WebSocket."""

    def __init__(self, socket):
        self.socket = socket
        self.documents: set[str] = set()
        self.open = True

    async def send(self, message: dict) -> None:
        if not self.open:
            return
        try:
            await self.socket.send_text(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            self.open = False

    async def close(self, code: int, reason: str) -> None:
        self.open = False
        with contextlib.suppress(Exception):
            await self.socket.close(code, reason)


class LanguageServer:
    def __init__(self, *, key: str, interpreter: str, stamp: str, command: list[str], directory: Path, environment: dict, idle: float = IDLE_SECONDS):
        self.key, self.interpreter, self.stamp = key, interpreter, stamp
        self.command, self.directory, self.environment, self.idle = command, directory, environment, idle
        self.roots: dict[str, dict] = {}
        self.announced: set[str] = set()
        self.state = "new"
        self.ready = asyncio.Event()
        self.capabilities: dict | None = None
        self.connection: Connection | None = None
        self.on_end: Callable[[LanguageServer], None] | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.job = None
        self.reader: asyncio.Task | None = None
        self.starting: asyncio.Task | None = None
        self.idle_task: asyncio.Task | None = None
        self.sequence = 0
        self.pending: dict[int, asyncio.Future | tuple] = {}
        self.owners: dict[str, Connection] = {}
        self.lock = asyncio.Lock()

    # ---- settings the server asks for ----
    def settings(self, root: str | None, section: str):
        value = (root and self.roots.get(root)) or next(iter(self.roots.values()), None) or {"paths": [], "declared": False, "mode": "standard"}
        analysis = {"extraPaths": value["paths"], "autoSearchPaths": not value["declared"], "diagnosticMode": "openFilesOnly",
                    "useLibraryCodeForTypes": True, "typeCheckingMode": value["mode"]}
        if section == "python":
            return {"pythonPath": self.interpreter}
        if section in {"python.analysis", "basedpyright.analysis"}:
            return analysis
        if section == "basedpyright":
            return {"analysis": analysis}
        return None

    async def set_root(self, uri: str, value: dict) -> None:
        """A folder whose documents this server analyses, with the import folders its support declared."""
        known = self.roots.get(uri)
        self.roots[uri] = value
        if self.state == "ready":
            if uri not in self.announced:
                await self._announce()
            elif known != value:
                await self.notify("workspace/didChangeConfiguration", {"settings": None})

    async def _announce(self) -> None:
        added = [{"uri": uri, "name": folder_name(uri)} for uri in self.roots if uri not in self.announced]
        if added:
            self.announced.update(folder["uri"] for folder in added)
            await self.notify("workspace/didChangeWorkspaceFolders", {"event": {"added": added, "removed": []}})

    # ---- process ----
    async def start(self) -> None:
        if self.state == "stopped":
            raise ContractError("unavailable", "The Python language service stopped.")
        if self.starting is None:
            self.starting = asyncio.create_task(self._launch())
        await asyncio.shield(self.starting)

    async def _launch(self) -> None:
        self.state = "starting"
        try:
            # The server is stopped by killing it, so it never removes its own temporary files: keep them in its
            # directory, which stop() deletes and the service clears on start.
            temporary = self.directory / "tmp"
            temporary.mkdir(parents=True, exist_ok=True)
            with open(self.directory / "stderr.log", "ab") as errors:
                self.process, self.job = await spawn(
                    self.command, cwd=str(self.directory), env={**self.environment, "TEMP": str(temporary), "TMP": str(temporary)},
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=errors, limit=2 ** 26)
            self.reader = asyncio.create_task(self._read())
            folders = [{"uri": uri, "name": folder_name(uri)} for uri in self.roots]
            self.announced = set(self.roots)
            async with asyncio.timeout(START_SECONDS):
                result = await self.request("initialize", {"processId": None, "rootUri": None, "workspaceFolders": folders,
                                                           "capabilities": CLIENT_CAPABILITIES, "clientInfo": {"name": "TALYX Developer"}})
            self.capabilities = result["capabilities"]
            await self.notify("initialized", {})
            self.state = "ready"
            await self._announce()
            self.ready.set()
        except BaseException as error:
            await self.stop()
            if isinstance(error, (asyncio.CancelledError, ContractError)):
                raise
            raise ContractError("unavailable", "The Python language service could not start.", {"reason": str(error)}) from error

    async def stop(self) -> None:
        if self.state == "stopped":
            return
        self.state = "stopped"
        self.ready.set()
        if self.idle_task is not None and self.idle_task is not asyncio.current_task():
            self.idle_task.cancel()
        reader, self.reader = self.reader, None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
        if self.job is not None:
            self.job.terminate()
            await self.job.ended()
            self.job.close()
        for waiting in self.pending.values():
            if isinstance(waiting, asyncio.Future) and not waiting.done():
                waiting.set_exception(ContractError("unavailable", "The Python language service stopped."))
        self.pending.clear()
        connection, self.connection = self.connection, None
        if connection is not None:
            await connection.close(4002, "The language service stopped")
        await asyncio.to_thread(shutil.rmtree, self.directory, True)
        if self.on_end is not None:
            self.on_end(self)

    async def _read(self) -> None:
        try:
            while (message := await read_message(self.process.stdout)) is not None:
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except (ContractError, ValueError, ConnectionError):
            pass
        await self.stop()

    # ---- messages ----
    async def _write(self, message: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        async with self.lock:
            try:
                self.process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
                await self.process.stdin.drain()
            except (ConnectionError, OSError) as error:
                raise ContractError("unavailable", "The Python language service stopped.") from error

    async def notify(self, method: str, params) -> None:
        await self._write({"method": method, "params": params})

    async def request(self, method: str, params):
        """The server's answer to a request this class makes for itself."""
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        await self._write({"id": self.sequence, "method": method, "params": params})
        return await future

    async def _dispatch(self, message: dict) -> None:
        method, ident = message.get("method"), message.get("id")
        if method is not None and ident is not None:
            await self._write({"id": ident, **self._answer(message)})
        elif method is not None:
            if method == "textDocument/publishDiagnostics" and self.connection is not None:
                await self.connection.send(message)
        elif ident in self.pending:
            waiting = self.pending.pop(ident)
            if isinstance(waiting, asyncio.Future):
                if "error" in message:
                    waiting.set_exception(ContractError("unavailable", message["error"].get("message", "The language server refused the request.")))
                else:
                    waiting.set_result(message.get("result"))
            else:
                connection, original = waiting
                await connection.send({**message, "id": original})

    def _answer(self, message: dict) -> dict:
        """What this client replies to the server's own requests."""
        method, params = message["method"], message.get("params") or {}
        if method == "workspace/configuration":
            return {"result": [self.settings(item.get("scopeUri"), item.get("section")) for item in params.get("items", [])]}
        if method == "workspace/workspaceFolders":
            return {"result": [{"uri": uri, "name": folder_name(uri)} for uri in self.announced]}
        if method == "workspace/applyEdit":
            return {"result": {"applied": False}}
        if method in {"client/registerCapability", "client/unregisterCapability", "window/workDoneProgress/create", "window/showMessageRequest",
                      "workspace/diagnostic/refresh", "workspace/semanticTokens/refresh", "workspace/inlayHint/refresh", "workspace/codeLens/refresh"}:
            return {"result": None}
        return {"error": {"code": -32601, "message": f"{method} is not supported"}}

    # ---- editor window ----
    async def attach(self, connection: Connection) -> None:
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None
        previous, self.connection = self.connection, connection
        if previous is not None:
            await self._release(previous)
            await previous.close(4001, "Another window took over this language service")

    async def detach(self, connection: Connection) -> None:
        if self.connection is not connection:
            return
        await self._release(connection)
        self.connection = None
        if self.state != "stopped":
            self.idle_task = asyncio.create_task(self._idle())

    async def _idle(self) -> None:
        await asyncio.sleep(self.idle)
        if self.connection is None:
            await self.stop()

    async def _release(self, connection: Connection) -> None:
        """Documents the window had open are closed on the server; its unanswered requests are dropped."""
        if self.state == "ready":
            for uri in tuple(connection.documents):
                with contextlib.suppress(ContractError):
                    await self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        for uri in connection.documents:
            self.owners.pop(uri, None)
        connection.documents.clear()
        for ident in [ident for ident, waiting in self.pending.items() if isinstance(waiting, tuple) and waiting[0] is connection]:
            del self.pending[ident]

    async def _settled(self) -> None:
        """Wait until the server is ready; a server that stopped meanwhile is an error, not a wait."""
        await self.ready.wait()
        if self.state != "ready":
            raise ContractError("unavailable", "The Python language service stopped.")

    async def from_client(self, connection: Connection, message: dict) -> None:
        method, ident = message.get("method"), message.get("id")
        if method is None:
            return
        if method == "initialize":
            await self._settled()
            await connection.send({"jsonrpc": "2.0", "id": ident, "result": {"capabilities": self.capabilities}})
        elif method == "shutdown":
            await connection.send({"jsonrpc": "2.0", "id": ident, "result": None})
        elif method in {"initialized", "exit"}:
            return
        elif method not in CLIENT_MESSAGES:
            if ident is not None:
                await connection.send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"{method} is not supported"}})
        else:
            await self._settled()
            await self._forward(connection, message)

    async def _forward(self, connection: Connection, message: dict) -> None:
        method, params = message["method"], message.get("params") or {}
        if method == "textDocument/didOpen":
            uri = params["textDocument"]["uri"]
            if uri in self.owners:
                await self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            self.owners[uri] = connection
            connection.documents.add(uri)
        elif method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.owners.pop(uri, None)
            connection.documents.discard(uri)
        elif method == "$/cancelRequest":
            ours = next((ident for ident, waiting in self.pending.items() if isinstance(waiting, tuple) and waiting[0] is connection and waiting[1] == params.get("id")), None)
            if ours is None:
                return
            message = {**message, "params": {**params, "id": ours}}
        if message.get("id") is not None:
            self.sequence += 1
            self.pending[self.sequence] = (connection, message["id"])
            message = {**message, "id": self.sequence}
        await self._write({key: value for key, value in message.items() if key != "jsonrpc"})
