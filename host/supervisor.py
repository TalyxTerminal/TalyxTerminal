"""Module processes: one per module, official and third-party alike, each in its own Job Object.

The Host listens on a loopback port; each module process connects back and says who it is with the one-time token
it was started with. That connection is the module's identity from then on. A process that exits on its own is
started again; after ``FAILURES`` quick failures in a row the module is marked failed with the reason (the end of
its error output). Reloading a module is stopping its process and starting a new one.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path

from talyx_sdk import wire
from talyx_sdk.contract import check_ready
from talyx_sdk.errors import ContractError
from talyx_sdk.jobs import Job, spawn

from kernel.router import Endpoint, Router

log = logging.getLogger("talyx.modules")
FAILURES = 3
# A process that ran this long before exiting counts as having started fine.
STABLE_SECONDS = 30.0


class ModuleEndpoint(Endpoint):
    """A module process's connection: frames it sends carry its identity; the workspace is what it says."""

    def __init__(self, process: ModuleProcess, writer: asyncio.StreamWriter, pid: int):
        self.process = process
        self.module = process.module_id
        self.name = f"module:{self.module}"
        self.writer = writer
        self.pid = pid  # The interpreter on this connection; a Windows venv launcher has a different PID.

    def deliver(self, envelope: dict, body: bytes = b"") -> None:
        if not self.writer.is_closing():
            self.writer.write(wire.frame(envelope, body))

    def caller(self, envelope: dict) -> dict:
        if "from" in envelope:
            raise ContractError("permission_denied", "A module cannot say who is calling; the Host does")
        workspace = envelope.get("ws")
        if workspace is not None:
            self.process.supervisor.check_workspace(workspace)
        return {"origin": "module", "module": self.module, "workspaceId": workspace}


