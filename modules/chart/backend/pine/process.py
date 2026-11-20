"""One Pine worker process, seen from the Host.

Protocol (JSON lines). Host to worker:
- ``{"op": "series", "series", "syminfo", "bars"}`` sets a series' closed bars (columns time,
  open, high, low, close, volume); instances on an earlier window of that key stop.
- ``{"op": "start", "id", "series", "path", "root", "params"}`` starts an instance on a series.
- ``{"op": "bar", "series", "bar", "closed"}`` a closed bar or the forming bar.
- ``{"op": "stop", "id"}``, ``{"op": "drop", "series"}``.
- ``{"op": "feed", "symbol", "timeframe", "kind": "replace", "file", "count", "tail", "forming"}``
  or ``{..., "kind": "bar", "bar", "closed"}``: another symbol's or timeframe's bars for
  ``request.security``: the history in a file (``history.py``, ``count`` rows), then the bars
  after it (``tail``, bars with time, closeTime, open, high, low, close, volume).
- ``{"op": "feed_failed", "symbol", "timeframe", "message"}``: Chart cannot supply that feed.
- ``{"op": "describe", "request", "path", "root"}``.
Worker to Host: ``{"op": "ready"}`` once; ``{"id", "op": replace|append|update, "time", "values",
"colors", "meta"?, "behindChart"?, "noScale"?, "drawings"?, "drawingChanges"?}`` (every drawing, on a replace or the first
time there are any; after that the ones made or changed and the ids gone, per family:
``{family: {"set", "drop"}}``),
``{"id", "op": "error", "code", "message", "line"}`` and ``{"id", "op": "need", "symbol", "timeframe"}`` (a request.security context of the
instance: it waits for that feed) per instance; ``{"reply", "ok" | "error"}`` for describe.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from talyx_sdk.jobs import Job, spawn

ROOT = Path(__file__).resolve().parents[4]
LINE_LIMIT = 256 * 1024 * 1024


class WorkerGone(RuntimeError):
    pass


class PineProcess:
    """``on_message`` receives each instance message; ``on_exit`` runs when the process ends
    without being closed (it died or was killed for a stuck script)."""

    def __init__(self, sites: tuple[str, ...], log: Path, feeds: Path, pycache: Path,
                 on_message: Callable[[dict], None], on_exit: Callable[[], None]):
        """``pycache``: where the process and its children keep compiled code, PyneCore's transformed
        scripts among it: transforming a script anew took about 60 ms of each run."""
        self.sites, self.log, self.feeds, self.pycache = sites, log, feeds, pycache
        self.on_message, self.on_exit = on_message, on_exit
        self.process: asyncio.subprocess.Process | None = None
        self.job: Job | None = None     # the worker and the processes its scripts start end together
        self.replies: dict[int, asyncio.Future] = {}
        self.requests = itertools.count()
        self.closing = False
        self.reader: asyncio.Task | None = None

    async def start(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("ab") as stderr:
            self.process, self.job = await spawn(
                [sys.executable, "-m", "modules.chart.backend.pine.worker", str(self.feeds), *self.sites],
                cwd=ROOT, env={**os.environ, "PYTHONPYCACHEPREFIX": str(self.pycache)},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr, limit=LINE_LIMIT)
        ready = json.loads(await self.process.stdout.readline())
        assert ready == {"op": "ready"}
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for line in self.process.stdout:
            message = json.loads(line)
            if "reply" in message:
                future = self.replies.pop(message["reply"])
                if not future.cancelled():  # the asker was cancelled while waiting (a cancelled screen run)
                    future.set_result(message)
            else:
                self.on_message(message)
        await self.process.wait()
        for future in self.replies.values():
            if not future.cancelled():
                future.set_exception(WorkerGone("The Pine worker stopped"))
        self.replies.clear()
        if not self.closing:
            self.job.close()
            self.on_exit()

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n")

    async def describe(self, path: Path, root: Path | None) -> dict:
        request = next(self.requests)
        future = self.replies[request] = asyncio.get_running_loop().create_future()
        self.send({"op": "describe", "request": request, "path": str(path), "root": str(root) if root else None})
        return await future

    def kill(self) -> None:
        """Ends the process and what its scripts started; ``on_exit`` follows (a stuck user script)."""
        self.job.terminate()

    async def close(self) -> None:
        self.closing = True
        self.job.terminate()
        await self.process.wait()
        await self.job.ended()
        self.job.close()
        if self.reader is not None:
            await self.reader
