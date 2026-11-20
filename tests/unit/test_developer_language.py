"""Developer's Python language service: environments from supports, one process per environment, the editor relay and its lifecycle."""
import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from talyx_sdk import ContractError

from modules.developer.backend.language import service as language_service
from modules.developer.backend.language.environment import (
    environment_key,
    language_spec,
    packages_stamp,
)
from modules.developer.backend.language.server import Connection, LanguageServer
from modules.developer.backend.language.service import (
    LanguageService,
    server_command,
    server_environment,
)

FAKE_SERVER = textwrap.dedent('''
    import json, sys, tempfile
    tempfile.mkdtemp(prefix="fake-")  # like basedpyright, which leaves its temporary folder behind when killed
    log = open(sys.argv[1], "a", encoding="utf8")
    first_folder = None

    def read():
        length = None
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                if length is None:
                    continue
                return json.loads(sys.stdin.buffer.read(length))
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value)

    def write(value):
        body = json.dumps({"jsonrpc": "2.0", **value}).encode()
        sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body) + body)
        sys.stdout.buffer.flush()

    while (message := read()) is not None:
        log.write(json.dumps(message) + "\\n")
        log.flush()
        method = message.get("method")
        if method == "initialize":
            first_folder = (message["params"]["workspaceFolders"] or [{"uri": None}])[0]["uri"]
            write({"id": message["id"], "result": {"capabilities": {"completionProvider": {}}, "serverInfo": {"name": "fake"}}})
        elif method == "initialized":
            write({"id": "settings", "method": "workspace/configuration", "params": {"items": [
                {"scopeUri": first_folder, "section": "python"}, {"scopeUri": first_folder, "section": "basedpyright"}, {"section": "other"}]}})
            write({"id": "register", "method": "client/registerCapability", "params": {"registrations": []}})
            write({"id": "unknown", "method": "custom/request", "params": {}})
        elif method == "textDocument/didOpen":
            write({"method": "textDocument/publishDiagnostics", "params": {"uri": message["params"]["textDocument"]["uri"], "diagnostics": [
                {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}, "message": "opened", "severity": 1}]}})
            write({"method": "window/logMessage", "params": {"type": 3, "message": "noise"}})
        elif method == "textDocument/completion":
            write({"id": message["id"], "result": {"items": [{"label": "data"}]}})
        elif method == "textDocument/hover":
            pass  # never answers: cancellation is tested with it
        elif method == "exit":
            break
''')


@pytest.fixture
def anyio_backend():
    return "asyncio"


def running(*servers):
    """The servers whose process has not exited."""
    return [server for server in servers if server.process is not None and server.process.returncode is None]


class Socket:
    """What a WebSocket gives the relay: text in either direction."""

    def __init__(self):
        self.received, self.closed = [], None

    async def send_text(self, text):
        self.received.append(json.loads(text))

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


async def until(condition, seconds=15):
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.02)


