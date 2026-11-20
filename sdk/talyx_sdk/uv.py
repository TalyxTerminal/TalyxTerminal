"""Python environments made with uv, kept where every module can use them, with who holds each.

The platform gives every module the same directory (``platform.storage.directories`` → ``environmentsDir``). An
environment is named by what it is made of (the Python version, the lock and the imports it must have), so two modules
asking for the same thing share one. Whoever relies on an environment writes a hold (``hold``) and takes it back when
it stops (``release``); ``prune`` deletes only environments nobody holds and nobody asked for lately, so an environment
a saved result was made in (say a published version) stays as long as that result does.

Preparation runs in the asking process, in a job of its own (``talyx_sdk.jobs``): ``submit`` returns at once, ``get``
reads the state (queued, running, completed, failed, cancelled).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from .errors import ContractError
from .jobs import spawn

UV_ISOLATED = ("--no-config",)
_PYTHON_VERSION = re.compile(r"^3\.(8|9|10|11|12|13)(\.\d+)?$")
_IMPORT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _now() -> int:
    return int(time.time() * 1000)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def runtime_roots(interpreter: str | Path) -> list[str]:
    """The folders an interpreter runs from: its environment and the base Python it was made from."""
    executable = Path(interpreter).resolve(strict=True)
    root = executable.parent.parent if executable.parent.name.casefold() in {"scripts", "bin"} else executable.parent
    roots = [str(root)]
    configuration = root / "pyvenv.cfg"
    if configuration.is_file():
        for line in configuration.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "home":
                roots.append(str(Path(value.strip()).resolve(strict=True)))
    return list(dict.fromkeys(roots))


class Environments:
    """The shared environments, as one module process uses them."""

    def __init__(self, root: str | Path, *, uv: str | None = None):
        self.root = Path(root)
        self.locks = self.root / "locks"
        self.logs = self.root / "logs"
        self.held = self.root / "holders"
        for folder in (self.root, self.locks, self.logs, self.held):
            folder.mkdir(parents=True, exist_ok=True)
        self.uv = uv or shutil.which("uv")
        self.running: dict[str, asyncio.Task] = {}
        self.jobs: dict[str, object] = {}

    # ── identity and records ────────────────────────────────────────────────────────────────────────────────────────
    def _record_path(self, preparation_id: str) -> Path:
        if not re.fullmatch(r"pep_[0-9a-f]{32}", preparation_id or ""):
            raise ContractError("not_found", f"No environment {preparation_id}")
        return self.root / (preparation_id[4:24] + ".json")

    def _record(self, preparation_id: str) -> dict:
        path = self._record_path(preparation_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ContractError("not_found", f"No environment {preparation_id}", {"preparationId": preparation_id}) from None

    def _save(self, record: dict) -> None:
        record["updatedAt"] = _now()
        _write(self._record_path(record["preparationId"]), record)

    def get(self, preparation_id: str) -> dict:
        """The environment's state, where it is and what it holds."""
        record = self._record(preparation_id)
        if record["state"] in {"queued", "running"} and preparation_id not in self.running and not self._alive(record):
            record = {**record, "state": "interrupted"}   # the process preparing it went away
        folder = self.root / record["preparationId"][4:24]
        ready = record["state"] == "completed"
        return {**record, "environmentPath": str(folder),
                "interpreterPath": str(folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")) if ready else None,
                "runtimeRoots": record.get("runtimeRoots", []) if ready else [],
                "lockPath": str(self.locks / f"{record['lockDigest']}.requirements.txt") if ready else None,
                "stdoutPath": str(self.logs / f"{preparation_id}.stdout.log"),
                "stderrPath": str(self.logs / f"{preparation_id}.stderr.log")}

    @staticmethod
    def _alive(record: dict) -> bool:
        pid = record.get("pid")
        if not pid:
            return False
        if pid == os.getpid():
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    # ── asking for one ──────────────────────────────────────────────────────────────────────────────────────────────
    def submit(self, *, python_version: str, requirements_lock: str, verify_imports: list[str], profile_id: str = "",
               lock_digest: str | None = None, **_ignored) -> dict:
        """The environment for this lock; preparation starts now unless it is there or under way. ``duplicate`` says
        it existed. Asking again is using it (it keeps it from being pruned)."""
        if not _PYTHON_VERSION.fullmatch(python_version):
            raise ContractError("invalid_input", "pythonVersion must name CPython 3.8 to 3.13", {"pythonVersion": python_version})
        if len(requirements_lock.encode()) > 1024 * 1024:
            raise ContractError("invalid_input", "The lock is over 1 MiB")
        actual = hashlib.sha256(requirements_lock.encode()).hexdigest()
        if lock_digest is not None and lock_digest != actual:
            raise ContractError("conflict", "The lock does not match its digest", {"expected": lock_digest, "actual": actual})
        if not verify_imports or any(not _IMPORT.fullmatch(name) for name in verify_imports):
            raise ContractError("invalid_input", "verifyImports names an invalid module")
        intent = hashlib.sha256(json.dumps({"python": python_version, "lock": actual, "imports": verify_imports},
                                           sort_keys=True).encode()).hexdigest()
        preparation_id = "pep_" + intent[:32]
        lock = self.locks / f"{actual}.requirements.txt"
        if not lock.exists():
            temporary = lock.with_name(lock.name + f".{os.getpid()}.tmp")
            temporary.write_text(requirements_lock, encoding="utf-8", newline="\n")
            temporary.replace(lock)
        path = self._record_path(preparation_id)
        if path.exists():
            record = self._record(preparation_id)
            state = self.get(preparation_id)["state"]
            if state == "interrupted":
                return {**self.retry(preparation_id), "duplicate": True}
            self._save(record)
            return {**self.get(preparation_id), "duplicate": True}
        record = {"preparationId": preparation_id, "profileId": profile_id, "profileDigest": intent,
                  "pythonVersion": python_version, "lockDigest": actual, "verifyImports": list(verify_imports),
                  "state": "queued", "attempt": 1, "error": None, "packages": [], "createdAt": _now()}
        self._save(record)
        self._start(preparation_id)
        return {**self.get(preparation_id), "duplicate": False}

    def retry(self, preparation_id: str) -> dict:
        """Prepare a failed, cancelled or interrupted environment again."""
        current = self.get(preparation_id)
        if current["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ContractError("conflict", "Only a failed, cancelled or interrupted preparation is tried again",
                                {"state": current["state"]})
        record = self._record(preparation_id)
        record.update(state="queued", attempt=record["attempt"] + 1, error=None)
        self._save(record)
        self._start(preparation_id)
        return self.get(preparation_id)

    async def cancel(self, preparation_id: str) -> dict:
        task = self.running.get(preparation_id)
        if task is not None:
            job = self.jobs.get(preparation_id)
            if job is not None:
                job.terminate()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.get(preparation_id)

    async def ready(self, preparation_id: str, *, poll: float = 0.2) -> dict:
        """Wait until the preparation ends; its final state."""
        while True:
            state = self.get(preparation_id)
            if state["state"] not in {"queued", "running"}:
                return state
            await asyncio.sleep(poll)

    # ── who holds it ────────────────────────────────────────────────────────────────────────────────────────────────
    def _holder_file(self, preparation_id: str, holder: str) -> Path:
        return self.held / preparation_id[4:24] / (hashlib.sha256(holder.encode()).hexdigest()[:32] + ".json")

    def hold(self, preparation_id: str, holder: str) -> None:
        """Say ``holder`` relies on this (completed) environment: it is not pruned while any hold remains."""
        if self.get(preparation_id)["state"] != "completed":
            raise ContractError("conflict", "Only a completed environment can be held", {"preparationId": preparation_id})
        path = self._holder_file(preparation_id, holder)
        path.parent.mkdir(exist_ok=True)
        _write(path, {"holder": holder, "since": _now()})

    def release(self, preparation_id: str, holder: str) -> None:
        with contextlib.suppress(FileNotFoundError, ContractError):
            self._holder_file(preparation_id, holder).unlink()

    def holders(self, preparation_id: str) -> list[str]:
        folder = self.held / preparation_id[4:24]
        if not folder.is_dir():
            return []
        return [json.loads(path.read_text(encoding="utf-8"))["holder"] for path in folder.glob("*.json")]

    def prune(self, *, min_age_days: float) -> list[str]:
        """Delete completed environments nobody holds and nobody asked for in ``min_age_days``."""
        cutoff = _now() - min_age_days * 86_400_000
        removed = []
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            identity = record["preparationId"]
            if record["updatedAt"] > cutoff or identity in self.running or self.holders(identity):
                continue
            folder = self.root / identity[4:24]
            doomed = folder.with_name(folder.name + ".deleting")
            if folder.exists():
                try:
                    folder.rename(doomed)
                except OSError:
                    continue    # in use
            path.unlink(missing_ok=True)
            shutil.rmtree(doomed, ignore_errors=True)
            removed.append(identity)
        return removed

    # ── preparing ───────────────────────────────────────────────────────────────────────────────────────────────────
    def _start(self, preparation_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._prepare(preparation_id))
        self.running[preparation_id] = task
        task.add_done_callback(lambda _: self.running.pop(preparation_id, None))

    async def _run(self, preparation_id: str, argv: list[str]) -> int:
        with open(self.logs / f"{preparation_id}.stdout.log", "ab") as out, \
                open(self.logs / f"{preparation_id}.stderr.log", "ab") as err:
            process, job = await spawn(argv, stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(self.root))
            self.jobs[preparation_id] = job
            try:
                return await process.wait()
            finally:
                job.terminate()
                await job.ended()
                job.close()
                self.jobs.pop(preparation_id, None)

    async def _prepare(self, preparation_id: str) -> None:
        record = self._record(preparation_id)
        record.update(state="running", pid=os.getpid())
        self._save(record)
        folder = self.root / preparation_id[4:24]
        interpreter = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        lock = self.locks / f"{record['lockDigest']}.requirements.txt"
        for log in ("stdout", "stderr"):
            (self.logs / f"{preparation_id}.{log}.log").unlink(missing_ok=True)
        try:
            if not self.uv:
                raise RuntimeError("uv is needed to prepare Python environments; install uv and try again")
            if folder.exists():
                await asyncio.to_thread(shutil.rmtree, folder)
            if await self._run(preparation_id, [self.uv, *UV_ISOLATED, "venv", "--no-project", "--python",
                                                record["pythonVersion"], "--managed-python", str(folder)]):
                raise RuntimeError("uv venv failed; see the preparation log")
            # Each environment owns its files (no hard links into uv's cache): one environment changing a file must
            # not change another's.
            if await self._run(preparation_id, [self.uv, *UV_ISOLATED, "pip", "sync", "--python", str(interpreter),
                                                "--link-mode", "copy", "--require-hashes", str(lock)]):
                raise RuntimeError("uv pip sync failed; see the preparation log")
            check = ("import importlib,json;"
                     f"[importlib.import_module(n) for n in {record['verifyImports']!r}];"
                     "import importlib.metadata as m;"
                     "print(json.dumps(sorted((d.metadata.get('Name') or '',d.version) for d in m.distributions())))")
            if await self._run(preparation_id, [str(interpreter), "-c", check]):
                raise RuntimeError("The environment does not import what it must; see the preparation log")
            last = (self.logs / f"{preparation_id}.stdout.log").read_text(encoding="utf-8").splitlines()[-1]
            record = self._record(preparation_id)
            record.update(state="completed", error=None, pid=None, runtimeRoots=runtime_roots(interpreter),
                          packages=[{"name": name, "version": version} for name, version in json.loads(last)])
            self._save(record)
        except asyncio.CancelledError:
            record = self._record(preparation_id)
            record.update(state="cancelled", pid=None)
            self._save(record)
            raise
        except Exception as exc:
            record = self._record(preparation_id)
            record.update(state="failed", pid=None, error={"code": "preparation_failed", "message": str(exc)})
            self._save(record)