class ModuleProcess:
    """One module's process over its lifetimes: started, ready, restarted, stopped."""

    def __init__(self, supervisor: Supervisor, module_id: str, directory: Path, package: str, entry: str,
                 contract: dict, python: str):
        self.supervisor, self.module_id, self.directory = supervisor, module_id, directory
        self.package, self.entry, self.contract, self.python = package, entry, contract, python
        self.state = "stopped"          # starting, ready, failed, stopped
        self.problem: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.job: Job | None = None
        self.endpoint: ModuleEndpoint | None = None
        self.token: str | None = None
        self.ready_info: dict | None = None
        self.started_at = 0.0
        self.failures = 0
        self.output: collections.deque[str] = collections.deque(maxlen=40)
        self.wanted = False
        self.ready_event = asyncio.Event()
        self.watcher: asyncio.Task | None = None
        self.activities: dict[str, dict] = {}

    async def start(self) -> None:
        self.wanted = True
        await self._launch()

    async def _launch(self) -> None:
        self.state, self.problem = "starting", None
        self.ready_event.clear()
        self.token = secrets.token_urlsafe(24)
        self.supervisor.tokens[self.token] = self
        root = self.supervisor.platform_root
        env = {**os.environ,
               # The platform's own code first, whatever else the interpreter would find (another checkout).
               "PYTHONPATH": os.pathsep.join([str(root), str(root / "sdk"), str(self.supervisor.import_root(self))]),
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               "TALYX_HOST": f"127.0.0.1:{self.supervisor.port}", "TALYX_TOKEN": self.token,
               "TALYX_MODULE": self.module_id, "TALYX_PACKAGE": self.package, "TALYX_ENTRY": self.entry,
               **self.supervisor.environment}
        self.started_at = time.monotonic()
        self.process, self.job = await spawn(
            [self.python, "-B", "-X", "utf8", "-m", "talyx_sdk.process"], env=env, cwd=str(root),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.watcher = asyncio.create_task(self._watch(self.process))

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        tail = b""
        while chunk := await process.stdout.read(8192):
            lines = (tail + chunk).split(b"\n")
            tail = lines.pop()[-8192:]
            for raw in lines:
                self._log_line(raw[-8192:])
        if tail:
            self._log_line(tail)
        code = await process.wait()
        if process is not self.process:
            return
        self._gone(stopped=not self.wanted)
        if not self.wanted:
            return
        ran = time.monotonic() - self.started_at
        self.failures = 0 if ran > STABLE_SECONDS else self.failures + 1
        if self.failures >= FAILURES:
            self.state = "failed"
            self.problem = f"Exited with code {code} {self.failures} times in a row:\n" + "\n".join(self.output)
            log.error("%s could not start: %s", self.module_id, self.problem)
            self.supervisor.changed(self)
            return
        log.warning("%s exited with code %s; starting it again", self.module_id, code)
        await asyncio.sleep(min(0.2 * 2 ** self.failures, 2.0))
        if self.wanted and self.process is process:
            await self._launch()

    def _log_line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace").rstrip()
        self.output.append(line)
        print(line, file=sys.stderr, flush=True)

    def _disconnected(self, *, stopped: bool) -> None:
        """The process's connection is gone: from now on calls wait for the next start (or fail, when stopped)
        instead of going into a connection nobody reads."""
        self.supervisor.router.provider_gone(self.module_id, self.endpoint, stopped=stopped)
        self.endpoint.writer.close()
        self.endpoint = None
        self.state = "stopped" if stopped else "starting"
        self.ready_event.clear()
        self.supervisor.changed(self)

    def _gone(self, *, stopped: bool) -> None:
        if self.token:
            self.supervisor.tokens.pop(self.token, None)
        if self.endpoint is not None:
            self._disconnected(stopped=stopped)
        if self.job is not None:
            self.job.close()
            self.job = None
        self.activities.clear()
        self.state = "stopped" if stopped else "starting"
        self.supervisor.changed(self)

    async def stop(self) -> None:
        """Stop on purpose: callers waiting for it get ``unavailable``, nothing restarts it. A module that failed
        activation stays failed, with its reason."""
        self.wanted = False
        failed = self.state == "failed"
        process = self.process
        if process is not None and process.returncode is None:
            self.job.terminate() if self.job else process.kill()
            await process.wait()
        if self.watcher is not None:
            await asyncio.gather(self.watcher, return_exceptions=True)
        if self.endpoint is not None or self.job is not None:
            self._gone(stopped=True)
        self.state = "failed" if failed else "stopped"
        self.supervisor.changed(self)

    async def restart(self) -> None:
        """Kill the process now (stuck or misbehaving); it starts again like after a crash."""
        if self.job is not None:
            self.job.terminate()
        elif self.process is not None:
            self.process.kill()

    # ── the connection ───────────────────────────────────────────────────

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, pid: int) -> None:
        self.endpoint = endpoint = ModuleEndpoint(self, writer, pid)
        router = self.supervisor.router
        while True:
            try:
                envelope, body = await wire.read_frame(reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                if self.endpoint is endpoint:
                    self._disconnected(stopped=not self.wanted)
                return
            kind = envelope["t"]
            try:
                if kind == "ready":
                    self._ready(wire.decode(body))
                elif kind == "failed":
                    self.problem = wire.decode(body)["message"]
                elif kind == "activity":
                    self.activities[envelope["activity"]] = {"id": envelope["activity"], "workspaceId": envelope["ws"],
                                                             **wire.decode(body)}
                    self.supervisor.changed(self)
                elif kind == "activity_progress":
                    self.activities[envelope["activity"]]["progress"] = wire.decode(body)
                    self.supervisor.on_module_frame(self, envelope, body)
                elif kind == "activity_end":
                    activity = self.activities.pop(envelope["activity"])
                    router.publish({"module": "platform"}, "platform.activity.ended", wire.encode({
                        "workspaceId": activity["workspaceId"], "module": self.module_id,
                        "activityId": activity["id"], "callId": activity["callId"],
                        "openTarget": activity["openTarget"], "result": envelope["outcome"],
                        **({"summary": wire.decode(body)} if body and body != b"null" else {}),
                    }))
                    self.supervisor.changed(self)
                elif kind in ("task_progress", "task_end", "done"):
                    self.supervisor.on_module_frame(self, envelope, body)
                else:
                    router.receive(endpoint, envelope, body)
            except ContractError as exc:
                if "id" in envelope and kind in ("call", "sub"):
                    endpoint.deliver({"t": "error", "id": envelope["id"], "err": exc.to_dict()})
                else:
                    log.warning("%s sent a frame that was refused: %s", self.module_id, exc.message)

    def _ready(self, info: dict) -> None:
        problems = check_ready(self.contract, info)
        if problems:
            # Activation fails: what runs must be what the contract says.
            self.wanted = False
            self.state, self.problem = "failed", "The running code does not match the contract:\n" + "\n".join(problems)
            log.error("%s: %s", self.module_id, self.problem)
            asyncio.get_running_loop().create_task(self.stop())
            self.supervisor.changed(self)
            return
        self.ready_info = info
        self.state = "ready"
        self.ready_event.set()
        self.supervisor.router.provider_ready(self.module_id, self.endpoint)
        self.supervisor.changed(self)


class Supervisor:
    def __init__(self, router: Router, platform_root: Path, check_workspace: Callable[[str], None],
                 on_change: Callable[[ModuleProcess], None], on_module_frame: Callable):
        self.router, self.platform_root, self.check_workspace = router, platform_root, check_workspace
        self.changed, self.on_module_frame = on_change, on_module_frame
        self.processes: dict[str, ModuleProcess] = {}
        self.tokens: dict[str, ModuleProcess] = {}
        self.environment: dict[str, str] = {}
        """Set for every module process (the platform's shared folder)."""
        self.server: asyncio.Server | None = None
        self.port = 0

    def import_root(self, process: ModuleProcess) -> Path:
        """The directory to import the module's package from (``package`` may be dotted)."""
        return process.directory.parents[process.package.count(".")]

    async def listen(self) -> None:
        self.server = await asyncio.start_server(self._connected, "127.0.0.1", 0, limit=2 ** 26)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        wire.tune(writer)
        try:
            envelope, _ = await asyncio.wait_for(wire.read_frame(reader), 30)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        process = self.tokens.pop(envelope.get("token", ""), None) if envelope.get("t") == "hello" else None
        if process is None or envelope.get("module") != process.module_id:
            log.warning("Refused a module connection with an unknown token")
            writer.close()
            return
        pid = envelope.get("pid")
        if type(pid) is not int or pid <= 0:
            log.warning("Refused %s connection without a valid process ID", process.module_id)
            writer.close()
            return
        await process.serve(reader, writer, pid)

    def add(self, process: ModuleProcess) -> None:
        self.processes[process.module_id] = process

    async def stop_all(self) -> None:
        await asyncio.gather(*(process.stop() for process in self.processes.values()), return_exceptions=True)
        if self.server is not None:
            self.server.close()
