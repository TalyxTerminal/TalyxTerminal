"""What Quant's services hold as ``ctx``: the platform as Quant uses it, on the SDK.

- The workspace a request is for: every action, private route and stream sets ``WORKSPACE`` before Quant's code runs,
  and background work started from it keeps it (asyncio tasks copy the context). ``workspace()`` reads it.
- The call being served (``CALL``): the platform call attribution.
- Background work: ``start`` registers a task under the workspace's activity (the platform lists it and stops it when
  the workspace closes); processes started inside it join the same activity, so stopping ends their trees.
- Processes: ``processes`` starts worker processes in jobs of their own (``talyx_sdk.jobs``), with a run token when
  their code calls TALYX, newline-JSON over stdin/stdout when asked, and logs to files.
- Python environments: ``environments`` (``talyx_sdk.uv``), in the folder every module shares.
- Files handed over: ``hand_over`` writes a ``FileRef`` (path, sha256, bytes) under Quant's data directory, kept 24
  hours; ``keep`` hard-links another module's ``FileRef`` into Quant's own directory for as long as Quant needs it.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from talyx_sdk import ContractError, wire
from talyx_sdk.i18n import t
from talyx_sdk.jobs import spawn
from talyx_sdk.process import Context
from talyx_sdk.uv import Environments

WORKSPACE: contextvars.ContextVar[str | None] = contextvars.ContextVar("quant_workspace", default=None)
CALL: contextvars.ContextVar[Any] = contextvars.ContextVar("quant_call", default=None)
ACTIVITY: contextvars.ContextVar[Any] = contextvars.ContextVar("quant_activity", default=None)
ACTIVITY_RUN: contextvars.ContextVar[str | None] = contextvars.ContextVar("quant_activity_run", default=None)
HAND_OVER_SECONDS = 24 * 3600
LINE_LIMIT = 16 * 1024 * 1024


def call_attribution():
    call = CALL.get()
    return {"callId": call.call_id if call else None, "callOrigin": call.caller.origin if call else None}


class Quant:
    """The platform as Quant's services use it."""

    def __init__(self, ctx: Context, directories: dict):
        self.sdk = ctx
        self.data_dir = Path(directories["dataDir"])
        self.user_dir = Path(directories["userDir"])
        self.environments = Environments(directories["environmentsDir"])
        self.processes = Processes(self)
        self.handlers: dict = {}          # public action name -> Quant's handler (args, call)
        self.files = self.data_dir / "files"          # Quant's own copies of what others handed over
        self.handoff = self.data_dir / "handoff"      # what Quant hands over (FileRef)
        for folder in (self.files, self.handoff):
            folder.mkdir(parents=True, exist_ok=True)

    # ── calling and publishing ──────────────────────────────────────────────────────────────────────────────────────
    async def call(self, name: str, input: Any, *, workspace: str | None = None) -> Any:
        """A public action of another module or the platform, for the workspace being served (or ``workspace``)."""
        from .changes import active, remember_child
        workspace = workspace or WORKSPACE.get()
        context = active()
        if context and context[1].caller.origin != "user" and name in {
            "developer.documents.edit", "developer.documents.save", "developer.documents.create"
        }:
            result, child = await self.sdk.call_with_id(name, input, workspace=workspace)
            changed = await self.sdk.call("platform.changes.list", {"callIds":[child]}, workspace=workspace)
            if changed:
                remember_child(context, child)
            return result
        return await self.sdk.call(name, input, workspace=workspace)

    def publish(self, event: str, payload: Any) -> None:
        spec = self.sdk.process.module.events[event]
        self.sdk.process.send({"t": "event", "cap": f"quant.{event}"}, self.sdk.process.codec(spec.payload).check(payload))


    async def developer(self, name: str, input: Any) -> Any:
        """A Developer action, or None when Developer is not installed (Quant works without it: files are read and
        written as they are on disk)."""
        try:
            return await self.call(name, input)
        except ContractError as error:
            if error.code in ("unknown_capability", "not_found") and "developer" in error.message.lower():
                return None
            raise

    async def setting(self, key: str, workspace: str | None = None) -> Any:
        return await self.sdk.platform.setting(key, workspace=workspace or self.workspace())

    # ── whose request ───────────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def workspace() -> str:
        workspace = WORKSPACE.get()
        if not workspace:
            raise ContractError("invalid_input", t("Quant works in a workspace; this request names none."))
        return workspace

    # ── background work ─────────────────────────────────────────────────────────────────────────────────────────────
    def start(self, coroutine, title: str = "Quant work", *, run_record=None, activity=None) -> asyncio.Task:
        """Run ``coroutine`` as background work of the workspace being served: the platform lists it and stops it
        (with the processes it starts) when the workspace closes. Work serving no workspace is a plain task."""
        workspace = run_record["workspaceId"] if run_record is not None else WORKSPACE.get()
        if not workspace:
            # Work of all of TALYX (the library's production loop): it runs until Quant stops.
            return asyncio.get_running_loop().create_task(coroutine)
        owns_activity = activity is None
        if owns_activity:
            details = {} if run_record is None else {
                "call_id": run_record.get("callId"), "origin": run_record.get("callOrigin") or "module",
                "open_target": {"module": "quant", "panelType": "workspace", "objectId": "run:" + run_record["id"]},
            }
            activity = self.sdk.activity(workspace, title, **details)

        context = contextvars.copy_context()
        context.run(ACTIVITY.set, activity)
        context.run(ACTIVITY_RUN.set, run_record["id"] if run_record is not None else None)
        context.run(WORKSPACE.set, workspace)
        async def run_and_report():
            try:
                return await coroutine
            finally:
                # Model checks attach all their queued training jobs before returning. The root
                # activity delivers its result after the last attached worker finishes.
                others = [task for task in activity.tasks if task is not asyncio.current_task()]
                if others:
                    await asyncio.gather(*others, return_exceptions=True)
                result = await self.run_result(run_record["id"])
                activity.set_result(result)

        work = run_and_report() if owns_activity and run_record is not None else coroutine
        return activity.add_task(asyncio.create_task(work, context=context))

    # ── files ───────────────────────────────────────────────────────────────────────────────────────────────────────
    async def hand_over(self, path: Path, name: str, *, move: bool = False) -> dict:
        """A finished file in Quant's hand-over folder (moved, or linked so the original stays); its ``FileRef``,
        kept 24 hours. It is never written again: the name carries the digest."""
        sha = await asyncio.to_thread(_digest, path)
        target = self.handoff / f"{sha[:32]}-{Path(name).name}"
        if not target.exists():
            temporary = target.with_name(f".{uuid.uuid4().hex}.tmp")
            if move:
                os.replace(path, temporary)
            else:
                try:
                    os.link(path, temporary)
                except OSError:
                    await asyncio.to_thread(shutil.copyfile, path, temporary)
            os.replace(temporary, target)
        elif move:
            Path(path).unlink()
        os.utime(target)    # handed over now: kept 24 hours from now
        return {"path": str(target), "sha256": sha, "bytes": target.stat().st_size}

    def sweep_handoff(self) -> None:
        cutoff = time.time() - HAND_OVER_SECONDS
        for item in self.handoff.iterdir():
            with contextlib.suppress(OSError):
                if item.stat().st_mtime < cutoff:
                    item.unlink()

    async def keep(self, reference: dict, owner: str) -> dict:
        """Quant's own copy of a handed-over file (a hard link, a copy across drives), checked against its digest;
        it stays until every owner lets it go (``let_go``)."""
        target = self.files / reference["sha256"]
        if not target.exists():
            temporary = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
            try:
                os.link(reference["path"], temporary)
            except OSError:
                await asyncio.to_thread(shutil.copyfile, reference["path"], temporary)
            if await asyncio.to_thread(_digest, temporary) != reference["sha256"]:
                temporary.unlink()
                raise ContractError("conflict", t("A handed-over file changed after it was handed over."))
            os.replace(temporary, target)
        owners = self.files / (reference["sha256"] + ".owners")
        owners.mkdir(exist_ok=True)
        (owners / hashlib.sha256(owner.encode()).hexdigest()[:32]).write_text(owner, encoding="utf-8")
        return {**reference, "path": str(target)}

    def let_go(self, reference: dict, owner: str) -> None:
        owners = self.files / (reference["sha256"] + ".owners")
        with contextlib.suppress(OSError):
            (owners / hashlib.sha256(owner.encode()).hexdigest()[:32]).unlink()
        with contextlib.suppress(OSError):
            if not any(owners.iterdir()):
                owners.rmdir()
                (self.files / reference["sha256"]).unlink()