def logged(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf8").splitlines()] if Path(path).exists() else []


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf8")
    return script, tmp_path / "fake.log"


def make_server(tmp_path, fake, *, key="environment", idle=0.2, stamp="1"):
    script, log = fake
    command = [sys.executable, "-B", str(script), str(log)]
    return LanguageServer(key=key, interpreter=sys.executable, stamp=stamp, command=command, directory=tmp_path / "run" / uuid4().hex,
                          environment=server_environment(), idle=idle)


ROOT_A = "file:///d%3A/work/a"
ROOT_B = "file:///d%3A/work/b"
SETTINGS = {"paths": ["Z:\\work\\a", "Z:\\quant"], "declared": True, "mode": "basic"}


def test_a_support_names_the_python_and_the_folders_imports_resolve_against():
    ready = {"environment": {"python": {"state": "ready", "interpreterPath": "Z:\\envs\\research\\Scripts\\python.exe", "environmentPath": "Z:\\envs\\research"}},
             "languageMaterials": [{"kind": "python-path-scope", "scope": "declared"}, {"kind": "python-path", "path": "Z:\\quant"},
                                   {"kind": "python-path", "path": "Z:\\quant"}, {"kind": "python-path", "path": "Z:\\common"},
                                   {"kind": "python-analysis", "typeCheckingMode": "strict"}, {"kind": "something-else"}]}
    assert language_spec(ready) == {"interpreter": "Z:\\envs\\research\\Scripts\\python.exe", "environment": "Z:\\envs\\research",
                                    "paths": ["Z:\\quant", "Z:\\common"], "declared": True, "mode": "strict"}
    plain = language_spec({**ready, "languageMaterials": [{"kind": "python-analysis", "typeCheckingMode": "nonsense"}]})
    assert plain and plain["declared"] is False and plain["mode"] == "standard" and plain["paths"] == []
    # Paths alone, or nothing at all: no language service for this document (a support names a Python once it is ready).
    assert language_spec({"languageMaterials": [{"kind": "python-path", "path": "Z:\\quant"}]}) is None
    assert language_spec(None) is None


def test_the_environment_is_the_interpreter_and_changes_when_packages_do(tmp_path):
    environment = tmp_path / "venv"
    packages = environment / "Lib" / "site-packages"
    packages.mkdir(parents=True)
    spec = {"interpreter": str(environment / "Scripts" / "python.exe"), "environment": str(environment)}
    assert environment_key(spec) == environment_key({**spec, "environment": None})
    assert environment_key(spec) != environment_key({**spec, "interpreter": str(tmp_path / "other" / "python.exe")})
    before = packages_stamp(spec)
    (packages / "newpackage").mkdir()
    os.utime(packages, ns=(1, 2_000_000_000))
    assert packages_stamp(spec) != before
    assert packages_stamp({"interpreter": str(tmp_path / "gone" / "bin" / "python"), "environment": None}) == ""


@pytest.mark.anyio
async def test_the_server_is_initialized_once_and_the_editor_is_served_from_it(tmp_path, fake):
    server = make_server(tmp_path, fake)
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    window = Connection(Socket())
    await server.attach(window)
    # The server asked for settings of the folder and for nothing else of us; the answers carry the support's paths and Python.
    await until(lambda: sum(1 for item in logged(fake[1]) if item.get("id") in {"settings", "register", "unknown"}) == 3)
    answers = {item["id"]: item for item in logged(fake[1]) if item.get("id") in {"settings", "register", "unknown"}}
    python, analysis, other = answers["settings"]["result"]
    assert python == {"pythonPath": sys.executable}
    assert analysis["analysis"] == {"extraPaths": ["Z:\\work\\a", "Z:\\quant"], "autoSearchPaths": False, "diagnosticMode": "openFilesOnly",
                                    "useLibraryCodeForTypes": True, "typeCheckingMode": "basic"}
    assert other is None and answers["register"]["result"] is None and answers["unknown"]["error"]["code"] == -32601
    # initialize/initialized/shutdown are this relay's own business: a window that reloads finds the server ready.
    await server.from_client(window, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    await server.from_client(window, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
    await server.from_client(window, {"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
    assert window.socket.received == [{"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {"completionProvider": {}}}},
                                      {"jsonrpc": "2.0", "id": 2, "result": None}]
    assert [item["method"] for item in logged(fake[1]) if item.get("method") in {"initialize", "initialized", "shutdown"}] == ["initialize", "initialized"]
    # Documents and features are relayed; the server's diagnostics reach the window and its noise does not.
    uri = ROOT_A + "/factor.py"
    await server.from_client(window, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                                      "params": {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": "x"}}})
    await until(lambda: any(item.get("method") == "textDocument/publishDiagnostics" for item in window.socket.received))
    await server.from_client(window, {"jsonrpc": "2.0", "id": 7, "method": "textDocument/completion",
                                      "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 1}}})
    await until(lambda: any(item.get("id") == 7 for item in window.socket.received))
    assert [item for item in window.socket.received if item.get("id") == 7] == [{"jsonrpc": "2.0", "id": 7, "result": {"items": [{"label": "data"}]}}]
    assert not any(item.get("method") == "window/logMessage" for item in window.socket.received)
    # Only the document and feature messages pass: a command, or a change to the workspace folders, is refused.
    for method in ("workspace/executeCommand", "workspace/didChangeWorkspaceFolders"):
        await server.from_client(window, {"jsonrpc": "2.0", "id": 9, "method": method, "params": {}})
        assert window.socket.received[-1]["error"]["code"] == -32601
    assert not any(item.get("method") in {"workspace/executeCommand", "workspace/didChangeWorkspaceFolders"} for item in logged(fake[1]))
    await server.from_client(window, {"jsonrpc": "2.0", "method": "workspace/didChangeWatchedFiles", "params": {"changes": [{"uri": uri, "type": 2}]}})
    await until(lambda: any(item.get("method") == "workspace/didChangeWatchedFiles" for item in logged(fake[1])))
    await server.stop()
    await until(lambda: not running(server))
    assert window.socket.closed and window.socket.closed[0] == 4002


@pytest.mark.anyio
async def test_requests_of_a_window_are_numbered_apart_and_a_cancel_reaches_the_server(tmp_path, fake):
    server = make_server(tmp_path, fake)
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    window = Connection(Socket())
    await server.attach(window)
    uri = ROOT_A + "/factor.py"
    await server.from_client(window, {"jsonrpc": "2.0", "id": 1, "method": "textDocument/hover", "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}}})
    await server.from_client(window, {"jsonrpc": "2.0", "id": 1, "method": "textDocument/completion", "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}}})
    await until(lambda: any(item.get("id") == 1 for item in window.socket.received))
    sent = [item for item in logged(fake[1]) if item.get("method") in {"textDocument/hover", "textDocument/completion"}]
    hover = next(item for item in sent if item["method"] == "textDocument/hover")
    assert len({item["id"] for item in sent}) == 2  # both were the window's request 1
    # The hover was never answered: the window's cancel of its request 1 names the server's number for it, not the answered one's.
    await server.from_client(window, {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 1}})
    await until(lambda: any(item.get("method") == "$/cancelRequest" for item in logged(fake[1])))
    assert next(item for item in logged(fake[1]) if item.get("method") == "$/cancelRequest")["params"]["id"] == hover["id"]
    await server.stop()


@pytest.mark.anyio
async def test_a_window_taking_over_closes_the_documents_of_the_one_it_replaces(tmp_path, fake):
    server = make_server(tmp_path, fake)
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    old, new = Connection(Socket()), Connection(Socket())
    await server.attach(old)
    uri = ROOT_A + "/factor.py"
    await server.from_client(old, {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": "x"}}})
    await server.attach(new)
    await until(lambda: any(item.get("method") == "textDocument/didClose" for item in logged(fake[1])))
    assert old.socket.closed and old.socket.closed[0] == 4001 and new.socket.closed is None
    await server.detach(old)  # the replaced window's handler ends: it does not release the new window
    assert server.connection is new and server.state == "ready"
    # The new window opens the same file: one open document on the server.
    await server.from_client(new, {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": "x"}}})
    documents = lambda: [item["method"] for item in logged(fake[1]) if item.get("method", "").startswith("textDocument/did")]
    await until(lambda: len(documents()) == 3)
    assert documents() == ["textDocument/didOpen", "textDocument/didClose", "textDocument/didOpen"]
    await server.stop()


@pytest.mark.anyio
async def test_the_process_ends_a_moment_after_the_last_window_leaves(tmp_path, fake):
    server = make_server(tmp_path, fake, idle=0.3)
    ended = []
    server.on_end = ended.append
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    first, second = Connection(Socket()), Connection(Socket())
    await server.attach(first)
    uri = ROOT_A + "/factor.py"
    await server.from_client(first, {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": "x"}}})
    await server.detach(first)
    assert running(server)
    await server.attach(second)  # back inside the grace: the process stays
    await asyncio.sleep(0.6)
    assert server.state == "ready" and running(server)
    await server.detach(second)
    await until(lambda: ended)
    await until(lambda: not running(server))
    assert ended == [server] and not server.directory.exists()
    with pytest.raises(ContractError):
        await server.start()


@pytest.mark.anyio
async def test_folders_and_settings_added_while_running_reach_the_server(tmp_path, fake):
    server = make_server(tmp_path, fake)
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    await server.set_root(ROOT_B, {"paths": ["Z:\\work\\b"], "declared": False, "mode": "standard"})
    await until(lambda: any(item.get("method") == "workspace/didChangeWorkspaceFolders" for item in logged(fake[1])))
    added = next(item for item in logged(fake[1]) if item.get("method") == "workspace/didChangeWorkspaceFolders")
    assert added["params"]["event"] == {"added": [{"uri": ROOT_B, "name": "b"}], "removed": []}
    await server.set_root(ROOT_B, {"paths": ["Z:\\work\\b", "Z:\\more"], "declared": False, "mode": "standard"})
    await until(lambda: any(item.get("method") == "workspace/didChangeConfiguration" for item in logged(fake[1])))
    analysis = server.settings(ROOT_B, "python.analysis")
    assert analysis and analysis["extraPaths"] == ["Z:\\work\\b", "Z:\\more"] and analysis["autoSearchPaths"] is True
    await server.stop()


class Documents:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.records = {}

    def add(self, name, *, project="p", language="python", root=ROOT_A):
        uri = (self.tmp_path / name).as_uri()
        document_id = "document:" + name
        self.records[document_id] = {"documentId": document_id, "uri": uri, "language": language,
                                      "context": {"projectId": project, "rootUri": root}, "source": {"kind": "file", "uri": uri}, "name": name, "version": 1}
        return document_id

    def record(self, document_id):
        return self.records[document_id]


class Supports:
    def __init__(self):
        self.answers, self.calls = {}, []

    def applicable(self, document_id):
        return [{"moduleId": "other", "id": "checks", "descriptor": {"capabilities": []}},
                {"moduleId": "quant", "id": "source_language", "descriptor": {"capabilities": ["language"]}}]

    async def ask(self, support, capability, command, document_id):
        self.calls.append((f"{support['moduleId']}:{support['id']}", command, capability))
        answer = self.answers[document_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


def research(python, **extra):
    return {"environment": {"python": {"state": "ready", "interpreterPath": python, "environmentPath": str(Path(python).parent.parent)}},
            "languageMaterials": [{"kind": "python-path", "path": "Z:\\quant"}], **extra}


@pytest.fixture
def service(tmp_path, fake):
    script, log = fake
    documents, supports = Documents(tmp_path), Supports()
    command = lambda: [sys.executable, "-B", str(script), str(log)]
    return SimpleNamespace(service=LanguageService(documents, supports, tmp_path / "storage", command=command, idle=0.2),
                           documents=documents, supports=supports, log=log)


@pytest.mark.anyio
async def test_documents_of_one_environment_share_a_server_and_each_folder_keeps_its_own_paths(tmp_path, service):
    interpreter = str(tmp_path / "research" / "Scripts" / "python.exe")
    (tmp_path / "research" / "Lib" / "site-packages").mkdir(parents=True)
    a, b, c = service.documents.add("a.py"), service.documents.add("b.py", root=ROOT_B), service.documents.add("c.py", project="q")
    for name in (a, b):
        service.supports.answers[name] = research(interpreter)
    service.supports.answers[c] = research(str(tmp_path / "strategy" / "Scripts" / "python.exe"))
    first, second, third = [await service.service.resolve(name) for name in (a, b, c)]
    assert first["session"] == second["session"] != third["session"]
    assert first["uri"] == service.documents.records[a]["uri"] and (first["rootUri"], second["rootUri"]) == (ROOT_A, ROOT_B)
    assert set(service.service.servers) == {first["session"], third["session"]}
    shared = service.service.servers[first["session"]]
    assert set(shared.roots) == {ROOT_A, ROOT_B} and shared.roots[ROOT_A]["paths"] == ["Z:\\quant"]
    # Only a support with the `language` capability is asked, with its `environment` command.
    assert set(service.supports.calls) == {("quant:source_language", "environment", "language")}
    await service.service.close()
    assert not service.service.servers


@pytest.mark.anyio
async def test_a_document_without_a_ready_python_gets_no_server(tmp_path, service):
    plain = service.documents.add("notes.txt", language="plaintext")
    unprepared = service.documents.add("early.py")
    failing = service.documents.add("failing.py")
    service.supports.answers[unprepared] = {"languageMaterials": [{"kind": "python-path", "path": "Z:\\quant"}]}
    service.supports.answers[failing] = ContractError("unavailable", "Not ready")
    for name in (plain, unprepared, failing):
        assert await service.service.resolve(name) == {"session": None}
    assert not service.service.servers


@pytest.mark.anyio
async def test_installing_packages_replaces_the_server_so_editors_reconnect_to_one_that_sees_them(tmp_path, service):
    interpreter = tmp_path / "research" / "Scripts" / "python.exe"
    packages = tmp_path / "research" / "Lib" / "site-packages"
    packages.mkdir(parents=True)
    document = service.documents.add("a.py")
    service.supports.answers[document] = research(str(interpreter))
    first = await service.service.resolve(document)
    old = service.service.servers[first["session"]]
    window = Connection(Socket())
    await old.start()
    await old.attach(window)
    again = await service.service.resolve(document)
    assert service.service.servers[again["session"]] is old  # nothing installed: the same server
    (packages / "pandas").mkdir()
    os.utime(packages, ns=(1, 3_000_000_000))
    installed = await service.service.resolve(document)
    new = service.service.servers[installed["session"]]
    assert installed["session"] == first["session"] and new is not old
    await until(lambda: not running(old))
    assert old.state == "stopped" and window.socket.closed and window.socket.closed[0] == 4002
    await service.service.close()


def test_the_editor_reaches_the_server_through_the_routes(tmp_path, service):
    interpreter = str(tmp_path / "research" / "Scripts" / "python.exe")
    document = service.documents.add("a.py")
    service.supports.answers[document] = research(interpreter)
    app = FastAPI()  # as Developer's private routes serve them

    @app.post("/language/resolve")
    async def resolve(value: dict):
        return await service.service.resolve(value["documentId"])

    @app.websocket("/language/sessions/{session}")
    async def socket(websocket: WebSocket, session: str):
        await service.service.serve(websocket, session)

    with TestClient(app) as client:
        resolved = client.post("/language/resolve", json={"documentId": document}).json()
        assert resolved["session"] and resolved["rootUri"] == ROOT_A
        with client.websocket_connect(f"/language/sessions/{resolved['session']}") as socket:
            socket.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
            assert socket.receive_json()["result"]["capabilities"] == {"completionProvider": {}}
            socket.send_text(json.dumps({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                                         "params": {"textDocument": {"uri": resolved["uri"], "languageId": "python", "version": 1, "text": "x"}}}))
            assert socket.receive_json()["method"] == "textDocument/publishDiagnostics"
            socket.send_text("not json")
            socket.send_text(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion", "params": {"textDocument": {"uri": resolved["uri"]}, "position": {"line": 0, "character": 1}}}))
            assert socket.receive_json() == {"jsonrpc": "2.0", "id": 2, "result": {"items": [{"label": "data"}]}}
        # A session nobody resolved (or one that ended) is refused so the editor resolves again.
        with client.websocket_connect("/language/sessions/unknown") as refused:
            assert refused.receive()["code"] == 4004
        servers = list(service.service.servers.values())
        deadline = time.monotonic() + 15
        while running(*servers) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not running(*servers)


def test_the_launcher_names_the_installed_server_and_keeps_inherited_python_settings_out(monkeypatch):
    assert server_command()[0].lower().endswith("basedpyright-langserver.exe" if os.name == "nt" else "basedpyright-langserver")
    assert server_command()[-1] == "--stdio"
    monkeypatch.setenv("PYTHONPATH", "Z:\\elsewhere")
    monkeypatch.setenv("VIRTUAL_ENV", "Z:\\venv")
    environment = server_environment()
    assert "PYTHONPATH" not in environment and "VIRTUAL_ENV" not in environment and environment["PYTHONUTF8"] == "1"
    monkeypatch.setattr(language_service.sys, "executable", str(Path(sys.executable).parent / "no-such-python.exe"))
    monkeypatch.setattr(language_service.Path, "is_file", lambda self: False)
    with pytest.raises(ContractError) as error:
        server_command()
    assert error.value.code == "unavailable"


@pytest.mark.anyio
async def test_the_real_server_marks_a_wrong_name_and_completes_a_module_from_the_declared_folder(tmp_path):
    """basedpyright itself: the support's folder holds a type stub that the editor completes from."""
    project, library = tmp_path / "project", tmp_path / "library"
    project.mkdir()
    library.mkdir()
    (library / "talyx_stub.py").write_text("def data(): ...\n", encoding="utf8")
    (library / "talyx_stub.pyi").write_text("class Prices:\n    close: int\n    def pct_change(self) -> int: ...\n\ndef data() -> Prices: ...\n", encoding="utf8")
    source = project / "factor.py"
    text = "import talyx_stub as q\nvalue = q.data().close\nq.data().pct_change()\nundefined_name + 1\nq.\n"
    source.write_text(text, encoding="utf8")
    server = LanguageServer(key="real", interpreter=sys.executable, stamp="1", command=server_command(), directory=tmp_path / "run",
                            environment=server_environment(), idle=0.2)
    root = project.as_uri()
    await server.set_root(root, {"paths": [str(library)], "declared": True, "mode": "standard"})
    await server.start()
    window = Connection(Socket())
    await server.attach(window)
    uri = source.as_uri()
    await server.from_client(window, {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": text}}})
    await until(lambda: any(item.get("method") == "textDocument/publishDiagnostics" for item in window.socket.received), 60)
    messages = [item["message"] for note in window.socket.received if note.get("method") == "textDocument/publishDiagnostics" for item in note["params"]["diagnostics"]]
    assert any('"undefined_name" is not defined' in message for message in messages)
    assert not any("could not be resolved" in message for message in messages)  # the declared folder is on the search path
    await server.from_client(window, {"jsonrpc": "2.0", "id": 5, "method": "textDocument/completion", "params": {"textDocument": {"uri": uri}, "position": {"line": 4, "character": 2}}})
    await until(lambda: any(item.get("id") == 5 for item in window.socket.received), 60)
    answer = next(item for item in window.socket.received if item.get("id") == 5)["result"]
    assert "data" in [item["label"] for item in (answer["items"] if isinstance(answer, dict) else answer)]
    await server.from_client(window, {"jsonrpc": "2.0", "id": 6, "method": "textDocument/completion", "params": {"textDocument": {"uri": uri}, "position": {"line": 2, "character": 10}}})
    await until(lambda: any(item.get("id") == 6 for item in window.socket.received), 60)
    answer = next(item for item in window.socket.received if item.get("id") == 6)["result"]
    assert "pct_change" in [item["label"] for item in (answer["items"] if isinstance(answer, dict) else answer)]
    ended = []
    server.on_end = ended.append
    await server.detach(window)
    await until(lambda: ended)
    await until(lambda: not running(server))


def test_a_type_stub_is_python_to_the_editor():
    from modules.developer.backend.documents.service import LANGUAGES
    assert LANGUAGES[".pyi"] == LANGUAGES[".py"] == "python"


@pytest.mark.anyio
async def test_the_servers_temporary_files_stay_in_its_directory_and_go_with_it(tmp_path, fake):
    server = make_server(tmp_path, fake)
    await server.set_root(ROOT_A, SETTINGS)
    await server.start()
    assert [path.name[:5] for path in (server.directory / "tmp").iterdir()] == ["fake-"]
    await server.stop()
    assert not server.directory.exists()
