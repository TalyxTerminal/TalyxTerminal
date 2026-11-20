"""Which language server a document uses, and the routes the editor reaches it through.

A document gets Python language features when a development support that applies to it declares the `language`
capability and names a ready Python (see `environment.py`). Documents of one environment share one server process,
started when an editor first connects and stopped a moment after the last editor of it goes away. Developer knows
nothing of what the environment is for: the support names it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from talyx_sdk import ContractError

from ..projects.service import canonical_uri, file_path
from .environment import environment_key, language_spec, packages_stamp
from .server import Connection, LanguageServer

ENVIRONMENT_VARIABLES = ("SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH")
NONE = {"session": None}


def server_command() -> list[str]:
    program = Path(sys.executable).parent / ("basedpyright-langserver.exe" if os.name == "nt" else "basedpyright-langserver")
    if not program.is_file():
        raise ContractError("unavailable", "The Python language service (basedpyright) is not installed.")
    return [str(program), "--stdio"]


def server_environment() -> dict[str, str]:
    """No inherited Python settings: the server learns its Python from the support, not from this process."""
    return {**{name: os.environ[name] for name in ENVIRONMENT_VARIABLES if name in os.environ}, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}


class LanguageService:
    def __init__(self, documents, supports, storage: Path, *, command=server_command, idle: float | None = None):
        self.documents, self.supports = documents, supports
        self.command, self.idle = command, idle
        self.directory = Path(storage) / "language"
        shutil.rmtree(self.directory, ignore_errors=True)
        self.servers: dict[str, LanguageServer] = {}

    async def resolve(self, document_id: str) -> dict:
        """The session an editor of this document connects to, or none when no support gives it a Python."""
        record = self.documents.record(document_id)
        uri = record["uri"]
        if record["language"] != "python" or not uri.startswith("file:"):
            return NONE
        spec = None
        for support in self.supports.applicable(document_id):
            if "language" not in support["descriptor"].get("capabilities", []):
                continue
            try:
                spec = language_spec(await self.supports.ask(support, "language", "environment", document_id))
            except ContractError:
                continue
            if spec is not None:
                break
        if spec is None:
            return NONE
        key, stamp = environment_key(spec), packages_stamp(spec)
        server = self.servers.get(key)
        if server is not None and server.stamp != stamp:
            # Packages were installed or removed: the editors reconnect to a server that sees them.
            await server.stop()
            server = None
        if server is None:
            server = self.servers[key] = LanguageServer(
                key=key, interpreter=spec["interpreter"], stamp=stamp, command=self.command(),
                directory=self.directory / f"{key}-{uuid.uuid4().hex[:8]}", environment=server_environment(),
                **({"idle": self.idle} if self.idle is not None else {}))
            server.on_end = self.ended
        root = record["context"]["rootUri"] or canonical_uri(file_path(uri).parent)
        await server.set_root(root, {"paths": spec["paths"], "declared": spec["declared"], "mode": spec["mode"]})
        return {"session": key, "uri": uri, "rootUri": root}

    def ended(self, server: LanguageServer) -> None:
        if self.servers.get(server.key) is server:
            del self.servers[server.key]

    async def close(self) -> None:
        for server in tuple(self.servers.values()):
            await server.stop()

    async def serve(self, websocket: WebSocket, session: str) -> None:
        """One editor window's connection to the server of ``session`` (what ``resolve`` answered)."""
        await websocket.accept()
        connection, server = Connection(websocket), self.servers.get(session)
        if server is None:
            await connection.close(4004, "Resolve the document again")
            return
        try:
            await server.start()
            await server.attach(connection)
        except ContractError as error:
            await connection.send({"jsonrpc": "2.0", "method": "talyx/error", "params": {"code": error.code, "message": error.message}})
            await connection.close(4002, error.message[:100])
            return
        try:
            while True:
                try:
                    message = json.loads(await websocket.receive_text())
                except ValueError:
                    continue
                await server.from_client(connection, message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except ContractError as error:
            await connection.close(4002, error.message[:100])
        finally:
            await server.detach(connection)