def _digest(path) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


class _Process:
    def __init__(self, process, job, streams, lines: bool, run: dict | None):
        self.process, self.job, self.streams, self.run = process, job, streams, run
        self.messages: asyncio.Queue | None = asyncio.Queue() if lines else None
        self.pump = asyncio.get_running_loop().create_task(self._read()) if lines else None
        self.done = asyncio.get_running_loop().create_task(process.wait())

    async def _read(self) -> None:
        try:
            while line := await self.process.stdout.readline():
                self.streams[0].write(line)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                self.messages.put_nowait(value)
        finally:
            self.messages.put_nowait(None)


class Processes:
    """Worker processes Quant starts. Each runs in a job of its own; ``cancel`` ends its whole tree (a training's
    joblib workers too). A process that calls TALYX gets a run token: its calls are recorded as code from Quant and
    the workspace, and ``context`` reaches the handlers it calls."""

    def __init__(self, quant: Quant):
        self.quant = quant
        self.running: dict[str, _Process] = {}

    async def start(self, launch: dict) -> str:
        """``launch``: argv, cwd, env, stdout_path, stderr_path; ``lines`` for newline-JSON over stdin/stdout;
        ``channel: {context}`` for a run token. Returns the process's id."""
        env = {str(key): str(value) for key, value in launch.get("env", {}).items()}
        run = None
        if "channel" in launch:
            context = (launch["channel"] or {}).get("context")
            run = await self.quant.sdk.run_token(WORKSPACE.get(), context)
            env.update(TALYX_CALL_URL=run["url"], TALYX_RUN_TOKEN=run["token"], TALYX_RUN_CONTEXT=json.dumps(context))
        lines = bool(launch.get("lines"))
        for key in ("stdout_path", "stderr_path"):
            Path(launch[key]).parent.mkdir(parents=True, exist_ok=True)
        stdout = open(launch["stdout_path"], "ab", buffering=0)   # noqa: SIM115 - closed with the process
        stderr = open(launch["stderr_path"], "ab", buffering=0)   # noqa: SIM115
        try:
            process, job = await spawn(list(launch["argv"]), cwd=launch["cwd"], env=env,
                                       stdin=asyncio.subprocess.PIPE if lines else asyncio.subprocess.DEVNULL,
                                       stdout=asyncio.subprocess.PIPE if lines else stdout, stderr=stderr,
                                       **({"limit": LINE_LIMIT} if lines else {}))
        except BaseException:
            stdout.close()
            stderr.close()
            raise
        activity = ACTIVITY.get()
        if activity is not None:
            activity.add_job(job)
        identity = "qp_" + uuid.uuid4().hex
        self.running[identity] = _Process(process, job, (stdout, stderr), lines, run)
        return identity

    def _get(self, identity: str) -> _Process:
        found = self.running.get(identity)
        if found is None:
            raise ContractError("target_gone", t("This process is gone."))
        return found

    async def status(self, identity: str) -> dict:
        found = self._get(identity)
        return {"running": found.process.returncode is None, "exitCode": found.process.returncode}

    async def wait(self, identity: str) -> int:
        found = self._get(identity)
        code = await asyncio.shield(found.done)
        if found.pump is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(found.pump), 5)
        return code

    async def send(self, identity: str, message: Any) -> None:
        found = self._get(identity)
        found.process.stdin.write(wire.encode(message) + b"\n")
        await found.process.stdin.drain()

    async def receive(self, identity: str, timeout: float) -> dict:
        """``{"state": "message", "message"}``, ``{"state": "timeout"}`` or ``{"state": "closed"}``."""
        found = self._get(identity)
        try:
            item = await asyncio.wait_for(found.messages.get(), timeout)
        except TimeoutError:
            return {"state": "timeout"}
        if item is None:
            found.messages.put_nowait(None)
            return {"state": "closed"}
        return {"state": "message", "message": item}

    async def calls(self, identity: str) -> list:
        """The calls the process's code made to TALYX."""
        found = self._get(identity)
        return [] if found.run is None else await self.quant.sdk.call("platform.runs.calls", {"runId": found.run["runId"]})

    async def cancel(self, identity: str) -> int | None:
        """End the process and everything it started."""
        found = self.running.get(identity)
        if found is None:
            return None
        found.job.terminate()
        await found.job.ended()
        with contextlib.suppress(ProcessLookupError):
            found.process.kill()
        return await asyncio.shield(found.done)

    async def close(self, identity: str) -> None:
        """Let go of a process that ended (or end it): its job, logs and run token."""
        found = self.running.pop(identity, None)
        if found is None:
            return
        if found.process.returncode is None:
            found.job.terminate()
            await found.job.ended()
        await asyncio.gather(found.done, return_exceptions=True)
        if found.pump is not None:
            found.pump.cancel()
            await asyncio.gather(found.pump, return_exceptions=True)
            with contextlib.suppress(Exception):
                found.process.stdin.close()
        found.job.close()
        for stream in found.streams:
            stream.close()
        if found.run is not None:
            with contextlib.suppress(ContractError):
                await self.quant.sdk.call("platform.runs.end", {"runId": found.run["runId"]})

    async def close_all(self) -> None:
        for identity in list(self.running):
            with contextlib.suppress(Exception):
                await self.close(identity)
